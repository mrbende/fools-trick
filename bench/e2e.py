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
    q = ("SELECT agent, json_extract(model,'$.providerID') AS prov, "
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
                    "the make targets in the Makefile and one to summarize what scripts/config.sh "
                    "configures. Then give me a two-line summary combining both."),
         "expect": r"(bootstrap|preflight|worker|fool)",
         "min_subagents": 1, "want_provider": "magus"},
        {"name": "single-fact",
         "prompt": "What port does the worker serve on, per scripts/config.sh? Answer with just the number.",
         "expect": r"8898", "min_subagents": 0},
        {"name": "reasoning",
         "prompt": ("If the worker runs 4 parallel slots and each holds 32768 tokens, what is the "
                    "total KV context in tokens? Answer with the number."),
         "expect": r"131072|131,072", "min_subagents": 0},
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
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--out")
    ap.add_argument("--md")
    ap.add_argument("--logfile")
    a = ap.parse_args()
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
    for t in tasks:
        with ui.console.status(f"running '{t['name']}' (orchestrating, may take minutes)..."):
            events, root, wall, rc = run_opencode_json(a.project, t["prompt"], a.timeout)

        answer, tasks_spawned = extract(events)
        children = db_children(a.project, root)

        n_sub = len(tasks_spawned)
        types = sorted({d["subagent"] for d in tasks_spawned if d["subagent"]})
        # endpoint proof: from DB session rows (authoritative), fallback to stream metadata
        provs = [c.get("prov") for c in children] if children else [d.get("provider") for d in tasks_spawned]
        on_want = bool(provs) and all(p == a.want_provider for p in provs if p)
        tok = sum((c.get("tokens_input", 0) or 0) + (c.get("tokens_output", 0) or 0) for c in children)
        cost = sum(c.get("cost", 0) or 0 for c in children)

        # correctness AND delegation expectation both required to pass
        answer_ok = bool(re.search(t.get("expect", "$^"), answer, re.I)) and rc == 0
        deleg_ok = n_sub >= t.get("min_subagents", 0)
        ok = answer_ok and deleg_ok
        passed += ok

        rec = {"test": "e2e", "task": t["name"], "pass": bool(ok),
               "answer_ok": answer_ok, "delegation_ok": deleg_ok, "rc": rc,
               "wall_s": round(wall, 1), "subagents": n_sub, "types": types,
               "on_want_provider": on_want, "want_provider": a.want_provider,
               "providers": provs, "tokens": tok, "cost": round(cost, 4),
               "root_session": root, "answer_tail": answer[-200:]}
        emit(rec, out)
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
    return 0 if passed == len(tasks) else 1


if __name__ == "__main__":
    sys.exit(main())
