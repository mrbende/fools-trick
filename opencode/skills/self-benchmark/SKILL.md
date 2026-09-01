---
name: self-benchmark
description: >
  Verify that the fools-trick system itself is working -- that the orchestrator actually delegated,
    that endpoints are healthy, and that a change did not regress -- using this system's own grounded
    signals (make test, make bench, make bench-e2e, the opencode delegation DB) rather than
    self-assessment. Use ONLY to check THIS harness after a change to its config, prompts, or wiring;
    not for benchmarking arbitrary code. Triggers on: verify delegation, is the system working,
    benchmark this, did that regress, prove it delegated, check the harness, self-test.
version: 1.0.0
metadata:
  fools:
    tags: [verification, delegation, benchmark, grounding]
    related_skills: [delegation-triage, systematic-debugging]
---

# self-benchmark

Meta-skill: for operating fools-trick ITSELF, not for a repo you are working in. Use it after
changing this harness's config, prompts, or wiring; ignore it when the task is coding in some
other project.

How to prove the fools-trick harness works, from ground truth, not belief. The system ships the
signals; this says which to run for which question. A run that "looks fine" but shows 0 subagents
on a fan-out task did not delegate.

## Pick the signal by the question

| Question | Command | What proves it |
|---|---|---|
| Is the config/wiring sound? | `make test-unit` | offline unit suite, no servers needed |
| Is everything wired + live? | `make test` | unit + config + a live worker round-trip |
| Are the endpoints healthy? | `make health` | real completions on both tiers + an opencode round-trip |
| Did the orchestrator delegate? | `make bench-e2e` | runs real tasks, PROVES subagents via the opencode DB |
| Does delegation hold at long ctx? | `make bench-longctx` | needle-at-depth + delegation fused, DB-verified |
| Does subagent prune keep workers competent? | `make bench-prune` | worker reads past its input budget, must still answer from an evicted early file |
| Does memory beat compaction? | `make bench-memory` | sliding-window recall A/B on a long session (LLM-judged) |
| Did speed regress? | `make bench-speed` | TTFT / prefill / decode / concurrency / cache |
| Did reasoning regress? | `make bench-capability` | reasoning + instruction-following accuracy |

## Delegation is proven in the DB, not the transcript

`make bench-e2e` does not trust the model's story about what it did. It runs `opencode run
--format json`, parses the task tool-calls, and cross-checks the opencode SQLite DB for child
sessions. A task passes only if BOTH correctness AND delegation expectation hold:

- `subagents` -- how many workers actually spawned (0 is correct for a task that needs no fan-out).
- the child session's `providerID` -- proof the work ran on the worker tier, not solo.

To check delegation directly without a full bench run:

```
opencode db --format json "SELECT agent, json_extract(model,'\$.providerID') AS prov, \
  tokens_output FROM session WHERE parent_id IS NOT NULL ORDER BY time_created DESC LIMIT 5"
```

## Discipline

Results land timestamped under `/tmp/fools-trick/bench/` so runs are comparable over time. When you
change config, prompts, or wiring, run the relevant signal BEFORE claiming the change is good. If
the signal is not green, the change is not done.
