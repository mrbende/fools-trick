#!/usr/bin/env python3
"""The canonical quality gate: the standards the harness holds code to, in any project it works.

Design, calibrated against the thread's consensus + the harness playbook (sensors reject bad work,
they don't just measure it):

  HARD GATES (block a commit; cheap, deterministic, reject bad structure):
    cyclomatic complexity per block   < 15
    cognitive complexity per function < 15
    Halstead difficulty per function  < 80
    dead code (vulture, conf >= 80)   = 0
    no NEW untyped functions          (ratchet: mypy, errors may not grow)

  REPORTED FLOOR (behavioral witness; never a hard zero):
    test coverage on core/            >= 85%  (reports; a drop below the floor fails)
      -- 100% is a fiction that games toward tests mirroring the implementation. The point is the
         suite discriminates behavior, not that every line executes. Mutation testing (opt-in
         `make bench-mutants`) is the real discriminating-power check.

  REPORTED ONLY (signals, not gates):
    lines of code per file (a weak proxy CC already covers) and duplication are reported as
    information; they are not hard blocks.

Run: make check-quality [--root DIR]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import shutil

# Tools resolve from PATH first (works in any project with its own venv on PATH), falling back to the
# harness's bench venv when gating a project that has none. This keeps the gate project-agnostic.
_HARNESS_VENV = Path(__file__).resolve().parents[2] / ".bench-venv" / "bin"


def _tool(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    local = _HARNESS_VENV / name
    return str(local) if local.exists() else name  # fall through to PATH anyway


def _tools() -> dict[str, str]:
    return {n: _tool(n) for n in ("radon", "lizard", "vulture", "coverage", "mypy", "pylint")}


# Hard-gate thresholds (structure), plus the behavioral floor.
LIMITS = {"cc": 15, "cognitive": 15, "halstead": 80, "coverage_floor": 60.0}

# Gate only the project's own source, never vendored deps, build dirs, or git submodules.
_EXCLUDE_DIRS = {".bench-venv", ".bfcl-venv", "node_modules", "__pycache__", ".git", ".opencode",
                 ".mutmut-cache", "mutants", "dist", "build", ".mypy_cache", "spark"}
# Scaffolding, not code the agent authors as the product -- covered by coverage, not per-file gates.
_SCAFFOLD_TOPS = ("test", "bench", "deploy", "docs", "scripts")


def _run(cmd: list[str], cwd: str) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=600)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.SubprocessError) as e:
        return 127, str(e)


def _own_files(root: Path, ext: str) -> list[str]:
    return [str(p) for p in sorted(root.rglob(ext))
            if not any(part in _EXCLUDE_DIRS or part.startswith(".") for part in p.parts)]


def _py_files(root: Path) -> list[str]:
    return _own_files(root, "*.py")


def _js_files(root: Path) -> list[str]:
    return _own_files(root, "*.js")


def _source_dirs(root: Path) -> list[str]:
    """Dirs of code held to the bar: canonical source roots if present, else non-scaffold dirs."""
    canonical = [d for d in ("core", "adapters", "src", "lib", "app") if (root / d).is_dir()]
    if canonical:
        return canonical
    seen: dict[str, bool] = {}
    for f in _py_files(root):
        top = os.path.relpath(f, root).split(os.sep)[0]
        if not top.startswith(_SCAFFOLD_TOPS):
            seen[top] = True
    return sorted(seen)


class Gate:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def hard(self, name: str, ok: bool, detail: str) -> None:
        print(f"  {'FAIL' if not ok else ' ok '}  {name}: {detail}")
        if not ok:
            self.failures.append(name)

    def report(self, name: str, detail: str) -> None:
        print(f"  info  {name}: {detail}")


def check_cyclomatic(g: Gate, root: Path) -> None:
    worst, worst_at = 0, ""
    dirs = _source_dirs(root)
    if dirs:
        rc, out = _run([_tool("radon"), "cc", *dirs, "-s", "-j"], str(root))
        if rc == 0:
            try:
                for path, blocks in json.loads(out).items():
                    for b in blocks:
                        if b.get("complexity", 0) > worst:
                            worst, worst_at = b["complexity"], f"{path}:{b.get('name')}"
            except json.JSONDecodeError:
                pass
    for f in _js_files(root):
        rc, out = _run([_tool("lizard"), f, "-l", "javascript", "--csv"], str(root))
        for line in out.splitlines():
            cols = line.split(",")
            if len(cols) > 1:
                try:
                    if int(cols[1]) > worst:
                        worst, worst_at = int(cols[1]), f
                except ValueError:
                    continue
    g.hard("cyclomatic<15", worst < LIMITS["cc"], f"max block CC={worst} ({worst_at or 'n/a'})")


def check_cognitive(g: Gate, root: Path) -> None:
    targets = _source_dirs(root) + _js_files(root)
    if not targets:
        g.hard("cognitive<15", True, "no source"); return
    rc, out = _run([_tool("lizard"), *targets, "-l", "python", "-l", "javascript",
                    "--CognitiveComplexity", str(LIMITS["cognitive"]), "--csv"], str(root))
    worst = 0
    for line in out.splitlines():
        cols = line.split(",")
        if len(cols) > 4 and cols[0].strip().isdigit():
            try:
                worst = max(worst, int(float(cols[4])))
            except (ValueError, IndexError):
                continue
    g.hard("cognitive<15", worst < LIMITS["cognitive"],
           f"max cognitive={worst}" if worst else "under limit")


def check_halstead(g: Gate, root: Path) -> None:
    worst = 0.0
    dirs = _source_dirs(root)
    if dirs:
        _, out = _run([_tool("radon"), "hal", *dirs, "-f"], str(root))
        for line in out.splitlines():
            if "difficulty:" in line:
                try:
                    worst = max(worst, float(line.split("difficulty:")[1].strip()))
                except ValueError:
                    pass
    g.hard("halstead<80", worst < LIMITS["halstead"], f"max difficulty={worst:.1f}")


def check_dead_code(g: Gate, root: Path) -> None:
    dirs = _source_dirs(root)
    if not dirs:
        g.hard("dead-code=0", True, "no python source"); return
    _, out = _run([_tool("vulture"), *dirs, "--min-confidence", "80"], str(root))
    findings = [l for l in out.splitlines() if l.strip()]
    g.hard("dead-code=0", not findings,
           f"{len(findings)} finding(s)" + (f": {findings[0]}" if findings else ""))


def check_duplication(g: Gate, root: Path) -> None:
    """No redundant/duplicated code. pylint's duplicate-code (R0801) is AST-based (semantic, not text
    token matching), so it catches real copied logic, not shared idioms. min-similarity-lines=8 is the
    floor below which a 'duplicate' is almost always a legitimate idiom (tool-body signature, a health
    probe's try/except). This is the careful version of 'redundant code = 0'."""
    dirs = [d for d in _source_dirs(root) if list((root / d).rglob("*.py"))]
    if not dirs:
        g.hard("duplication=0", True, "no python source"); return
    _, out = _run([_tool("pylint"), *dirs, "--disable=all", "--enable=duplicate-code",
                   "--min-similarity-lines=8"], str(root))
    dups = [l for l in out.splitlines() if "duplicate-code" in l or "R0801" in l]
    # dedupe the file-line pairs pylint emits per file
    files = sorted({l.split(":")[0] for l in out.splitlines() if "R0801" in l})
    g.hard("duplication=0", not files, f"duplicated blocks in: {', '.join(files)}" if files else "none")


def check_types_ratchet(g: Gate, root: Path) -> None:
    """No NEW untyped defs: the mypy strict error count may not exceed the recorded baseline.
    A ratchet, not a hard zero -- legacy surface annotates over time without blocking new work."""
    dirs = [d for d in _source_dirs(root) if list((root / d).rglob("*.py"))]
    if not dirs:
        g.hard("types: no-new-untyped", True, "no python source"); return
    rc, out = _run([_tool("mypy"), "--strict", "--disallow-untyped-defs", "--disallow-untyped-calls", *dirs],
                   str(root))
    errors = sum(1 for l in out.splitlines() if ": error:" in l)
    baseline = _load_baseline(root).get("mypy_errors")
    if baseline is None:
        _save_baseline(root, {"mypy_errors": errors})
        g.hard("types: no-new-untyped", True, f"baseline set at {errors} mypy error(s); ratchet from here")
        return
    g.hard("types: no-new-untyped", errors <= baseline,
           f"{errors} mypy error(s) vs baseline {baseline} (must not grow)")


def check_coverage_floor(g: Gate, root: Path) -> None:
    """Behavioral floor: run the target repo's own unit suite under coverage; report + fail only if
    it drops below the floor. Not 100% -- the suite must discriminate behavior, not execute lines.
    Generic: finds the target's test dir (tests/ or test/) and measures its source roots."""
    tests_dir = next((t for t in ("tests", "test") if (root / t).is_dir()), None)
    if not tests_dir:
        g.report("coverage-floor", "no tests dir in target; skipped"); return
    source = ",".join(_source_dirs(root))
    if not source:
        g.report("coverage-floor", "no python source in target; skipped"); return
    # The floor is the DETERMINISTIC suite only: live-service tests (test_live_*) and bench parsers
    # depend on external services / optional deps and belong to `make test`, not the offline floor --
    # a down service must not tank it. Derive module names from the files' paths.
    live_prefixes = ("test_live_", "test_bench")
    test_files = [p for p in (root / tests_dir).rglob("test_*.py")
                  if not p.stem.startswith(live_prefixes)]
    if not test_files:
        g.report("coverage-floor", "no deterministic tests found; skipped"); return
    mods = [".".join(p.relative_to(root).with_suffix("").parts) for p in test_files]
    _run([_tool("coverage"), "run", f"--source={source}", "-m", "unittest", *mods], str(root))
    _, out = _run([_tool("coverage"), "report"], str(root))
    total = 0.0
    for line in out.splitlines():
        if line.strip().startswith("TOTAL"):
            try:
                total = float(line.split()[-1].rstrip("%"))
            except (ValueError, IndexError):
                pass
    ok = total >= LIMITS["coverage_floor"]
    if ok:
        g.report("coverage-floor", f"{total:.0f}% (>= {LIMITS['coverage_floor']:.0f}%)")
    else:
        g.hard("coverage-floor", False, f"{total:.0f}% < {LIMITS['coverage_floor']:.0f}% floor")


def report_loc_and_dup(g: Gate, root: Path) -> None:
    """Signals only -- not gates. LOC/file is a weak proxy CC already covers; duplication is noisy."""
    over = [f"{os.path.relpath(p, root)} ({sum(1 for _ in open(p, errors='ignore'))})"
            for p in _py_files(root) + _js_files(root)
            if sum(1 for _ in open(p, errors="ignore")) >= 500]
    g.report("loc/file>=500 (info)", "; ".join(over) or "none")


def _baseline_path(root: Path) -> Path:
    # the ratchet baseline belongs to the TARGET project, not the harness
    return root / ".quality-baseline.json"


def _load_baseline(root: Path) -> dict:
    try:
        return json.loads(_baseline_path(root).read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _save_baseline(root: Path, d: dict) -> None:
    try:
        _baseline_path(root).write_text(json.dumps(d, indent=2))
    except OSError:
        pass


def run_gate(root: str | Path = ".") -> dict:
    """The programmatic entry: run all checks against the target root and return the result.

    Returns {"ok": bool, "failures": [...], "report": [...human lines]}. This is what the
    `quality_gate` tool and the canonicalize path call; main() is the CLI wrapper over it.
    """
    root = Path(root).resolve()
    g = Gate()
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        print(f"quality gate on {root}")
        print(" hard gates (block):")
        for fn in (check_cyclomatic, check_cognitive, check_halstead, check_dead_code,
                   check_duplication, check_types_ratchet):
            fn(g, root)
        print(" behavioral floor:")
        check_coverage_floor(g, root)
        print(" signals (info only):")
        report_loc_and_dup(g, root)
        if g.failures:
            print(f"GATE FAILED: {len(g.failures)} hard gate(s): {', '.join(g.failures)}")
        else:
            print("GATE PASSED")
    return {"ok": not g.failures, "failures": g.failures, "report": buf.getvalue()}


def main() -> int:
    ap = argparse.ArgumentParser(prog="quality-gate")
    ap.add_argument("--root", default=".", help="repo root to gate (default: cwd)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    result = run_gate(args.root)
    print(result["report"])
    if args.json:
        print(json.dumps({"ok": result["ok"], "failed": result["failures"]}))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
