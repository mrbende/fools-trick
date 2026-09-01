// The one boundary helper: calls the Python core over a subprocess, and loads the gate
// policy the core exports as JSON. This is the only file that knows the core is Python.
//
// Tools cross here (subprocess-per-call, fine at tool latency). The gate before-check loads
// the blocked-pattern list ONCE at startup and matches in-process (fast, synchronous). See
// docs/harness-design.md 3.5.

import { execFile, execFileSync } from "node:child_process"
import { fileURLToPath } from "node:url"
import { dirname, resolve } from "node:path"

const HERE = dirname(fileURLToPath(import.meta.url))
// repo root is two levels up from adapters/opencode/
const ROOT = resolve(HERE, "..", "..")
const PYTHON = process.env.FOOLS_PYTHON || "python3"

// The parent-walk command opencode's adapter supplies to the core so thread resolution can
// climb the session tree. The core stays harness-blind; this string is the harness knowledge.
const PARENT_CMD =
  process.env.FOOLS_PARENT_CMD ||
  `opencode db --format json ` +
  `"SELECT parent_id FROM session WHERE id = '{sid}'" ` +
  `| ${PYTHON} -c "import sys,json; r=json.load(sys.stdin); print((r[0].get('parent_id') or '') if r else '')"`

function childEnv() {
  return { ...process.env, PYTHONPATH: ROOT, FOOLS_PARENT_CMD: PARENT_CMD }
}

// The adapter's budgets come from the ONE config loader, not ambient env. opencode launches
// directly (not via make/env.sh), so process.env often lacks them -- a stale hardcoded default
// would silently desync the prune trigger from the served context. Read the loader once at
// plugin init (fast, ~40ms); env vars still override for CI/one-offs.
let _cfg = null
export function configSnapshot() {
  if (_cfg) return _cfg
  try {
    const out = execFileSync(PYTHON, ["-m", "core.config", "--json"],
      { cwd: ROOT, env: childEnv(), timeout: 8000 })
    _cfg = JSON.parse(String(out))
  } catch {
    _cfg = {} // fall back to the per-key defaults below
  }
  return _cfg
}

// Resolve a numeric config value: env override first, then the loader, then a fallback.
export function cfgNum(envKey, cfgPath, fallback) {
  if (process.env[envKey] != null) return Number(process.env[envKey])
  const cfg = configSnapshot()
  let v = cfg
  for (const part of cfgPath.split(".")) v = v?.[part]
  return v != null ? Number(v) : fallback
}

// Health gate: a tool whose backend is down errors cleanly, not a hang. Cached briefly so we
// don't run the health check on every call.
let _health = { at: 0, map: {} }
function toolsetHealth() {
  if (Date.now() - _health.at < 5000) return _health.map
  try {
    const out = execFileSync(PYTHON, ["-c",
      "import sys; sys.path.insert(0, r'" + ROOT + "'); " +
      "import json; from core.tools.registry import health; print(json.dumps(health()))"],
      { cwd: ROOT, env: childEnv(), timeout: 8000 })
    _health = { at: Date.now(), map: JSON.parse(String(out)) }
  } catch {
    _health = { at: Date.now(), map: {} }  // unknown -> allow (don't block on a health-check failure)
  }
  return _health.map
}

// Call a core tool. Returns the parsed neutral result object, or a clean error if the backend
// toolset is down.
// Whether an incident is open (the war-room mode). The runtime-context injector reads this each
// orchestrator turn; it tightens the posture only while an incident is open.
export async function incidentStatus() {
  try {
    const r = await callTool("incident_status", {}, {})
    return r?.open ? String(r.reason || "open") : null
  } catch {
    return null
  }
}

// The worker's deterministic thread-state prefill (contract + decisions + open incident). Read each
// worker turn so a worker starts with its task's state already present -- background awareness, not
// retrieval. Returns the fenced block or "" (a fresh thread injects nothing).
export async function threadState(sessionID) {
  try {
    const r = await callTool("thread_state", { session: sessionID }, {})
    return String(r?.state || "")
  } catch {
    return ""
  }
}

export function callTool(toolName, args, ctx = {}) {
  return new Promise((res) => {
    // gate on the toolset's health: a down backend returns a clean error, not a hang
    const h = toolsetHealth()
    const ts = Object.entries(h).find(([, v]) => (v.tools || []).includes(toolName))
    if (ts && ts[1].ok === false) {
      res({ title: `${toolName} unavailable`, output: `${toolName} is unavailable: ${ts[1].reason}. The ${ts[0]} toolset backend is down.`, metadata: { toolset: ts[0] } })
      return
    }
    // The payload travels via stdin (--json -), never argv: a large tool result would overflow the
    // OS arg-length limit (spawn E2BIG). Only session/agent/call-id ride argv (always small).
    const argv = [
      "-m", "core.tools.cli", toolName,
      "--json", "-",
      "--session", ctx.sessionID || "",
      "--agent", ctx.agent || "",
    ]
    if (ctx.callID) argv.push("--call-id", ctx.callID)
    const proc = execFile(PYTHON, argv, { cwd: ROOT, env: childEnv(), timeout: 20000, maxBuffer: 8 * 1024 * 1024 },
      (err, stdout) => {
        if (err) { res({ title: `${toolName} failed`, output: String(err.message || err), metadata: {} }); return }
        try { res(JSON.parse(stdout)) }
        catch { res({ title: `${toolName} ok`, output: String(stdout).trim(), metadata: {} }) }
      })
    proc.stdin.on("error", () => {})   // a closed stdin (child exited early) must not throw
    proc.stdin.write(JSON.stringify(args || {}))
    proc.stdin.end()
  })
}

let _warnedCoreDown = false
function _warnCoreDown(err) {
  if (_warnedCoreDown) return
  _warnedCoreDown = true
  process.stderr.write(`fools-trick: context core unreachable, the per-turn prune is OFF (${String(err).slice(0,120)}). Start the rig (make up); the worker degrades to no-prune until it's back.\n`)
}

// Ask the core for a context decision synchronously (the transform hook is sync-in-effect --
// it must mutate the array before the turn is sent). Returns the decision, or null on failure.
// A failure is logged ONCE (a trip wire), not silent -- a silently-skipped prune is how the
// routing bug hid.
export function planContext(which, { turns, inputBudget, keepRecent, keepTail, distilled, pinned }) {
  // turns go on stdin (large tool results exceed the argv limit); small args stay on argv.
  const argv = ["-m", "core.context.cli", which,
    "--input-budget", String(inputBudget), "--keep-recent", String(keepRecent ?? 3),
    "--keep-tail", String(keepTail ?? 6),
    "--distilled", JSON.stringify(distilled || []), "--pinned", JSON.stringify(pinned || [])]
  try {
    const out = execFileSync(PYTHON, argv, {
      cwd: ROOT, env: childEnv(), timeout: 8000, maxBuffer: 16 * 1024 * 1024,
      input: JSON.stringify(turns),
    })
    return JSON.parse(String(out))
  } catch (e) {
    _warnCoreDown(e)
    return null
  }
}

// Load the human-gate blocked patterns from the Python source of truth, once, synchronously.
export function loadBlocked() {
  try {
    const out = execFileSync(PYTHON, ["-c",
      "import sys; sys.path.insert(0, r'" + ROOT + "'); " +
      "from core.gates.policy import export_blocked_json; print(export_blocked_json())"],
      { cwd: ROOT, env: childEnv(), timeout: 8000 })
    return JSON.parse(String(out)).map((e) => ({ re: new RegExp(e.source, "i"), reason: e.reason }))
  } catch {
    // Fail closed toward safety with a minimal hardcoded floor if the core can't be read.
    return [
      { re: /\bgit\s+push\b/i, reason: "git push is human-gated (core unreachable; failing safe)." },
      { re: /\bterraform\s+(apply|destroy)\b/i, reason: "infra apply/destroy is human-gated." },
    ]
  }
}

// Load the always-protected branch names from the Python source of truth, once, synchronously.
export function loadProtectedBranches() {
  try {
    const out = execFileSync(PYTHON, ["-c",
      "import sys; sys.path.insert(0, r'" + ROOT + "'); " +
      "from core.gates.policy import export_protected_branches_json; print(export_protected_branches_json())"],
      { cwd: ROOT, env: childEnv(), timeout: 8000 })
    return new Set(JSON.parse(String(out)).map((b) => String(b).toLowerCase()))
  } catch {
    return new Set(["master", "main", "staging"])   // fail closed on the always-protected set
  }
}

// Load the code-file + verify-command regexes from the Python policy (single source of truth).
export function loadGatePatterns() {
  try {
    const out = execFileSync(PYTHON, ["-c",
      "import sys; sys.path.insert(0, r'" + ROOT + "'); " +
      "from core.gates.policy import export_gate_patterns_json; print(export_gate_patterns_json())"],
      { cwd: ROOT, env: childEnv(), timeout: 8000 })
    const d = JSON.parse(String(out))
    return { CODE_EXT: new RegExp(d.code_ext), VERIFY_CMD: new RegExp(d.verify_cmd) }
  } catch {
    return { CODE_EXT: null, VERIFY_CMD: null }   // caller fails safe (treat nothing as code/verify)
  }
}

// The current branch of the repo the agent is working IN. Defaults to the process cwd (the agent's
// working directory, where its `git commit` runs) -- NOT the fools-trick ROOT, since the harness
// config is loaded from ROOT but the agent operates on the user's project. Lowercase, "" if none.
export function currentBranch(cwd) {
  try {
    const out = execFileSync("git", ["rev-parse", "--abbrev-ref", "HEAD"],
      { cwd: cwd || process.cwd(), timeout: 3000 })
    return String(out).trim().toLowerCase()
  } catch {
    return ""
  }
}
