---
name: self-benchmark
description: Verify that the fools-trick system itself is working -- that the orchestrator actually delegated, that the servers are healthy, and that a change did not regress speed or quality -- using this system's own grounded signals (make test, make bench, make bench-e2e, the opencode delegation DB) rather than self-assessment. Use ONLY to check THIS distributed harness after a change to its config, prompts, serving, or worker setup; not for benchmarking arbitrary code. Triggers on: verify delegation, is the system working, benchmark this, did that regress, prove it delegated, check the harness, run the eval, self-test, is it healthy after that change.
---

# self-benchmark

How to prove the fools-trick harness works, from ground truth, not belief. The system already
ships the signals -- this skill says which to run for which question. Grounded signals beat
self-judgment: a run that "looks fine" but shows 0 subagents on a fan-out task did not delegate.

## Pick the signal by the question

| Question | Command | What proves it |
|---|---|---|
| Is the config/wiring sound? | `make test-unit` | 23 unit tests (bench parsers + lib), no servers needed |
| Is everything wired + live? | `make test` | unit + config + a live worker round-trip |
| Are both servers healthy? | `make health` | real completions on fool + magus + opencode round-trip |
| Did the orchestrator delegate? | `make bench-e2e` | runs real tasks, PROVES subagents via the opencode DB |
| Did speed regress? | `make bench-speed` | TTFT / prefill / decode / concurrency / cache, both nodes |
| Did reasoning regress? | `make bench-eval` | real gsm8k + ruler + deep multi-hop accuracy |

## Delegation is proven in the DB, not the transcript

`make bench-e2e` is the real eval: it does not trust the model's story about what it did. It runs
`opencode run --format json`, parses the task tool-calls, and cross-checks the opencode SQLite DB
for child sessions. A task passes only if BOTH correctness (answer matches) AND delegation
expectation (enough subagents, on the right provider) hold. The columns to read:

- `subagents` -- how many workers actually spawned (0 is correct for a task that needs no fan-out).
- `on magus` -- proof the workers ran on the worker endpoint, from the child session's providerID.
- a fan-out task with `pass=yes, subagents>=2, on magus=yes` is delegation working end to end.

To check delegation directly without a full bench run:

```
opencode db --format json "SELECT agent, json_extract(model,'\$.providerID') AS prov, \
  tokens_output FROM session WHERE parent_id IS NOT NULL ORDER BY time_created DESC LIMIT 5"
```

## Reference numbers (a healthy system, measured)

Use these to spot a regression, not as hard thresholds:
- worker decode: a single un-contended stream approaches ~32-38 tok/s, but under real 4-way
  concurrency each slot drops to ~18-21 tok/s. Size timeouts against the CONTENDED rate: a worker
  producing a large answer (say 8-11k output tokens) needs ~7-10 minutes, which is why the magus
  provider timeout is `false` (unbounded) and runaway workers are bounded by `steps`, not wall time.
- orchestrator prefill-bound: TTFT ~1.6s at 800 tok climbing to ~90s at 100k; cache ~97%; 0 preempt.
- quality: gsm8k ~95% both tiers; ruler 95-100%; deep multi-hop 100% to 262k tokens.
- e2e: fan-out task completes correctly with 2 workers on magus in a few hundred seconds; a
  substantive artifact-writing worker legitimately takes many minutes (slow is expected; the
  workers are thorough, not fast, and the orchestrator is a deep single stream, not a fast API).

## Discipline

Results (JSONL + markdown + a run log) land under `/tmp/fools-trick/bench/` timestamped, so runs
are comparable over time. When you change config, prompts, serving, or the worker, run the relevant
signal BEFORE claiming the change is good. If the signal is not green, the change is not done.
