"""Event Log store tests. stdlib unittest, no harness on disk -- the portability proof for core/log.

Run: python3 -m unittest discover -s tests   (or: make test)
Mirrors the JS store tests (tests/test_memory.mjs) plus the new expand(seq) recovery path.
"""

import os
import sys
import tempfile
import unittest

# Make the repo root importable so `import core...` works regardless of cwd.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.log.store import EpisodeStore  # noqa: E402


class TestEpisodeStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = EpisodeStore(os.path.join(self._tmp.name, "test.db"))
        self.ids = {
            "kv": self.store.append(
                thread="A", session="s1", agent="build", role="user",
                content="chose q8_0 KV to avoid the CPU spill on the hybrid arch",
            ),
            "ctx": self.store.append(
                thread="A", session="s2", agent="explore", role="assistant",
                content="worker serves 45056 context per slot across two GPUs",
            ),
            "redis_b": self.store.append(
                thread="B", session="s3", agent="general", role="user",
                content="unrelated thread about redis containers",
            ),
        }

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()

    def test_fts_finds_relevant_episode(self):
        self.assertGreaterEqual(len(self.store.search(thread="A", query="KV spill")), 1)

    def test_thread_scoping_no_leak(self):
        self.assertEqual(len(self.store.search(thread="A", query="redis")), 0)
        self.assertEqual(len(self.store.search(thread="B", query="redis")), 1)

    def test_recent_preserves_insertion_order(self):
        self.assertTrue(self.store.recent(thread="A", k=5)[0].content.startswith("chose q8_0"))

    def test_bm25_best_match_first(self):
        self.assertIn("45056", self.store.search(thread="A", query="context slot GPUs")[0].content)

    def test_expand_recovers_by_seq(self):
        ep = self.store.expand(self.ids["ctx"])
        self.assertIsNotNone(ep)
        self.assertEqual(ep.seq, self.ids["ctx"])
        self.assertIn("45056", ep.content)
        self.assertEqual(ep.agent, "explore")

    def test_expand_missing_returns_none(self):
        self.assertIsNone(self.store.expand(999999))

    def test_punctuation_query_does_not_raise(self):
        self.assertIsInstance(self.store.search(thread="A", query="!!! ??? ***"), list)

    def test_scoped_search_by_role_agent_and_seq(self):
        # decisions vs tool results are separable; a seq range scopes to a window
        self.store.append(thread="S", session="s", agent="build", role="memory",
                          content="chose the q8_0 KV cache", ts=100)
        self.store.append(thread="S", session="s", agent="explore", role="tool",
                          content="read output mentioning the q8_0 KV cache", ts=200)
        self.store.append(thread="S", session="s", agent="general", role="memory",
                          content="a later q8_0 KV cache decision", ts=300)
        by_role = self.store.search(thread="S", query="q8_0 KV", role="memory")
        self.assertTrue(all(e.role == "memory" for e in by_role))
        by_agent = self.store.search(thread="S", query="q8_0 KV", agent="explore")
        self.assertTrue(all(e.agent == "explore" for e in by_agent))
        seqs = [e.seq for e in self.store.search(thread="S", query="q8_0 KV", after_seq=1)]
        self.assertTrue(all(s > 1 for s in seqs))


if __name__ == "__main__":
    unittest.main()
