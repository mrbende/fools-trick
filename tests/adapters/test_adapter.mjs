// opencode adapter tests: the human-gate (policy from the Python core), the bridge boundary
// (JS -> Python core -> SQLite -> back), and the in-process worker-prune mechanics (option a,
// mirroring core/context/window.py). Run: node tests/adapters/test_adapter.mjs
//
// No opencode server needed; we exercise the pure adapter functions and the subprocess bridge.

import { fileURLToPath } from "node:url"
import { dirname, resolve } from "node:path"
import { mkdtempSync } from "node:fs"
import { tmpdir } from "node:os"

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, "..", "..")

let pass = 0, fail = 0
const ok = (n) => { console.log(`  ok   ${n}`); pass++ }
const bad = (n, d) => { console.log(`  FAIL ${n} ${d ?? ""}`); fail++ }
const eq = (n, a, b) => (a === b ? ok(n) : bad(n, `(${JSON.stringify(a)} !== ${JSON.stringify(b)})`))

// Force sqlite fallback + an isolated db so the test needs no Redis and no shared state.
const db = `${mkdtempSync(resolve(tmpdir(), "ft-adapter-"))}/m.db`
process.env.MEMORY_DB = db
process.env.REDIS_URL = "redis://127.0.0.1:6399"

console.log("adapter: human-gate (policy loaded from the Python core)")
{
  const gates = (await import("../../adapters/opencode/plugin_gates.js")).default
  const hooks = await gates()
  let blocked = false
  try { await hooks["tool.execute.before"]({ tool: "bash" }, { args: { command: "git push origin main" } }) }
  catch (e) { blocked = String(e.message).includes("human-gate") }
  eq("blocks git push", blocked, true)
  let allowed = true
  try { await hooks["tool.execute.before"]({ tool: "bash" }, { args: { command: "make test" } }) } catch { allowed = false }
  eq("allows make test", allowed, true)
}

console.log("adapter: bridge boundary (JS -> Python core -> SQLite -> back)")
{
  const { callTool } = await import("../../adapters/opencode/bridge.js")
  await callTool("memory_write", { content: "adapter bridge roundtrip fact" }, { sessionID: "s1", agent: "build" })
  const r = await callTool("memory_search", { query: "roundtrip" }, { sessionID: "s1", agent: "build" })
  eq("bridge round-trip finds the written fact", (r.metadata?.hits ?? 0) >= 1, true)
  eq("output carries the fact", r.output.includes("roundtrip fact"), true)
}

console.log("adapter: prune decision comes from the Python core (no mirrored logic)")
{
  const { planContext } = await import("../../adapters/opencode/bridge.js")
  const { toWorkerTurns, applyEvict, liveToolParts } = await import("../../adapters/opencode/shape.js")
  // 4 tool results, one huge; keep_recent=1 protects only the newest.
  const toolMsg = (id, chars) => ({
    info: { role: "assistant", agent: "explore", sessionID: "w" },
    parts: [{ type: "tool", callID: id, state: { status: "completed", output: "x".repeat(chars), time: {} } }],
  })
  const msgs = [toolMsg("small", 7000), toolMsg("huge", 200000), toolMsg("mid", 40000), toolMsg("recent", 7000)]
  const d = planContext("prune", { turns: toWorkerTurns(msgs), inputBudget: 20000, keepRecent: 1, distilled: [], pinned: [] })
  eq("core returned a decision", d != null && d.changed, true)
  eq("core evicts the largest first", d.evict_call_ids.includes("huge"), true)
  eq("core protects the newest", d.evict_call_ids.includes("recent"), false)
  // apply the core's decision to the live array (the adapter's only in-process job)
  applyEvict(msgs, d.evict_call_ids)
  eq("huge is compacted after applyEvict", liveToolParts(msgs).some((p) => p.callID === "huge"), false)
  eq("recent stays live", liveToolParts(msgs).some((p) => p.callID === "recent"), true)
}

console.log("adapter: read-loop sensor blocks the Nth identical re-read, not a new range")
{
  const gates = (await import("../../adapters/opencode/plugin_gates.js")).default
  const h = await gates()
  const before = h["tool.execute.before"]
  let blocked = 0
  for (let i = 0; i < 4; i++) {
    try { await before({ tool: "read", sessionID: "w" }, { args: { filePath: "/x/f.py" } }) }
    catch (e) { if (String(e.message).includes("read-loop")) blocked++ }
  }
  eq("the 4th identical read is blocked", blocked, 1)
  // a different line range is a new window, not a loop -> not blocked
  let rangedBlocked = false
  try { await before({ tool: "read", sessionID: "w" }, { args: { filePath: "/x/f.py", offset: 100, limit: 50 } }) }
  catch { rangedBlocked = true }
  eq("a different line range is not blocked", rangedBlocked, false)
}

console.log("adapter: per-result cap spills an oversized read and leaves a seq pointer")
{
  const mem = (await import("../../adapters/opencode/plugin_memory.js")).default
  const hooks = await mem()
  const big = "y".repeat(60000) // ~60KB, over the 8000-token cap
  const output = { title: "read", output: big, metadata: {} }
  await hooks["tool.execute.after"]({ tool: "read", sessionID: "w", callID: "c9", args: {} }, output)
  eq("oversized output is truncated", output.output.length < big.length, true)
  eq("the note carries a recallable seq", /seq=\d+/.test(output.output), true)
  eq("the note points at a scratch file", /tool-c9\.txt/.test(output.output), true)
}

console.log(`\nadapter: ${pass} passed, ${fail} failed`)
process.exit(fail === 0 ? 0 : 1)
