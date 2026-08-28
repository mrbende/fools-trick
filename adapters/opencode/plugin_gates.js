// opencode adapter for the Gate Policy. The human-gate blocked patterns are the Python core's
// source of truth, loaded once at startup; the verify-state machine is small enough to run in
// the adapter. Policy is owned in core/gates/policy.py; this file is plumbing.

import { loadBlocked } from "./bridge.js"

const CODE_EXT = /\.(py|js|ts|jsx|tsx|mjs|cjs|go|rs|c|h|cc|cpp|hpp|java|rb|sh|bash|lua|zig|swift|kt|scala|clj)$/
const VERIFY_CMD = /\b(make\s+(test|check|bench|lint|build)|pytest|npm\s+test|npm\s+run\s+(test|build|lint|typecheck)|go\s+test|cargo\s+(test|check|build|clippy)|ruff|eslint|tsc|mypy|shellcheck|bats)\b/

const BLOCKED = loadBlocked()
const dirty = new Map() // sessionID -> { files:Set, verifiedSince:bool }
const reads = new Map() // sessionID -> Map(path -> count); the read-loop sensor

// Re-reading the same path past this is the loop the substantive run exposed (a worker read
// redis.py 28x). A ranged re-read (different offset) is legitimate; the block only fires when the
// worker repeats a read with no new ground covered.
const READ_LOOP_THRESHOLD = 3

function mark(sid, file) {
  let s = dirty.get(sid)
  if (!s) { s = { files: new Set(), verifiedSince: true }; dirty.set(sid, s) }
  s.files.add(file); s.verifiedSince = false
}
function clearVerified(sid) { const s = dirty.get(sid); if (s) { s.verifiedSince = true; s.files.clear() } }

// The read-loop key: path + offset + limit. A different line range is a different window (not a
// loop); an identical re-read of the same span is the repeat we're blocking.
function readKey(args) {
  const fp = args?.filePath ?? args?.file_path ?? args?.path
  if (!fp) return null
  return `${fp}:${args?.offset ?? ""}:${args?.limit ?? ""}`
}

export default async () => ({
  "tool.execute.before": async (input, output) => {
    // Read-loop sensor (before bash gate). A worker re-reading the same path past the threshold
    // is the doom-loop the substantive run exposed. A ranged read (an offset/limit) is a new
    // window, not a repeat; an identical re-read of the same span is the loop.
    if (input.tool === "read") {
      const sid = input.sessionID
      const key = readKey(output?.args)
      if (key) {
        let m = reads.get(sid); if (!m) { m = new Map(); reads.set(sid, m) }
        const n = (m.get(key) || 0) + 1; m.set(key, n)
        if (n > READ_LOOP_THRESHOLD) {
          throw new Error(
            `[read-loop] You have read this exact file+range ${n} times without new ground. ` +
            `Re-reading the same path does not return more. If it was truncated, call recall(seq) ` +
            `for the full content, or read a DIFFERENT line range (offset/limit). If you already ` +
            `have enough, stop reading and write the report.`)
        }
      }
      return
    }
    if (input.tool !== "bash") return
    const cmd = String(output?.args?.command ?? "")
    if (!cmd) return
    for (const { re, reason } of BLOCKED) {
      if (re.test(cmd)) {
        throw new Error(
          `[human-gate] Blocked: ${reason}\nCommand: ${cmd}\n` +
          `This action is irreversible and gated to the human. Do not retry it another ` +
          `way. State the exact command and hand it back.`)
      }
    }
  },

  "tool.execute.after": async (input) => {
    const sid = input.sessionID
    if (input.tool === "edit" || input.tool === "write") {
      const file = String(input?.args?.filePath ?? "")
      if (CODE_EXT.test(file)) mark(sid, file)
    } else if (input.tool === "bash") {
      if (VERIFY_CMD.test(String(input?.args?.command ?? ""))) clearVerified(sid)
    }
  },

  "experimental.text.complete": async (input, output) => {
    const s = dirty.get(input.sessionID)
    if (!s || s.verifiedSince || s.files.size === 0) return
    const files = [...s.files].slice(0, 6).join(", ")
    output.text +=
      `\n\n---\n[verify-gate] Code was edited (${files}) but no test/build/lint has run ` +
      `since. Ground "done" in a real signal: run the canonical check (make test / ` +
      `make bench-e2e / the repo's own suite), read the result, and fix if red. Do not ` +
      `report done on intent.`
    s.verifiedSince = true
  },
})
