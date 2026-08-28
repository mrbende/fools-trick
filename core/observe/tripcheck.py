"""Trip-wire detection: compare a task's rollup against a rolling baseline and flag drift.

A trip wire turns a regression into a signal, not a vibe (playbook Layer 6 / Table VII). Each
wire has a threshold relative to the recent baseline: if a run trips one, it surfaces loudly. The
baseline is the median of recent prior tasks, so the wires track normal drift instead of a frozen
number.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from core.observe.rollups import TaskRollup


@dataclass
class TripWire:
    name: str
    fired: bool
    detail: str


def check(current: TaskRollup, baseline: list[TaskRollup]) -> list[TripWire]:
    """Compare `current` against the baseline (recent prior tasks). Returns fired + clear wires."""
    if not baseline:
        return [TripWire("baseline", False, "no prior tasks to compare against")]
    tok_base = median(t.tokens_total for t in baseline) or 1
    wall_base = median(t.wall_s for t in baseline) or 1
    wires = [
        TripWire(
            "token-spike",
            current.tokens_total > 2 * tok_base,
            f"tokens {current.tokens_total} vs baseline ~{int(tok_base)} (2x)",
        ),
        TripWire(
            "duration-spike",
            current.wall_s > 3 * wall_base,
            f"wall {current.wall_s:.0f}s vs baseline ~{int(wall_base)}s (3x)",
        ),
        TripWire(
            "delegation-vanished",
            baseline and any(t.subagents > 0 for t in baseline) and current.subagents == 0,
            "this task delegated 0 subagents where prior tasks did",
        ),
        TripWire(
            "reasoning-runaway",
            current.tokens_reasoning > 0.6 * max(1, current.tokens_total),
            f"reasoning is {current.tokens_reasoning}/{current.tokens_total} of tokens (over-reasoning)",
        ),
    ]
    return wires
