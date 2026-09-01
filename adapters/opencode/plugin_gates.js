// opencode adapter for the Gate Policy. The human-gate blocked patterns are the Python core's
// source of truth, loaded once at startup; the verify-state machine is small enough to run in
// the adapter. Policy is owned in core/gates/policy.py; this file is plumbing.

import { loadBlocked, loadProtectedBranches, loadGatePatterns, currentBranch } from "./bridge.js"

const BLOCKED = loadBlocked()
const PROTECTED = loadProtectedBranches()
// Code-file and verify-command regexes come from the Python policy (single source of truth), so the
// gate never drifts from what test_gates.py asserts. If the policy is unreadable, both are null and
// the verify/canonicalize gates fail safe (treat nothing as code, so no spurious blocks).
const { CODE_EXT, VERIFY_CMD } = loadGatePatterns()
const dirty = new Map()    // sessionID -> { files:Set, verifiedSince:bool, everVerified:bool }
const reads = new Map()    // sessionID -> Map(path -> count); the read-loop sensor
const contract = new Map() // sessionID -> { signal:string, signalRan:bool }; the goal-direction gate

// Does a run bash command satisfy the recorded contract SIGNAL? The SIGNAL is the exact check the
// orchestrator named as "done" (record_contract). We match leniently: the command contains the
// signal's core (first token/path), so `pytest tests/x.py -q` still satisfies signal `pytest tests/x.py`.
function commandMatchesSignal(cmd, signal) {
  if (!signal) return false
  const core = signal.trim().split(/\s+/).slice(0, 3).join(" ")   // e.g. "pytest tests/test_auth.py"
  return cmd.includes(core) || cmd.includes(signal.trim())
}

// The canonicalize gate: a git commit is the point work becomes canonical. Hybrid enforcement --
// hard-block a commit with code edited and a verify command NEVER run since (canonicalizing on pure
// belief), nudge when a verify ran but may be stale. Amend/message-only paths still count as commits.
const COMMIT_CMD = /\bgit\s+(-[^\s]+\s+)*commit\b/
// A push targeting a branch: capture the ref so a push to a protected branch is caught even though
// push itself is already human-gated (this gives the protected-branch reason, not a generic gate).
const PUSH_CMD = /\bgit\s+push\b/

// Re-reading the same path past this is the loop the substantive run exposed (a worker read
// redis.py 28x). A ranged re-read (different offset) is legitimate; the block only fires when the
// worker repeats a read with no new ground covered.
const READ_LOOP_THRESHOLD = 3

function mark(sid, file) {
  let s = dirty.get(sid)
  if (!s) { s = { files: new Set(), verifiedSince: true, everVerified: false, everEdited: false }; dirty.set(sid, s) }
  s.files.add(file); s.verifiedSince = false; s.everEdited = true
}
// A verify command ran: clears the dirty set (verifiedSince) and records that verification has run
// at least once this session (everVerified) -- the canonicalize gate hard-blocks only when it hasn't.
function clearVerified(sid) {
  const s = dirty.get(sid)
  if (s) { s.verifiedSince = true; s.everVerified = true; s.files.clear() }
}

// The read-loop key: path + offset + limit. A different line range is a different window (not a
// loop); an identical re-read of the same span is the repeat we're blocking.
function readKey(args) {
  const fp = args?.filePath ?? args?.file_path ?? args?.path
  if (!fp) return null
  return `${fp}:${args?.offset ?? ""}:${args?.limit ?? ""}`
}

// Read-loop sensor: a worker re-reading the same path past the threshold is the doom-loop the
// substantive run exposed. A ranged read (offset/limit) is a new window, not a repeat; an identical
// re-read of the same span is the loop.
function checkReadLoop(input, output) {
  if (input.tool !== "read") return
  const sid = input.sessionID
  const key = readKey(output?.args)
  if (!key) return
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

// Protected-branch gate: never commit to or push a protected branch directly. Work on feature
// branches; integration is a human PR/merge, not a direct agent commit. Checked before the
// BLOCKED loop so the message names the real reason (protected branch), not a generic push gate.
function checkProtectedBranch(cmd) {
  if (!(COMMIT_CMD.test(cmd) || PUSH_CMD.test(cmd))) return
  const branch = currentBranch()
  if (branch && PROTECTED.has(branch)) {
    throw new Error(
      `[protected-branch] '${branch}' is always protected -- no direct commit or push. ` +
      `Create/switch to a feature branch (git switch -c <feature>), commit there, and hand the ` +
      `merge back to the human as a PR. Command: ${cmd}`)
  }
}

// Canonicalize gate: a commit with code edited but the objective unproven is canonicalizing on
// belief -- hard block. If a contract was recorded, require ITS specific SIGNAL to have run (the
// goal-direction close): an unrelated `make lint` must not satisfy a `pytest x` signal. With no
// contract, fall back to "any verify command ran since edits." `everEdited` tracks that code was
// touched at all -- independent of the dirty set, which a verify command clears.
function checkCanonicalize(cmd, sid) {
  if (!COMMIT_CMD.test(cmd)) return
  const s = dirty.get(sid)
  const c = contract.get(sid)
  const editedCode = !!s && s.everEdited
  if (c && editedCode && !c.signalRan) {
    throw new Error(
      `[canonicalize-gate] Refusing to commit: the success-contract SIGNAL for this task ` +
      `(\`${c.signal}\`) has not run since the code was edited. Run it, read the result, fix if ` +
      `red, THEN commit. Do not canonicalize on belief.`)
  }
  if (!c && editedCode && !s.everVerified) {
    throw new Error(
      `[canonicalize-gate] Refusing to commit: code was edited and no test/build/lint has run this ` +
      `session, and no success-contract was recorded. Record a contract (record_contract) or run ` +
      `the repo's check, read the result, fix if red, THEN commit. Do not canonicalize on belief.`)
  }
}

// Human-gate: the irreversible/publishing commands are gated to the human (policy.py is the source).
function checkHumanGate(cmd) {
  for (const { re, reason } of BLOCKED) {
    if (re.test(cmd)) {
      throw new Error(
        `[human-gate] Blocked: ${reason}\nCommand: ${cmd}\n` +
        `This action is irreversible and gated to the human. Do not retry it another ` +
        `way. State the exact command and hand it back.`)
    }
  }
}

function gateBefore(input, output) {
  if (input.tool === "read") { checkReadLoop(input, output); return }
  if (input.tool !== "bash") return
  const cmd = String(output?.args?.command ?? "")
  if (!cmd) return
  checkProtectedBranch(cmd)
  checkCanonicalize(cmd, input.sessionID)
  checkHumanGate(cmd)
}

// tool.execute.after handlers, keyed by tool. Dispatching a map keeps each gate's branch surface
// flat and the hook readable; adding a handler is one entry, not another else-if.
const _afterHandlers = {
  edit: (input) => _afterEdit(input),
  write: (input) => _afterEdit(input),
  record_contract: (input, output) => _afterContract(input, output),
  bash: (input) => _afterBash(input),
}

function _afterEdit(input) {
  const file = String(input?.args?.filePath ?? "")
  if (CODE_EXT && CODE_EXT.test(file)) mark(input.sessionID, file)
}

function _afterContract(input, output) {
  const sig = output?.metadata?.signal
  if (sig) contract.set(input.sessionID, { signal: String(sig), signalRan: false })
}

function _afterBash(input) {
  const cmd = String(input?.args?.command ?? "")
  if (VERIFY_CMD && VERIFY_CMD.test(cmd)) clearVerified(input.sessionID)
  const c = contract.get(input.sessionID)
  if (c && commandMatchesSignal(cmd, c.signal)) c.signalRan = true
}

function gateAfter(input, output) {
  _afterHandlers[input.tool]?.(input, output)
}

function verifyGate(input, output) {
  const s = dirty.get(input.sessionID)
  if (!s || s.verifiedSince || s.files.size === 0) return
  const files = [...s.files].slice(0, 6).join(", ")
  output.text +=
    `\n\n---\n[verify-gate] Code was edited (${files}) but no test/build/lint has run ` +
    `since. Ground "done" in a real signal: run the project's own check (its test/build/lint ` +
    `command), read the result, and fix if red. Do not report done on intent.`
  // independent review, structural (producer != verifier), orchestrator-only (workers can't dispatch)
  const agent = input?.agent ?? ""
  if (agent === "build" || agent === "plan") {
    output.text +=
      `\n[verify-gate] Before accepting this, dispatch the @reviewer subagent on the diff ` +
      `(independent read-only review). Fold its findings back in.`
  }
  s.verifiedSince = true
}

export default async () => ({
  "tool.execute.before": gateBefore,
  "tool.execute.after": gateAfter,
  "experimental.text.complete": verifyGate,
})
