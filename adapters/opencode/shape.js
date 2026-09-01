// The message-array seam: opencode's shape <-> our neutral turns, and applying an eviction
// decision back onto opencode's live array. Everything genuinely opencode-specific lives here
// (the empty-input transform, the state.time.compacted flag, message-array mutation). See
// docs/harness-design.md 3.5.
//
// An opencode message: { info:{role,agent,sessionID}, parts:[{type,...}] }
//   text part: { type:"text", text }
//   reasoning part: { type:"reasoning", text }
//   tool part: { type:"tool", callID, state:{ status, output, time:{ compacted? } } }

import { cfgNum } from "./bridge.js"

// The token estimator's divisor. Single source of truth is config.chars_per_token (the Python core's
// estimator reads the same value); no mirrored constant. Conservative on markup, errs OVER -- this
// estimate gates the worker slot, a hard wall.
const est = (s) => Math.ceil((s ? String(s).length : 0) / cfgNum("CHARS_PER_TOKEN", "chars_per_token", 2.5))

export function agentOf(msgs) {
  for (let i = msgs.length - 1; i >= 0; i--) if (msgs[i]?.info?.agent) return msgs[i].info.agent
  return ""
}

// The provider a turn runs on. A subagent's messages inherit the PARENT agent name (a worker turn
// reads agent=build), so agent-based routing misfires -- the provider is the reliable tier signal.
export function providerOf(msgs) {
  for (let i = msgs.length - 1; i >= 0; i--) {
    const p = msgs[i]?.info?.model?.providerID ?? msgs[i]?.info?.providerID ?? msgs[i]?.model?.providerID
    if (p) return p
  }
  return ""
}
export function sessionOf(msgs) {
  for (let i = msgs.length - 1; i >= 0; i--) if (msgs[i]?.info?.sessionID) return msgs[i].info.sessionID
  return ""
}

// Live (uncompacted, completed) tool parts across the array, in stream order, with their host msg.
export function liveToolParts(msgs) {
  const out = []
  for (const m of msgs)
    for (const p of m?.parts || [])
      if (p?.type === "tool" && p?.state?.status === "completed" && !p.state.time?.compacted)
        out.push(p)
  return out
}

// Total live input tokens (text + reasoning + uncompacted completed tool outputs).
export function inputTokens(msgs) {
  let n = 0
  for (const m of msgs)
    for (const p of m?.parts || []) {
      if (p?.type === "text") n += est(p.text)
      else if (p?.type === "reasoning") n += est(p.text)
      else if (p?.type === "tool" && p?.state?.status === "completed" && !p.state.time?.compacted)
        n += est(p.state.output)
    }
  return n
}

// Apply the core's worker-prune decision: compact the named tool results in place. Returns the
// evicted parts with their message/part refs (so the caller can persist the output by seq and index
// the recovery pointer onto the part it evicted).
export function applyEvict(msgs, evictCallIDs) {
  const set = new Set(evictCallIDs)
  const evicted = []
  for (const m of msgs)
    for (const p of m?.parts || [])
      if (p?.type === "tool" && set.has(p.callID) && !p.state?.time?.compacted) {
        p.state = p.state || {}
        p.state.time = p.state.time || {}
        p.state.time.compacted = Date.now()
        evicted.push({ callID: p.callID, output: p.state.output, msg: m, part: p })
      }
  return evicted
}

// Build the neutral worker view the core prunes over: one Turn per message, tool results mapped.
// Kept minimal -- the core only needs call_id/text/compacted and role/text for estimation.
export function toWorkerTurns(msgs) {
  return msgs.map((m) => ({
    role: m?.info?.role || "",
    text: (m?.parts || []).filter((p) => p?.type === "text").map((p) => p.text).join(" "),
    reasoning: (m?.parts || []).filter((p) => p?.type === "reasoning").map((p) => p.text).join(" "),
    tool_results: (m?.parts || [])
      .filter((p) => p?.type === "tool" && p?.state?.status === "completed")
      .map((p) => ({ call_id: p.callID, text: p.state.output || "", compacted: !!p.state.time?.compacted })),
  }))
}

  // The orchestrator sliding view. The slide's input budget must see the WHOLE context, including
  // tool results (reads, library, web) -- they are the bulk of a real session. Dropping them makes
  // the slide undercount and never fire (the 800k-hang bug). Tool outputs are folded into the turn's
  // text for accounting; the slide still drops whole turns, not individual results.
  export function toOrchestratorTurns(msgs) {
    return msgs.map((m) => {
      const parts = m?.parts || []
      const text = parts.filter((p) => p?.type === "text").map((p) => p.text).join(" ")
      const reasoning = parts.filter((p) => p?.type === "reasoning").map((p) => p.text).join(" ")
      // tool result bulk counts toward the window so the slide's budget reflects reality
      const toolText = parts.filter((p) => p?.type === "tool" && p?.state?.status === "completed")
        .map((p) => p.state.output || "").join(" ")
      return {
        role: m?.info?.role || m?.role || "",
        agent: m?.info?.agent || "",
        session: m?.info?.sessionID || "",
        text: toolText ? `${text} ${toolText}` : text,
        reasoning,
      }
    })
  }

