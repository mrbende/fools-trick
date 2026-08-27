// Coordinates the durable store (SQLite) and the hot tier (Redis) into one memory API.
//
//   write:  writeEpisode -> Redis stream (XADD) -> drain() -> SQLite. Redis down -> SQLite directly.
//   read:   searchMemory -> SQLite FTS5 (BM25);  recentEpisodes -> Redis cache, else SQLite tail.
//
// The stream is the serialization point: many workers XADD concurrently, one drain() consumer moves
// them into SQLite in order. That many-writer safety is why this is a datastore, not markdown files.

import * as store from "./store.js"
import { createRedis } from "./redis.js"
export { resolveThread } from "./thread.js"

const STREAM = "fools:mem:stream"
const GROUP = "fools:mem:drain"
const recentKey = (thread) => `fools:mem:recent:${thread}`

let redis = null

// store.open is async (runtime-adaptive sqlite import); hold the promise so concurrent callers await
// one open, and every op awaits ensureDb() first.
let _opening = null
export function initMemory({ dbPath, redisUrl }) {
  if (!_opening) _opening = store.open(dbPath)
  if (!redis) redis = createRedis(redisUrl)
  return { redis, store }
}
async function ensureDb() { if (_opening) await _opening }

// Durability lives in the XADD; the recent-cache (LPUSH/LTRIM/EXPIRE) is best-effort. The SQLite
// fallback must fire ONLY when XADD fails: if XADD succeeded, the episode is already in the stream,
// and a fallback append would duplicate it when drain() runs. So the cache writes are in a separate
// try that never reaches the fallback.
export async function writeEpisode(ep) {
  const rec = {
    thread: ep.thread, session: ep.session || "", agent: ep.agent || "",
    role: ep.role || "", content: ep.content || "", ts: String(ep.ts || Date.now()),
  }
  try {
    await redis.cmd("XADD", STREAM, "*",
      "thread", rec.thread, "session", rec.session, "agent", rec.agent,
      "role", rec.role, "content", rec.content, "ts", rec.ts)
  } catch {
    await ensureDb()
    store.append({ ...rec, ts: Number(rec.ts) })
    return { queued: false, persisted: true }
  }
  try {
    await redis.cmd("LPUSH", recentKey(rec.thread), JSON.stringify(rec))
    await redis.cmd("LTRIM", recentKey(rec.thread), "0", "49")
    await redis.cmd("EXPIRE", recentKey(rec.thread), String(ep.recentTtl || 3600))
  } catch { /* cache is optional; the stream entry is the source of truth */ }
  return { queued: true }
}

// Move stream entries into SQLite. A consumer group delivers each entry once; ack + delete after
// append. Called opportunistically from the plugin, so recall stays current without a daemon.
export async function drain(limit = 500) {
  await ensureDb()
  await redis.cmd("XGROUP", "CREATE", STREAM, GROUP, "0", "MKSTREAM").catch(() => {})
  let moved = 0
  try {
    const res = await redis.cmd("XREADGROUP", "GROUP", GROUP, "drainer", "COUNT", String(limit), "STREAMS", STREAM, ">")
    if (!res) return 0
    for (const [, entries] of res) {
      for (const [id, fields] of entries) {
        const f = {}
        for (let i = 0; i < fields.length; i += 2) f[fields[i]] = fields[i + 1]
        store.append({ ...f, ts: Number(f.ts) || Date.now() })
        await redis.cmd("XACK", STREAM, GROUP, id)
        await redis.cmd("XDEL", STREAM, id)
        moved++
      }
    }
  } catch { /* redis down: nothing to drain, writes went straight to SQLite */ }
  return moved
}

export async function searchMemory({ thread, query, k = 10 }) {
  await drain()   // so just-written episodes are searchable
  await ensureDb()
  return store.search({ thread, query, k })
}

export async function recentEpisodes({ thread, k = 20 }) {
  try {
    const cached = await redis.cmd("LRANGE", recentKey(thread), "0", String(k - 1))
    if (cached && cached.length) return cached.map((s) => JSON.parse(s)).reverse()
  } catch { /* fall through to SQLite */ }
  await drain()
  await ensureDb()
  return store.recent({ thread, k })
}
