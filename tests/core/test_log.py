"""MemoryLog + ThreadResolver tests. stdlib unittest, no harness on disk.

Redis round-trip runs only if reachable (skipped cleanly otherwise, like the JS suite).
The SQLite-fallback, thread resolution, and expand paths all run fully offline.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.log.log import MemoryLog  # noqa: E402
from core.log.redis import Redis, RedisUnavailable  # noqa: E402
from core.log.thread import MISSING, dict_resolver, identity_resolver  # noqa: E402


def _redis_up() -> bool:
    try:
        return Redis(timeout=0.5).cmd("PING") == "PONG"
    except RedisUnavailable:
        return False


class TestThreadResolver(unittest.TestCase):
    def test_identity_scopes_to_self(self):
        r = identity_resolver()
        self.assertEqual(r.resolve("sess-1"), "sess-1")

    def test_empty_is_default(self):
        self.assertEqual(identity_resolver().resolve(""), "default")

    def test_walks_to_root(self):
        # child -> mid -> root(None)
        r = dict_resolver({"child": "mid", "mid": "root", "root": None})
        self.assertEqual(r.resolve("child"), "root")
        self.assertEqual(r.resolve("mid"), "root")

    def test_missing_lookup_falls_back_to_self(self):
        r = dict_resolver({})  # every lookup is MISSING
        self.assertEqual(r.resolve("orphan"), "orphan")

    def test_cycle_guard_terminates(self):
        r = dict_resolver({"a": "b", "b": "a"})  # cycle
        # must terminate within max_hops and return something, not hang
        self.assertIn(r.resolve("a"), {"a", "b"})


class TestMemoryLogOffline(unittest.TestCase):
    """With Redis down, write_episode must fall back to SQLite and stay searchable/expandable."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        # point at an unreachable redis so the fallback path is exercised deterministically
        self.log = MemoryLog(
            db_path=os.path.join(self._tmp.name, "m.db"),
            redis_url="redis://127.0.0.1:6399",  # nothing listens here
            resolve_thread=identity_resolver().resolve,
        )

    def tearDown(self):
        self.log.close()
        self._tmp.cleanup()

    def test_write_falls_back_to_sqlite_and_searchable(self):
        self.log.write_episode(
            thread="T", session="s", agent="build", role="memory",
            content="decided to use q8_0 KV for the hybrid arch",
        )
        hits = self.log.search(thread="T", query="q8_0 KV")
        self.assertGreaterEqual(len(hits), 1)

    def test_expand_recovers_written_episode(self):
        self.log.write_episode(
            thread="T", session="s", agent="build", role="memory", content="anchor fact",
        )
        hit = self.log.search(thread="T", query="anchor")[0]
        ep = self.log.expand(hit.seq)
        self.assertIsNotNone(ep)
        self.assertEqual(ep.content, "anchor fact")

    def test_drain_redelivery_does_not_duplicate(self):
        # The redelivery hole: a crash between the SQLite append and the Redis ack would
        # redeliver the stream entry. append_if_absent must keep the original seq, not mint a dup.
        store = self.log.store
        s1 = store.append_if_absent(thread="T", session="s", agent="a", role="memory",
                                    content="crash-window entry", ts=111)
        s2 = store.append_if_absent(thread="T", session="s", agent="a", role="memory",
                                    content="crash-window entry", ts=111)
        self.assertEqual(s1, s2)  # same seq, no duplicate
        self.assertEqual(len(self.log.search(thread="T", query="crash-window")), 1)


@unittest.skipUnless(_redis_up(), "redis not reachable")
class TestMemoryLogRedis(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.log = MemoryLog(
            db_path=os.path.join(self._tmp.name, "m.db"),
            resolve_thread=identity_resolver().resolve,
        )
        self.log.redis.cmd("DEL", "fools:mem:stream")

    def tearDown(self):
        self.log.close()
        self._tmp.cleanup()

    def test_stream_drains_to_sqlite(self):
        self.log.write_episode(thread="C", session="o", agent="build", role="user",
                               content="concurrent write about sliding window")
        self.log.write_episode(thread="C", session="w1", agent="explore", role="assistant",
                               content="concurrent write about memory recall")
        self.assertGreaterEqual(len(self.log.search(thread="C", query="sliding recall")), 1)


if __name__ == "__main__":
    unittest.main()
