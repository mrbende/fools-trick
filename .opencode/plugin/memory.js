// fools-trick memory: two context-management subsystems plus a shared recall store.
//
//   Orchestrator (build/plan): a lossless sliding window. Turns past WINDOW_INPUT_TOKENS are
//     persisted to the episode store and dropped, so a long session never summarizes-and-drops.
//   Subagent (workers): a distill-gated prune. Tool results the worker has noted (reasoned over)
//     are cleared from context first, oldest-first as a backstop, so a bounded worker runs past its
//     window without going amnesiac. Workers do not persist; their output is their report + scratch.
//
// Both run in the experimental.chat.messages.transform hook and evict via opencode's tool-part
// state.time.compacted flag. Message-array logic is in ../memory/window.js (unit-tested); the store
// coordination is in ../memory/memory.js. Design: docs/memory-design.md.

import { tool } from "@opencode-ai/plugin"
import { appendFileSync, mkdirSync } from "node:fs"
import { dirname } from "node:path"
import { agentOf, sessionOf, pruneWorker, selectSlide } from "../memory/window.js"

const DB = process.env.MEMORY_DB || `${process.env.HOME}/.local/share/fools-trick/memory.db`
const REDIS_URL = process.env.REDIS_URL || "redis://127.0.0.1:6379"
const WINDOW_INPUT_TOKENS = Number(process.env.WINDOW_INPUT_TOKENS || 160000)
const WORKER_INPUT_TOKENS = Number(process.env.WORKER_INPUT_TOKENS || 26000)
const WORKER_KEEP_RECENT = Number(process.env.WORKER_KEEP_RECENT || 3)
const RECENT_TTL = Number(process.env.MEMORY_RECENT_TTL || 3600)
const ENABLED = (process.env.MEMORY_ENABLED ?? "1") === "1"
const SCRATCH = process.env.FOOLS_SCRATCH || "/tmp/fools-trick/scratch"
const ORCHESTRATOR_AGENTS = new Set(["build", "plan"])

// The store imports node:sqlite; importing it at plugin-eval time can silently fail the whole
// plugin's tool registration under opencode's runtime, so we defer it to first use.
let _mem = null
async function mem() {
  if (!_mem) {
    _mem = await import("../memory/memory.js")
    _mem.initMemory({ dbPath: DB, redisUrl: REDIS_URL })
  }
  return _mem
}

// callIDs of tool results a worker has distilled via note(), safe to evict first. Bounded so a
// long-lived process spawning many workers doesn't grow it without limit; oldest marks fall out.
const distilled = new BoundedSet(4096)
// Most-recent tool result per session, so note() with no callID marks what the worker just saw.
// ToolContext has no callID; the tool.execute.after hook does.
const lastCall = new Map()

function formatRecall(eps) {
  if (!eps || !eps.length) return "(no relevant memory found)"
  const lines = eps.map((e) => `- [${e.agent ? `${e.role}/${e.agent}` : e.role || "?"}] ${e.content}`)
  return `<recalled_memory>\n${lines.join("\n")}\n</recalled_memory>`
}

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
          const m = await mem()
          const thread = await m.resolveThread(ctx?.sessionID || "")
          await m.writeEpisode({ thread, session: ctx?.sessionID || "", agent: ctx?.agent || "", role: "memory", content, recentTtl: RECENT_TTL })
          return { title: "memory saved", output: `saved to thread ${thread}`, metadata: { thread } }
        },
      }),

      memory_search: tool({
        description:
          "Search this conversation's persistent memory for relevant past context (decisions, facts, " +
          "earlier turns that have slid out of the window). Use when you need something from earlier.",
        args: {
          query: tool.schema.string().describe("What to recall, in a few keywords or a question."),
          k: tool.schema.number().optional().describe("Max results (default 10)."),
        },
        async execute({ query, k }, ctx) {
          const m = await mem()
          const thread = await m.resolveThread(ctx?.sessionID || "")
          const eps = await m.searchMemory({ thread, query, k: k || 10 })
          return { title: `recall: ${query}`, output: formatRecall(eps), metadata: { thread, hits: eps.length } }
        },
      }),

      // A worker's distill action: record a finding so its raw tool result can be cleared without
      // losing what was learned. Writes the finding to a scratch notes file and marks the distilled
      // result evictable (by callID, else the most recent result the worker saw).
      note: tool({
        description:
          "Record a finding you extracted from a tool result, so its raw output can be cleared from " +
          "your context without losing what you learned. State the finding and its evidence " +
          "(file:line, query, url). Call this after you get the value out of a large result. Write " +
          "conclusions, not raw data; only your final report goes back to the orchestrator.",
        args: {
          finding: tool.schema.string().describe("What you learned, self-contained, with evidence (e.g. 'auth is in auth/session.ts:42, JWT, no refresh')."),
          callID: tool.schema.string().optional().describe("callID of the tool result this distills. Omit to mark the most recent result you saw."),
        },
        async execute({ finding, callID }, ctx) {
          const target = callID || lastCall.get(ctx?.sessionID || "")
          if (target) distilled.add(target)
          const file = `${SCRATCH}/notes-${ctx?.sessionID || "worker"}.md`
          try { mkdirSync(dirname(file), { recursive: true }); appendFileSync(file, `- ${finding}\n`) } catch { /* best-effort; the mark is what matters */ }
          return { title: "noted", output: `finding recorded (${file})`, metadata: { distilled: target || "none" } }
        },
      }),
    },

    // The one seam for both subsystems. opencode calls it every turn with an empty input and the
    // live message array in output.messages, which it then sends to the model; mutations take effect
    // this turn. Identity comes from the messages (agentOf), since input carries none.
    "experimental.chat.messages.transform": async (_input, output) => {
      const msgs = output?.messages
      if (!Array.isArray(msgs) || !msgs.length) return
      if (ORCHESTRATOR_AGENTS.has(agentOf(msgs))) await slideOrchestrator(msgs, output)
      else pruneWorker(msgs, { inputBudget: WORKER_INPUT_TOKENS, keepRecent: WORKER_KEEP_RECENT, distilled })
    },

    "tool.execute.after": async (input) => {
      if (input.tool === "note" || input.tool === "memory_write" || input.tool === "memory_search") return
      if (input.sessionID && input.callID) lastCall.set(input.sessionID, input.callID)
    },

    "experimental.text.complete": async () => { try { await (await mem()).drain() } catch { /* soft */ } },
  }

  // Persist the oldest turns past the window to the episode store, then drop them from the array.
  async function slideOrchestrator(msgs, output) {
    const { evicted } = selectSlide(msgs, { inputBudget: WINDOW_INPUT_TOKENS, keepTail: 6 })
    if (!evicted.length) return
    const m = await mem()
    const session = sessionOf(msgs)
    const agent = agentOf(msgs)
    const thread = await m.resolveThread(session)
    for (const e of evicted) {
      await m.writeEpisode({ thread, session, agent, role: e.role, content: e.text, recentTtl: RECENT_TTL })
    }
    const drop = new Set(evicted.map((e) => e.idx))
    output.messages = msgs.filter((_, i) => !drop.has(i))
  }
}

// A Set that forgets its oldest entries past a cap. Insertion order is Set iteration order, so the
// first key is the oldest.
class BoundedSet {
  constructor(max) { this.max = max; this.set = new Set() }
  add(v) {
    this.set.add(v)
    if (this.set.size > this.max) this.set.delete(this.set.values().next().value)
  }
  has(v) { return this.set.has(v) }
}
