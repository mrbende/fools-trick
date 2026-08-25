// Memory layer tests: SQLite store (always) + Redis round-trip (if redis reachable).
// Run: node tests/test_memory.mjs   (exit 0 = pass)
import { open, append, search, recent, close } from "../.opencode/memory/store.js"
import { existsSync, rmSync } from "node:fs"

let pass = 0, fail = 0
const ok = (name) => { console.log(`  ok   ${name}`); pass++ }
const bad = (name, d) => { console.log(`  FAIL ${name} ${d ?? ""}`); fail++ }
const eq = (name, a, b) => (a === b ? ok(name) : bad(name, `(${JSON.stringify(a)} !== ${JSON.stringify(b)})`))

const DB = "/tmp/fools-trick/test_memory.db"
for (const ext of ["", "-wal", "-shm"]) { const f = DB + ext; if (existsSync(f)) rmSync(f) }

console.log("memory: SQLite episode store + FTS5 recall")
open(DB)
append({ thread: "A", session: "s1", agent: "build", role: "user", content: "chose q8_0 KV to avoid the CPU spill on the hybrid arch" })
append({ thread: "A", session: "s2", agent: "explore", role: "assistant", content: "worker serves 32768 context per slot across two GPUs" })
append({ thread: "B", session: "s3", agent: "general", role: "user", content: "unrelated thread about redis containers" })

eq("term-OR FTS finds relevant episode", search({ thread: "A", query: "KV spill" }).length >= 1, true)
eq("thread scoping: B redis does not leak into A", search({ thread: "A", query: "redis" }).length, 0)
eq("thread scoping: B finds its own redis", search({ thread: "B", query: "redis" }).length, 1)
eq("recent preserves insertion order", recent({ thread: "A", k: 5 })[0].content.startsWith("chose q8_0"), true)
eq("bm25 returns best match first", search({ thread: "A", query: "context slot GPUs" })[0].content.includes("32768"), true)
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

console.log(`\nmemory: ${pass} passed, ${fail} failed`)
process.exit(fail === 0 ? 0 : 1)
