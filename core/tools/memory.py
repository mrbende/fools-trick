"""Memory tool bodies: memory_write, memory_search, note, recall.

Each is (args: dict, ctx: ToolContext, log: MemoryLog) -> dict. The dict return is the
neutral tool result the adapter renders into the harness's tool-result shape.

note is the distill action: it records a finding to a scratch notes file and returns the
call_id it distilled, so the adapter can add that id to the session's distilled set (feeding
plan_worker_prune's distilled-first pass). Unlike the old design, note marks the result the
finding NAMES (passed as call_id), not the single most-recent result -- fixing bug 1.
"""

from __future__ import annotations

import os
from typing import Optional

from core.log.log import MemoryLog
from core.types import ToolContext


def memory_write(args: dict, ctx: ToolContext, log: MemoryLog) -> dict:
    content = str(args.get("content", "")).strip()
    if not content:
        return {"title": "memory not saved", "output": "empty content", "metadata": {}}
    thread = log.resolve_thread(ctx.sessionID or "")
    # durable=True assigns the seq synchronously and returns it -- used when the caller needs the
    # Event Log address back immediately (eviction persistence), not fire-and-forget through Redis.
    durable = bool(args.get("durable", False))
    seq = log.write_episode(thread=thread, session=ctx.sessionID or "", agent=ctx.agent or "",
                            role="memory", content=content, durable=durable)
    return {"title": "memory saved", "output": f"saved to thread {thread}",
            "metadata": {"thread": thread, "seq": seq}}


def memory_search(args: dict, ctx: ToolContext, log: MemoryLog) -> dict:
    query = str(args.get("query", ""))
    k = int(args.get("k") or 10)
    thread = log.resolve_thread(ctx.sessionID or "")
    eps = log.search(thread=thread, query=query, k=k)
    return {"title": f"recall: {query}", "output": _format_recall(eps),
            "metadata": {"thread": thread, "hits": len(eps)}}


def recall(args: dict, ctx: ToolContext, log: MemoryLog) -> dict:
    """Positional recall: recover an evicted result verbatim by its Event Log seq address.

    The complement to memory_search: a worker that evicted a tool result from its view gets
    it back exactly, making eviction recoverable rather than lossy.
    """
    seq = args.get("seq")
    if seq is None:
        return {"title": "recall failed", "output": "no seq given", "metadata": {}}
    ep = log.expand(int(seq))
    if ep is None:
        return {"title": "recall miss", "output": f"no episode at seq {seq}",
                "metadata": {"seq": seq}}
    return {"title": f"recalled seq {seq}", "output": ep.content,
            "metadata": {"seq": seq, "role": ep.role, "agent": ep.agent}}


def note(args: dict, ctx: ToolContext, log: MemoryLog,
         scratch: Optional[str] = None) -> dict:
    """Record a distilled finding so its raw tool result can be safely evicted.

    Returns the distilled call_id so the adapter marks it evictable. call_id names the result
    the finding is about (fix for bug 1); if omitted, the adapter falls back to the most
    recent result it saw for this session.
    """
    finding = str(args.get("finding", "")).strip()
    call_id = args.get("callID") or args.get("call_id")
    scratch = scratch or os.environ.get("FOOLS_SCRATCH", "/tmp/fools-trick/scratch")
    path = os.path.join(scratch, f"notes-{ctx.sessionID or 'worker'}.md")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as fh:
            fh.write(f"- {finding}\n")
    except OSError:
        pass  # best-effort; the distilled mark is what matters
    return {"title": "noted", "output": f"finding recorded ({path})",
            "metadata": {"distilled": call_id or "most-recent", "call_id": call_id}}


def promote(args: dict, ctx: ToolContext, log: MemoryLog,
            scratch: Optional[str] = None) -> dict:
    """Promote to the orchestrator: hand off when the task exceeds this worker's window or depth.

    A worker is a leaf; it cannot message the orchestrator mid-task, only in its final report.
    This tool gathers the worker's distilled findings (its notes file), persists them to the
    shared Event Log durably (so the evidence survives and is recallable by seq), and returns a
    structured escalation packet for the worker to put in its final report. The orchestrator reads
    the packet and can pull the full detail from the log by seq. This is the escalation path, not
    a guess: a worker that is stuck or out of context hands off cleanly instead of doom-looping.
    """
    reason = str(args.get("reason", "")).strip()
    status = str(args.get("status", "blocked")).strip()  # blocked | needs-deeper-context | done-partial
    scratch = scratch or os.environ.get("FOOLS_SCRATCH", "/tmp/fools-trick/scratch")
    notes_path = os.path.join(scratch, f"notes-{ctx.sessionID or 'worker'}.md")
    findings = ""
    try:
        findings = open(notes_path).read().strip()
    except OSError:
        pass
    # The durable escalation record: findings + why + what the orchestrator must decide.
    record = (
        f"[ESCALATION from {ctx.agent or 'worker'}] status={status}\n"
        f"reason: {reason}\n"
        f"findings:\n{findings or '(none recorded via note)'}"
    )
    seq = log.write_episode(thread=log.resolve_thread(ctx.sessionID or ""),
                            session=ctx.sessionID or "", agent=ctx.agent or "",
                            role="escalation", content=record, durable=True)
    packet = (
        f"ESCALATION (seq={seq}). I could not complete this within my context/scope. "
        f"Status: {status}. Why: {reason}. "
        f"My distilled findings and evidence are persisted in the shared memory at seq={seq}; "
        f"call memory_search/recall to retrieve them. The orchestrator should take it from here "
        f"with the full-context read."
    )
    return {"title": "promoted to orchestrator", "output": packet,
            "metadata": {"seq": seq, "status": status}}


def _format_recall(eps) -> str:
    if not eps:
        return "(no relevant memory found)"
    lines = []
    for e in eps:
        who = f"{e.role}/{e.agent}" if e.agent else (e.role or "?")
        prefix = f"seq={e.seq} " if e.seq is not None else ""
        lines.append(f"- {prefix}[{who}] {e.content}")
    return "<recalled_memory>\n" + "\n".join(lines) + "\n</recalled_memory>"
