// fools-trick memory layer: persistent recall + sliding context window.
//
// Two jobs (docs/memory-design.md):
//  1. SLIDING WINDOW -- hold a live input window and slide it instead of letting opencode
//     summarize-and-drop (lossy compaction). experimental.chat.messages.transform prunes the
//     oldest raw turns once we cross WINDOW_INPUT_TOKENS, persisting each evicted turn as an
//     episode first. Decode headroom is defended by capping input well under the model context.
//  2. RECALL -- memory_search / memory_write tools (orchestrator AND subagents), backed by
//     Redis (hot, shared, write-queue) draining to SQLite (durable, FTS5, thread-scoped).
//
// Episodes are keyed by THREAD = the conversation's root session id, so recall is scoped to the
// conversation across its subagent child sessions.

import { tool } from "@opencode-ai/plugin"
import {
  initMemory, writeEpisode, searchMemory, drain, formatEpisodes,
} from "../memory/memory.js"

const DB = process.env.MEMORY_DB || `${process.env.HOME}/.local/share/fools-trick/memory.db`
const REDIS_URL = process.env.REDIS_URL || "redis://127.0.0.1:6379"
const WINDOW_INPUT_TOKENS = Number(process.env.WINDOW_INPUT_TOKENS || 160000)
const RECENT_TTL = Number(process.env.MEMORY_RECENT_TTL || 3600)
const ENABLED = (process.env.MEMORY_ENABLED ?? "1") === "1"

// Rough token estimate (~3.5 chars/token for code+prose). Cheap and good enough for windowing;
// we cap conservatively so the estimate erring low still leaves decode headroom.
const estTokens = (s) => Math.ceil((s ? String(s).length : 0) / 3.5)

// The conversation thread: opencode gives each session a sessionID; a subagent's child session
// carries the parent. We key on the top-level id when available, else the session's own id.
function threadOf(input) {
  return input?.rootSessionID || input?.parentSessionID || input?.sessionID || "default"
}

export default async () => {
  if (!ENABLED) return {}
  initMemory({ dbPath: DB, redisUrl: REDIS_URL, recentTtl: RECENT_TTL })

  return {
    tool: {
      memory_write: tool({
        description:
          "Persist a durable memory (a decision, fact, preference, or handoff) to the shared " +
          "cross-session store, so it survives context sliding and is recalled later. Use for " +
          "anything that should outlive the current window: decisions and their rationale, " +
          "standing user preferences, task handoffs between agents, facts not to re-derive.",
        args: {
          content: tool.schema.string().describe("The memory to persist, one self-contained statement."),
        },
        async execute({ content }, ctx) {
          const thread = threadOf(ctx)
          await writeEpisode({
            thread, session: ctx?.sessionID || "", agent: ctx?.agent || "",
            role: "memory", content, recentTtl: RECENT_TTL,
          })
          return { title: "memory saved", output: `saved to thread ${thread}`, metadata: { thread } }
        },
      }),

      memory_search: tool({
        description:
          "Search this conversation's persistent memory for relevant past context (decisions, " +
          "facts, earlier turns that have slid out of the window). Returns the most relevant " +
          "stored episodes. Use when you need something discussed earlier that may no longer be " +
          "in context.",
        args: {
          query: tool.schema.string().describe("What to recall, in a few keywords or a question."),
          k: tool.schema.number().optional().describe("Max results (default 10)."),
        },
        async execute({ query, k }, ctx) {
          const thread = threadOf(ctx)
          const eps = await searchMemory({ thread, query, k: k || 10 })
          const out = formatEpisodes(eps) || "(no relevant memory found)"
          return { title: `recall: ${query}`, output: out, metadata: { thread, hits: eps.length } }
        },
      }),
    },

    // SLIDING WINDOW. Runs every turn. If the assembled input exceeds WINDOW_INPUT_TOKENS, evict
    // the OLDEST non-system messages (persisting each as an episode) until back under budget.
    // System messages and the most recent turns always stay. This replaces opencode's compaction
    // (which must be disabled via compaction.auto=false in opencode.json) with lossless sliding.
    "experimental.chat.messages.transform": async (input, output) => {
      const msgs = output?.messages
      if (!Array.isArray(msgs) || !msgs.length) return
      const thread = threadOf(input)

      const textOf = (m) =>
        (m?.parts || []).map((p) => (p?.type === "text" ? p.text : "")).join(" ")

      let total = msgs.reduce((n, m) => n + estTokens(textOf(m)), 0)
      if (total <= WINDOW_INPUT_TOKENS) return

      // Never evict system messages or the last 6 turns (keep immediate coherence).
      const isSystem = (m) => (m?.info?.role || m?.role) === "system"
      const keepTailFrom = Math.max(0, msgs.length - 6)
      const evicted = []
      for (let i = 0; i < msgs.length && total > WINDOW_INPUT_TOKENS; i++) {
        if (i >= keepTailFrom) break
        if (isSystem(msgs[i])) continue
        const t = textOf(msgs[i])
        if (!t) continue
        evicted.push({ idx: i, role: msgs[i]?.info?.role || msgs[i]?.role || "", text: t })
        total -= estTokens(t)
      }
      if (!evicted.length) return

      // Persist evicted turns losslessly, then drop them from the outgoing array.
      for (const e of evicted) {
        await writeEpisode({
          thread, session: input?.sessionID || "", agent: input?.agent || "",
          role: e.role, content: e.text, recentTtl: RECENT_TTL,
        })
      }
      const drop = new Set(evicted.map((e) => e.idx))
      output.messages = msgs.filter((_, i) => !drop.has(i))
    },

    // Opportunistically drain the write-stream into SQLite as the session runs, so recall is
    // always current without a separate daemon.
    "experimental.text.complete": async () => { try { await drain() } catch { /* soft */ } },
  }
}
