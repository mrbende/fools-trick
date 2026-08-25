// Memory orchestration: ties the durable store (SQLite) and the hot tier (Redis) together.
//
// Write path (many concurrent agents, contention-free):
//   agent -> writeEpisode() -> XADD to Redis stream  -> drain() consumer -> SQLite append
//   If Redis is down, writeEpisode falls back to writing SQLite directly (durability first).
// Read path:
//   searchMemory() -> SQLite FTS5 (BM25), thread-scoped
//   recentEpisodes() -> Redis recent-cache if warm, else SQLite tail (and rewarm)
//
// The stream is the serialization point: concurrent workers push, a single drain() consumer
// (run by the orchestrator's plugin on a timer) moves them into SQLite in order. This is the
// many-writer safety markdown files can't give us.

import * as store from "./store.js"
import { createRedis } from "./redis.js"

const STREAM = "fools:mem:stream"
const GROUP = "fools:mem:drain"
const recentKey = (thread) => `fools:mem:recent:${thread}`

let redis = null
let dbReady = false

export function initMemory({ dbPath, redisUrl, recentTtl = 3600 }) {
  if (!dbReady) { store.open(dbPath); dbReady = true }
  if (!redis) redis = createRedis(redisUrl)
  return { redis, store }
}

// Append an episode. Prefer the Redis stream (serialized, drained to SQLite); on any Redis
// failure, write SQLite directly so a memory is NEVER lost when the hot tier is down.
export async function writeEpisode(ep) {
  const rec = {
    thread: ep.thread, session: ep.session || "", agent: ep.agent || "",
    role: ep.role || "", content: ep.content || "", ts: String(ep.ts || Date.now()),
  }
  try {
    await redis.cmd("XADD", STREAM, "*",
      "thread", rec.thread, "session", rec.session, "agent", rec.agent,
      "role", rec.role, "content", rec.content, "ts", rec.ts)
    // keep a small hot recent-cache per thread for fast tails
    await redis.cmd("LPUSH", recentKey(rec.thread), JSON.stringify(rec))
    await redis.cmd("LTRIM", recentKey(rec.thread), "0", "49")
    await redis.cmd("EXPIRE", recentKey(rec.thread), String(ep.recentTtl || 3600))
    return { queued: true }
  } catch {
    store.append({ ...rec, ts: Number(rec.ts) })   // durability-first fallback
    return { queued: false, persisted: true }
  }
}

// Drain the Redis stream into SQLite. Idempotent-ish: uses a consumer group so each entry is
// delivered once; ack after append. Call periodically from the orchestrator plugin.
export async function drain(limit = 500) {
  try {
    await redis.cmd("XGROUP", "CREATE", STREAM, GROUP, "0", "MKSTREAM").catch(() => {})
  } catch { /* group may already exist */ }
  let moved = 0
  try {
    const res = await redis.cmd("XREADGROUP", "GROUP", GROUP, "drainer", "COUNT", String(limit), "STREAMS", STREAM, ">")
    if (!res) return 0
    for (const [, entries] of res) {
      for (const [id, fields] of entries) {
        const f = {}
        for (let i = 0; i < fields.length; i += 2) f[fields[i]] = fields[i + 1]
        store.append({
          thread: f.thread, session: f.session, agent: f.agent,
          role: f.role, content: f.content, ts: Number(f.ts) || Date.now(),
        })
        await redis.cmd("XACK", STREAM, GROUP, id)
        await redis.cmd("XDEL", STREAM, id)
        moved++
      }
    }
  } catch { /* redis down: nothing to drain, writes went straight to SQLite */ }
  return moved
}

// Thread-scoped FTS recall. Drains first so just-written episodes are searchable.
export async function searchMemory({ thread, query, k = 10 }) {
  await drain()
  return store.search({ thread, query, k })
}

export async function recentEpisodes({ thread, k = 20 }) {
  try {
    const cached = await redis.cmd("LRANGE", recentKey(thread), "0", String(k - 1))
    if (cached && cached.length) return cached.map((s) => JSON.parse(s)).reverse()
  } catch { /* fall through */ }
  await drain()
  return store.recent({ thread, k })
}

// Format episodes into a compact context block (Zep-style: few tokens, structured).
export function formatEpisodes(eps) {
  if (!eps || !eps.length) return ""
  const lines = eps.map((e) => {
    const who = e.agent ? `${e.role}/${e.agent}` : e.role || "?"
    return `- [${who}] ${e.content}`
  })
  return `<recalled_memory>\n${lines.join("\n")}\n</recalled_memory>`
}
