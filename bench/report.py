#!/usr/bin/env python3
"""Aggregate one benchmark run's JSONL files into a single scorecard (stdout + report.md).

Reads every *-<stamp>.jsonl under the bench dir for a given stamp and pulls the summary
rows each suite emits, so the operator gets one consolidated view instead of 4-5 files.

  bench/report.py --dir /tmp/fools-trick/bench --stamp 20260825-013724
"""
import argparse, glob, json, os, sys


def summaries(paths):
    """Return list of (node, suite, metric_str) rows from all summary records."""
    rows = []
    for p in paths:
        node = "worker" if "worker" in os.path.basename(p) else (
            "fool" if "fool" in os.path.basename(p) else
            ("e2e" if "e2e" in os.path.basename(p) else "?"))
        with open(p) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = r.get("test", "")
                if t.endswith("_summary") or r.get("summary"):
                    if t == "e2e_summary":
                        rows.append((node, "e2e delegation",
                                     f"{r['passed']}/{r['total']} tasks"))
                    elif "accuracy_pct" in r:
                        name = t.replace("_summary", "")
                        n = r.get("total") or r.get("n") or "?"
                        rows.append((node, name, f"{r['accuracy_pct']}%  (n={n})"))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--stamp", required=True)
    ap.add_argument("--md", help="write consolidated markdown here")
    a = ap.parse_args()
    paths = sorted(glob.glob(os.path.join(a.dir, f"*{a.stamp}*.jsonl")))
    rows = summaries(paths)
    if not rows:
        print("no summary rows found for stamp", a.stamp)
        return 0

    lines = [f"# fools-trick benchmark scorecard  ({a.stamp})", ""]
    lines.append(f"{'node':<8} {'suite':<18} result")
    lines.append("-" * 48)
    for node, suite, metric in rows:
        lines.append(f"{node:<8} {suite:<18} {metric}")
    report = "\n".join(lines)
    print("\n" + report + "\n")
    if a.md:
        with open(a.md, "w") as f:
            f.write(report + "\n")
        print(f"scorecard -> {a.md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
