// Pure message-array logic for both context subsystems, dependency-free so the plugin and the tests
// share the same code. A message is opencode's transform-hook shape:
//   { info: {role, agent, sessionID}, parts: [{type, ...}] }
//   tool part: { type:"tool", callID, state:{status, output, time:{compacted?}} }

// ~3.5 chars/token; deliberately low so the estimate erring under still leaves decode headroom.
export const estTokens = (s) => Math.ceil((s ? String(s).length : 0) / 3.5)

export const textOf = (m) =>
  (m?.parts || []).map((p) => (p?.type === "text" ? p.text : "")).join(" ")

// Identity from the message stream. opencode calls experimental.chat.messages.transform with an
// EMPTY input, so agent/sessionID must come from the messages' own info, not the hook input.
export function agentOf(msgs) {
  for (let i = msgs.length - 1; i >= 0; i--) {
    const a = msgs[i]?.info?.agent
    if (a) return a
  }
  return ""
}
export function sessionOf(msgs) {
  for (let i = msgs.length - 1; i >= 0; i--) {
    const s = msgs[i]?.info?.sessionID
    if (s) return s
  }
  return ""
}

// Total estimated input tokens: text parts + live (not-yet-cleared) completed tool outputs. A
// compacted tool result is sent as a short placeholder, so it no longer counts toward the budget.
export function inputTokens(msgs) {
  let n = 0
  for (const m of msgs) {
    for (const p of m?.parts || []) {
      if (p?.type === "text") n += estTokens(p.text)
      else if (p?.type === "tool" && p?.state?.status === "completed" && !p.state.time?.compacted) {
        n += estTokens(p.state.output)
      }
    }
  }
  return n
}

// Mark a completed tool part evicted (state.time.compacted); opencode then sends the cleared-content
// placeholder for it. Returns tokens reclaimed. Never touches non-tool parts, so reasoning/text and
// system/user turns are structurally safe from eviction.
export function evict(part) {
  if (part?.type !== "tool" || part?.state?.status !== "completed" || part.state.time?.compacted) return 0
  const reclaimed = estTokens(part.state.output)
  part.state.time = part.state.time || {}
  part.state.time.compacted = Date.now()
  return reclaimed
}

// SUBAGENT distill-gated prune. Keep worker input under inputBudget by evicting tool results only,
// preserving the head, all reasoning/text, and the last keepRecent tool results. Two passes:
//   1. distilled-first: clear results whose callID is in `distilled` (worker recorded a note).
//   2. backstop: clear the oldest remaining prunable results until under budget.
// Mutates msgs in place (sets compacted flags). Returns the number of parts evicted.
export function pruneWorker(msgs, { inputBudget, keepRecent = 3, distilled = new Set() } = {}) {
  if (!Array.isArray(msgs) || inputTokens(msgs) <= inputBudget) return 0

  const tools = []
  for (const mm of msgs) {
    for (const p of mm?.parts || []) {
      if (p?.type === "tool" && p?.state?.status === "completed" && !p.state.time?.compacted) tools.push(p)
    }
  }
  const prunable = tools.slice(0, Math.max(0, tools.length - keepRecent))

  let budget = inputTokens(msgs)
  let count = 0
  for (const p of prunable) {
    if (budget <= inputBudget) break
    if (distilled.has(p.callID)) { budget -= evict(p); count++ }
  }
  for (const p of prunable) {
    if (budget <= inputBudget) break
    if (!p.state.time?.compacted) { budget -= evict(p); count++ }
  }
  return count
}

// ORCHESTRATOR sliding window. Select the oldest raw non-system turns to evict past inputBudget,
// keeping the last keepTail turns. Returns { evicted: [{idx, role, text}], keep: Set<idx> } WITHOUT
// mutating msgs -- the caller persists the evicted turns (lossless) before dropping them.
export function selectSlide(msgs, { inputBudget, keepTail = 6 } = {}) {
  let total = msgs.reduce((n, m) => n + estTokens(textOf(m)), 0)
  const evicted = []
  if (total <= inputBudget) return { evicted, total }

  const isSystem = (m) => (m?.info?.role || m?.role) === "system"
  const keepTailFrom = Math.max(0, msgs.length - keepTail)
  for (let i = 0; i < msgs.length && total > inputBudget; i++) {
    if (i >= keepTailFrom) break
    if (isSystem(msgs[i])) continue
    const t = textOf(msgs[i])
    if (!t) continue
    evicted.push({ idx: i, role: msgs[i]?.info?.role || msgs[i]?.role || "", text: t })
    total -= estTokens(t)
  }
  return { evicted, total }
}
