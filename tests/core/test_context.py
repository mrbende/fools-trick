"""Context policy tests: reasoning-aware estimation, recoverable worker prune, orchestrator
slide, and an explicit doom-loop reproduction that the fix must survive.

stdlib unittest, no harness on disk.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.context.estimate import est_tokens, input_tokens  # noqa: E402
from core.context.window import plan_slide, plan_worker_prune  # noqa: E402
from core.types import ToolResult, Turn  # noqa: E402


def _tool_turn(call_id, chars, agent="explore"):
    return Turn(role="assistant", agent=agent, session="w",
                tool_results=[ToolResult(call_id=call_id, text="x" * chars)])


class TestEstimate(unittest.TestCase):
    def test_reasoning_counts_toward_budget(self):
        # bug 3: a turn whose bulk is reasoning must not read as near-empty
        t = Turn(role="assistant", text="ok", reasoning="y" * 35000)  # ~10k tokens
        with_r = input_tokens([t], include_reasoning=True)
        without_r = input_tokens([t], include_reasoning=False)
        self.assertGreater(with_r, without_r + 9000)

    def test_compacted_result_not_counted(self):
        t = Turn(role="assistant",
                 tool_results=[ToolResult(call_id="c", text="z" * 3500, compacted=True)])
        self.assertEqual(input_tokens([t]), 0)


class TestWorkerPrune(unittest.TestCase):
    def test_noop_under_budget(self):
        turns = [_tool_turn("s1", 3500), _tool_turn("s2", 3500)]
        d = plan_worker_prune(turns, input_budget=26000, keep_recent=3)
        self.assertFalse(d.changed)

    def test_distilled_evicted_first(self):
        # 6 results ~7000 tokens each (24500 chars), budget 26000, keep_recent 2
        turns = [_tool_turn(f"c{i}", 24500) for i in range(1, 7)]
        d = plan_worker_prune(turns, input_budget=26000, keep_recent=2,
                              distilled={"c2", "c4"})
        self.assertIn("c2", d.evict_call_ids)
        self.assertIn("c4", d.evict_call_ids)
        self.assertNotIn("c6", d.evict_call_ids)  # last keep_recent protected
        self.assertNotIn("c5", d.evict_call_ids)

    def test_evicted_results_are_persisted_and_indexed(self):
        turns = [_tool_turn(f"c{i}", 24500) for i in range(1, 7)]
        d = plan_worker_prune(turns, input_budget=26000, keep_recent=2, distilled={"c1"})
        # every evicted result must be persisted (lossless) and carry an index headline
        self.assertEqual(len(d.persist), len(d.evict_call_ids))
        self.assertEqual(len(d.index_entries), len(d.evict_call_ids))
        self.assertTrue(all("recall with expand" in e for e in d.index_entries))

    def test_backstop_is_size_aware_largest_first(self):
        # bug 2: with nothing distilled, the biggest result should be evicted to reclaim most
        turns = [
            _tool_turn("small", 7000),
            _tool_turn("huge", 70000),
            _tool_turn("mid", 21000),
            _tool_turn("recent", 3500),
        ]
        d = plan_worker_prune(turns, input_budget=20000, keep_recent=1)
        self.assertIn("huge", d.evict_call_ids)  # largest goes first
        self.assertNotIn("recent", d.evict_call_ids)  # protected tail

    def test_pinned_result_never_evicted(self):
        turns = [_tool_turn(f"c{i}", 24500) for i in range(1, 7)]
        d = plan_worker_prune(turns, input_budget=26000, keep_recent=1, pinned={"c1"})
        self.assertNotIn("c1", d.evict_call_ids)

    def test_doom_loop_reproduction_fixed(self):
        # The doom-loop: worker reads A(c1), reads B(c2), distills A. The OLD code marked the
        # most-recent callID (c2 = B, the file it's USING) evictable and cleared it, keeping
        # the already-extracted A. Here the distill set names c1 (A) correctly, so the fix
        # evicts A and keeps B -- the worker retains its live working set. And even if the
        # gate were wrong, B is recoverable via expand(seq), so it can never be a lobotomy.
        A = _tool_turn("c1", 12000)   # read file A (~4800 tok at the 2.5 divisor), distilled
        B = _tool_turn("c2", 12000)   # read file B, actively in use
        C = _tool_turn("c3", 12000)   # forces over-budget (3x4800 = 14400)
        turns = [A, B, C]
        # budget forces exactly ONE eviction so the scenario tests the choice, not the budget;
        # keep_recent=1 protects C only
        d = plan_worker_prune(turns, input_budget=10000, keep_recent=1, distilled={"c1"})
        self.assertIn("c1", d.evict_call_ids)       # the distilled (extracted) file goes
        self.assertNotIn("c2", d.evict_call_ids)    # the in-use file is kept
        # and the evicted file is recoverable, not lost
        self.assertTrue(any("call=c1" in e for e in d.index_entries))


class TestSlide(unittest.TestCase):
    def _turn(self, role, text):
        return Turn(role=role, session="root", agent="build", text=text)

    def test_slide_keeps_system_and_tail(self):
        turns = [self._turn("system", "s" * 3500)] + [
            self._turn("user", "u" * 35000) for _ in range(10)
        ]
        d = plan_slide(turns, input_budget=26000, keep_tail=6)
        self.assertTrue(d.drop_turn_indices)
        self.assertNotIn(0, d.drop_turn_indices)  # system never dropped
        keep_from = len(turns) - 6
        self.assertTrue(all(i < keep_from for i in d.drop_turn_indices))

    def test_slide_persists_evicted_lossless(self):
        turns = [self._turn("user", "u" * 35000) for _ in range(10)]
        d = plan_slide(turns, input_budget=26000, keep_tail=3)
        self.assertTrue(all(p["content"] for p in d.persist))


if __name__ == "__main__":
    unittest.main()
