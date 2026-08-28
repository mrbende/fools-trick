// The message-array seam: opencode's shape <-> our neutral turns, and applying an eviction
// decision back onto opencode's live array. Everything genuinely opencode-specific lives here
// (the empty-input transform, the state.time.compacted flag, message-array mutation). See
// docs/harness-design.md 3.5.
//
// An opencode message: { info:{role,agent,sessionID}, parts:[{type,...}] }
//   text part: { type:"text", text }
//   reasoning part: { type:"reasoning", text }
//   tool part: { type:"tool", callID, state:{ status, output, time:{ compacted? } } }

const est = (s) => Math.ceil((s ? String(s).length : 0) / 3.5)

export function agentOf(msgs) {
  for (let i = msgs.length - 1; i >= 0; i--) if (msgs[i]?.info?.agent) return msgs[i].info.agent
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
// evicted parts (so the caller can persist them to the Event Log before/with eviction).
export function applyEvict(msgs, evictCallIDs) {
  const set = new Set(evictCallIDs)
  const evicted = []
  for (const m of msgs)
    for (const p of m?.parts || [])
      if (p?.type === "tool" && set.has(p.callID) && !p.state?.time?.compacted) {
        p.state = p.state || {}
        p.state.time = p.state.time || {}
        p.state.time.compacted = Date.now()
        evicted.push({ callID: p.callID, output: p.state.output })
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

// The orchestrator sliding view: text turns only (the slide operates on raw turn text).
export function toOrchestratorTurns(msgs) {
  return msgs.map((m) => ({
    role: m?.info?.role || m?.role || "",
    agent: m?.info?.agent || "",
    session: m?.info?.sessionID || "",
    text: (m?.parts || []).filter((p) => p?.type === "text").map((p) => p.text).join(" "),
    reasoning: (m?.parts || []).filter((p) => p?.type === "reasoning").map((p) => p.text).join(" "),
  }))
}

