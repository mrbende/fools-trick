# Persistent memory + sliding context: architecture

Status: SUPERSEDED (in part) by docs/harness-design.md, which is the current ground-up design.
This doc records the original memory design and its research basis (the Zep/compaction argument,
the two-subsystem split); that reasoning still holds and is worth reading. But the AS-BUILT system
differs: the logic is now a harness-agnostic PYTHON core (core/log, core/context, core/tools), not
JS plugins under .opencode/memory; the subagent tier does RECOVERABLE eviction (evict from the
view, keep the durable copy addressable by seq, recover via expand()) rather than the lossy
in-place prune described below; and config lives in config.yaml, not config.sh. Where this doc and
harness-design.md disagree on the subagent context path, harness-design.md wins.

## The problem, precisely

opencode's default behavior when a session fills its context window is
**compaction**: summarize the conversation so far and drop the raw messages.
This is lossy and jarring -- the agent visibly gets dumber mid-session, because
a summary is a worse representation than the tokens it replaced. The Zep paper
(arXiv:2501.13956) measures exactly this: recursive summarization scores 35.3%
on Deep Memory Retrieval, the worst of every method tested. Full-context is
accurate but slow and expensive (115k tokens, ~30s latency); structured
retrieval matches or beats full-context accuracy at ~1.6k tokens and ~2.5s.

Our orchestrator (DeepSeek-V4-Flash on the Spark) is bandwidth-bound and
single-stream, capped at 384k context. Two consequences drive the design:

1. Input and output compete for the same window. If the working set eats all
   384k, decode has no room to do real work. We must cap the live INPUT window
   well below the ceiling and defend a decode-headroom budget.
2. A long coding session runs for millions of tokens. We cannot hold them in
   context and we will not summarize-and-drop them. Instead we hold a moving
   working set and persist everything else losslessly for recall.

## The shape: two jobs, three tiers

The memory layer has two distinct jobs. Keeping them separate is what keeps the
design clean.

### Job 1 -- the sliding window (the orchestrator's working set)

Hold a live window of ~100-200k input tokens and SLIDE it as the session grows,
instead of compacting. Everything that slides out is persisted verbatim (Job 2),
never summarized. Always reserve a decode-headroom budget so output tokens have
room to work.

This is a plugin concern, not a datastore concern:
- `compaction.auto: false` in opencode.json disables opencode's summarize-drop.
- `experimental.chat.messages.transform` runs every turn; there we prune the
  message array to the window budget (keep system + recent turns + any recalled
  memory), evicting the oldest raw turns once we cross the input cap. Evicted
  turns are handed to Job 2 for durable persistence before they leave the array.
- The cap is `WINDOW_INPUT_TOKENS` (default 160k), chosen so that
  `WINDOW_INPUT_TOKENS + DECODE_HEADROOM <= orchestrator context (384k)` with
  wide margin. Prompt caching keeps the stable prefix cheap across turns.

### Job 2 -- recall (getting the right past back into the window)

When the model needs something that has slid out, it retrieves it. Two tiers:

**Tier A -- durable episode store (SQLite, source of truth).** Every message/turn
is an EPISODE (Zep's term), stored raw and non-lossy, session-scoped, with a
timestamp. SQLite FTS5 gives full-text (BM25) recall over episodes -- one of the
three search methods Zep itself uses, and enough on its own for a single-user
coding partner. A `memory_search` tool runs the query and returns the top-k
matching episodes as a compact context string (few tokens, like Zep's ~1.6k).
Rebuildable, greppable via SQL, survives restart. This is the durable truth.

**Tier B -- hot shared working memory (Redis).** The 3 concurrent workers plus
the orchestrator share a fast tier for "what is happening right now":
- Per-session recent-episode cache (fast reads for the whole swarm, TTL'd so it
  self-prunes; structured so recall spends few tokens, not markdown prose).
- A write STREAM (Redis Streams) that serializes memory writes from all agents.
  Concurrent agents PUSH episodes onto the stream; a single consumer DRAINS the
  stream into SQLite. This is how we get many-writer safety without file-lock
  contention -- the thing markdown files structurally cannot do under a swarm.
- Redis is rebuildable/rewarmable from SQLite; SQLite is the source of truth.
  Redis persistence (AOF) is a convenience, not the durability guarantee.

Both orchestrator and subagents can READ memory (`memory_search`) and WRITE
memory (`memory_write` -> Redis stream -> SQLite). Tools carry `agent` and
`sessionID` (opencode ToolContext), so every episode is attributed to who wrote
it in which session.

### Tier 3 -- knowledge graph (DEFERRED, design settled)

If FTS recall proves insufficient -- the model asks for things BM25 can't find
because they need semantic or relational search -- add a graph layer over the
episodes, exactly as Zep/Graphiti does: extract entities and facts, embed for
cosine recall, track temporal edge validity/invalidation, rerank. This is a
heavy pipeline (an LLM call per ingested message for extraction + resolution +
temporal reasoning, plus embeddings and a graph store). It is the RIGHT design
when simple recall fails, and the WRONG default for a single-user coding partner:
it would spend our scarcest resource -- LLM calls -- on memory bookkeeping, on a
single-stream orchestrator that is already the throughput bottleneck. Build it
only when Tier A's FTS recall measurably misses. Reference: Zep
(arXiv:2501.13956), Graphiti (github.com/getzep/graphiti).

## Why this layering (the growth rule)

Start with the smallest thing that works end to end: sliding window (Job 1) +
SQLite episodes with FTS5 + memory_search/memory_write (Job 2, Tier A). Add
Redis (Tier B) for swarm-shared hot memory + write-serialization the moment
subagents write concurrently -- which is our design, so Tier B is in v1. Add the
knowledge graph (Tier 3) only if FTS recall demonstrably fails. Each layer keeps
the system runnable; none is a stopgap meant to be thrown away.

## Config (config.sh)

- `MEMORY_ENABLED` (default 1)
- `REDIS_URL` (default redis://127.0.0.1:6379)
- `MEMORY_DB` (default ~/.local/share/fools-trick/memory.db -- durable,
  NOT /tmp; survives reboot)
- `WINDOW_INPUT_TOKENS` (default 160000) -- the sliding input cap
- `DECODE_HEADROOM` (default 32000) -- reserved output budget; invariant
  WINDOW_INPUT_TOKENS + DECODE_HEADROOM < orchestrator context (384k)
- `MEMORY_RECENT_TTL` (default 3600s) -- Redis recent-cache expiry

## Two subsystems: orchestrator sliding vs subagent pruning

Context management is NOT one mechanism. The two agent tiers have different jobs, different
windows, and different lifetimes, so they get different subsystems that only share primitives (the
`experimental.chat.messages.transform` hook and opencode's tool-part `state.time.compacted`
eviction flag). Keeping them separate is the point -- the orchestrator's job is long-term memory;
the subagent's job is surviving a bounded task on a small window.

### Orchestrator (build/plan) -- lossless sliding window
Covered above (Job 1). Deep single stream (384k). Long-lived. Evicts oldest raw turns past
`WINDOW_INPUT_TOKENS`, persisting each verbatim to the episode store first, so everything stays
recallable. This tier owns durable memory.

### Subagent (explore/general/reviewer) -- distill-gated prune, no persistence
A worker is a one-shot bounded session on a single slot (`WORKER_CTX_PER_SLOT`). It must run a long
multi-step task (search, multi-file edit, review) without overflowing its small window, and without
going amnesiac from crude truncation. It does NOT persist to the episode store -- its only durable
outputs are its final report to the orchestrator and any scratch artifacts. So its context is
managed IN PLACE:

- Budget: `WORKER_INPUT_TOKENS` is the prune trigger; `WORKER_DECODE_HEADROOM` is reserved for
  output. Invariant `WORKER_INPUT_TOKENS + WORKER_DECODE_HEADROOM <= WORKER_CTX_PER_SLOT`
  (test-guarded, the worker analog of the orchestrator's window+headroom invariant). Headroom is
  generous because the abliterated worker over-reasons (measured 20k+ output tokens at medium).
- What gets evicted: only completed TOOL RESULTS (the big read/grep/command dumps). The worker's own
  reasoning and text are never touched, and neither is the head (system prompt + the task brief) nor
  the last `WORKER_KEEP_RECENT` tool results. Eviction sets `state.time.compacted`, and opencode's
  `toModelMessages` then sends `[Old tool result content cleared]` in place of the payload.
- Distill-gated, not age-gated: a result is evicted FIRST once the worker has reasoned over it --
  i.e. it called the `note` tool to record the finding + evidence (which lives in the worker's
  reasoning/notes, and is written to a scratch notes file). This is evict-raw / retain-distilled:
  the lesson stays, the bulk goes. Basis: tool-result clearing improves agentic accuracy (Anthropic
  `clear_tool_uses`), lossy prose summarization hurts it (Zep, arXiv:2501.13956), and the safe form
  is clearing raw observations the agent has already distilled.
- Backstop: if distilled-first eviction is not enough to get under budget, the oldest remaining
  results are force-evicted. A worker that never distilled still cannot overflow -- degraded (it
  loses raw context it never captured), never dead.
- Prompt-cache aware: the head is never mutated, so the cacheable prefix stays stable; eviction only
  ever touches older tool results behind it.

### Config (config.sh)
- `WORKER_INPUT_TOKENS` (default 26000) -- subagent prune trigger
- `WORKER_DECODE_HEADROOM` (default 16000) -- subagent reserved output budget; invariant
  `WORKER_INPUT_TOKENS + WORKER_DECODE_HEADROOM <= WORKER_CTX_PER_SLOT`
- `WORKER_KEEP_RECENT` (default 3) -- most-recent raw tool results never pruned

## What we explicitly do NOT build now

- No knowledge graph / embeddings / entity extraction (Tier 3, deferred above).
- No autonomous background curator (an aux-model loop mutating memory on a
  schedule). Memory mutates only through agent tool calls, in the loop, visible.
- No markdown memory files. Episodes are structured rows, not prose to manage;
  the earlier markdown-first plan is superseded by the swarm-shared requirement
  (concurrent workers reading/writing is a datastore job, not a file job).

## RESOLVED: three bugs found by the orchestrator critiquing its own design, live

The thread-scoping assumption is broken. `threadOf` (.opencode/plugin/memory.js) resolves the
conversation thread as `rootSessionID || parentSessionID || sessionID`, but opencode's plugin
ToolContext and hook inputs expose ONLY `sessionID` and `agent` -- `rootSessionID` and
`parentSessionID` DO NOT EXIST on those objects (verified against @opencode-ai/plugin types). So
`threadOf` always falls through to `sessionID`, which is DIFFERENT for every subagent child session.

Consequence: a subagent's `memory_write` lands under the worker's own child sessionID; the
orchestrator's `memory_search` queries the root sessionID; `WHERE thread = ?` never matches. The
swarm-shared recall tier (Tier B, the entire point of Redis being shared) silently returns nothing
-- no error, no warning. Cross-agent memory is effectively dead until this is fixed. The
orchestrator's own evicted turns still work (it consistently uses its own root sessionID), so
single-agent sliding still functions; only cross-agent sharing is broken.

The bench does not catch this: it pins `--session sid` on every turn (bench/memory.py), so it never
exercises subagent-write -> orchestrator-read across distinct session ids.

FIXED. Three bugs, all found in one self-review pass and all now verified fixed end-to-end:
1. thread-scoping: `threadOf` now resolves `sessionID` -> walks `session.parent_id` (via `opencode
   db`) to the root, cached. A subagent's write and the orchestrator's read land under the same
   root thread. VERIFIED LIVE: a `general` subagent's memory_write landed under the orchestrator's
   root thread and the orchestrator's memory_search retrieved it.
2. runtime sqlite: opencode's embedded runtime is BUN, which cannot import `node:sqlite`. store.js
   now detects Bun and uses `bun:sqlite` (Database), falling back to `node:sqlite` (DatabaseSync)
   under Node for tests. Without this the memory tools errored on every call -- they had never
   worked through opencode until now.
3. tool registration: the memory module is now imported DYNAMICALLY inside the tools/hooks, not at
   plugin-eval time, so the sqlite import chain can't silently fail the whole plugin's tool
   registration. Tools now appear in the agent's toolset.
Also fixed: the duplicate-episode-on-partial-Redis-failure bug (XADD now in its own try; the
best-effort recent-cache writes can't trigger the SQLite fallback).

## RESOLVED: the transform hook filed every evicted turn under thread "default"

A fourth bug of the same family, found while adding the subagent subsystem. The sliding-window hook
read `input?.agent` and `input?.sessionID` from the `experimental.chat.messages.transform` input.
But opencode invokes that hook with an EMPTY input object -- verified in the runtime binary:
`trigger("experimental.chat.messages.transform", {}, {messages:C})`. So `agent` was always `""` and
`sessionID` was always `""`; `resolveThread("")` returns `"default"`, and every evicted orchestrator
turn was persisted under the thread `"default"` instead of the conversation root. Recall keyed on the
real root never found them -- the orchestrator's own sliding recall was silently misfiled (the same
failure mode as bug 1, just via a different empty field).

FIXED. Identity is now read from the message stream itself: assistant messages carry `.info.agent`
and `.info.sessionID`, so `agentOf`/`sessionOf` scan `output.messages` for them. This is also what
lets the hook tell an orchestrator turn from a worker turn (to pick the sliding vs pruning
subsystem). The dead `input.agent`/`input.sessionID` reads and the early-return worker guard that
depended on them are removed.

Still unguarded by the bench: subagent-write -> orchestrator-read across distinct session ids, and
the subagent prune path (the bench pins `--session`). Covered now by tests/test_memory.mjs unit
tests for identity resolution and the prune policy; a live cross-agent bench remains future work.
