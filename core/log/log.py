"""Coordinates the durable store (SQLite) and the hot tier (Redis) into one memory API.

  write:  write_episode -> Redis stream (XADD) -> drain() -> SQLite. Redis down -> SQLite directly.
  read:   search -> SQLite FTS5 (BM25);  recent -> Redis cache, else SQLite tail.
  expand: expand(seq) -> SQLite verbatim recovery (positional recall).

The stream is the serialization point: many workers XADD concurrently, one drain() consumer
moves them into SQLite in order. That many-writer safety is why this is a datastore, not
markdown files. Port of memory.js.
"""

from __future__ import annotations

import sys
import time
from typing import Optional

from core.log.redis import Redis, RedisUnavailable
from core.log.store import EpisodeStore
from core.types import Episode, Seq

_STREAM = "fools:mem:stream"
_GROUP = "fools:mem:drain"


_warned_redis_down = False


def _warn_redis_down() -> None:
    """Redis down degrades the shared hot tier silently unless we say so. Log it ONCE (a trip
    wire, per the harness playbook), not per call -- the SQLite fallback keeps durability intact,
    but a degraded swarm is a thing the operator should see, not a mode we hide."""
    global _warned_redis_down
    if not _warned_redis_down:
        _warned_redis_down = True
        print("fools-trick memory: Redis unreachable -- writing straight to SQLite "
              "(write-stream + hot tier degraded). Redis is started by `make up`.",
              file=sys.stderr)


class MemoryLog:
    """The one memory API the tools and context policy call.

    resolve_thread is INJECTED (a callable str -> str): the core never names a harness.
    The opencode adapter passes a walker over `opencode db`; another harness passes its own;
    tests pass identity. Default is identity (single-thread scoping) so the core is usable
    standalone.
    """

    def __init__(
        self,
        db_path: str,
        redis_url: str = "redis://127.0.0.1:6379",
        resolve_thread=None,
    ):
        self.store = EpisodeStore(db_path)
        self.redis = Redis(redis_url)
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
        durable: bool = False,
    ) -> Optional[Seq]:
        """Durably record an episode. Returns the seq (Event Log address) if one is assigned
        synchronously, else None.

        Return contract:
          durable=True -> synchronous SQLite append; returns the seq now. Use when the caller
            needs the address immediately (eviction persistence, where the index line handed to
            the worker must reference an address that already exists).
          durable=False, Redis up -> XADD onto the write stream; the seq is assigned later on
            drain, so this returns None (no address yet). The many-writer serialization path.
          durable=False, Redis down -> SQLite fallback append; returns the seq (it was assigned
            synchronously). So a Redis-down write is addressable while a Redis-up write is not.

        The eviction-index race this fixes: the queue path left the seq None at eviction time,
        so a just-evicted result had no address to recover by (found by the orchestrator reviewing
        this code). Eviction persistence uses durable=True so the address exists before the payload
        leaves the view.
        """
        rec = {
            "thread": thread,
            "session": session or "",
            "agent": agent or "",
            "role": role or "",
            "content": content or "",
            "ts": int(ts if ts is not None else int(time.time() * 1000)),
        }
        if durable:
            return self.store.append(
                thread=rec["thread"], session=rec["session"], agent=rec["agent"],
                role=rec["role"], content=rec["content"], ts=rec["ts"],
            )
        try:
            self.redis.cmd(
                "XADD", _STREAM, "*",
                "thread", rec["thread"], "session", rec["session"], "agent", rec["agent"],
                "role", rec["role"], "content", rec["content"], "ts", str(rec["ts"]),
            )
        except RedisUnavailable:
            _warn_redis_down()
            return self.store.append(
                thread=rec["thread"], session=rec["session"], agent=rec["agent"],
                role=rec["role"], content=rec["content"], ts=rec["ts"],
            )
        return None

    def drain(self, limit: int = 500) -> int:
        """Move stream entries into SQLite. Consumer group delivers each once; ack + delete."""
        try:
            try:
                self.redis.cmd("XGROUP", "CREATE", _STREAM, _GROUP, "0", "MKSTREAM")
            except RedisUnavailable:
                pass  # group may already exist
            res = self.redis.cmd(
                "XREADGROUP", "GROUP", _GROUP, "drainer", "COUNT", str(limit),
                "STREAMS", _STREAM, ">",
            )
        except RedisUnavailable:
            return 0  # redis down: nothing to drain, writes went straight to SQLite
        if not res:
            return 0
        moved = 0
        for _stream_name, entries in res:
            for entry_id, fields in entries:
                f = {fields[i]: fields[i + 1] for i in range(0, len(fields), 2)}
                # Idempotent append: a crash between the SQLite write and the XACK/XDEL would
                # redeliver this entry; dedup on (thread, ts, content) keeps the original seq
                # instead of minting a duplicate with a new address (closes the redelivery hole).
                self.store.append_if_absent(
                    thread=f.get("thread", ""), session=f.get("session", ""),
                    agent=f.get("agent", ""), role=f.get("role", ""),
                    content=f.get("content", ""),
                    ts=int(f["ts"]) if f.get("ts") else int(time.time() * 1000),
                )
                try:
                    self.redis.cmd("XACK", _STREAM, _GROUP, entry_id)
                    self.redis.cmd("XDEL", _STREAM, entry_id)
                except RedisUnavailable:
                    pass
                moved += 1
        return moved

    def search(self, *, thread: str, query: str, k: int = 10) -> list[Episode]:
        self.drain()  # so just-written episodes are searchable
        return self.store.search(thread=thread, query=query, k=k)

    def recent(self, *, thread: str, k: int = 20) -> list[Episode]:
        # The hot recent-cache tier was cut: nothing in the live path read it. If a real reader
        # ever needs low-latency recent recall, add the Redis cache back behind this method.
        self.drain()
        return self.store.recent(thread=thread, k=k)

    def expand(self, seq: Seq) -> Optional[Episode]:
        """Recover an episode verbatim by Event Log address (positional recall)."""
        self.drain()
        return self.store.expand(seq)

    def close(self) -> None:
        self.store.close()
        self.redis.close()
