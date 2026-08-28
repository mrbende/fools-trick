#!/usr/bin/env python3
"""End-to-end harness benchmark: the real eval, with PROVEN delegation.

Runs `opencode run --format json` on real tasks and parses the NDJSON event stream to
prove -- not guess -- what the whole distributed system actually did:
  - correctness : did the answer match the expected regex, and did the run succeed
  - wall-clock  : end-to-end time
  - delegation  : how many subagents were spawned (task tool-uses), which types
  - endpoint    : did those subagents run on magus (the worker) -- cross-checked in the DB
  - cost/tokens : per-run token + cost totals from the session rows

Every subagent spawn appears in the stream as a part with tool == "task", carrying
subagent_type and the child sessionId. The DB (opencode db) confirms each child's
resolved provider and token usage. No proxies.

Tasks: bench/tasks/*.json as {name, prompt, expect (regex), min_subagents, want_provider}.

  bench/e2e.py --project DIR [--tasks bench/tasks] [--timeout 900] [--out FILE]
"""
import argparse, json, os, re, subprocess, sys, time, glob
import ui
import shared  # noqa: E402

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from core.observe import task_rollup, check  # noqa: E402


def run_opencode_json(project, prompt, timeout):
    """Run one task with --format json. Return (events[list], root_session_id, wall_s, rc)."""
    t0 = time.perf_counter()
    try:
        p = subprocess.run(
            ["opencode", "run", "--format", "json", prompt],
            cwd=project, capture_output=True, text=True, timeout=timeout)
        wall = time.perf_counter() - t0
    except subprocess.TimeoutExpired:
        return [], None, time.perf_counter() - t0, 124
    events, root = [], None
    for line in p.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        events.append(ev)
        if root is None and ev.get("sessionID"):
            root = ev["sessionID"]
    return events, root, wall, p.returncode


def extract(events):
    """Pull the answer text and the task (subagent) spawns out of the event stream."""
    text_parts, tasks = [], []
    for ev in events:
        part = ev.get("part") or {}
        if part.get("type") == "text" and part.get("text"):
            text_parts.append(part["text"])
        if part.get("type") == "tool" and part.get("tool") == "task":
            st = part.get("state") or {}
            inp = st.get("input") or {}
            meta = st.get("metadata") or {}
            tm = st.get("time") or {}
            if st.get("status") in ("completed", "error"):
                tasks.append({
                    "subagent": inp.get("subagent_type"),
                    "child": meta.get("sessionId"),
                    "provider": (meta.get("model") or {}).get("providerID"),
                    "ms": (tm.get("end", 0) - tm.get("start", 0)) if tm.get("end") else None,
                    "status": st.get("status"),
                })
    return "\n".join(text_parts), tasks


def db_children(project, root):
    """Airtight cross-check: per-subagent provider + tokens + cost from the session DB."""
    if not root:
        return []
    q = ("SELECT id, agent, json_extract(model,'$.providerID') AS prov, "
         "tokens_input, tokens_output, cost FROM session WHERE parent_id = '%s'" % root)
    try:
        p = subprocess.run(["opencode", "db", "--format", "json", q],
                           cwd=project, capture_output=True, text=True, timeout=30)
        return json.loads(p.stdout) if p.stdout.strip() else []
    except Exception:
        return []


def emit(rec, out):
    if out:
        out.write(json.dumps(rec) + "\n"); out.flush()


def default_tasks():
    return [
        {"name": "fanout-review",
         "prompt": ("Use subagents to inspect this repo in parallel: dispatch one worker to list "
                    "the make targets in the Makefile and one to summarize what config.yaml "
                    "configures. Then give me a two-line summary combining both."),
         "expect": r"(bootstrap|preflight|worker|orchestrator)",
         "min_subagents": 1, "want_provider": "magus"},
        {"name": "single-fact",
         "prompt": "What port does the worker serve on, per config.yaml? Answer with just the number.",
         "expect": r"8898", "min_subagents": 0},
        {"name": "reasoning",
         "prompt": ("If the worker runs 4 parallel slots and each holds 32768 tokens, what is the "
                    "total context in tokens? Answer with just the number."),
         "expect": r"131072|131,072", "min_subagents": 0},
        {"name": "substantive-audit",
         "prompt": ("Dispatch one worker to read deploy/scripts/down.sh and write a concise "
                    "function-by-function audit (one short paragraph per function: purpose, "
                    "inputs, any bug) to /tmp/fools-trick/scratch/down-audit.md, then return a "
                    "2-line summary. Keep the audit under 400 words -- concise, not exhaustive."),
         "expect": r"(down_worker|down_fool|fuser|systemctl)",
         "min_subagents": 1, "want_provider": "magus",
         "artifact": "/tmp/fools-trick/scratch/down-audit.md", "artifact_min_bytes": 400},
    ]


def load_tasks(tasks_dir):
    files = sorted(glob.glob(os.path.join(tasks_dir, "*.json"))) if tasks_dir else []
    if not files:
        return default_tasks()
    tasks = []
    for f in files:
        with open(f) as fh:
            t = json.load(fh); t.setdefault("name", os.path.basename(f)[:-5]); tasks.append(t)
    return tasks


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--tasks", default="")
    ap.add_argument("--want-provider", default="magus", help="provider subagents should run on")
    ap.add_argument("--timeout", type=int, default=2400)
    ap.add_argument("--out")
    ap.add_argument("--md")
    ap.add_argument("--logfile")
    a = ap.parse_args()
    shared.assert_our_config(a.project)
    ui.setup_logging(a.logfile)
    out = open(a.out, "a") if a.out else None
    md = open(a.md, "a") if a.md else None
    tasks = load_tasks(a.tasks)

    ui.phase(f"e2e (opencode harness, proven delegation) -- {len(tasks)} tasks")
    if md:
        md.write(f"# e2e  {time.strftime('%Y-%m-%dT%H:%M:%S')}  project={a.project}\n\n")
    tbl = ui.Table("e2e delegation", ["task", "pass", "wall s", "subagents", "types",
                                      f"on {a.want_provider}", "tokens", "cost $"], md,
                   justify=["left", "center", "right", "right", "left", "center", "right", "right"])

    passed = 0
    prior_roots = []  # root session ids of completed tasks this run, for trip-wire baselines
    for t in tasks:
        with ui.console.status(f"running '{t['name']}' (orchestrating, may take minutes)..."):
            events, root, wall, rc = run_opencode_json(a.project, t["prompt"], a.timeout)

        answer, tasks_spawned = extract(events)
        children = db_children(a.project, root)

        # The DB is authoritative for delegation: the stream only shows a task part as a spawn
        # once it reaches completed/error status, so a still-running or timed-out fanout can
        # show 0 in the stream while the DB proves the children ran. Count the DB children as
        # the delegation signal, falling back to the stream only when the DB is empty.
        n_sub = len(children) if children else len(tasks_spawned)
        types = sorted({d["subagent"] for d in tasks_spawned if d["subagent"]}
                       or {c.get("agent") for c in children if c.get("agent")})
        # endpoint proof: from DB session rows (authoritative), fallback to stream metadata
        provs = [c.get("prov") for c in children] if children else [d.get("provider") for d in tasks_spawned]
        on_want = bool(provs) and all(p == a.want_provider for p in provs if p)
        tok = sum((c.get("tokens_input", 0) or 0) + (c.get("tokens_output", 0) or 0) for c in children)
        cost = sum(c.get("cost", 0) or 0 for c in children)

        # correctness AND delegation expectation both required to pass; an artifact task
        # additionally requires the worker to have finished writing its file (a mid-generation
        # timeout produces DB tokens but no artifact -- the failure this catches).
        answer_ok = bool(re.search(t.get("expect", "$^"), answer, re.I)) and rc == 0
        deleg_ok = n_sub >= t.get("min_subagents", 0)
        artifact_ok = True
        art = t.get("artifact")
        if art:
            try:
                artifact_ok = os.path.getsize(art) >= t.get("artifact_min_bytes", 1)
            except OSError:
                artifact_ok = False
        ok = answer_ok and deleg_ok and artifact_ok
        passed += ok

        # Observability rollup: this task's root+descendant tokens/delegation/wall, and any
        # trip-wire it trips against the prior tasks in THIS run. Grounds pass/fail in the
        # resource signal (Layer 6), not just the answer.
        rollup = task_rollup(root, a.project) if root else None
        wires = [w for w in check(rollup, [task_rollup(r, a.project) for r in prior_roots]) if w.fired] \
            if (rollup and prior_roots) else []

        rec = {"test": "e2e", "task": t["name"], "pass": bool(ok),
               "answer_ok": answer_ok, "delegation_ok": deleg_ok, "artifact_ok": artifact_ok, "rc": rc,
               "wall_s": round(wall, 1), "subagents": n_sub, "types": types,
               "on_want_provider": on_want, "want_provider": a.want_provider,
               "providers": provs, "tokens": tok, "cost": round(cost, 4),
               "root_session": root, "answer_tail": answer[-200:],
               "rollup_tokens": rollup.tokens_total if rollup else None,
               "trip_wires": [w.name for w in wires]}
        emit(rec, out)
        if rollup:
            ui.log.info("%s: rollup total_tokens=%d subs=%d wall=%ds%s",
                        t["name"], rollup.tokens_total, rollup.subagents, round(rollup.wall_s),
                        (" tripwires=" + ",".join(w.name for w in wires) if wires else ""))
        if root:
            prior_roots.append(root)
        ui.log.info("%s: pass=%s subagents=%d types=%s on_%s=%s tokens=%d",
                    t["name"], ok, n_sub, types, a.want_provider, on_want, tok)

        prov_cell = "yes" if on_want else ("n/a" if n_sub == 0 else "NO")
        tbl.add([t["name"], "yes" if ok else "NO", f"{wall:.0f}", n_sub,
                 ",".join(types) or "-", prov_cell, tok or "-", round(cost, 4) or "-"],
                style=(None if ok else "red"))

    tbl.render()
    ui.summary("e2e", passed, len(tasks), "delegation + correctness both required")
    emit({"test": "e2e_summary", "passed": passed, "total": len(tasks)}, out)
    if out: out.close()
    if md: md.close()
    # Exit 0 on a successful MEASUREMENT regardless of score -- a benchmark that measures 3/4 has
    # done its job. Only infrastructure failure (unreachable server, crash) should be non-zero, so
    # a realistic imperfect score never aborts `make bench` before finish() writes the report.
    return 0


if __name__ == "__main__":
    sys.exit(main())
