"""Durable episode store: SQLite (source of truth) + FTS5 recall.

Port of the JS store.js, now native to Python. An episode is one raw, non-lossy unit of
memory (a turn, a memory_write, or an evicted tool result), keyed by thread (the
conversation's root session id) so recall is scoped per conversation. The autoincrement
`id` IS the Event Log `seq` address. No external dependency: stdlib sqlite3 with FTS5.
"""

from __future__ import annotations

import os
import sqlite3
import time
from typing import Optional

from core.types import Episode, Seq

_SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  thread  TEXT NOT NULL,
  session TEXT NOT NULL,
  agent   TEXT,
  role    TEXT,
  content TEXT NOT NULL,
  ts      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_episodes_thread ON episodes(thread, id);
CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(
  content, thread UNINDEXED, content='episodes', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS episodes_ai AFTER INSERT ON episodes BEGIN
  INSERT INTO episodes_fts(rowid, content, thread) VALUES (new.id, new.content, new.thread);
END;
CREATE TRIGGER IF NOT EXISTS episodes_ad AFTER DELETE ON episodes BEGIN
  INSERT INTO episodes_fts(episodes_fts, rowid, content, thread)
    VALUES('delete', old.id, old.content, old.thread);
END;
"""

# Match on ANY alphanumeric term (OR-combined), so recall finds "related past" rather than
# an exact phrase, and arbitrary punctuation cannot raise an FTS syntax error.
import re

_TERM = re.compile(r"[a-z0-9]+")


class EpisodeStore:
    """Owns one SQLite connection to the durable episode store.

    A DB failure is the caller's to handle; this class does not swallow errors except the
    FTS-syntax fallback in search(). WAL mode gives concurrent readers + one writer, which
    is what the single-drainer, many-reader design needs.
    """

    def __init__(self, path: str):
        self.path = path
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        # check_same_thread=False: the store may be touched from a drain thread and a
        # request thread; access is serialized by the single-writer discipline above it.
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode = WAL;")
        self._db.execute("PRAGMA synchronous = NORMAL;")
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def append(
        self,
        *,
        thread: str,
        session: str,
        agent: Optional[str],
        role: Optional[str],
        content: str,
        ts: Optional[int] = None,
    ) -> Seq:
        """Append an episode. Returns its seq (the Event Log address)."""
        ts = ts if ts is not None else int(time.time() * 1000)
        cur = self._db.execute(
            "INSERT INTO episodes (thread, session, agent, role, content, ts) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (thread, session, agent, role, content, ts),
        )
        self._db.commit()
        return int(cur.lastrowid)

    def append_if_absent(
        self,
        *,
        thread: str,
        session: str,
        agent: Optional[str],
        role: Optional[str],
        content: str,
        ts: Optional[int] = None,
    ) -> Seq:
        """Idempotent append for the drain path: if an identical episode (same thread, ts,
        content) already exists, return its existing seq instead of a duplicate.

        The drain moves a stream entry to SQLite then acks it; a crash between the two redelivers
        the entry, which would otherwise mint a duplicate episode with a NEW seq and silently
        invalidate any eviction index that captured the original address. Dedup on the
        (thread, ts, content) fingerprint closes that redelivery hole.
        """
        ts = ts if ts is not None else int(time.time() * 1000)
        row = self._db.execute(
            "SELECT id FROM episodes WHERE thread = ? AND ts = ? AND content = ? LIMIT 1",
            (thread, ts, content),
        ).fetchone()
        if row:
            return int(row[0])
        return self.append(thread=thread, session=session, agent=agent, role=role,
                           content=content, ts=ts)

    def search(self, *, thread: str, query: str, k: int = 10) -> list[Episode]:
        """FTS5/BM25 recall within a thread; best match first.

        Terms are lowercased, alphanumeric-tokenized, quoted and OR-joined. Falls back to a
        LIKE scan if FTS yields nothing or errors (so a query of only punctuation still
        returns something rather than raising).
        """
        terms = _TERM.findall(str(query).lower())
        if terms:
            match = " OR ".join(f'"{t}"' for t in terms)
            try:
                rows = self._db.execute(
                    "SELECT e.id, e.thread, e.session, e.agent, e.role, e.content, e.ts "
                    "FROM episodes_fts f JOIN episodes e ON e.id = f.rowid "
                    "WHERE f.thread = ? AND episodes_fts MATCH ? "
                    "ORDER BY bm25(episodes_fts) LIMIT ?",
                    (thread, match, k),
                ).fetchall()
                if rows:
                    return [self._row(r) for r in rows]
            except sqlite3.OperationalError:
                pass  # fall through to LIKE
        rows = self._db.execute(
            "SELECT id, thread, session, agent, role, content, ts FROM episodes "
            "WHERE thread = ? AND content LIKE ? ORDER BY id DESC LIMIT ?",
            (thread, f"%{query}%", k),
        ).fetchall()
        return [self._row(r) for r in rows]

    def recent(self, *, thread: str, k: int = 20) -> list[Episode]:
        """Most recent N episodes in a thread, oldest-first (for rewarming a cache or a tail)."""
        rows = self._db.execute(
            "SELECT id, thread, session, agent, role, content, ts FROM episodes "
            "WHERE thread = ? ORDER BY id DESC LIMIT ?",
            (thread, k),
        ).fetchall()
        return [self._row(r) for r in reversed(rows)]

    def expand(self, seq: Seq) -> Optional[Episode]:
        """Recover one episode verbatim by its Event Log address.

        The positional complement to search(): a worker that evicted a result from its view
        gets it back by seq. This is what makes eviction recoverable rather than lossy.
        """
        row = self._db.execute(
            "SELECT id, thread, session, agent, role, content, ts FROM episodes WHERE id = ?",
            (seq,),
        ).fetchone()
        return self._row(row) if row else None

    def close(self) -> None:
        self._db.close()

    @staticmethod
    def _row(r) -> Episode:
        return Episode(
            seq=int(r[0]),
            thread=r[1],
            session=r[2],
            agent=r[3],
            role=r[4],
            content=r[5],
            ts=int(r[6]),
        )
