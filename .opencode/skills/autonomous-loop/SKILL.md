---
name: autonomous-loop
description: >
  Keep working the current task until it's done and verified, without stopping for confirmation.
  The user runs `make loop` to start a self-continuation runner that re-prompts the session on an
  interval; this skill is the discipline the agent follows while the loop is on. Triggers on: keep
  going, don't stop, autonomous, work until done, keep at it, run the loop, self-continue, don't
  ask just do it.
version: 1.0.0
metadata:
  fools:
    tags: [autonomy, long-horizon, continuation]
    related_skills: [systematic-debugging, self-benchmark]
---

# Autonomous loop

The user has started a self-continuation loop (`make loop`): the session will be re-prompted on an
interval until the task is done and verified, or the user stops it. While the loop is on, you do
NOT stop for confirmation. You keep going.

## The discipline

- **Hold the live goal.** Restate it compactly at the start of each continuation so a long loop
  stays oriented. The goal is the current task, not a new one.
- **Keep working until done AND verified.** "Done" means the success signal is green (the test
  passes, the task is complete), not that you ran out of turns. If you're mid-task, continue it.
- **Never idle.** If a continuation prompt arrives and there's work left, do the next step. If the
  task is genuinely complete and verified, say so and stop -- a real stop condition, not a stall.
- **Recover, don't repeat.** If you're stuck (a worker failed, a tool errored, context slid), use
  the recovery paths: `trace` the failed worker, `recall(seq)` an evicted result, `promote` if the
  task outgrew a worker. Do not re-run the same failing call.
- **Ground each step.** Verify against a real signal (the repo's tests, make test, the bench) before
  moving on. A loop that runs unverified is a runaway.

The loop ends when the user stops it (`make loop-stop`) or the budget is spent. Your job is to make
each continuation count: real progress, verified, no repetition.
