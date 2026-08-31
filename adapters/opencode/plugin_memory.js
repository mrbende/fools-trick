// opencode adapter: Event Log + context policy. Tools cross to the Python core via the bridge;
// the per-turn transform applies the core's eviction decision in-process.

import { tool } from "@opencode-ai/plugin"
import { callTool, planContext, cfgNum, configSnapshot } from "./bridge.js"
import {
  agentOf, sessionOf, providerOf, inputTokens, applyEvict, toWorkerTurns, toOrchestratorTurns,
} from "./shape.js"

// Budgets from the config loader, so the prune trigger tracks the served context.
const WINDOW_INPUT = cfgNum("WINDOW_INPUT_TOKENS", "window_input_tokens", 160000)
const WORKER_INPUT = cfgNum("WORKER_INPUT_TOKENS", "worker_input_tokens", 18000)
const KEEP_RECENT = cfgNum("WORKER_KEEP_RECENT", "worker_keep_recent", 3)
const TOOL_RESULT_CAP = cfgNum("WORKER_TOOL_RESULT_CAP", "worker_tool_result_cap", 8000)
const ENABLED = process.env.MEMORY_ENABLED != null
  ? process.env.MEMORY_ENABLED === "1"
  : configSnapshot().memory_enabled !== false
const ORCH_PROVIDER = process.env.FOOLS_ORCH_PROVIDER || "fool-ds4"

// A Map that evicts its oldest key past a cap. Per-session distilled/lastCall state must not grow
// unboundedly across a long-lived process spawning many workers.
class BoundedMap {
  constructor(max, makeDefault) { this.max = max; this.makeDefault = makeDefault; this.m = new Map() }
  get(k) {
    if (!this.m.has(k)) { this.m.set(k, this.makeDefault ? this.makeDefault() : undefined); this._trim() }
    return this.m.get(k)
  }
  set(k, v) { this.m.set(k, v); this._trim() }
  _trim() { while (this.m.size > this.max) this.m.delete(this.m.keys().next().value) }
}

// Per-session distilled callIDs (fed by note) and the most-recent tool call seen.
const distilled = new BoundedMap(512, () => new Set())
const lastCall = new BoundedMap(1024)
function distilledFor(sid) { return distilled.get(sid) }

export default async () => {
  if (!ENABLED) return {}
  return {
    tool: {
      memory_write: tool({
        description:
          "Persist a durable memory (a decision, fact, preference, or handoff) to the shared " +
          "cross-session store, so it survives context sliding and is recalled later.",
        args: { content: tool.schema.string().describe("The memory to persist, one self-contained statement.") },
        async execute({ content }, ctx) {
          return await callTool("memory_write", { content }, ctx)
        },
      }),
      record_contract: tool({
        description:
          "Record this task's success-contract BEFORE dispatching work: the definition-of-done the " +
          "finished work is checked against before it is committed. Set the exact SIGNAL that proves " +
          "done (a command/test/check, e.g. `make test`, `pytest tests/x.py`), the one-line GOAL, and " +
          "BOUNDARIES. Do this at the start of a non-trivial coding task -- an implicit objective makes " +
          "verification meaningless.",
        args: {
          goal: tool.schema.string().describe("One line: what this task achieves."),
          signal: tool.schema.string().describe("The exact command/check that proves done (green = done)."),
          boundaries: tool.schema.string().optional().describe("What is out of scope / must not change."),
        },
        async execute({ goal, signal, boundaries }, ctx) {
          return await callTool("record_contract", { goal, signal, boundaries }, ctx)
        },
      }),
      memory_search: tool({
        description:
          "Search this conversation's persistent memory for relevant past context (decisions, " +
          "facts, earlier turns that have slid out of the window). BM25 relevance order. If a fact " +
          "may have CHANGED over time, don't trust the top hit -- read the seq-ordered neighbors " +
          "(recall(seq) / a seq range) to get the latest version, since the log preserves both " +
          "sides of an update in order.",
        args: {
          query: tool.schema.string().describe("What to recall, in a few keywords or a question."),
          k: tool.schema.number().optional().describe("Max results (default 10)."),
          role: tool.schema.string().optional().describe("Scope to a role, e.g. 'memory' (decisions), 'tool' (tool results), 'escalation'."),
          agent: tool.schema.string().optional().describe("Scope to one agent's episodes."),
          after_seq: tool.schema.number().optional().describe("Only episodes after this seq."),
          before_seq: tool.schema.number().optional().describe("Only episodes before this seq."),
        },
        async execute({ query, k, role, agent, after_seq, before_seq }, ctx) {
          return await callTool("memory_search", { query, k, role, agent, after_seq, before_seq }, ctx)
        },
      }),
      recall: tool({
        description:
          "Recover an evicted tool result verbatim by its Event Log seq address (shown in the " +
          "eviction-index line as seq=N). Use to get back a raw result you pruned but now need.",
        args: { seq: tool.schema.number().describe("The seq address of the evicted result.") },
        async execute({ seq }, ctx) {
          return await callTool("recall", { seq }, ctx)
        },
      }),
      note: tool({
        description:
          "Record a finding you extracted from a tool result, so its raw output can be cleared " +
          "from your context without losing what you learned. State the finding and its evidence " +
          "(file:line, query, url). Pass callID to mark the exact result you distilled.",
        args: {
          finding: tool.schema.string().describe("What you learned, self-contained, with evidence."),
          callID: tool.schema.string().optional().describe("callID of the result this distills; omit for the most recent."),
        },
        async execute({ finding, callID }, ctx) {
          const target = callID || lastCall.get(ctx?.sessionID || "")
          if (target) distilledFor(ctx?.sessionID || "").add(target)
          return await callTool("note", { finding, callID: target }, ctx)
        },
      }),
      promote: tool({
        description:
          "Promote to the orchestrator: hand off when the task exceeds your window or depth. " +
          "Persists your distilled findings + evidence to the shared Event Log (recallable by seq) " +
          "and returns a structured escalation packet to put in your final report, so the " +
          "orchestrator takes over with the full context. Use when stuck or out of context -- do " +
          "not guess or loop.",
        args: {
          reason: tool.schema.string().describe("Why you are handing off (what blocked you / what's missing)."),
          status: tool.schema.string().optional().describe("blocked | needs-deeper-context | done-partial (default blocked)."),
        },
        async execute({ reason, status }, ctx) {
          return await callTool("promote", { reason, status }, ctx)
        },
      }),
      report: tool({
        description:
          "Return your typed handoff at the END of a unit (required before finishing). 'done, looks " +
          "good' must not advance the workflow: the orchestrator verifies your work independently, " +
          "so give it a packet it can act on. Persisted to shared memory (recallable by seq); put the " +
          "returned HANDOFF line at the top of your final report.",
        args: {
          status: tool.schema.string().describe("done | done-partial | blocked."),
          artifact: tool.schema.string().describe("What was produced: files touched (path:line), scratch artifact path, or the diff."),
          evidence: tool.schema.string().optional().describe("What you verified and how (command run + result). Empty if unverified."),
          assumptions: tool.schema.string().optional().describe("What you assumed that the orchestrator must confirm."),
          unresolved: tool.schema.string().optional().describe("Conflicts, follow-ups, hazards the orchestrator must reconcile."),
        },
        async execute(args, ctx) {
          return await callTool("report", args, ctx)
        },
      }),
      library_search: tool({
        description:
          "Hybrid content search over the agent's own research library (attune-library, ~50k " +
          "documents: papers, books, articles). Semantic + lexical, fused. Returns hits with " +
          "canonical_id + chunk_index + a snippet; follow up with library_read to read around a " +
          "hit. Use this for what the corpus SAYS; for metadata (authors/years/journals/counts) " +
          "use library_query.",
        args: {
          query: tool.schema.string().describe("The content question, in a few keywords or a sentence."),
          k: tool.schema.number().optional().describe("Max hits (default 10)."),
          collection: tool.schema.string().optional().describe("Scope to one collection."),
        },
        async execute({ query, k, collection }, ctx) {
          return await callTool("library_search", { query, k, collection }, ctx)
        },
      }),
      library_read: tool({
        description:
          "Read a document from the research library: a chunk window around a search hit " +
          "(around + radius), or the whole document record. Pure SQL, always available. Take the " +
          "canonical_id from a library_search hit.",
        args: {
          canonical_id: tool.schema.string().describe("The document's canonical_id (from a search hit)."),
          around: tool.schema.number().optional().describe("Read the chunk window around this chunk_index."),
          radius: tool.schema.number().optional().describe("Chunks either side of `around` (default 1)."),
        },
        async execute({ canonical_id, around, radius }, ctx) {
          return await callTool("library_read", { canonical_id, around, radius }, ctx)
        },
      }),
      library_query: tool({
        description:
          "Structured metadata query over the research library's documents table: filter by " +
          "author/title/year/collection/lang/doi, or aggregate with count_by (e.g. count_by=year " +
          "for the corpus's year distribution). Pure SQL, always available, no inference. This is " +
          "the stats/coverage instrument; for what documents SAY use library_search.",
        args: {
          author: tool.schema.string().optional().describe("Author substring (case-insensitive)."),
          title: tool.schema.string().optional().describe("Title substring (case-insensitive)."),
          year: tool.schema.number().optional().describe("Exact publication year."),
          collection: tool.schema.string().optional().describe("Collection name."),
          lang: tool.schema.string().optional().describe("Language code, e.g. 'en'."),
          doi: tool.schema.string().optional().describe("Exact DOI."),
          count_by: tool.schema.string().optional().describe("Aggregate instead of listing: year | collection | lang."),
        },
        async execute(args, ctx) {
          return await callTool("library_query", args, ctx)
        },
      }),
      library_fetch: tool({
        description:
          "Acquire a document INTO the permanent research library by reference (doi/arxiv/url/" +
          "title): the library resolves it, downloads from open-access sources, and queues it " +
          "for the normal ETL (convert, OCR, index). This is PERMANENCE, not reading -- slow " +
          "(external sources), and the reply is an acquire report, not the text. To read a paper " +
          "once without ingesting it, use pdf_read instead.",
        args: {
          doi: tool.schema.string().optional().describe("The DOI, e.g. 10.1038/..."),
          url: tool.schema.string().optional().describe("A direct URL (PDF or a page that resolves to one)."),
          arxiv: tool.schema.string().optional().describe("The arXiv id, e.g. 2301.00001."),
          title: tool.schema.string().optional().describe("The title (weakest; resolved to an identifier first)."),
          authors: tool.schema.string().optional().describe("Authors, to disambiguate a title lookup."),
          collection: tool.schema.string().optional().describe("Target collection (default 'inbox')."),
        },
        async execute(args, ctx) {
          return await callTool("library_fetch", args, ctx)
        },
      }),
      pdf_read: tool({
        description:
          "Download a PDF from the web and read it directly, right now -- extracts the text to " +
          "your task scratch dir and returns the first window plus the path; page the rest with " +
          "the read tool. Ephemeral: this does NOT ingest to the library (use library_fetch for " +
          "permanence). Scanned PDFs have no text layer -- those need library_fetch's OCR path.",
        args: {
          url: tool.schema.string().describe("The direct PDF URL."),
        },
        async execute({ url }, ctx) {
          return await callTool("pdf_read", { url }, ctx)
        },
      }),
      trace: tool({
        description:
          "Reconstruct a session's trajectory: the tools a worker/orchestrator called, their " +
          "status, errors, truncations, evictions, tokens, and where it stopped. Use to debug a " +
          "failed or surprising subagent without digging the DB by hand. Pass a sessionID to trace " +
          "one, or none for the most recent subagents.",
        args: {
          sessionID: tool.schema.string().optional().describe("The session to trace; omit for recent subagents."),
          limit: tool.schema.number().optional().describe("How many recent subagents (default 5)."),
        },
        async execute({ sessionID, limit }, ctx) {
          return await callTool("trace", { sessionID, limit }, ctx)
        },
      }),
      tripcheck: tool({
        description:
          "Trip-wire check: compare the current task's cost/behavior against the recent baseline and " +
          "report any fired wires (token spike, duration spike, delegation vanished, reasoning runaway). " +
          "Run this to catch a regression as a signal, not a vibe -- e.g. mid-way through a long task " +
          "or before canonicalizing, to confirm you are not drifting wildly from normal.",
        args: {},
        async execute(_args, ctx) {
          return await callTool("tripcheck", {}, ctx)
        },
      }),
      delegate_cheap: tool({
        description:
          "Run a cheap, shallow sub-task on a fast worker slot instead of spending deep-stream " +
          "tokens: a one-shot classification, summary, extraction, or small transform that needs " +
          "no tools and no orchestration depth. Returns the worker's answer. Use for sub-tasks " +
          "that don't need your full reasoning.",
        args: {
          task: tool.schema.string().describe("The self-contained cheap task."),
          max_tokens: tool.schema.number().optional().describe("Cap the answer length (default 400)."),
        },
        async execute({ task, max_tokens }, ctx) {
          return await callTool("delegate_cheap", { task, max_tokens }, ctx)
        },
      }),
      scratch_write: tool({
        description:
          "Write an ephemeral artifact (a large result, a generated document) to the shared " +
          "scratch dir, scoped to this task, and return the path. Use for output too big for a " +
          "reply -- write the artifact, return only a reference + the headline.",
        args: {
          content: tool.schema.string().describe("The artifact content."),
          name: tool.schema.string().optional().describe("A filename (default: a timestamped .txt)."),
        },
        async execute({ content, name }, ctx) {
          return await callTool("scratch_write", { content, name }, ctx)
        },
      }),
    },

    // Inject each agent's config-derived context+job each turn so the role split tracks config.yaml.
    "experimental.chat.system.transform": async (input, output) => {
      if (!output || !Array.isArray(output.system)) return
      const block = runtimeContextBlock(input)
      if (!block) return
      // a second system block breaks the worker chat template ("System message must be at the beginning")
      if (output.system.length) output.system[output.system.length - 1] += "\n\n" + block
      else output.system.push(block)
    },

    "experimental.chat.messages.transform": async (_input, output) => {
      const msgs = output?.messages
      if (!Array.isArray(msgs) || !msgs.length) return
      // Route on the model provider, not the agent name: a subagent's messages inherit the parent
      // orchestrator's agent (build), which would send every worker turn to the orchestrator slide.
      const prov = providerOf(msgs)
      if (prov === ORCH_PROVIDER) await slideOrchestrator(msgs, output)
      else if (prov) await pruneWorker(msgs)
    },

    "tool.execute.after": async (input, output) => {
      if (["note", "memory_write", "memory_search", "recall", "promote"].includes(input.tool)) return
      if (input.sessionID && input.callID) lastCall.set(input.sessionID, input.callID)
      await capToolResult(input, output)
    },

    // drain the write-stream to SQLite at each turn boundary (no writes park in the ephemeral tier)
    "experimental.text.complete": async () => { callTool("drain", {}, {}) },
  }

  // Orchestrator: lossless slide. The core decides which turns to drop; the adapter persists
  // them to the Event Log (lossless) and removes them from the array.
  function slideOrchestrator(msgs, output) {
    const turns = toOrchestratorTurns(msgs)
    const d = planContext("slide", { turns, inputBudget: WINDOW_INPUT, keepTail: 6 })
    if (!d || !d.changed) return
    const sid = sessionOf(msgs)
    const agent = agentOf(msgs)
    for (const p of d.persist) callTool("memory_write", { content: p.content }, { sessionID: sid, agent })
    const drop = new Set(d.drop_turn_indices)
    output.messages = msgs.filter((_, i) => !drop.has(i))
  }

  // Subagent recoverable eviction: the core decides what to evict; for each, persist durably (the
  // seq must exist before the index line references it and before the payload leaves the view),
  // then index it, then compact it.
  async function pruneWorker(msgs) {
    if (inputTokens(msgs) <= WORKER_INPUT) return
    const sid = sessionOf(msgs)
    const agent = agentOf(msgs)
    const turns = toWorkerTurns(msgs)
    const d = planContext("prune", {
      turns, inputBudget: WORKER_INPUT, keepRecent: KEEP_RECENT,
      distilled: [...distilledFor(sid)], pinned: [],
    })
    if (!d || !d.changed) return

    // The core decided which results to evict; the adapter's one compaction applier (applyEvict)
    // marks them and hands back their outputs, which we then persist durably + index by seq so the
    // worker can recover them. Persist must reference the seq, so persist-then-index per result.
    const evicted = applyEvict(msgs, d.evict_call_ids)
    for (const { callID, output: text, msg, part } of evicted) {
      const r = await callTool("memory_write", { content: text, durable: true }, { sessionID: sid, agent })
      insertIndexNote(msg, part, r?.metadata?.seq)
    }
    rollupIndexNotes(msgs)
  }
}

// Bound the eviction index. A flat index grows linearly with the session (one note per eviction,
// itself a context leak); Scroll's Algorithm 1 step 7 rolls it up so it stays ~O(k log n). After
// each prune, keep the newest KEEP_INDEX eviction notes in full and collapse the older ones into a
// single rolled-up line carrying their seq span -- recoverable by memory_search/recall, no longer
// one text part each.
const KEEP_INDEX = 4
const INDEX_NOTE = /^\[evicted a tool result/

function rollupIndexNotes(msgs) {
  const idx = []   // [ {msg, partIndex, text} ] for each eviction note, in order
  for (const m of msgs) {
    (m?.parts || []).forEach((p, i) => {
      if (p?.type === "text" && INDEX_NOTE.test(p.text || "")) idx.push({ m, i, text: p.text })
    })
  }
  const stale = idx.length - KEEP_INDEX
  if (stale <= 0) return
  // pull the seq span out of the notes being rolled up, then remove them from the view
  const seqs = []
  for (const e of idx.slice(0, stale)) {
    const m = e.text.match(/seq=(\d+)/)
    if (m) seqs.push(Number(m[1]))
    const j = e.m.parts.findIndex((p) => p.type === "text" && INDEX_NOTE.test(p.text || "") && p.text === e.text)
    if (j !== -1) e.m.parts.splice(j, 1)
  }
  if (!seqs.length) return
  const line = `[${stale} earlier tool results evicted, seq ${Math.min(...seqs)}-${Math.max(...seqs)}; recall(seq) to recover any of them.]`
  // insert the single rolled-up line where the oldest stale note was
  const anchor = idx[stale] ? idx[stale] : idx[idx.length - 1]
  if (anchor) anchor.m.parts.splice(Math.max(0, anchor.i), 0, { type: "text", text: line })
}

// Insert the eviction-index line as a short text part next to the tool result (a compacted part
// renders a fixed placeholder and cannot carry it). This is what the worker reads to recover.
function insertIndexNote(msg, part, seq) {
  const where = seq != null ? `seq=${seq}` : "recall via memory_search"
  const note = `[evicted a tool result (${preview(part.state.output)}). Recover it: ${where}.]`
  part.state = part.state || {}
  part.state.time = part.state.time || {}
  msg.parts = msg.parts || []
  const i = msg.parts.indexOf(part)
  msg.parts.splice(i + 1, 0, { type: "text", text: note })
}

function preview(s, n = 60) {
  const t = (s || "").replace(/\s+/g, " ").trim()
  return t.length > n ? t.slice(0, n) + "..." : t
}

// Cap a single tool result's in-view payload. One oversized read (a big file) must not overflow
// the worker slot before the per-turn prune runs. Spill the full output to scratch + the durable
// log (so it stays recoverable by seq), and leave a bounded preview + recovery pointer in view.
async function capToolResult(input, output) {
  const text = output?.output
  if (typeof text !== "string") return
  const capChars = TOOL_RESULT_CAP * 2.5  // the estimator's conservative divisor (see estimate.py)
  if (text.length <= capChars) return
  const sid = input.sessionID
  const r = await callTool("memory_write", { content: text, durable: true }, { sessionID: sid })
  const seq = r?.metadata?.seq
  const w = await callTool("scratch_write", { content: text, name: `tool-${input.callID}.txt` }, { sessionID: sid })
  const scratch = w?.metadata?.path || "(scratch write failed)"
  output.output =
    text.slice(0, capChars) +
    `\n\n[truncated: ${text.length} chars total, kept the first ${capChars}. ` +
    `The full result is recoverable: seq=${seq ?? "?"} via recall(seq), or read ${scratch}. ` +
    `If you need a later section, recall it or re-read that file with a line range.]`
}

// The config-bound runtime-context block injected into the system prompt each turn. Every agent
// learns its own context size and its job from the live config, so the context/role split follows
// config.yaml rather than a hardcoded number baked into a prompt.
function runtimeContextBlock(input) {
  const cfg = configSnapshot()
  if (!cfg || !cfg.worker) return null
  const workerCtx = cfg.worker_ctx_per_slot
  const orchCtx = cfg.orchestrator?.context
  const budget = cfg.worker_input_tokens
  const modelID = input?.model?.id || input?.model?.modelID || ""
  const onWorker = modelID === (cfg.worker && cfg.worker.model_id)
  if (onWorker) {
    return (
      `You run on a fast worker slot: ~${workerCtx} tokens of context, shared with your reasoning ` +
      `and output. It is small and bounded by design. The durable long-term memory belongs to the ` +
      `orchestrator (deep, ${orchCtx}-token context); your job is one bounded task. When your input ` +
      `approaches ~${budget} tokens, completed tool results are evicted (recoverably, by seq). If a ` +
      `task needs more context or depth than your slot holds, call promote(reason, status) to hand ` +
      `off to the orchestrator instead of guessing or looping.`
    )
  }
  return (
    `You are the orchestrator: a deep, single stream with a ${orchCtx}-token context. You hold the ` +
    `whole task and the durable long-term memory. The workers are fast, concurrent, and small ` +
    `(${workerCtx} tokens each) -- delegate bounded units to them; a worker that outgrows its slot ` +
    `escalates back to you via promote. Synthesize; do not fill your own window with raw file dumps.`
  )
}
