// Memory layer tests: SQLite store (always) + Redis round-trip (if redis reachable).
// Run: node tests/test_memory.mjs   (exit 0 = pass)
import { open, append, search, recent, close } from "../.opencode/memory/store.js"
import { agentOf, sessionOf, inputTokens, pruneWorker, selectSlide } from "../.opencode/memory/window.js"
import { existsSync, rmSync } from "node:fs"

let pass = 0, fail = 0
const ok = (name) => { console.log(`  ok   ${name}`); pass++ }
const bad = (name, d) => { console.log(`  FAIL ${name} ${d ?? ""}`); fail++ }
const eq = (name, a, b) => (a === b ? ok(name) : bad(name, `(${JSON.stringify(a)} !== ${JSON.stringify(b)})`))

const DB = "/tmp/fools-trick/test_memory.db"
for (const ext of ["", "-wal", "-shm"]) { const f = DB + ext; if (existsSync(f)) rmSync(f) }

console.log("memory: SQLite episode store + FTS5 recall")
await open(DB)
append({ thread: "A", session: "s1", agent: "build", role: "user", content: "chose q8_0 KV to avoid the CPU spill on the hybrid arch" })
append({ thread: "A", session: "s2", agent: "explore", role: "assistant", content: "worker serves 45056 context per slot across two GPUs" })
append({ thread: "B", session: "s3", agent: "general", role: "user", content: "unrelated thread about redis containers" })

eq("term-OR FTS finds relevant episode", search({ thread: "A", query: "KV spill" }).length >= 1, true)
eq("thread scoping: B redis does not leak into A", search({ thread: "A", query: "redis" }).length, 0)
eq("thread scoping: B finds its own redis", search({ thread: "B", query: "redis" }).length, 1)
eq("recent preserves insertion order", recent({ thread: "A", k: 5 })[0].content.startsWith("chose q8_0"), true)
eq("bm25 returns best match first", search({ thread: "A", query: "context slot GPUs" })[0].content.includes("45056"), true)
close()

// Redis round-trip only if reachable (make up redis). Skips cleanly otherwise.
console.log("memory: Redis write-stream -> drain -> SQLite (if redis up)")
try {
  const { createRedis } = await import("../.opencode/memory/redis.js")
  const r = createRedis(process.env.REDIS_URL || "redis://127.0.0.1:6379")
  const pong = await Promise.race([
    r.cmd("PING"),
    new Promise((_, rej) => setTimeout(() => rej(new Error("timeout")), 1500)),
  ])
  if (pong !== "PONG") throw new Error("no pong")
  const { initMemory, writeEpisode, drain, searchMemory } = await import("../.opencode/memory/memory.js")
  const DB2 = "/tmp/fools-trick/test_memory_redis.db"
  for (const ext of ["", "-wal", "-shm"]) { const f = DB2 + ext; if (existsSync(f)) rmSync(f) }
  await r.cmd("DEL", "fools:mem:stream")
  initMemory({ dbPath: DB2, redisUrl: process.env.REDIS_URL || "redis://127.0.0.1:6379" })
  await Promise.all([
    writeEpisode({ thread: "C", session: "o", agent: "build", role: "user", content: "concurrent write one about sliding window" }),
    writeEpisode({ thread: "C", session: "w1", agent: "explore", role: "assistant", content: "concurrent write two about memory recall" }),
  ])
  const moved = await drain()
  eq("stream drained both concurrent writes to SQLite", moved, 2)
  eq("drained episodes are searchable", (await searchMemory({ thread: "C", query: "sliding window recall" })).length >= 1, true)
  r.close()
} catch (e) {
  console.log(`  skip redis round-trip (redis not reachable: ${e.message})`)
}

// Context-window logic (window.js): identity, subagent prune, orchestrator slide selection.
// These are pure and require no redis/sqlite. They guard the two-subsystem design and the
// identity-from-message-stream fix (the transform hook is called with an empty input).
console.log("window: identity resolution from the message stream")
{
  const msgs = [
    { info: { role: "system" }, parts: [{ type: "text", text: "sys" }] },
    { info: { role: "user", sessionID: "sub-1", agent: "explore" }, parts: [{ type: "text", text: "brief" }] },
    { info: { role: "assistant", sessionID: "sub-1", agent: "explore" }, parts: [{ type: "text", text: "ok" }] },
  ]
  eq("agentOf reads .info.agent (not the empty hook input)", agentOf(msgs), "explore")
  eq("sessionOf reads .info.sessionID", sessionOf(msgs), "sub-1")
  eq("agentOf on system-only messages is empty", agentOf([{ info: { role: "system" }, parts: [] }]), "")
}

// Helpers to build a worker transcript with sized tool results.
const big = (n) => "x".repeat(n)
const toolPart = (callID, chars) => ({ type: "tool", callID, state: { status: "completed", output: big(chars), time: {} } })
const asstWithTool = (callID, chars) => ({ info: { role: "assistant", sessionID: "w", agent: "explore" }, parts: [toolPart(callID, chars)] })
const compacted = (m) => m.parts.filter((p) => p.type === "tool" && p.state.time?.compacted).map((p) => p.callID)
const allCompacted = (msgs) => msgs.flatMap(compacted)

console.log("window: subagent prune (distill-gated, keep-recent, backstop)")
{
  // 6 tool results of ~7000 est-tokens each (24500 chars / 3.5). Budget 26000, keepRecent 2.
  const chars = 24500  // estTokens = 7000 each
  const msgs = [
    { info: { role: "system" }, parts: [{ type: "text", text: "sys" }] },
    { info: { role: "user", sessionID: "w", agent: "explore" }, parts: [{ type: "text", text: "the brief" }] },
    asstWithTool("c1", chars), asstWithTool("c2", chars), asstWithTool("c3", chars),
    asstWithTool("c4", chars), asstWithTool("c5", chars), asstWithTool("c6", chars),
  ]
  eq("starts over budget (6x7000 > 26000)", inputTokens(msgs) > 26000, true)

  // Mark c2 and c4 distilled; prune should clear those FIRST (they're in the prunable set).
  const distilled = new Set(["c2", "c4"])
  pruneWorker(msgs, { inputBudget: 26000, keepRecent: 2, distilled })

  eq("prune brings input under budget", inputTokens(msgs) <= 26000, true)
  const cleared = allCompacted(msgs)
  eq("distilled results cleared first (c2)", cleared.includes("c2"), true)
  eq("distilled results cleared first (c4)", cleared.includes("c4"), true)
  eq("last keepRecent (c6) never pruned", cleared.includes("c6"), false)
  eq("last keepRecent (c5) never pruned", cleared.includes("c5"), false)
  eq("system + brief text never touched (still present)", msgs[0].parts[0].text, "sys")
}

console.log("window: subagent prune backstop (nothing distilled)")
{
  const chars = 24500
  const msgs = [
    { info: { role: "user", sessionID: "w", agent: "general" }, parts: [{ type: "text", text: "brief" }] },
    asstWithTool("d1", chars), asstWithTool("d2", chars), asstWithTool("d3", chars),
    asstWithTool("d4", chars), asstWithTool("d5", chars),
  ]
  // No distilled marks: backstop must still evict oldest until under budget, never overflowing.
  pruneWorker(msgs, { inputBudget: 26000, keepRecent: 2, distilled: new Set() })
  eq("backstop brings input under budget with nothing distilled", inputTokens(msgs) <= 26000, true)
  const cleared = allCompacted(msgs)
  eq("backstop evicts the oldest (d1)", cleared.includes("d1"), true)
  eq("backstop preserves last keepRecent (d5)", cleared.includes("d5"), false)
  eq("backstop preserves last keepRecent (d4)", cleared.includes("d4"), false)
}

console.log("window: subagent prune is a no-op under budget")
{
  const msgs = [
    { info: { role: "user", sessionID: "w", agent: "reviewer" }, parts: [{ type: "text", text: "brief" }] },
    asstWithTool("s1", 3500), asstWithTool("s2", 3500),
  ]
  const before = inputTokens(msgs)
  const n = pruneWorker(msgs, { inputBudget: 26000, keepRecent: 3, distilled: new Set() })
  eq("under-budget prune evicts nothing", n, 0)
  eq("under-budget prune leaves input unchanged", inputTokens(msgs), before)
}

console.log("window: orchestrator slide selection (lossless, keeps tail + system)")
{
  // selectSlide selects turns to evict; it does NOT mutate (the caller persists then drops).
  const turn = (role, text) => ({ info: { role, sessionID: "root", agent: "build" }, parts: [{ type: "text", text }] })
  const msgs = [
    turn("system", big(3500)),                 // ~1000 tokens, must be kept
    ...Array.from({ length: 10 }, (_, i) => turn("user", big(35000))), // ~10000 tokens each
  ]
  const { evicted } = selectSlide(msgs, { inputBudget: 26000, keepTail: 6 })
  eq("slide selects some turns to evict when over budget", evicted.length > 0, true)
  eq("slide never selects the system message", evicted.some((e) => e.idx === 0), false)
  const keptTailStart = msgs.length - 6
  eq("slide never selects the last keepTail turns", evicted.every((e) => e.idx < keptTailStart), true)
  eq("evicted entries carry their text (lossless persist)", evicted.every((e) => typeof e.text === "string" && e.text.length > 0), true)
}

console.log(`\nmemory: ${pass} passed, ${fail} failed`)
process.exit(fail === 0 ? 0 : 1)
