#!/usr/bin/env python3
"""Capture a run manifest: what produced these numbers, so a scorecard is interpretable months
later. Records the harness git state, the run's size/seed, and the served model shapes on both
nodes (read live from /v1/models). Written once per run alongside the results.

  bench/manifest.py --stamp 20260825-110959 --size smoke --out DIR
"""
import argparse, json, os, subprocess, sys, time, urllib.request


def git(*args):
    try:
        return subprocess.check_output(["git", *args], stderr=subprocess.DEVNULL, text=True).strip()
    except Exception:
        return None


def git_state():
    return {
        "sha": git("rev-parse", "HEAD"),
        "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
        "describe": git("describe", "--tags", "--always", "--dirty"),
        "dirty": bool(git("status", "--porcelain")),
    }


def served_model(url):
    """Read what a node is actually serving right now (id + reported context), so the manifest
    reflects reality, not just config."""
    try:
        d = json.loads(urllib.request.urlopen(url + "/v1/models", timeout=5).read().decode())
        m = (d.get("models") or d.get("data") or [{}])[0]
        return {"served_id": m.get("id") or m.get("model") or m.get("name"),
                "url": url, "reachable": True}
    except Exception as e:
        return {"url": url, "reachable": False, "error": str(e)[:80]}


def build(stamp, size, seed, worker_url, fool_url, extra=None):
    return {
        "stamp": stamp,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "size": size,
        "seed": seed,
        "harness_git": git_state(),
        "nodes": {
            "worker": served_model(worker_url) if worker_url else None,
            "orchestrator": served_model(fool_url) if fool_url else None,
        },
        **(extra or {}),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stamp", required=True)
    ap.add_argument("--size", default="small")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--worker-url", default=os.environ.get("WORKER_URL", ""))
    ap.add_argument("--fool-url", default=os.environ.get("ORCHESTRATOR_URL", ""))
    ap.add_argument("--out", required=True, help="dir to write manifest-<stamp>.json")
    a = ap.parse_args()
    mf = build(a.stamp, a.size, a.seed, a.worker_url, a.fool_url)
    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, f"manifest-{a.stamp}.json")
    with open(path, "w") as f:
        json.dump(mf, f, indent=2)
    print(f"manifest -> {path}")
    if mf["harness_git"]["dirty"]:
        sys.stderr.write("[manifest] NOTE: harness tree is dirty; run is not from a clean commit.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
