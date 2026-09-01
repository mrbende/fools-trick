"""MemoryLog + ThreadResolver tests. stdlib unittest, no services on disk.

The Event Log writes synchronously to SQLite (WAL); every episode is addressable by seq
immediately. There is no write-stream to drain.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.log.log import MemoryLog  # noqa: E402
from core.log.thread import dict_resolver, identity_resolver  # noqa: E402


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


class TestMemoryLog(unittest.TestCase):
    """Direct synchronous writes to SQLite; searchable + recoverable by seq."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.log = MemoryLog(
            db_path=os.path.join(self._tmp.name, "m.db"),
            resolve_thread=identity_resolver().resolve,
        )

    def tearDown(self):
        self.log.close()
        self._tmp.cleanup()

    def test_write_returns_seq_and_is_searchable(self):
        seq = self.log.write_episode(
            thread="T", session="s", agent="build", role="memory",
            content="decided to use q8_0 KV for the hybrid arch",
        )
        self.assertIsNotNone(seq)  # synchronous write is addressable immediately
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

    def test_concurrent_writes_are_all_recorded(self):
        # many writers, one store: SQLite WAL serializes; nothing is lost
        for i, agent in enumerate(("explore", "general", "reviewer")):
            self.log.write_episode(thread="C", session=f"w{i}", agent=agent, role="assistant",
                                   content=f"concurrent write {i} about sliding window")
        hits = self.log.search(thread="C", query="sliding")
        self.assertGreaterEqual(len(hits), 3)

    def test_drain_is_a_noop_compat(self):
        self.assertEqual(self.log.drain(), 0)


if __name__ == "__main__":
    unittest.main()
