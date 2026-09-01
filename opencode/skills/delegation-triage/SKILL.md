---
name: delegation-triage
description: >
  Diagnose the fools-trick system when delegation or a worker seems off -- a dispatched worker never
    returns, delegation appears stuck, a task hangs, or an endpoint stops responding. Uses this
    system's actual signals (make status/health, the opencode delegation DB, the serving endpoints).
    Use ONLY for operating THIS distributed system; not for generic code debugging. Triggers on:
    worker not responding, delegation stuck, task hung, subagent never returned, endpoint
    unreachable, is the worker up, orchestrator slow, is it broken, why did that hang.
version: 1.0.0
metadata:
  fools:
    tags: [operations, delegation, diagnosis, health]
    related_skills: [self-benchmark, systematic-debugging]
---

# delegation-triage

Meta-skill: for operating fools-trick ITSELF, not for a repo you are working in. Use it when this
harness's own delegation or endpoints seem off; ignore it when the task is coding in some other
project.

How to diagnose the fools-trick system when something seems wrong. Most "it's broken" reports are
actually "it's slow" -- a deep orchestrator driving concurrent workers legitimately takes minutes on
a real fan-out. Rule out slow before treating a hang as a failure.

## First: slow vs. broken

Before calling a hang a failure, confirm work is actually happening:

- Did it dispatch? Check the delegation DB (below). If child sessions exist and their `tokens_output`
  is climbing, the workers are running and the orchestrator is waiting -- that is normal, not stuck.
- A deep-reasoning turn with a large context is slow by nature. A fan-out task taking minutes is
  expected, not a bug.

## The signal ladder (cheapest first)

1. `make status` -- endpoints up? served model ids match config?
2. `make health` -- real completions on both tiers + an opencode round-trip.
3. Endpoints directly: `curl -sS -m5 <endpoint>/v1/models` for the orchestrator and worker base URLs
   (from `python3 -m core.config --json`). A 200 with the expected model id means the tier is live.

## Prove (or disprove) delegation from the opencode DB

Delegation is authoritative in the opencode SQLite DB, not the transcript. Each subagent is a child
session with `parent_id` set:

```
opencode db --format json "SELECT agent, json_extract(model,'\$.providerID') AS prov, \
  tokens_output, datetime(time_created/1000,'unixepoch') t \
  FROM session WHERE parent_id IS NOT NULL ORDER BY time_created DESC LIMIT 5"
```

Read it:
- recent child rows on the worker provider -> the orchestrator IS delegating. Good.
- no recent child rows during a task that should fan out -> it answered solo (fine for simple tasks)
  or the brief was too vague to trigger a fan-out (triage the plan, not the system).
- `tokens_output` growing across a query -> workers are actively producing. Still running.

## Common causes, in order of likelihood

- **Just slow.** Deep reasoning + worker round-trips across a single orchestrator stream. Not a bug;
  confirm via the DB (children exist and producing).
- **Endpoint unreachable.** An orchestrator or worker base URL not answering `/v1/models` -- check
  the provider/network. For a cloud rig, a bad or missing API key surfaces here.
- **Provider timeout cancelling a long worker.** If the DB shows a worker produced many output
  tokens yet the task reports a timeout, the request was cut mid-generation. The provider `timeout`
  must be `false` (unbounded); runaway workers are bounded by their `steps` cap, not wall time.
- **Brief too big for the worker slot.** A dispatch whose required reading exceeds the worker input
  window forces eviction or a promote. If workers keep escalating, the decomposition is too coarse.

## What NOT to conclude

A 0-subagent result on a task that did not need parallel work is correct behavior, not a failure.
The system deciding to answer solo is a feature. Only treat missing delegation as a fault when the
task genuinely required fan-out and the brief clearly asked for it.
