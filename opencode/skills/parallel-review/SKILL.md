---
name: parallel-review
description: >
  Review recent changes with focused reviewers running in parallel, partitioned by
    concern, then synthesize. Four narrow reviewers beat one broad reviewer; each searches the diff
    for one class of problem without diluting attention, and they run concurrently so you pay one
    review's latency. Use for: review my changes, clean this up, simplify, a pre-commit pass.
    Triggers on: review my changes, simplify, clean up, parallel review, pre-commit review.
version: 1.0.0
metadata:
  fools:
    tags: [code-review, cleanup, refactor, delegation, parallel]
    related_skills: [systematic-debugging, self-benchmark]
---

# Parallel review

Review a change with several focused workers in parallel, each hunting ONE class of problem, then
synthesize. This is a cleanup/quality pass on code that already works -- not a bug hunt (that's
systematic-debugging) and not a correctness gate (that's the reviewer + tests).

## The pattern

Fan out reviewers on the same diff, partitioned by concern so they don't collide:

- **reuse** -- duplication, a helper that already exists, a pattern the codebase has.
- **quality** -- naming, dead code, needless nesting, a comment narrating the obvious.
- **efficiency** -- a wasteful loop, a redundant recomputation, an unbounded structure.
- **altitude** -- is this at the right level of abstraction, or a workaround where a cleaner shape exists.

Dispatch them in one turn (independent units, parallel). Each returns findings as a path:line list
with a one-line fix. Synthesize: where reviewers agree, it's real; where they conflict, decide.
Apply only the fixes worth applying; report the rest.

## When NOT to use

Not after every edit, and not for correctness bugs. It costs a fan-out of workers; invoke it on a
real change the user asks to review or clean, not reactively.

On this system: dispatch `reviewer`/`explore` workers in parallel via the Task tool, one per
concern, each scoped to the same diff. The orchestrator synthesizes. A change must still pass
`make test` after.
