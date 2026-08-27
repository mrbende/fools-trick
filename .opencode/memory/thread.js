// Resolve an opencode sessionID to its conversation thread: the root of the session tree, shared by
// the orchestrator and every subagent it spawns. Episodes key on this root so a worker's write and
// the orchestrator's read land under the same thread. opencode's plugin context exposes only the
// current sessionID, so we walk session.parent_id via `opencode db`. Parent chains are immutable, so
// each id is resolved once and cached. A DB failure degrades to per-session scoping, not an error.

import { execFile } from "node:child_process"

const _cache = new Map()

function parentOf(sessionID) {
  return new Promise((resolve) => {
    execFile("opencode", ["db", "--format", "json",
      `SELECT parent_id FROM session WHERE id = '${sessionID.replace(/'/g, "")}'`],
      { timeout: 8000 }, (err, stdout) => {
        if (err) return resolve(undefined)
        try {
          const rows = JSON.parse(stdout)
          resolve(rows?.[0]?.parent_id ?? null)   // null: this is a root. undefined: not found.
        } catch { resolve(undefined) }
      })
  })
}

export async function resolveThread(sessionID) {
  if (!sessionID) return "default"
  if (_cache.has(sessionID)) return _cache.get(sessionID)
  let cur = sessionID
  const chain = [cur]
  for (let hops = 0; hops < 16; hops++) {   // depth guard against cycles
    const parent = await parentOf(cur)
    if (parent === undefined) { cur = sessionID; break }   // lookup failed: fall back to self
    if (parent === null) break                             // reached root
    cur = parent; chain.push(cur)
  }
  for (const s of chain) _cache.set(s, cur)
  return cur
}
