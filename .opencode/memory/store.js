// Durable episode store: SQLite (source of truth) + FTS5 recall. An episode is one raw, non-lossy
// unit of memory (a turn or a memory_write), keyed by thread (the conversation's root session id)
// so recall is scoped per conversation. No external dep.

import { mkdirSync } from "node:fs"
import { dirname } from "node:path"

// opencode's runtime is Bun (bun:sqlite); tests run under Node (node:sqlite). Same
// prepare/run/all/exec/close surface, so only the constructor differs.
async function openDatabase(path) {
  if (typeof globalThis.Bun !== "undefined") {
    const { Database } = await import("bun:sqlite")
    return new Database(path, { create: true })
  }
  const { DatabaseSync } = await import("node:sqlite")
  return new DatabaseSync(path)
}

let db = null

export async function open(path) {
  if (db) return db
  mkdirSync(dirname(path), { recursive: true })
  db = await openDatabase(path)
  db.exec("PRAGMA journal_mode = WAL;")           // concurrent readers + one writer
  db.exec("PRAGMA synchronous = NORMAL;")
  db.exec(`
    CREATE TABLE IF NOT EXISTS episodes (
      id      INTEGER PRIMARY KEY AUTOINCREMENT,
      thread  TEXT NOT NULL,
      session TEXT NOT NULL,
      agent   TEXT,
      role    TEXT,
      content TEXT NOT NULL,
      ts      INTEGER NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_episodes_thread ON episodes(thread, id);
    CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(
      content, thread UNINDEXED, content='episodes', content_rowid='id'
    );
    CREATE TRIGGER IF NOT EXISTS episodes_ai AFTER INSERT ON episodes BEGIN
      INSERT INTO episodes_fts(rowid, content, thread) VALUES (new.id, new.content, new.thread);
    END;
    CREATE TRIGGER IF NOT EXISTS episodes_ad AFTER DELETE ON episodes BEGIN
      INSERT INTO episodes_fts(episodes_fts, rowid, content, thread) VALUES('delete', old.id, old.content, old.thread);
    END;
  `)
  return db
}

// Append an episode. Returns its id.
export function append({ thread, session, agent, role, content, ts }) {
  const d = db
  const stmt = d.prepare(
    "INSERT INTO episodes (thread, session, agent, role, content, ts) VALUES (?, ?, ?, ?, ?, ?)"
  )
  const info = stmt.run(thread, session, agent ?? null, role ?? null, content, ts ?? Date.now())
  return info.lastInsertRowid
}

// FTS5 recall within a thread. Returns top-k episodes ranked by BM25 (best first).
// The query is tokenized to alphanumeric terms, each quoted, joined with OR -- so recall matches
// on any term (what you want for "find related past"), not an exact phrase, and arbitrary
// punctuation can't produce an FTS syntax error. Falls back to a LIKE scan if FTS yields nothing.
export function search({ thread, query, k = 10 }) {
  const d = db
  const terms = String(query).toLowerCase().match(/[a-z0-9]+/g) || []
  if (terms.length) {
    const match = terms.map((t) => `"${t}"`).join(" OR ")
    try {
      const rows = d.prepare(`
        SELECT e.id, e.agent, e.role, e.content, e.ts
        FROM episodes_fts f JOIN episodes e ON e.id = f.rowid
        WHERE f.thread = ? AND episodes_fts MATCH ?
        ORDER BY bm25(episodes_fts) LIMIT ?
      `).all(thread, match, k)
      if (rows.length) return rows
    } catch { /* fall through to LIKE */ }
  }
  return d.prepare(`
    SELECT id, agent, role, content, ts FROM episodes
    WHERE thread = ? AND content LIKE ? ORDER BY id DESC LIMIT ?
  `).all(thread, `%${query}%`, k)
}

// Most recent N episodes in a thread (for rewarming Redis or a plain tail).
export function recent({ thread, k = 20 }) {
  return db.prepare(
    "SELECT id, agent, role, content, ts FROM episodes WHERE thread = ? ORDER BY id DESC LIMIT ?"
  ).all(thread, k).reverse()
}

export function close() {
  if (db) { db.close(); db = null }
}
