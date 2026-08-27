---
name: fool-triage
description: Diagnose the fools-trick two-node system when delegation or a worker seems off -- a dispatched worker never returns, delegation appears stuck, a task hangs, or an endpoint stops responding. Uses this system's actual signals (make status/health, journald, the opencode delegation DB, the serving endpoints). Use ONLY for operating THIS distributed system (DeepSeek orchestrator on fool + Qwen workers on magus); not for generic code debugging. Triggers on: worker not responding, delegation stuck, task hung, subagent never returned, fool unreachable, is the worker up, orchestrator slow, is it broken, why did that hang.
---

# fool-triage

How to diagnose the fools-trick distributed system when something is wrong. This system is
two nodes: the DeepSeek orchestrator on fool (`http://fool:8888`, slow deep stream) and Qwen
workers on magus (`http://127.0.0.1:8898`, a systemd unit `fools-worker`, 3 concurrent slots).
Most "it's broken" reports are actually "it's slow" -- rule that out first.

## First: slow vs. broken

The orchestrator decodes at ~24-28 tok/s and its prefill grows with context (TTFT climbs to
~90s at 100k tokens). A fan-out task legitimately takes minutes -- one measured repo-inspection
run dispatched 2 workers and completed correctly in 425s. Before treating a hang as a failure:

- Is fool's GPU busy or idle right now? Busy = still working. Idle = waiting on workers or done.
  `ssh fool 'nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader'`
- Did it actually dispatch? Check the delegation DB (below). If child sessions exist and are
  updating, the workers are running and the orchestrator is waiting -- that is normal, not stuck.

## The signal ladder (cheapest first)

1. `make status` -- both servers up? served model ids match config? spark clone synced?
2. `make health` -- real completions on both endpoints + an opencode round-trip.
3. Endpoints directly:
   - worker: `curl -sS -m4 http://127.0.0.1:8898/v1/models`
   - fool:   `curl -sS -m5 http://fool:8888/v1/models`
4. Worker lifecycle + log (worker runs as a systemd --user unit, journald owns its log):
   - `systemctl --user is-active fools-worker`
   - `journalctl --user -u fools-worker -n 50 --no-pager`   (or `-f` to follow)
   - restart: `make worker-down && make worker-up`
5. Orchestrator log (docker on fool):
   - `make fool-logs`   (tails `docker compose logs` in the spark clone)

## Prove (or disprove) delegation from the opencode DB

Delegation is authoritative in the opencode SQLite DB, not in the transcript. Each subagent is a
child session with `parent_id` set. To see what the orchestrator actually spawned recently:

```
opencode db --format json "SELECT agent, json_extract(model,'\$.providerID') AS prov, \
  tokens_output, datetime(time_created/1000,'unixepoch') t \
  FROM session WHERE parent_id IS NOT NULL ORDER BY time_created DESC LIMIT 5"
```

Read it:
- rows with `prov = magus` and recent `t` -> the orchestrator IS delegating to the workers. Good.
- no recent child rows during a task that should fan out -> it answered solo (fine for simple
  tasks) or the brief was too vague to trigger a fan-out (see fool-triage of the plan, not the box).
- `tokens_output` growing across a query -> workers are actively producing. Still running.

## Common causes, in order of likelihood

- **Just slow.** The orchestrator's prefill + reasoning + worker round-trips are serial across a
  slow stream. Not a bug. Confirm via the DB (children exist) and fool GPU (busy or waiting).
- **Provider timeout cancelling a slow worker.** The telltale: the delegation DB shows a worker
  produced thousands of output tokens, yet the task reports "operation timed out" and its scratch
  artifact was never written -- and the worker journald shows `srv stop: cancel task`. That is
  opencode cancelling the HTTP request mid-generation. The magus provider `timeout` must be `false`
  (unbounded); under 3-way contention a worker decodes at ~18-21 tok/s, so any answer over ~5-6k
  output tokens exceeds a 300s cap. Runaway workers are bounded by `steps`, not wall time.
- **Worker OOM / degraded load.** If the worker won't hold context or 400s on large prompts,
  check its load log for `cudaMalloc failed` / `retrying without pipeline parallelism`. The fix is
  fewer/smaller slots (`WORKER_CTX_PER_SLOT`, `WORKER_PARALLEL`) -- Q4_K_S fits 3x45056, not 4x45056.
- **Depth over the worker's per-slot limit.** A prompt above `WORKER_CTX_PER_SLOT` (45056) 400s.
- **fool not synced.** `make status` flags DRIFT if fool's spark clone diverges from the pin;
  `make fool-sync`.
- **Effort too high.** If every orchestrator turn is glacial, confirm `FOOL_EFFORT=high` (not
  `max`) is what fool booted with; changing it needs a fool restart.

## What NOT to conclude

A 0-subagent result on a task that did not need parallel work is correct behavior, not a failure.
The system deciding to answer solo is a feature. Only treat missing delegation as a fault when the
task genuinely required fan-out and the brief clearly asked for it.
