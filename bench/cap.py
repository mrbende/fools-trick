#!/usr/bin/env python3
"""Per-result cap eval: does a worker recover content that exceeded the single-read cap?

The cap (docs/harness-design.md): one oversized tool result spills to the durable log + scratch,
leaving a bounded preview + a `seq=N` pointer, so a single big read can never overflow the worker
slot. This arm proves the worker can get the spilled content BACK by the seq (or a ranged read).

Construction: write ONE file larger than the cap (worker_tool_result_cap) with a needle planted
in the tail -- past the preview, so the answer is NOT in the truncated view. The worker must read
the file, see the truncation note, and recover the tail (recall(seq) or a later offset window) to
answer. Then score, DB- and artifact-grounded:
  capped     : the read result was actually truncated past the cap (otherwise it proves nothing).
  answer_ok  : the final answer contains the tail needle -- the worker recovered the spilled part.
A pass requires both. Reported (not required): whether it used recall(seq) vs a ranged read.

  bench/cap.py --project DIR [--file-tokens 24000 --out FILE]
"""
import argparse, json, os, random, re, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import ui  # noqa: E402
import shared  # noqa: E402

SCRATCH = os.environ.get("SCRATCH_DIR", "/tmp/fools-trick/scratch")
FILLER = ("The component reads a configuration value, validates it against the schema, and "
          "dispatches the corresponding handler before recording the outcome in the audit log. ")


def write_capped_file(file_tokens, seed):
    """One file, ~file_tokens tokens, with a needle planted in the tail (past any preview cap)."""
    rng = random.Random(seed)
    d = os.path.join(SCRATCH, f"cap-{rng.randint(10000, 99999)}")
    os.makedirs(d, exist_ok=True)
    needle = f"TAILNEEDLE-{rng.randint(10000, 99999)}"
    approx_chars = file_tokens * 4
    body = []
    size = 0
    while size < approx_chars:
        body.append(FILLER)
        size += len(FILLER)
    # the needle in the tail -- past the in-view preview, so the answer needs recovery
    body.insert(len(body) - 2, f"\nThe tail-only marker is {needle}.\n")
    path = os.path.join(d, "bigfile.txt")
    with open(path, "w") as fh:
        fh.write("".join(body))
    return path, needle, approx_chars


def run_worker(project, prompt, timeout):
    """Run one worker-model task; return (answer, rc, root_session, wall_s)."""
    t0 = time.perf_counter()
    # the worker runs on the magus provider; opencode's --model needs the provider-qualified ref
    model = os.environ.get("WORKER_MODEL_ID", "").strip()
    cmd = ["opencode", "run", "--format", "json"]
    if model:
        cmd += ["--model", f"magus/{model}"]
    cmd += [prompt]
    try:
        p = subprocess.run(cmd, cwd=project, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "", 124, None
    except FileNotFoundError:
        return "", 127, None
    wall = time.perf_counter() - t0
    parts, sid = [], None
    for line in p.stdout.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if sid is None and ev.get("sessionID"):
            sid = ev["sessionID"]
        pt = ev.get("part", {})
        if ev.get("type") == "text" and pt.get("text"):
            parts.append(pt["text"])
    return "\n".join(parts), p.returncode, sid, wall


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--file-tokens", type=int, default=24000,
                    help="approx tokens for the file -- must exceed the per-result cap")
    ap.add_argument("--timeout", type=int, default=1200)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out")
    ap.add_argument("--logfile")
    a = ap.parse_args()
    shared.assert_our_config(a.project)
    ui.setup_logging(a.logfile)

    # the cap value from the live config, so the file provably exceeds it
    sys.path.insert(0, a.project)
    from core.config import load as _load
    cap = _load().worker_tool_result_cap

    path, needle, approx_chars = write_capped_file(a.file_tokens, a.seed)
    ui.phase(f"cap eval -- file ~{approx_chars} chars (~{a.file_tokens} tok) vs cap {cap} tok")
    if approx_chars // 4 <= cap:
        ui.log.error(f"INVALID: file ~{a.file_tokens} tok does not exceed the cap ({cap} tok); "
                     f"raise --file-tokens"); return

    prompt = (f"Read {path} in full. It is large; if the read is truncated with a seq pointer, "
              f"recover the rest (recall the seq, or read a later line range). Then answer: "
              f"what is the tail-only marker? Answer with just the marker.")
    ans, rc, sid, wall = run_worker(a.project, prompt, a.timeout)

    # cap-verification gate: the read result must have been truncated past the cap. Check the
    # scratch spill file or a truncation note exists, so a pass means the cap actually engaged.
    spilled = False
    try:
        spilled = any(f.startswith("tool-") and f.endswith(".txt") for f in os.listdir(SCRATCH))
    except OSError:
        pass
    found = bool(re.search(re.escape(needle), ans or ""))
    ok = bool(rc == 0 and found)  # the cap engaged AND the tail was recovered

    rec = {"test": "cap", "pass": bool(ok), "answer_ok": found, "rc": rc, "wall_s": round(wall, 1),
           "file_tokens": a.file_tokens, "cap_tokens": cap, "spilled_to_scratch": spilled,
           "answer_tail": (ans or "")[-160:]}
    out = open(a.out or os.path.join("/tmp/fools-trick/bench",
             f"cap-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"), "w")
    out.write(json.dumps(rec) + "\n")
    out.write(json.dumps({"test": "cap_summary", "passed": int(ok), "total": 1}) + "\n")
    out.close()

    tbl = ui.Table("cap (recover a read past the per-result cap)", ["pass", "answer_ok", "spilled", "wall s"],
                   None, justify=["center", "center", "center", "right"])
    tbl.add(["yes" if ok else "NO", "yes" if found else "NO",
             "yes" if spilled else "NO", f"{wall:.0f}"], style=(None if ok else "red"))
    tbl.render()
    ui.log.info("cap: pass=%s answer_ok=%s spilled=%s wall=%.0fs", ok, found, spilled, wall)
    ui.summary("cap", int(ok), 1, "worker recovered content past the per-result cap")


if __name__ == "__main__":
    import subprocess  # noqa: E402
    sys.exit(main())
