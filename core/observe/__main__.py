"""`python -m core.observe` -- print recent per-task rollups and trip-wires.

This is the operator-facing Layer-6 read: tokens/delegation/wall per task, and any trip-wire the
latest task trips against the recent baseline. Local rigs measure tokens + wall, not dollars.
"""

from __future__ import annotations

import sys

from core.observe import check, scorecard, task_rollups


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

    # The real metric (Layer 6): outcomes, not tokens. Contracts = goal-direction; verified handoffs
    # = work proven before it advanced; escalations = clean hand-offs instead of guessing.
    sc = scorecard()
    if sc.get("available"):
        rate = f"{sc['verification_rate']:.0%}" if sc["verification_rate"] is not None else "n/a"
        print("\nscorecard (goal-direction + verified work):")
        print(f"  contracts recorded:   {sc['contracts']}")
        print(f"  worker handoffs:      {sc['handoffs']}  (verified: {sc['handoffs_verified']})")
        print(f"  escalations:          {sc['escalations']}")
        print(f"  verification rate:    {rate}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
