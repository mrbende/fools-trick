"""`python -m core.observe` -- print recent per-task rollups and trip-wires.

This is the operator-facing Layer-6 read: tokens/delegation/wall per task, and any trip-wire the
latest task trips against the recent baseline. Local rigs measure tokens + wall, not dollars.
"""

from __future__ import annotations

import sys

from core.observe import check, task_rollups


def main() -> int:
    cwd = "."
    limit = 8
    rs = task_rollups(cwd, limit=limit)
    if not rs:
        print("no tasks in the opencode session DB yet")
        return 0
    print(f"recent tasks (root + descendants), newest first -- {len(rs)} shown")
    for t in rs:
        print(
            f"  {t.agent or '?':9} {t.provider or '?':9} subs={t.subagents:<2} "
            f"in={t.tokens_input:<7} out={t.tokens_output:<6} reasoning={t.tokens_reasoning:<6} "
            f"total={t.tokens_total:<7} wall={t.wall_s:.0f}s"
        )
    if len(rs) >= 2:
        print("\ntrip-wires on the latest task (vs the median of the rest):")
        for w in check(rs[0], rs[1:]):
            mark = "FIRED" if w.fired else "ok"
            print(f"  {mark:5} {w.name}: {w.detail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
