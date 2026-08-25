#!/usr/bin/env python3
"""Export a benchmark run's scorecard to a formatted Google Sheet.

AUTH (the "different Google account" case): a Google API key will NOT work -- creating a file
requires a real identity. We use a SERVICE ACCOUNT, so the target account is just a different
key file. Point GOOGLE_APPLICATION_CREDENTIALS (or --credentials) at the service-account JSON.
The sheet is created in the service account's own Drive, then shared to --share-with so it lands
in a human's Drive. To target a different Google account, use that account's service-account key.

  GOOGLE_APPLICATION_CREDENTIALS=/path/key.json \
    bench/export_sheets.py --dir /tmp/fools-trick/bench --stamp 20260825-110959 \
      --share-with you@example.com

Reads the same run outputs report.py aggregates, so the sheet mirrors the CLI scorecard, with a
tab per axis and the manifest recorded on a Run Info tab.
"""
import argparse, glob, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import report as report_mod  # reuse the exact aggregation so sheet == scorecard

SCOPES = ["https://www.googleapis.com/auth/drive.file",
          "https://www.googleapis.com/auth/spreadsheets"]


def _credentials(path):
    cand = path or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not cand:
        sys.exit("ERROR: no service-account key. Set GOOGLE_APPLICATION_CREDENTIALS=/path/key.json "
                 "(the target account's service-account JSON) or pass --credentials.")
    if not os.path.exists(cand):
        sys.exit(f"ERROR: credentials file not found: {cand}")
    try:
        from google.oauth2.service_account import Credentials
    except ImportError:
        sys.exit("ERROR: need google-auth + gspread: .bench-venv/bin/pip install gspread google-auth")
    try:
        return Credentials.from_service_account_file(cand, scopes=SCOPES)
    except Exception as e:
        sys.exit(f"ERROR: invalid service-account key {cand}: {e}")


def _rows(bench_dir, stamp):
    """Same rows the CLI scorecard shows: (axis, node, suite, metric)."""
    return (report_mod.speed_rows(bench_dir, stamp)
            + report_mod.capability_rows(bench_dir, stamp)
            + report_mod.jsonl_rows(bench_dir, stamp))


def _manifest(bench_dir, stamp):
    p = os.path.join(bench_dir, f"manifest-{stamp}.json")
    return json.load(open(p)) if os.path.exists(p) else {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--stamp", required=True)
    ap.add_argument("--credentials", help="service-account JSON (else GOOGLE_APPLICATION_CREDENTIALS)")
    ap.add_argument("--share-with", help="email to share the created sheet with (editor)")
    ap.add_argument("--title", default="")
    a = ap.parse_args()

    rows = _rows(a.dir, a.stamp)
    if not rows:
        sys.exit(f"no results for stamp {a.stamp}")
    import gspread
    gc = gspread.authorize(_credentials(a.credentials))
    title = a.title or f"fools-trick benchmark {a.stamp}"
    sh = gc.create(title)
    if a.share_with:
        sh.share(a.share_with, perm_type="user", role="writer", notify=False)

    # One worksheet per axis (matches the CLI grouping), plus a Run Info tab from the manifest.
    order = ["speed", "capability", "code/tools", "safety", "delegation", "long-context"]
    by_axis = {}
    for axis, node, suite, metric in rows:
        by_axis.setdefault(axis, []).append([node, suite, metric])
    first = True
    for axis in [x for x in order if x in by_axis] + [x for x in by_axis if x not in order]:
        data = [["node", "suite", "result"]] + by_axis[axis]
        ws = sh.sheet1 if first else sh.add_worksheet(title=axis[:30], rows=len(data) + 2, cols=3)
        if first:
            ws.update_title(axis[:30]); first = False
        ws.update(values=data, range_name="A1")
        ws.format("A1:C1", {"textFormat": {"bold": True}})

    mf = _manifest(a.dir, a.stamp)
    if mf:
        info = sh.add_worksheet(title="Run Info", rows=20, cols=2)
        git = mf.get("harness_git", {})
        nodes = mf.get("nodes", {})
        info.update(values=[
            ["stamp", mf.get("stamp", "")],
            ["created", mf.get("created", "")],
            ["size", mf.get("size", "")],
            ["seed", str(mf.get("seed", ""))],
            ["harness git sha", git.get("sha", "")],
            ["harness dirty", str(git.get("dirty", ""))],
            ["worker served", (nodes.get("worker") or {}).get("served_id", "")],
            ["orchestrator served", (nodes.get("orchestrator") or {}).get("served_id", "")],
        ], range_name="A1")

    print(f"sheet -> {sh.url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
