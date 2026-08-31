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
import time
from typing import Optional

from core.log.log import MemoryLog
from core.types import ToolContext
import json as _json
import urllib.request as _urlreq


def scratch_write(args: dict, ctx: ToolContext, log: MemoryLog) -> dict:
    """Write an ephemeral artifact to the per-task scratch dir and return its path. Scoped by the
    root session so a task's artifacts don't collide or leak across tasks."""
    import core.config as _cfg
    from core.scratch import task_dir
    content = str(args.get("content", ""))
    if not content:
        return {"title": "scratch_write failed", "output": "empty content", "metadata": {}}
    name = str(args.get("name") or f"artifact-{int(time.time()*1000)}.txt")
    name = os.path.basename(name)  # no path traversal
    d = task_dir(_cfg.load().scratch_dir, log.resolve_thread(ctx.sessionID or ""))
    path = os.path.join(d, name)
    with open(path, "w") as fh:
        fh.write(content)
    return {"title": "scratch written", "output": path, "metadata": {"path": path}}


def delegate_cheap(args: dict, ctx: ToolContext, log: MemoryLog) -> dict:
    """Run a cheap sub-task on the worker instead of burning deep-stream tokens: a one-shot
    classification, summary, or small transform that needs no tools and no orchestration depth.
    The orchestrator's scarce resource is deep reasoning; a shallow task belongs on a fast slot.
    Returns the worker's answer text."""
    task = str(args.get("task", "")).strip()
    if not task:
        return {"title": "delegate_cheap failed", "output": "empty task", "metadata": {}}
    import core.config as _cfg
    cfg = _cfg.load()
    url = cfg.worker.base_url.rstrip("/") + "/chat/completions"
    body = _json.dumps({
        "model": cfg.worker.model_id,
        "messages": [
            {"role": "system", "content": "Answer concisely and only from what is asked."},
            {"role": "user", "content": task},
        ],
        "max_tokens": int(args.get("max_tokens") or 400),
        "temperature": 0.0,
    }).encode()
    # A real User-Agent is required: cloud gateways (Zen) 403 the default Python-urllib UA.
    headers = {"Content-Type": "application/json", "User-Agent": "fools-trick/1.0"}
    if cfg.worker.api_key and cfg.worker.api_key != "dummy":
        headers["Authorization"] = f"Bearer {cfg.worker.api_key}"
    try:
        req = _urlreq.Request(url, data=body, headers=headers)
        with _urlreq.urlopen(req, timeout=120) as r:
            d = _json.loads(r.read())
        msg = d["choices"][0]["message"]
        out = (msg.get("content") or msg.get("reasoning_content") or "").strip()
        return {"title": "delegated to worker", "output": out, "metadata": {"model": cfg.worker.model_id}}
    except Exception as e:
        return {"title": "delegate_cheap failed", "output": f"worker unreachable: {e}", "metadata": {}}


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
    eps = log.search(
        thread=thread, query=query, k=k,
        role=args.get("role"), agent=args.get("agent"),
        after_seq=_int_or_none(args.get("after_seq")),
        before_seq=_int_or_none(args.get("before_seq")),
    )
    return {"title": f"recall: {query}", "output": _format_recall(eps),
            "metadata": {"thread": thread, "hits": len(eps)}}


def _int_or_none(v):
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


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
    if not finding:
        return {"title": "note not recorded", "output": "empty finding", "metadata": {}}
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
    if not reason:
        return {"title": "promote rejected",
                "output": "a reason is required -- say what blocked you or what is missing",
                "metadata": {}}
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


def record_contract(args: dict, ctx: ToolContext, log: MemoryLog) -> dict:
    """Record the task's success-contract before dispatching work: the definition-of-done the
    accumulated work is checked against before it is canonicalized.

    A harness amplifies its objective; an implicit or vague objective makes every downstream sensor
    validate garbage. So the objective is a tracked object. Persisted durably to the Event Log as
    role="contract", addressable by seq, surviving eviction and restart. The canonicalize gate reads
    the SIGNAL to decide whether a commit is verified.
    """
    goal = str(args.get("goal", "")).strip()
    signal = str(args.get("signal", "")).strip()
    boundaries = str(args.get("boundaries", "")).strip()
    if not goal or not signal:
        return {"title": "contract not recorded",
                "output": "both goal and signal are required (signal = the exact command/check that proves done)",
                "metadata": {}}
    record = f"GOAL: {goal}\nSIGNAL: {signal}\nBOUNDARIES: {boundaries or '(none stated)'}"
    seq = log.write_episode(thread=log.resolve_thread(ctx.sessionID or ""),
                            session=ctx.sessionID or "", agent=ctx.agent or "",
                            role="contract", content=record, durable=True)
    return {"title": f"contract recorded (seq={seq})",
            "output": f"Success-contract for this task is set (seq={seq}). Done means: {signal}",
            "metadata": {"seq": seq, "signal": signal}}


def report(args: dict, ctx: ToolContext, log: MemoryLog) -> dict:
    """The typed handoff a worker returns to the orchestrator at the end of a unit.

    A free-form "done, looks good" must not advance the workflow: the orchestrator needs a typed
    packet it can act on and verify independently (artifact pointer, evidence, unresolved seams).
    This generalizes the escalation packet to every worker return. Persisted durably as
    role="handoff" (recallable by seq, cross-session), and returned as a marker the worker puts at
    the end of its report so the orchestrator sees the structured summary up front.
    """
    status = str(args.get("status", "")).strip().lower()  # done | done-partial | blocked
    artifact = str(args.get("artifact", "")).strip()
    evidence = str(args.get("evidence", "")).strip()
    assumptions = str(args.get("assumptions", "")).strip()
    unresolved = str(args.get("unresolved", "")).strip()
    if not status or not artifact:
        return {"title": "report not recorded",
                "output": "status and artifact are required. status: done|done-partial|blocked; "
                          "artifact: the files touched (path:line), scratch path, or diff reference.",
                "metadata": {}}
    if status not in ("done", "done-partial", "blocked"):
        return {"title": "report not recorded",
                "output": f"status must be done|done-partial|blocked (got {status!r})",
                "metadata": {}}
    record = (f"STATUS: {status}\nARTIFACT: {artifact}\nEVIDENCE: {evidence or '(unverified)'}\n"
              f"ASSUMPTIONS: {assumptions or '(none)'}\nUNRESOLVED: {unresolved or '(none)'}")
    seq = log.write_episode(thread=log.resolve_thread(ctx.sessionID or ""),
                            session=ctx.sessionID or "", agent=ctx.agent or "",
                            role="handoff", content=record, durable=True)
    packet = (f"HANDOFF (seq={seq}) STATUS={status}. Artifact: {artifact}. "
              f"Evidence: {evidence or 'unverified'}. Unresolved: {unresolved or 'none'}.")
    return {"title": f"report recorded (seq={seq})", "output": packet,
            "metadata": {"seq": seq, "status": status, "artifact": artifact,
                         "verified": bool(evidence and "unverif" not in evidence.lower())}}


def _format_recall(eps) -> str:
    if not eps:
        return "(no relevant memory found)"
    lines = []
    for e in eps:
        who = f"{e.role}/{e.agent}" if e.agent else (e.role or "?")
        prefix = f"seq={e.seq} " if e.seq is not None else ""
        lines.append(f"- {prefix}[{who}] {e.content}")
    return "<recalled_memory>\n" + "\n".join(lines) + "\n</recalled_memory>"
