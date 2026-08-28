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

// Call a core tool. Returns the parsed neutral result object, or a soft error object.
export function callTool(toolName, args, ctx = {}) {
  return new Promise((res) => {
    const argv = [
      "-m", "core.tools.cli", toolName,
      "--json", JSON.stringify(args || {}),
      "--session", ctx.sessionID || "",
      "--agent", ctx.agent || "",
    ]
    if (ctx.callID) argv.push("--call-id", ctx.callID)
    execFile(PYTHON, argv, { cwd: ROOT, env: childEnv(), timeout: 20000, maxBuffer: 8 * 1024 * 1024 },
      (err, stdout) => {
        if (err) { res({ title: `${toolName} failed`, output: String(err.message || err), metadata: {} }); return }
        try { res(JSON.parse(stdout)) }
        catch { res({ title: `${toolName} ok`, output: String(stdout).trim(), metadata: {} }) }
      })
  })
}

// Ask the core for a context decision synchronously (the transform hook is sync-in-effect --
// it must mutate the array before the turn is sent). Returns the decision, or null on failure
// (fail open: the worker degrades, never blocks the turn).
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
  } catch {
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
