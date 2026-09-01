"""The Event Log: a durable, addressable memory store backed by SQLite.

  write:  write_episode -> synchronous SQLite append (WAL + busy_timeout handles concurrent writers)
  read:   search -> SQLite FTS5 (BM25);  recent -> SQLite tail
  expand: expand(seq) -> SQLite verbatim recovery (positional recall)

Every episode is addressable by seq the moment it's written. The earlier Redis write-stream was
removed: every load-bearing write already went durable/direct to SQLite, and WAL absorbs the ~5
concurrent workers' write rate, so the stream added a container without changing durability.
"""

from __future__ import annotations

import time
from typing import Optional

from core.log.store import EpisodeStore
from core.types import Episode, Seq


class MemoryLog:
    """The one memory API the tools and context policy call.

    resolve_thread is INJECTED (a callable str -> str): the core never names a harness.
    The opencode adapter passes a walker over `opencode db`; another harness passes its own;
    tests pass identity. Default is identity (single-thread scoping) so the core is usable
    standalone.
    """

    def __init__(self, db_path: str, resolve_thread=None):
        self.store = EpisodeStore(db_path)
        self._resolve_thread = resolve_thread or (lambda sid: sid or "default")

    def resolve_thread(self, session_id: str) -> str:
        return self._resolve_thread(session_id)

    def write_episode(
        self,
        *,
        thread: str,
        session: str,
        agent: str,
        role: str,
        content: str,
        ts: Optional[int] = None,
        durable: bool = False,  # kept for call-site compat; all writes are durable/synchronous now
    ) -> Optional[Seq]:
        """Durably record an episode and return its seq (Event Log address) immediately."""
        return self.store.append(
            thread=thread, session=session or "", agent=agent or "", role=role or "",
            content=content or "", ts=int(ts if ts is not None else int(time.time() * 1000)),
        )

    def drain(self, limit: int = 500) -> int:
        """No-op compat shim: writes are synchronous now (no write-stream to drain)."""
        return 0

    def search(self, *, thread: str, query: str, k: int = 10,
               role=None, agent=None, after_seq=None, before_seq=None) -> list[Episode]:
        return self.store.search(thread=thread, query=query, k=k,
                                 role=role, agent=agent, after_seq=after_seq, before_seq=before_seq)

    def recent(self, *, thread: str, k: int = 20) -> list[Episode]:
        return self.store.recent(thread=thread, k=k)

    def expand(self, seq: Seq) -> Optional[Episode]:
        """Recover an episode verbatim by Event Log address (positional recall)."""
        return self.store.expand(seq)

    def close(self) -> None:
        self.store.close()
