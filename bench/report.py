#!/usr/bin/env python3
"""Aggregate one benchmark run into a single trustworthy scorecard (stdout + report.md).

Pulls from every output the run produces, in their native formats:
  - capability : lm-eval writes results_*.json under cap-<node>-<stamp>/<model>/. We read the
                 primary metric + stderr + effective N per task (real statistical reporting).
  - code/tools : our eval.py JSONL summary rows (accuracy_pct, n).
  - safety     : our safety.py JSONL summary rows (compliance_pct by axis, n, verdict counts).
  - e2e/longctx: pass/total JSONL summary rows.
  - deep       : per-depth accuracy rows (disambiguated by depth, not collapsed).
  - speed      : key throughput numbers (decode t/s, aggregate, cache hit) per node.

Grouped by axis so the operator reads one coherent scorecard. Every metric carries its N and,
where the source provides it, a +/- stderr, so a small-sample number is never mistaken for a
precise one.

  bench/report.py --dir /tmp/fools-trick/bench --stamp 20260825-110959 [--md report.md]
"""
import argparse, glob, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import wilson  # noqa: E402


def ci(correct, total):
    """'X.X% [lo-hi, n=N]' with 95% Wilson CI, so small-n is never read as precise."""
    if not total:
        return "n/a (n=0)"
    lo, hi = wilson(correct, total)
    return f"{100.0*correct/total:.1f}% [{lo:.0f}-{hi:.0f}, n={total}]"


def _node_of(path):
    b = os.path.basename(path)
    if "worker" in b:
        return "worker"
    if "fool" in b:
        return "fool"
    return ""


def capability_rows(bench_dir, stamp):
    """Read lm-eval results_*.json for each node. One row per task: value +/- stderr (n=eff)."""
    rows = []
    for capdir in glob.glob(os.path.join(bench_dir, f"cap-*-{stamp}")):
        node = "worker" if "cap-worker" in capdir else ("fool" if "cap-fool" in capdir else "")
        results = sorted(glob.glob(os.path.join(capdir, "*", "results_*.json")))
        if not results:
            continue
        d = json.load(open(results[-1]))  # latest
        nsamp = d.get("n-samples", {})
        for task, metrics in d.get("results", {}).items():
            # pick the primary metric: prefer strict/exact/acc, skip alias/stderr keys
            prim_key = None
            for k in metrics:
                if k.endswith("_stderr") or ",none" not in k and "_stderr" in k:
                    continue
                if any(s in k for s in ("exact_match,strict", "prompt_level_strict",
                                        "acc,none", "pass@1", "acc_norm,none", "exact_match,")):
                    prim_key = k
                    break
            if prim_key is None:  # fall back to first non-stderr metric
                prim_key = next((k for k in metrics if "_stderr" not in k and k != "alias"), None)
            if prim_key is None:
                continue
            val = metrics[prim_key]
            if not isinstance(val, (int, float)):
                continue
            stderr = metrics.get(prim_key.replace(",", "_stderr,", 1)) or \
                     metrics.get(prim_key + "_stderr")
            n = nsamp.get(task, {}).get("effective", "?")
            metric_short = prim_key.split(",")[0]
            se = f" +/-{stderr*100:.1f}" if isinstance(stderr, (int, float)) else ""
            rows.append(("capability", node, f"{task} ({metric_short})",
                         f"{val*100:.1f}%{se}  (n={n})"))
    return rows


def jsonl_rows(bench_dir, stamp):
    """Read our own JSONL summary rows: code/tools/safety/e2e/deep/longctx."""
    rows = []
    for p in sorted(glob.glob(os.path.join(bench_dir, f"*{stamp}*.jsonl"))):
        node = _node_of(p)
        deep_seen = {}
        for line in open(p):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = r.get("test", "")
            is_sum = t.endswith("_summary") or r.get("summary")
            if not is_sum:
                # capture per-depth deep rows (they have no summary; aggregate here)
                if t == "deep" and "target_ctx" in r:
                    pass
                continue
            if t == "e2e_summary":
                rows.append(("delegation", node or "e2e", "e2e fan-out",
                             ci(r["passed"], r["total"]) + " pass"))
            elif t == "longctx_summary":
                rows.append(("long-context", "fool", "agentic delegation-at-depth",
                             ci(r["passed"], r["total"]) + " pass"))
            elif t == "deep_summary":
                depth = r.get("len") or r.get("depth") or f"run{len(deep_seen)}"
                deep_seen[depth] = 1
                rows.append(("long-context", "fool", f"deep needle @ {depth} tok",
                             ci(r.get("correct", 0), r.get("total", 0))))
            elif t.startswith("safety_"):
                ds = t.replace("safety_", "")
                axis = r.get("axis", "?")
                a, df, rf = r.get("answered", 0), r.get("deflected", 0), r.get("refused", 0)
                n = r.get("n", a + df + rf)
                lbl = "compliance" if axis == "harmful" else "compliance (benign: want high)"
                rows.append(("safety", "worker", f"{ds} [{axis}]",
                             f"{ci(a, n)} {lbl}  (defl={df} ref={rf})"))
            elif "accuracy_pct" in r:  # code, tools
                name = t.replace("_summary", "")
                # reconstruct correct count from pct*n for the CI
                nn = r.get("n", r.get("total", 0))
                cc = round(r["accuracy_pct"] / 100.0 * nn) if nn else 0
                rows.append(("code/tools", node or "worker", name, ci(cc, nn)))
    return rows


def speed_rows(bench_dir, stamp):
    """Surface headline speed numbers per node: decode t/s, aggregate, cache hit."""
    rows = []
    for p in sorted(glob.glob(os.path.join(bench_dir, f"speed-*{stamp}*.jsonl"))):
        node = _node_of(p)
        decodes, agg, cache = [], None, None
        for line in open(p):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("test") == "speed" and r.get("decode_tps"):
                decodes.append(r["decode_tps"])
            elif r.get("test") == "concurrency":
                agg = max(agg or 0, r.get("agg_tps", 0))
            elif r.get("test") == "cache" and r.get("run", "").startswith("2"):
                cache = r.get("hit_pct")
        if decodes:
            d = f"decode {min(decodes):.0f}-{max(decodes):.0f} t/s"
            if agg:
                d += f", agg {agg:.0f} t/s"
            if cache is not None:
                d += f", cache {cache:.0f}%"
            rows.append(("speed", node, "throughput", d))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--stamp", required=True)
    ap.add_argument("--md")
    a = ap.parse_args()

    rows = (speed_rows(a.dir, a.stamp) + capability_rows(a.dir, a.stamp)
            + jsonl_rows(a.dir, a.stamp))
    if not rows:
        print("no results found for stamp", a.stamp)
        return 0

    # group by axis in a stable order
    order = ["speed", "capability", "code/tools", "safety", "delegation", "long-context"]
    rows.sort(key=lambda r: (order.index(r[0]) if r[0] in order else 99, r[1], r[2]))

    lines = [f"# fools-trick benchmark scorecard  ({a.stamp})", ""]
    cur = None
    for axis, node, suite, metric in rows:
        if axis != cur:
            lines.append("")
            lines.append(f"## {axis}")
            cur = axis
        lines.append(f"  {node:<7} {suite:<34} {metric}")
    report = "\n".join(lines)
    print("\n" + report + "\n")
    if a.md:
        with open(a.md, "w") as f:
            f.write(report + "\n")
        print(f"scorecard -> {a.md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
