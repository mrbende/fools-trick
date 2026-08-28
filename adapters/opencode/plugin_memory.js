// opencode adapter: Event Log + context policy. Tools cross to the Python core via the bridge;
// the per-turn transform applies the core's eviction decision in-process.

import { tool } from "@opencode-ai/plugin"
import { mkdirSync, writeFileSync } from "node:fs"
import { dirname } from "node:path"
import { callTool, planContext, cfgNum, configSnapshot } from "./bridge.js"
import {
  agentOf, sessionOf, inputTokens, applyEvict, toWorkerTurns, toOrchestratorTurns,
} from "./shape.js"

// Budgets from the config loader, so the prune trigger tracks the served context.
const WINDOW_INPUT = cfgNum("WINDOW_INPUT_TOKENS", "window_input_tokens", 160000)
const WORKER_INPUT = cfgNum("WORKER_INPUT_TOKENS", "worker_input_tokens", 18000)
const KEEP_RECENT = cfgNum("WORKER_KEEP_RECENT", "worker_keep_recent", 3)
const TOOL_RESULT_CAP = cfgNum("WORKER_TOOL_RESULT_CAP", "worker_tool_result_cap", 8000)
const ENABLED = process.env.MEMORY_ENABLED != null
  ? process.env.MEMORY_ENABLED === "1"
  : configSnapshot().memory_enabled !== false
const ORCHESTRATORS = new Set(["build", "plan"])

// Per-session distilled callIDs (fed by note) and the most-recent tool call seen. Bounded.
const distilled = new Map() // sessionID -> Set<callID>
const lastCall = new Map()  // sessionID -> callID
function distilledFor(sid) { let s = distilled.get(sid); if (!s) { s = new Set(); distilled.set(sid, s) } return s }

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
        },
        async execute({ query, k }, ctx) {
          return await callTool("memory_search", { query, k }, ctx)
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
      if (ORCHESTRATORS.has(agentOf(msgs))) await slideOrchestrator(msgs, output)
      else await pruneWorker(msgs)
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

    const toEvict = new Set(d.evict_call_ids)
    for (const m of msgs) {
      for (const p of m?.parts || []) {
        if (p?.type !== "tool" || !toEvict.has(p.callID) || p.state?.time?.compacted) continue
        const r = await callTool("memory_write", { content: p.state.output, durable: true }, { sessionID: sid, agent })
        const seq = r?.metadata?.seq
        insertIndexNote(m, p, seq)
        markCompacted(p)
      }
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

function markCompacted(part) {
  part.state = part.state || {}
  part.state.time = part.state.time || {}
  part.state.time.compacted = Date.now()
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
  const capChars = TOOL_RESULT_CAP * 3.5
  if (text.length <= capChars) return
  const sid = input.sessionID
  const r = await callTool("memory_write", { content: text, durable: true }, { sessionID: sid })
  const seq = r?.metadata?.seq
  const scratch = `${configSnapshot().scratch_dir || "/tmp/fools-trick/scratch"}/tool-${input.callID}.txt`
  try { mkdirSync(dirname(scratch), { recursive: true }); writeFileSync(scratch, text) } catch { /* scratch is best-effort */ }
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
