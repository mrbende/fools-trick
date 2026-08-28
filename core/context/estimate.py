"""Token estimation over neutral turns.

Reasoning-aware (fixes the estimator blind spot, docs/harness-design.md section 2, bug 3):
the old JS estimator counted only text and completed tool outputs, so an over-reasoning
worker's ballooning reasoning was invisible to the prune -- it under-fired while the real
slot filled, and --no-context-shift then hard-stopped the worker. Here reasoning text is
counted toward the input budget so the prune decision and the server's real occupancy agree.
"""

from __future__ import annotations

import math

from core.types import Turn

# ~3.5 chars/token, deliberately low so the estimate errs UNDER, leaving decode headroom.
_CHARS_PER_TOKEN = 3.5


def est_tokens(s: str | None) -> int:
    if not s:
        return 0
    return math.ceil(len(str(s)) / _CHARS_PER_TOKEN)


def turn_tokens(turn: Turn, *, include_reasoning: bool = True) -> int:
    """Estimated tokens a turn contributes to the live input window.

    Counts the turn's own text, its reasoning text (unless excluded), and every live
    (not-compacted) tool result. A compacted result is sent as a short placeholder by the
    harness, so it no longer counts.
    """
    n = est_tokens(turn.text)
    if include_reasoning:
        n += est_tokens(getattr(turn, "reasoning", ""))
    for tr in turn.tool_results:
        if not tr.compacted:
            n += est_tokens(tr.text)
    return n


def input_tokens(turns: list[Turn], *, include_reasoning: bool = True) -> int:
    return sum(turn_tokens(t, include_reasoning=include_reasoning) for t in turns)
