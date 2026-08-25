#!/usr/bin/env python3
"""Abliterated-vs-base comparison: run the same eval suite against two model variants and
diff the results. This is the measurement the field is missing -- whether abliteration
helps, hurts, or is neutral on reasoning, coding, and (never-before-measured) tool-calling.

It does not serve models; it drives eval.py against whatever endpoint each variant is on.
The operator serves variant A, runs its arm; serves variant B, runs its arm; then diffs.
Each arm writes results/<variant>.jsonl; --diff reads two and prints the delta table.

  # arm A (e.g. abliterated worker already serving on :8898)
  bench/compare.py run --label abliterated --url http://127.0.0.1:8898 --model qwen3.8-27b-obliterated
  # (swap the served weights to base, then)
  bench/compare.py run --label base --url http://127.0.0.1:8898 --model qwen3.8-27b
  # diff
  bench/compare.py diff --a abliterated --b base

The suites run: gsm8k (reasoning), code (HumanEval+, executed), tools (BFCL-style). Same n,
same seed, same endpoint shape -- the only variable is the weights. That isolates the
abliteration effect.
"""
import argparse, json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.environ.get("COMPARE_DIR", "/tmp/fools-trick/compare")
SUITES = ("gsm8k", "code", "tools")


def run_arm(label, url, model, n_gsm8k, n_code, timeout):
    os.makedirs(RESULTS, exist_ok=True)
    out = os.path.join(RESULTS, f"{label}.jsonl")
    open(out, "w").close()  # fresh
    py = sys.executable
    ev = os.path.join(HERE, "eval.py")
    plans = [("gsm8k", ["--n", str(n_gsm8k)]),
             ("code", ["--n", str(n_code)]),
             ("tools", [])]
    for suite, extra in plans:
        cmd = [py, ev, suite, "--url", url, "--model", model,
               "--timeout", str(timeout), "--out", out] + extra
        print(f"[{label}] {suite} ...", flush=True)
        r = subprocess.run(cmd)
        if r.returncode != 0:
            print(f"[{label}] {suite} exited {r.returncode}", file=sys.stderr)
    print(f"[{label}] done -> {out}")


def load_summaries(label):
    path = os.path.join(RESULTS, f"{label}.jsonl")
    out = {}
    with open(path) as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("summary"):
                out[rec["test"]] = rec["accuracy_pct"]
    return out


def diff(a_label, b_label):
    a = load_summaries(a_label)
    b = load_summaries(b_label)
    suites = [s for s in SUITES if s in a or s in b]
    w = max(len(s) for s in suites) if suites else 8
    print(f"\n{'suite'.ljust(w)}  {a_label:>12}  {b_label:>12}  {'delta':>8}")
    print("-" * (w + 40))
    for s in suites:
        av = a.get(s); bv = b.get(s)
        astr = f"{av:.1f}%" if av is not None else "-"
        bstr = f"{bv:.1f}%" if bv is not None else "-"
        if av is not None and bv is not None:
            d = av - bv
            dstr = f"{d:+.1f}"
            mark = "  <- abliteration helps" if d > 1 else ("  <- abliteration hurts" if d < -1 else "")
        else:
            dstr = "-"; mark = ""
        print(f"{s.ljust(w)}  {astr:>12}  {bstr:>12}  {dstr:>8}{mark}")
    print()
    print(f"(a={a_label}, b={b_label}; delta = a - b; >0 means {a_label} scored higher)")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--label", required=True, help="variant name, e.g. abliterated or base")
    r.add_argument("--url", required=True)
    r.add_argument("--model", required=True)
    r.add_argument("--n-gsm8k", type=int, default=40)
    r.add_argument("--n-code", type=int, default=15)
    r.add_argument("--timeout", type=int, default=300)
    d = sub.add_parser("diff")
    d.add_argument("--a", required=True)
    d.add_argument("--b", required=True)
    a = ap.parse_args()
    if a.cmd == "run":
        run_arm(a.label, a.url, a.model, a.n_gsm8k, a.n_code, a.timeout)
    else:
        diff(a.a, a.b)


if __name__ == "__main__":
    sys.exit(main())
