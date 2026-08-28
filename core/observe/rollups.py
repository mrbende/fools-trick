"""Per-task rollups from the opencode session DB.

A "task" is a root session plus its descendant (subagent) sessions, joined on parent_id. The
rollup aggregates tokens, wall time, and delegation count across the whole tree, so delegation
stays visible in the cost (Prime Agent: accounting must aggregate root + descendants). Local
endpoints report cost 0; the metric is tokens + wall, not dollars.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field


@dataclass
class TaskRollup:
    session_id: str
    agent: str
    provider: str
    subagents: int            # descendant sessions (delegation count)
    tokens_input: int         # root + descendants
    tokens_output: int
    tokens_reasoning: int
    tokens_total: int
    wall_s: float             # last-update minus created, on the root
    model_ids: list[str] = field(default_factory=list)


def _query(sql: str, cwd: str) -> list[dict]:
    out = subprocess.run(
        ["opencode", "db", "--format", "json", sql],
        cwd=cwd, capture_output=True, text=True, timeout=30,
    )
    if out.returncode != 0 or not out.stdout.strip():
        return []
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        return []


def task_rollup(root_session_id: str, cwd: str) -> TaskRollup:
    """Aggregate one root session + its descendants into a per-task rollup."""
    rows = _query(
        "SELECT id, parent_id, agent, json_extract(model,'$.providerID') AS prov, "
        "json_extract(model,'$.modelID') AS mdl, tokens_input, tokens_output, "
        "tokens_reasoning, time_created, time_updated FROM session "
        f"WHERE id = '{root_session_id}' OR parent_id = '{root_session_id}'",
        cwd,
    )
    return _rollup(rows, root_session_id)


def task_rollups(cwd: str, limit: int = 50) -> list[TaskRollup]:
    """The most recent root sessions (tasks), each rolled up with its descendants."""
    roots = _query(
        "SELECT id FROM session WHERE parent_id IS NULL ORDER BY time_created DESC "
        f"LIMIT {int(limit)}", cwd)
    return [task_rollup(r["id"], cwd) for r in roots]


def _rollup(rows: list[dict], root_id: str) -> TaskRollup:
    root = next((r for r in rows if r["id"] == root_id), {})
    subs = [r for r in rows if r.get("parent_id") == root_id]
    tin = sum(r.get("tokens_input") or 0 for r in rows)
    tout = sum(r.get("tokens_output") or 0 for r in rows)
    treas = sum(r.get("tokens_reasoning") or 0 for r in rows)
    created = root.get("time_created") or 0
    updated = root.get("time_updated") or 0
    return TaskRollup(
        session_id=root_id,
        agent=root.get("agent", ""),
        provider=root.get("prov", ""),
        subagents=len(subs),
        tokens_input=tin, tokens_output=tout, tokens_reasoning=treas,
        tokens_total=tin + tout + treas,
        wall_s=max(0.0, (updated - created) / 1000.0),
        model_ids=sorted({r.get("mdl") for r in rows if r.get("mdl")}),
    )
