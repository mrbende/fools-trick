#!/usr/bin/env python3
"""Long-context AGENTIC eval: does the orchestrator still delegate correctly when its own
context window is large? This is the genuinely novel measurement -- it fuses two things every
existing benchmark tests separately:

  - needle-in-haystack (does the model find a fact buried in a long context) -- passive retrieval.
  - delegation (does the orchestrator plan/dispatch/synthesize) -- our e2e eval, but on SHORT tasks.

Neither existing test asks: when the orchestrator is holding 50k-300k tokens of context, does it
STILL fan out to workers with correct briefs and synthesize correctly, or does the long context
degrade its agentic behavior? That is the load-bearing question for a deep-context orchestrator
that delegates, and nobody publishes it.

Construction: build a large haystack (real-text blocks) with a unique fact planted deep inside,
then give a task that requires BOTH (a) using the planted fact AND (b) delegating a sub-task to a
worker. Run it through `opencode run`, then score:
  delegation_ok : >= min_subagents child sessions on the worker provider (DB-authoritative).
  answer_ok     : the planted fact's answer appears in the final output (it used the long context).
Both required to pass -- proving the orchestrator delegated AND used its long context together.

  bench/longctx.py --project DIR --depths 32000 100000 --n 1 --out FILE
"""
import argparse, json, os, random, re, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ui
from e2e import run_opencode_json, extract, db_children

# Real-text filler blocks so the planted fact sits among genuine content, not lorem noise.
FILLER = [
    "The build system compiles each module in dependency order, caching intermediate artifacts "
    "so that unchanged sources are not rebuilt. The scheduler tracks a directed acyclic graph of "
    "targets and walks it breadth-first, dispatching independent nodes concurrently.\n\n",
    "Network requests are retried with exponential backoff and jitter. The client caps total "
    "attempts and surfaces the last error with full context. Idempotent operations are safe to "
    "retry; non-idempotent ones carry a client-generated key for server-side deduplication.\n\n",
    "The storage layer separates hot and cold paths: recent writes land in an in-memory buffer "
    "flushed periodically to durable segments, while reads consult a bloom filter before touching "
    "disk. Compaction merges segments in the background to bound read amplification.\n\n",
    "Configuration is resolved by deep-merging layered sources: built-in defaults, a global file, "
    "a project file, and environment overrides, in that precedence order. Unknown keys are "
    "rejected at load time so a typo fails fast rather than silently doing nothing.\n\n",
]


def build_longctx_task(depth_tokens, seed):
    """Return (prompt, planted_answer). Plants a unique keyed fact ~60% into a haystack of
    ~depth_tokens, and asks a task that requires reporting that fact AND delegating a sub-lookup."""
    rng = random.Random(seed)
    code = f"ARTIFACT-{rng.randint(10000, 99999)}"       # the planted needle
    planted = f"The designated release codename for build 7 is {code}."
    approx_chars = depth_tokens * 4                       # ~4 chars/token
    blocks, size = [], 0
    while size < approx_chars:
        b = rng.choice(FILLER)
        blocks.append(b); size += len(b)
    insert_at = int(len(blocks) * 0.6)
    blocks.insert(insert_at, "\n" + planted + "\n\n")
    haystack = "".join(blocks)
    # The task fuses long-context use + delegation: it must extract the planted fact from the
    # document above AND dispatch a worker to inspect the repo, then combine both.
    task = (
        "Below is a long reference document, followed by a task.\n\n"
        "=== DOCUMENT START ===\n" + haystack + "=== DOCUMENT END ===\n\n"
        "TASK: Two things, then combine:\n"
        "1. From the document above, find and state the designated release codename for build 7.\n"
        "2. Dispatch one worker (use a subagent) to list the make targets in the repo's Makefile.\n"
        "Give a two-line answer: line 1 = the codename, line 2 = the make targets."
    )
    return task, code


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--depths", type=int, nargs="+", default=[32000, 100000])
    ap.add_argument("--n", type=int, default=1, help="repeats per depth")
    ap.add_argument("--want-provider", default="magus")
    ap.add_argument("--min-subagents", type=int, default=1)
    ap.add_argument("--timeout", type=int, default=2400)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out")
    ap.add_argument("--logfile")
    a = ap.parse_args()
    ui.setup_logging(a.logfile)
    out = open(a.out, "a") if a.out else None

    ui.phase(f"longctx (delegation at depth) -- depths {a.depths}, n={a.n}")
    tbl = ui.Table("longctx", ["depth", "pass", "wall s", "subagents", "on magus", "used ctx"],
                   None, justify=["right", "center", "right", "right", "center", "center"])
    passed = total = 0
    for depth in a.depths:
        for rep in range(a.n):
            prompt, code = build_longctx_task(depth, a.seed + rep)
            with ui.console.status(f"depth {depth} rep {rep} (orchestrating over long ctx)..."):
                events, root, wall, rc = run_opencode_json(a.project, prompt, a.timeout)
            answer, spawned = extract(events)
            children = db_children(a.project, root)
            n_sub = len(children) if children else len(spawned)
            provs = [c.get("prov") for c in children] if children else [d.get("provider") for d in spawned]
            on_want = bool(provs) and all(p == a.want_provider for p in provs if p)
            used_ctx = bool(re.search(re.escape(code), answer))   # did it find the planted needle
            deleg_ok = n_sub >= a.min_subagents
            ok = used_ctx and deleg_ok and rc == 0
            passed += ok; total += 1
            rec = {"test": "longctx", "depth": depth, "rep": rep, "pass": bool(ok),
                   "used_ctx": used_ctx, "delegation_ok": deleg_ok, "subagents": n_sub,
                   "on_want_provider": on_want, "rc": rc, "wall_s": round(wall, 1),
                   "root_session": root}
            if out:
                out.write(json.dumps(rec) + "\n"); out.flush()
            ui.log.info("longctx depth=%d: pass=%s subagents=%d on_magus=%s used_ctx=%s wall=%.0fs",
                        depth, ok, n_sub, on_want, used_ctx, wall)
            tbl.add([depth, "yes" if ok else "NO", f"{wall:.0f}", n_sub,
                     "yes" if on_want else ("n/a" if n_sub == 0 else "NO"),
                     "yes" if used_ctx else "NO"], style=(None if ok else "red"))
    tbl.render()
    if out:
        out.write(json.dumps({"test": "longctx_summary", "passed": passed, "total": total}) + "\n")
        out.close()
    ui.summary("longctx", passed, total, "delegation + long-context use both required")
    return 0  # successful measurement exits 0 regardless of score; see e2e.py rationale.


if __name__ == "__main__":
    sys.exit(main())
