"""CLI entrypoint for the per-turn context decision: `python -m core.context.cli`.

The opencode adapter calls this from the transform hook: it passes the neutral worker view
(turns with tool results) and the session's distilled/pinned call_ids as JSON, and gets back
the EvictionDecision. One implementation of the prune policy (window.py) serves both the core
tests and the live adapter -- no mirrored logic to drift.

Latency note: this is a subprocess per turn. That is the honest cost of a single source of
truth across the language boundary; if it ever measures hot, the next step is a resident socket
daemon, not a second implementation.
"""

from __future__ import annotations

import argparse
import json
import sys

from core.context.window import plan_slide, plan_worker_prune
from core.types import ToolResult, Turn


def _turns(payload: list[dict]) -> list[Turn]:
    out = []
    for t in payload:
        out.append(
            Turn(
                role=t.get("role", ""),
                text=t.get("text", ""),
                reasoning=t.get("reasoning", ""),
                agent=t.get("agent", ""),
                session=t.get("session", ""),
                tool_results=[
                    ToolResult(
                        call_id=r.get("call_id", ""),
                        text=r.get("text", ""),
                        compacted=bool(r.get("compacted", False)),
                        seq=r.get("seq"),
                    )
                    for r in t.get("tool_results", [])
                ],
            )
        )
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="core.context")
    p.add_argument("which", choices=["prune", "slide"])
    # turns travel on stdin, not argv: a worker view with large tool results exceeds the OS
    # argv length limit. The adapter writes the JSON to the child's stdin.
    p.add_argument("--input-budget", type=int, required=True)
    p.add_argument("--keep-recent", type=int, default=3)
    p.add_argument("--keep-tail", type=int, default=6)
    p.add_argument("--distilled", default="[]", help="JSON array of distilled call_ids")
    p.add_argument("--pinned", default="[]", help="JSON array of pinned call_ids")
    args = p.parse_args(argv)

    turns = _turns(json.loads(sys.stdin.read()))
    distilled = set(json.loads(args.distilled))
    pinned = set(json.loads(args.pinned))

    if args.which == "prune":
        d = plan_worker_prune(
            turns, input_budget=args.input_budget, keep_recent=args.keep_recent,
            distilled=distilled, pinned=pinned,
        )
    else:
        d = plan_slide(turns, input_budget=args.input_budget, keep_tail=args.keep_tail)

    print(json.dumps({
        "evict_call_ids": d.evict_call_ids,
        "drop_turn_indices": d.drop_turn_indices,
        "persist": d.persist,
        "index_entries": d.index_entries,
        "changed": d.changed,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
