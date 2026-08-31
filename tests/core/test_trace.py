"""Trace tests: the trajectory reconstruction reads the opencode DB correctly. Uses a real session
from the live DB if present (the runaway worker is ideal), else skips -- this is an integration
read against opencode's store, not a pure unit. stdlib unittest.
"""

import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.observe.trace import _ended, _tool_counts  # noqa: E402


def _db_up():
    try:
        out = subprocess.run(["opencode", "db", "--format", "json", "SELECT 1"],
                             capture_output=True, text=True, timeout=10)
        return out.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


class TestTraceHelpers(unittest.TestCase):
    def test_tool_counts(self):
        steps = [{"tool": "read"}, {"tool": "read"}, {"tool": "note"}]
        self.assertEqual(_tool_counts(steps), {"read": 2, "note": 1})

    def test_ended_states(self):
        self.assertEqual(_ended([]), "no-parts")
        self.assertEqual(_ended([{"t": "tool", "status": "error"}]), "tool-error")
        self.assertEqual(_ended([{"t": "text"}]), "answered")
        self.assertEqual(_ended([{"t": "step-finish"}]), "step-finish")


@unittest.skipUnless(_db_up(), "opencode db not reachable")
class TestTraceLive(unittest.TestCase):
    def test_trace_a_real_subagent_session(self):
        from core.observe.trace import query_opencode_db, trace_session
        rows = query_opencode_db(
            "SELECT id FROM session WHERE parent_id IS NOT NULL ORDER BY time_created DESC LIMIT 1",
            ".")
        if not rows:
            self.skipTest("no subagent sessions in the DB")
        sid = rows[0]["id"]
        t = trace_session(sid, ".")
        self.assertIn(t["agent"], ("explore", "general", "reviewer", "build", "plan"))
        self.assertIsInstance(t["tool_calls"], int)
        self.assertIsInstance(t["tools"], dict)


if __name__ == "__main__":
    unittest.main()
