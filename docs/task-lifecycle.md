# Task lifecycle: the coherent development loop

The harness's development loop, designed against the 6-layer playbook (guides, sensors, agentic
loop, memory, permissions, observability) and its multi-agent extensions (typed handoffs, shared
state, independent verifier). This is the target design; it builds on the existing canonical
foundations (Event Log, gates plugin, observe rollups, permission model) rather than replacing them.

## The loop

```
DEFINE ──> PLAN ──> DISPATCH ──> VERIFY-UNIT ──> REVIEW ──> RECONCILE ──> CANONICALIZE
   │                    │             │             │                         │
success-            typed          computational  independent             gated on
contract            handoff        sensor         verifier                green + review
```

The orchestrator owns the loop. Workers execute bounded units and return typed handoffs. Three
structural additions turn the existing prompted loop into an enforced one:

1. the success-contract (DEFINE) -- a first-class definition-of-done, recorded at task start.
2. typed handoffs -- workers return a structured packet, not a free-form report.
3. hardened gates -- verify and review move from prompt-nudge to enforced gate at canonicalize.

## 1. The success-contract (DEFINE)

The paper's load-bearing warning: a harness amplifies its objective; a bad or absent objective makes
every downstream sensor validate garbage. So the objective becomes a tracked object, not an implicit
intent.

At the start of a non-trivial task the orchestrator records a contract: the exact signal that will
be green when the task is done -- a command, a test, an observable check -- plus the goal in one
line and the boundaries. It is persisted to the Event Log as a durable episode (`role="contract"`),
addressable by seq, so it survives context eviction and a session restart.

The contract is checked at CANONICALIZE: work is not done until its named signal is actually green.
This is the difference between "the model believes it is done" and "the defined check passes."

The orchestrator writes it at task start with the `record_contract` tool, before dispatching any
work: GOAL/SIGNAL/BOUNDARIES persisted to the Event Log as `role="contract"`, returning the seq.

Contract shape (persisted content):
```
GOAL:     one line -- what this task achieves.
SIGNAL:   the exact command/check that proves done (e.g. `make test`, `pytest tests/x.py`, exit 0).
BOUNDARIES: what is out of scope / must not change.
```

## 2. Typed handoffs

The paper: "done, looks good must not advance a production workflow." A worker's return is a typed
packet the orchestrator can act on and verify, not prose it must interpret. This generalizes the
existing `promote` escalation packet (status + reason + findings + seq) to every worker return.

Handoff shape:
```
STATUS:     done | done-partial | blocked | needs-deeper-context
ARTIFACT:   pointer(s) -- files touched (path:line), scratch artifact path, or the diff.
EVIDENCE:   what was verified and how (command run + result), or "unverified" honestly.
ASSUMPTIONS: what the worker assumed that the orchestrator must confirm.
UNRESOLVED: conflicts, follow-ups, hazards the orchestrator must reconcile.
```

A worker that returns STATUS=done with EVIDENCE=unverified is telling the orchestrator exactly what
still needs a sensor run -- no fluent summary hides partial work.

## 3. Hardened gates

Today the verify-gate and reviewer are prompt-level nudges (the model can ignore them). The control-
reliability ladder says the strong fix is structural. These move down the ladder toward enforcement:

- VERIFY-UNIT: after code is edited, the unit is not accepted until a computational sensor
  (the contract's SIGNAL, or the repo's own test/lint/build) has run green since the edit. The
  verify-state machine already tracks dirty-since-verified per session; the gate makes an
  unverified-done a hard stop, not a suggestion.
- REVIEW: for a non-trivial diff, the orchestrator must dispatch the independent reviewer
  (producer != verifier) before accepting. The reviewer reports failures; it does not rewrite.
- CANONICALIZE: the commit is the canonicalization point. The gate is hybrid by verification state:
  a commit with the contract SIGNAL NEVER run since the last code edit is HARD-BLOCKED (the worst
  case -- canonicalizing on pure belief); a commit where the SIGNAL ran but may be stale gets a
  NUDGE, not a block. This catches the real failure mode without re-running the suite on every
  commit. Publishing (push/PR/release) stays human-gated as it is today.

## What this reuses (not rebuilt)

- Event Log (SQLite+FTS5): stores the contract and handoffs as typed episodes, recoverable by seq.
- Gates plugin + policy: already owns the verify-state machine and the human-gate; the hardened
  gates extend it.
- Observe rollups + tripcheck: the observability layer that measures the loop -- cost/delegation
  per task, trip-wires on regression. The scorecard metric is verified tasks per manual
  intervention, not tokens.
- Permission model: unchanged; least-privilege per agent, human-gate on irreversible actions.
