# Context priming: the intuition layer

The two mechanisms that give agents "background awareness" without paying reasoning prices for
retrieval, designed against the 2025-2026 context-engineering evidence (Anthropic's harness work,
Cuconasu "Power of Noise", Chroma "Context Rot", Liu "Lost in the Middle", Adaptive-RAG).

The principle (Hale's intuition, made structural): the fast associative layer should surface the
right context *before* the expensive reasoning fires. Don't make the model retrieve; prefill the
association.

## The two mechanisms

**1. Worker state prefill (deterministic, not retrieved).** A worker starts a bounded unit with the
thread's state already present: the task's contract (objective + the exact signal that proves done +
boundaries), the recent decisions/findings, and the return contract. This is fetched by role/ID from
the Event Log, never similarity-searched -- retrieval invites stale-state clashes (a retrieved "state"
contradicting the live contract). Evidence: Anthropic's long-running-agents harness externalizes state
to artifacts and prefills by ID; Cognition's "actions carry implicit decisions" (a worker must not
re-decide what's decided).

**2. Library associative prior (gated + score-floor).** For a task with real corpus overlap, inject a
small reranked dose of library material adjacent to the task, labeled as reference. The trigger is the
load-bearing choice: skip on trivial tasks and on no-overlap tasks (a failed keyword probe means pure
distractor risk -- Cuconasu). Only inject above a relevance floor, capped small (~3-8 chunks, ~1-2k
tokens). Evidence: "Power of Noise" -- semantically-related-but-wrong distractors degrade accuracy
monotonically, worse near the query. So: precision over recall; a prior's job is to be *right*, not
*complete*.

## What these are NOT

- Not a history dump (context rot; the "telephone" loss).
- Not a substitute for the on-demand tools -- the prefill ends with pointers (memory_search,
  library_search) so the agent pulls more when the prefill isn't enough.
- Not unconditional. Both are gated: the worker prefill only when the thread has state; the library
  prior only when a probe clears the floor.

## The failure modes the design avoids

- Injecting distractors (the worst case; worse than nothing).
- Context clash (retrieved stale state vs. live contract) -- the prefill is fenced, labeled, and
  carries a precedence note: contract > thread state > prior findings > library.
- Unbounded growth -- hard token caps on both.
