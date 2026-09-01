---
name: systematic-debugging
description: >
  Root-cause-first debugging. Understand the bug and build a tight feedback loop before
    any fix. Use for test failures, bugs, unexpected behavior, perf problems, build/integration
    failures -- especially under time pressure or after a failed fix. Triggers on: debug, why is this
    broken, this fails, investigate, root cause, reproduce, it worked before, regression, flaky test.
version: 1.0.0
metadata:
  fools:
    tags: [debugging, root-cause, investigation, testing]
    related_skills: [self-benchmark, delegation-triage]
---

# Systematic debugging

Core principle: no fixes without root-cause investigation first. A symptom fix is a failure -- it
masks the real issue and creates new ones.

## The loop is the work

Before theorizing, build a tight feedback loop: a fast, deterministic, agent-runnable command that
goes RED on the exact symptom and GREEN when the bug is fixed. Not "doesn't crash" -- asserts the
specific failure. If you can't reproduce it, gather more data; do not guess. Spend disproportionate
effort on the loop; a 50% flake is debuggable, a 1% flake is not (raise the rate: repeat, stress,
parallelize, narrow timing).

Ways to build the loop, cheapest first: a failing unit/integration test at the seam; a curl/CLI
repro against a running service; a throwaway harness that boots the smallest slice; a bisect
between two known states; a differential loop (old vs new, two configs). Tighten it: faster,
sharper signal, more deterministic.

## The four phases, in order

1. **Root cause.** Read the error completely (stack, line numbers, codes). Find the failing path in
   the code. Build the tight loop. Check recent changes. Do NOT propose a fix yet.
2. **Form a hypothesis** grounded in the evidence, then test it against the loop. If it doesn't
   reproduce the exact symptom, the hypothesis is wrong -- discard it, don't force it.
3. **Fix** the root cause, minimal and correct. Confirm the loop goes green.
4. **Verify no regression**: run the broader suite. A fix that passes its loop but breaks siblings
   is not done.

On this system: `make test` is the canonical suite; `make bench-e2e` proves a harness-level change;
`trace` reconstructs a failed worker's trajectory; `make observe` shows a run's rollup and
trip-wires. For a worker that failed, trace it before theorizing -- read what it actually did.
