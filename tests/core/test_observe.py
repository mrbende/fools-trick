"""Observability tests: rollup aggregation + trip-wire detection. Pure synthetic data, no
opencode DB needed (the _rollup/check functions are pure). stdlib unittest.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.observe.rollups import _rollup  # noqa: E402
from core.observe.tripcheck import check  # noqa: E402


def _row(id, parent, tin=0, tout=0, treas=0, agent="build", prov="magus", mdl="m"):
    return {"id": id, "parent_id": parent, "agent": agent, "prov": prov, "mdl": mdl,
            "tokens_input": tin, "tokens_output": tout, "tokens_reasoning": treas,
            "time_created": 1000, "time_updated": 5000}


class TestRollup(unittest.TestCase):
    def test_root_plus_descendants_aggregate(self):
        rows = [
            _row("root", None, tin=1000, tout=200),
            _row("c1", "root", tin=500, tout=100, agent="explore"),
            _row("c2", "root", tin=500, tout=100, agent="general"),
        ]
        t = _rollup(rows, "root")
        self.assertEqual(t.subagents, 2)
        self.assertEqual(t.tokens_total, 1000 + 200 + 500 + 100 + 500 + 100)

    def test_wall_seconds_from_ms(self):
        t = _rollup([_row("root", None)], "root")
        self.assertEqual(t.wall_s, 4.0)

    def test_no_descendants(self):
        t = _rollup([_row("root", None)], "root")
        self.assertEqual(t.subagents, 0)


class TestTripWires(unittest.TestCase):
    def _baseline(self, n=5, tokens=1000, wall=10):
        return [_rollup([_row(f"b{i}", None, tin=tokens, tout=0)], f"b{i}") for i in range(n)]

    def test_token_spike_fires(self):
        cur = _rollup([_row("c", None, tin=5000, tout=0)], "c")
        fired = [w for w in check(cur, self._baseline()) if w.name == "token-spike" and w.fired]
        self.assertTrue(fired)

    def test_normal_task_no_spike(self):
        cur = _rollup([_row("c", None, tin=1000, tout=0)], "c")
        fired = [w.name for w in check(cur, self._baseline()) if w.fired]
        self.assertNotIn("token-spike", fired)

    def test_empty_baseline_is_soft(self):
        cur = _rollup([_row("c", None, tin=9999, tout=0)], "c")
        wires = check(cur, [])
        self.assertEqual(wires[0].name, "baseline")
        self.assertFalse(wires[0].fired)


if __name__ == "__main__":
    unittest.main()
