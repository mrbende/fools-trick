"""Reconstruct a session's trajectory from the opencode DB: what a worker (or the orchestrator)
actually did -- the tools it called, their status, errors, truncations, evictions, tokens, and
where it stopped. This is the orchestrator's debugging instrument: answer 'why did that worker
fail' without hand-writing SQL.
"""

from __future__ import annotations

import json
import subprocess


from core.observe.rollups import query_opencode_db



def trace_session(session_id: str, cwd: str = ".") -> dict:
    """Reconstruct one session's trajectory. Returns a dict for the caller to render."""
    parts = query_opencode_db(
        "SELECT json_extract(data,'$.type') AS t, json_extract(data,'$.tool') AS tool, "
        "json_extract(data,'$.state.status') AS status, json_extract(data,'$.state.error') AS err, "
        "json_extract(data,'$.state.metadata.truncated') AS truncated, "
        "json_extract(data,'$.state.time.compacted') AS compacted, time_created "
        f"FROM part WHERE session_id = '{session_id}' ORDER BY time_created",
        cwd,
    )
    sess = query_opencode_db(
        "SELECT agent, json_extract(model,'$.providerID') AS prov, tokens_input, tokens_output, "
        "tokens_reasoning, time_created, time_updated FROM session "
        f"WHERE id = '{session_id}'",
        cwd,
    )
    steps = []
    errors = []
    evictions = 0
    for p in parts:
        if p.get("t") == "tool" and p.get("tool"):
            st = p.get("status") or "?"
            step = {"tool": p["tool"], "status": st}
            if p.get("truncated"):
                step["truncated"] = True
            if p.get("compacted"):
                step["evicted"] = True
                evictions += 1
            if p.get("err"):
                step["error"] = str(p["err"])[:200]
                errors.append(step)
            steps.append(step)
    s = sess[0] if sess else {}
    return {
        "session": session_id,
        "agent": s.get("agent", ""),
        "provider": s.get("prov", ""),
        "tool_calls": len(steps),
        "tools": _tool_counts(steps),
        "evictions": evictions,
        "errors": errors,
        "tokens_input": s.get("tokens_input"),
        "tokens_output": s.get("tokens_output"),
        "tokens_reasoning": s.get("tokens_reasoning"),
        "steps": steps,
        "ended": _ended(parts),
    }


def trace_recent_subagents(cwd: str = ".", limit: int = 5) -> list[dict]:
    """The most recent subagent (worker) sessions, traced. The orchestrator's debugging view."""
    rows = query_opencode_db(
        "SELECT id FROM session WHERE parent_id IS NOT NULL ORDER BY time_created DESC "
        f"LIMIT {int(limit)}", cwd)
    return [trace_session(r["id"], cwd) for r in rows]


def _tool_counts(steps):
    out = {}
    for s in steps:
        out[s["tool"]] = out.get(s["tool"], 0) + 1
    return out


def _ended(parts) -> str:
    if not parts:
        return "no-parts"
    last = parts[-1]
    if last.get("t") == "tool" and last.get("status") == "error":
        return "tool-error"
    if last.get("t") == "step-finish":
        return "step-finish"
    if last.get("t") == "text":
        return "answered"
    return last.get("t", "?")


def format_trace(t: dict) -> str:
    lines = [
        f"session {t['session'][-12:]}  agent={t['agent']} provider={t['provider']} "
        f"tools={t['tool_calls']} evictions={t['evictions']} ended={t['ended']}",
        f"  tokens in={t['tokens_input']} out={t['tokens_output']}",
        f"  tool histogram: {t['tools']}",
    ]
    for e in t["errors"]:
        lines.append(f"  ERROR: {e['tool']}: {e.get('error','')}")
    return "\n".join(lines)
