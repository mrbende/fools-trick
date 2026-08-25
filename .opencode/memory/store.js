// Durable episode store: SQLite (source of truth) with FTS5 full-text recall.
// An EPISODE is one raw, non-lossy unit of conversation memory (a message/turn or an
// explicit memory_write), keyed by conversation THREAD so recall is scoped per conversation.
// Node 25 ships node:sqlite; no external dependency.
//
// Schema:
//   episodes(id, thread, session, agent, role, content, ts)   -- raw episodes, thread-scoped
//   episodes_fts                                               -- FTS5 mirror over content (BM25)
// thread   = the conversation's root session id (stable across a conversation's child sessions)
// session  = the specific opencode sessionID that wrote it (orchestrator or a subagent)
// agent    = which agent wrote it (build/explore/general/reviewer/...)

import { DatabaseSync } from "node:sqlite"
import { mkdirSync } from "node:fs"
import { dirname } from "node:path"

let db = null

export function open(path) {
  if (db) return db
  mkdirSync(dirname(path), { recursive: true })
  db = new DatabaseSync(path)
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
