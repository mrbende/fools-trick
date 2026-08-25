# Persistent memory + sliding context: architecture

Status: BUILDING. This is the settled shape; implement against it in layers.

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

**Tier B -- hot shared working memory (Redis).** The 4 concurrent workers plus
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
- `MEMORY_DB` (default /home/reed/.local/share/fools-trick/memory.db -- durable,
  NOT /tmp; survives reboot)
- `WINDOW_INPUT_TOKENS` (default 160000) -- the sliding input cap
- `DECODE_HEADROOM` (default 32000) -- reserved output budget; invariant
  WINDOW_INPUT_TOKENS + DECODE_HEADROOM < orchestrator context (384k)
- `MEMORY_RECENT_TTL` (default 3600s) -- Redis recent-cache expiry

## What we explicitly do NOT build now

- No knowledge graph / embeddings / entity extraction (Tier 3, deferred above).
- No autonomous background curator (an aux-model loop mutating memory on a
  schedule). Memory mutates only through agent tool calls, in the loop, visible.
- No markdown memory files. Episodes are structured rows, not prose to manage;
  the earlier markdown-first plan is superseded by the swarm-shared requirement
  (concurrent workers reading/writing is a datastore job, not a file job).
