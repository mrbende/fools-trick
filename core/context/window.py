"""Context-window decisions. Pure functions: turns in, an EvictionDecision out.

The adapter applies the decision to its live message array (setting the harness's
compacted flag, dropping turns); the core never mutates a harness structure. This is the
split that keeps the doom-loop fix in the owned core while the ~40 lines of in-process
mutation stay in the adapter (docs/harness-design.md 3.5, option a).

Fixes folded in from the audit (section 2):
  - bug 1 (distill-gate mis-attribution): a note now marks the result it NAMES, and the
    gate's stakes are low anyway because eviction is recoverable via expand(seq). We evict
    distilled results first, then fall back size-aware.
  - bug 2 (relevance-blind backstop): the backstop evicts LARGEST-oldest first (reclaim the
    most tokens per eviction) rather than blind oldest, and honors a pin set the worker can
    protect.
  - bug 3 (reasoning-blind estimation): input_tokens counts reasoning (estimate.py).
  - recoverability: every evicted result is durable in the Event Log by seq, so eviction is
    a cheap round-trip (expand) not a lobotomy.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.context.estimate import est_tokens, input_tokens
from core.types import Turn


@dataclass
class EvictionDecision:
    """What the adapter should do to bring the view under budget, and what left it.

    evict_call_ids: tool-result call_ids to compact (evict from the view; durable in the log).
    drop_turn_indices: turn indices to remove entirely (orchestrator slide only).
    persist: turns/results the adapter must persist to the Event Log BEFORE removing them,
             as (thread-agnostic) episode dicts -- lossless. For the worker prune this is the
             evicted tool results; for the slide it is the evicted raw turns.
    index_entries: address-anchored headlines to keep in-view for evicted spans (the eviction
             index): short strings like "[evicted call=c3: read serve.sh -- expand to recover]".
    """

    evict_call_ids: list[str] = field(default_factory=list)
    drop_turn_indices: list[int] = field(default_factory=list)
    persist: list[dict] = field(default_factory=list)
    index_entries: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.evict_call_ids or self.drop_turn_indices)


def plan_worker_prune(
    turns: list[Turn],
    *,
    input_budget: int,
    keep_recent: int = 3,
    distilled: set[str] | None = None,
    pinned: set[str] | None = None,
) -> EvictionDecision:
    """Decide which subagent tool results to evict to get under input_budget.

    Order of eviction (all recoverable via expand(seq)):
      1. distilled-first: results the worker recorded a note for (safe to clear -- the lesson
         is kept, the bulk goes). Never a pinned result.
      2. size-aware backstop: remaining prunable results, LARGEST first, until under budget.
    Never evicts: the last `keep_recent` results, anything pinned, or any reasoning/text.
    """
    distilled = distilled or set()
    pinned = pinned or set()

    decision = EvictionDecision()
    if input_tokens(turns) <= input_budget:
        return decision

    # gather live (uncompacted, completed) tool results in stream order
    live: list[tuple[Turn, "object"]] = [
        (t, tr) for t in turns for tr in t.tool_results if not tr.compacted
    ]
    prunable = [p for p in live[: max(0, len(live) - keep_recent)] if p[1].call_id not in pinned]

    _evict_passes(prunable, distilled, input_tokens(turns), input_budget, decision)
    return decision


def _evict_passes(
    prunable: list[tuple[Turn, "object"]],
    distilled: set[str],
    budget: int,
    input_budget: int,
    decision: EvictionDecision,
) -> None:
    """Run the two eviction passes against the prunable results until under budget.

    `_evict` is closed over the running budget + decision so each pass shares the same accounting.
    """

    def _evict(tr) -> None:
        nonlocal budget
        decision.evict_call_ids.append(tr.call_id)
        decision.persist.append(_result_episode(tr))
        decision.index_entries.append(
            f"[evicted call={tr.call_id}"
            + (f" seq={tr.seq}" if tr.seq is not None else "")
            + f": {_preview(tr.text)} -- recall with expand]"
        )
        budget -= est_tokens(tr.text)

    # pass 1: distilled-first
    for _t, tr in prunable:
        if budget <= input_budget:
            break
        if tr.call_id in distilled:
            _evict(tr)

    # pass 2: size-aware backstop (largest remaining first)
    remaining = [
        (t, tr) for (t, tr) in prunable
        if tr.call_id not in decision.evict_call_ids
    ]
    remaining.sort(key=lambda pair: est_tokens(pair[1].text), reverse=True)
    for _t, tr in remaining:
        if budget <= input_budget:
            break
        _evict(tr)


def plan_slide(
    turns: list[Turn],
    *,
    input_budget: int,
    keep_tail: int = 6,
) -> EvictionDecision:
    """Decide which orchestrator raw turns to evict (lossless slide) past input_budget.

    Selects oldest non-system turns, keeping the last keep_tail. The adapter persists each
    to the Event Log (lossless) BEFORE dropping it. Never selects a system turn.
    """
    decision = EvictionDecision()
    total = sum(est_tokens(t.text) + est_tokens(t.reasoning) for t in turns)
    if total <= input_budget:
        return decision

    keep_from = max(0, len(turns) - keep_tail)
    for i, t in enumerate(turns):
        if total <= input_budget or i >= keep_from:
            break
        if t.role == "system":
            continue
        text = t.text
        if not text:
            continue
        decision.drop_turn_indices.append(i)
        decision.persist.append(
            {"session": t.session, "agent": t.agent, "role": t.role, "content": text}
        )
        total -= est_tokens(text)
    return decision


def _result_episode(tr) -> dict:
    return {"role": "tool", "content": tr.text, "call_id": tr.call_id, "seq": tr.seq}


def _preview(text: str, n: int = 80) -> str:
    text = " ".join((text or "").split())
    return text[:n] + ("..." if len(text) > n else "")
