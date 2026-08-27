#!/usr/bin/env python3
"""Subagent context-prune eval: does a WORKER run past its input budget without going amnesiac?

The orchestrator's long-context/delegation is covered by longctx.py + e2e.py. This covers the other
half: the subagent in-context prune (docs/memory-design.md). A worker has a small window
(WORKER_INPUT_TOKENS, default 26000). As it reads file after file the tool results pile up; the
plugin evicts the ones the worker has distilled via note() and, as a backstop, the oldest -- so the
worker must be able to run a long read task and still answer from something it saw EARLY, whose raw
tool result was evicted. That is the competency-under-eviction claim, and nothing else tests it live.

Construction: write N large files to scratch, with a unique needle planted in the FIRST one. Give a
worker a task that must read all of them in order and answer using the needle (from file 0) plus a
fact from a LATE file. Reading all N drives the worker's input past WORKER_INPUT_TOKENS, so the
early file's raw result is evicted before the worker answers. Then score, DB- and artifact-grounded:
  budget_crossed : the worker's tokens_input (from the session DB) exceeded WORKER_INPUT_TOKENS,
                   so the prune actually engaged (otherwise the test proved nothing).
  distilled      : the worker's notes scratch file exists and is non-empty (it used note()).
  answer_ok      : the final answer contains the early needle AND the late fact -- competency
                   survived eviction.
A pass requires all three: the worker exceeded its window, distilled, and stayed correct.

  bench/prune.py --project DIR [--files 8 --file-tokens 6000 --out FILE]
"""
import argparse, json, os, random, re, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ui
from e2e import run_opencode_json, extract, db_children

SCRATCH = os.environ.get("FOOLS_SCRATCH", "/tmp/fools-trick/scratch")
WORKER_INPUT_TOKENS = int(os.environ.get("WORKER_INPUT_TOKENS", "26000"))

# Real-text blocks so files are genuine content the worker must actually read, not compressible noise.
FILLER = [
    "The scheduler walks a directed acyclic graph of targets breadth-first, dispatching independent "
    "nodes concurrently and caching intermediate artifacts so unchanged sources are not rebuilt.\n",
    "Requests retry with exponential backoff and jitter; idempotent operations are safe to retry, "
    "non-idempotent ones carry a client-generated key for server-side deduplication.\n",
    "The storage layer buffers recent writes in memory and flushes to durable segments; reads "
    "consult a bloom filter before touching disk, and background compaction bounds read amplification.\n",
    "Configuration deep-merges layered sources -- defaults, global file, project file, environment -- "
    "and rejects unknown keys at load time so a typo fails fast instead of silently doing nothing.\n",
]


def write_haystack(n_files, file_tokens, seed):
    """Write n_files large files to scratch; plant a needle in file 0 and a late fact in file n-1.
    Return (dir, needle, late_fact)."""
    rng = random.Random(seed)
    d = os.path.join(SCRATCH, f"prune-{rng.randint(10000, 99999)}")
    os.makedirs(d, exist_ok=True)
    needle = f"NEEDLE-{rng.randint(10000, 99999)}"
    late = f"LATEFACT-{rng.randint(10000, 99999)}"
    approx_chars = file_tokens * 4
    for i in range(n_files):
        body = []
        size = 0
        while size < approx_chars:
            b = rng.choice(FILLER)
            body.append(b); size += len(b)
        if i == 0:
            body.insert(len(body) // 2, f"\nThe secret build codename is {needle}.\n")
        if i == n_files - 1:
            body.insert(len(body) // 2, f"\nThe final deployment region is {late}.\n")
        with open(os.path.join(d, f"doc_{i:02d}.txt"), "w") as fh:
            fh.write("".join(body))
    return d, needle, late


def worker_input_tokens(children):
    """Max tokens_input across dispatched worker sessions (the one that did the reading)."""
    return max((c.get("tokens_input", 0) or 0 for c in children), default=0)


def notes_nonempty(root_children):
    """A worker wrote notes if any notes-<sid>.md under scratch is non-empty. We match on any child
    session id, since the notes file is keyed by the worker's own sessionID."""
    for c in root_children:
        sid = c.get("id") or c.get("session_id")
        if not sid:
            continue
        f = os.path.join(SCRATCH, f"notes-{sid}.md")
        try:
            if os.path.getsize(f) > 0:
                return True
        except OSError:
            pass
    return False


def any_notes_since(t0):
    """Fallback: any notes file written after the run started (when child ids aren't in the DB row)."""
    try:
        for name in os.listdir(SCRATCH):
            if name.startswith("notes-") and name.endswith(".md"):
                p = os.path.join(SCRATCH, name)
                if os.path.getmtime(p) >= t0 and os.path.getsize(p) > 0:
                    return True
    except OSError:
        pass
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--files", type=int, default=8, help="haystack files the worker must read")
    ap.add_argument("--file-tokens", type=int, default=6000, help="approx tokens per file")
    ap.add_argument("--want-provider", default="magus")
    ap.add_argument("--timeout", type=int, default=2400)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out")
    ap.add_argument("--logfile")
    a = ap.parse_args()
    ui.setup_logging(a.logfile)
    out = open(a.out, "a") if a.out else None

    d, needle, late = write_haystack(a.files, a.file_tokens, a.seed)
    budget_target = a.files * a.file_tokens
    ui.phase(f"prune (subagent competency under eviction) -- {a.files} files x ~{a.file_tokens} tok "
             f"(~{budget_target} tok read >> {WORKER_INPUT_TOKENS} budget)")

    prompt = (
        f"Dispatch ONE worker (a subagent) to do this task and report back:\n"
        f"Read every file in {d} in order (doc_00.txt, doc_01.txt, ... doc_{a.files-1:02d}.txt). "
        f"As you read each file, note any distinctive labelled fact you find. After reading ALL of "
        f"them, answer with exactly two lines:\n"
        f"line 1: the secret build codename (a NEEDLE-##### value)\n"
        f"line 2: the final deployment region (a LATEFACT-##### value)."
    )

    t0 = time.time()
    with ui.console.status("running prune task (worker reads a large haystack)..."):
        events, root, wall, rc = run_opencode_json(a.project, prompt, a.timeout)
    answer, _ = extract(events)
    children = db_children(a.project, root)

    win = worker_input_tokens(children)
    budget_crossed = win > WORKER_INPUT_TOKENS
    distilled = notes_nonempty(children) or any_notes_since(t0)
    found_needle = bool(re.search(re.escape(needle), answer))
    found_late = bool(re.search(re.escape(late), answer))
    answer_ok = found_needle and found_late and rc == 0
    ok = budget_crossed and distilled and answer_ok

    rec = {"test": "prune", "pass": bool(ok), "budget_crossed": budget_crossed,
           "worker_input_tokens": win, "budget": WORKER_INPUT_TOKENS, "distilled": distilled,
           "found_early_needle": found_needle, "found_late_fact": found_late, "rc": rc,
           "wall_s": round(wall, 1), "root_session": root}
    if out:
        out.write(json.dumps(rec) + "\n")
        out.write(json.dumps({"test": "prune_summary", "passed": int(ok), "total": 1}) + "\n")
        out.close()
    ui.log.info("prune: pass=%s worker_input=%d/%d distilled=%s needle=%s late=%s wall=%.0fs",
                ok, win, WORKER_INPUT_TOKENS, distilled, found_needle, found_late, wall)

    tbl = ui.Table("prune", ["pass", "worker input tok", "budget crossed", "distilled",
                             "early needle", "late fact", "wall s"], None,
                   justify=["center", "right", "center", "center", "center", "center", "right"])
    tbl.add(["yes" if ok else "NO", win, "yes" if budget_crossed else "NO",
             "yes" if distilled else "NO", "yes" if found_needle else "NO",
             "yes" if found_late else "NO", f"{wall:.0f}"], style=(None if ok else "red"))
    tbl.render()
    ui.summary("prune", int(ok), 1, "worker crossed its window, distilled, and stayed correct")
    return 0


if __name__ == "__main__":
    sys.exit(main())
