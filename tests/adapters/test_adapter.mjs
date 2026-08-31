// opencode adapter tests: the human-gate (policy from the Python core), the bridge boundary
// (JS -> Python core -> SQLite -> back), and the in-process worker-prune mechanics (option a,
// mirroring core/context/window.py). Run: node tests/adapters/test_adapter.mjs
//
// No opencode server needed; we exercise the pure adapter functions and the subprocess bridge.

import { fileURLToPath } from "node:url"
import { dirname, resolve } from "node:path"
import { mkdtempSync } from "node:fs"
import { tmpdir } from "node:os"

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, "..", "..")

let pass = 0, fail = 0
const ok = (n) => { console.log(`  ok   ${n}`); pass++ }
const bad = (n, d) => { console.log(`  FAIL ${n} ${d ?? ""}`); fail++ }
const eq = (n, a, b) => (a === b ? ok(n) : bad(n, `(${JSON.stringify(a)} !== ${JSON.stringify(b)})`))

// Force sqlite fallback + an isolated db so the test needs no Redis and no shared state.
const db = `${mkdtempSync(resolve(tmpdir(), "ft-adapter-"))}/m.db`
process.env.MEMORY_DB = db
process.env.REDIS_URL = "redis://127.0.0.1:6399"

console.log("adapter: human-gate (policy loaded from the Python core)")
{
  const gates = (await import("../../adapters/opencode/plugin_gates.js")).default
  const hooks = await gates()
  // git push is always blocked -- by the protected-branch gate when on master/main/staging, else by
  // the human-gate. Assert the security property (blocked), tolerant of which gate fires.
  let pushErr = ""
  try { await hooks["tool.execute.before"]({ tool: "bash" }, { args: { command: "git push origin main" } }) }
  catch (e) { pushErr = String(e.message) }
  eq("blocks git push", /human-gate|protected-branch/.test(pushErr), true)

  // Mutating infra is human-gated; read-only cloud inspection is allowed.
  let tfErr = ""
  try { await hooks["tool.execute.before"]({ tool: "bash" }, { args: { command: "terraform import x y" } }) }
  catch (e) { tfErr = String(e.message) }
  eq("blocks terraform", /human-gate/.test(tfErr), true)
  let awsMutBlocked = false
  try { await hooks["tool.execute.before"]({ tool: "bash" }, { args: { command: "aws ec2 run-instances --image-id ami-1" } }) }
  catch { awsMutBlocked = true }
  eq("blocks mutating aws", awsMutBlocked, true)
  let awsReadOk = true
  try { await hooks["tool.execute.before"]({ tool: "bash" }, { args: { command: "aws ec2 describe-instances" } }) } catch { awsReadOk = false }
  eq("allows read-only aws", awsReadOk, true)

  let allowed = true
  try { await hooks["tool.execute.before"]({ tool: "bash" }, { args: { command: "make test" } }) } catch { allowed = false }
  eq("allows make test", allowed, true)
}

console.log("adapter: goal-direction gate (canonicalize keyed off the recorded contract SIGNAL)")
{
  // Run in a throwaway feature-branch repo so the protected-branch gate does not mask the SIGNAL gate.
  const { execSync } = await import("node:child_process")
  const repo = mkdtempSync(resolve(tmpdir(), "ft-gate-"))
  execSync("git init -q && git checkout -q -b feature/probe && git commit -q --allow-empty -m init", { cwd: repo, shell: "/bin/bash" })
  const cwd0 = process.cwd()
  process.chdir(repo)
  const gates = (await import("../../adapters/opencode/plugin_gates.js?goal")).default
  const g = await gates()
  const B = g["tool.execute.before"], A = g["tool.execute.after"]
  const sid = "goal"
  await A({ tool: "record_contract", sessionID: sid }, { metadata: { signal: "pytest tests/test_x.py" } })
  await A({ tool: "edit", sessionID: sid, args: { filePath: "/r/x.py" } })
  await A({ tool: "bash", sessionID: sid, args: { command: "make lint" } })   // unrelated verify
  let blockedWrong = false
  try { await B({ tool: "bash", sessionID: sid }, { args: { command: "git commit -m wip" } }) }
  catch (e) { blockedWrong = /SIGNAL for this task/.test(e.message) }
  eq("blocks commit when the contract SIGNAL has not run", blockedWrong, true)
  await A({ tool: "bash", sessionID: sid, args: { command: "pytest tests/test_x.py -q" } })   // the real signal
  let allowedAfter = true
  try { await B({ tool: "bash", sessionID: sid }, { args: { command: "git commit -m done" } }) } catch { allowedAfter = false }
  eq("allows commit after the contract SIGNAL runs", allowedAfter, true)
  process.chdir(cwd0)
}

console.log("adapter: bridge boundary (JS -> Python core -> SQLite -> back)")
{
  const { callTool } = await import("../../adapters/opencode/bridge.js")
  await callTool("memory_write", { content: "adapter bridge roundtrip fact" }, { sessionID: "s1", agent: "build" })
  const r = await callTool("memory_search", { query: "roundtrip" }, { sessionID: "s1", agent: "build" })
  eq("bridge round-trip finds the written fact", (r.metadata?.hits ?? 0) >= 1, true)
  eq("output carries the fact", r.output.includes("roundtrip fact"), true)
}

console.log("adapter: prune decision comes from the Python core (no mirrored logic)")
{
  const { planContext } = await import("../../adapters/opencode/bridge.js")
  const { toWorkerTurns, applyEvict, liveToolParts } = await import("../../adapters/opencode/shape.js")
  // 4 tool results, one huge; keep_recent=1 protects only the newest.
  const toolMsg = (id, chars) => ({
    info: { role: "assistant", agent: "explore", sessionID: "w" },
    parts: [{ type: "tool", callID: id, state: { status: "completed", output: "x".repeat(chars), time: {} } }],
  })
  const msgs = [toolMsg("small", 7000), toolMsg("huge", 200000), toolMsg("mid", 40000), toolMsg("recent", 7000)]
  const d = planContext("prune", { turns: toWorkerTurns(msgs), inputBudget: 20000, keepRecent: 1, distilled: [], pinned: [] })
  eq("core returned a decision", d != null && d.changed, true)
  eq("core evicts the largest first", d.evict_call_ids.includes("huge"), true)
  eq("core protects the newest", d.evict_call_ids.includes("recent"), false)
  // apply the core's decision to the live array (the adapter's only in-process job)
  applyEvict(msgs, d.evict_call_ids)
  eq("huge is compacted after applyEvict", liveToolParts(msgs).some((p) => p.callID === "huge"), false)
  eq("recent stays live", liveToolParts(msgs).some((p) => p.callID === "recent"), true)
}

console.log("adapter: read-loop sensor blocks the Nth identical re-read, not a new range")
{
  const gates = (await import("../../adapters/opencode/plugin_gates.js")).default
  const h = await gates()
  const before = h["tool.execute.before"]
  let blocked = 0
  for (let i = 0; i < 4; i++) {
    try { await before({ tool: "read", sessionID: "w" }, { args: { filePath: "/x/f.py" } }) }
    catch (e) { if (String(e.message).includes("read-loop")) blocked++ }
  }
  eq("the 4th identical read is blocked", blocked, 1)
  // a different line range is a new window, not a loop -> not blocked
  let rangedBlocked = false
  try { await before({ tool: "read", sessionID: "w" }, { args: { filePath: "/x/f.py", offset: 100, limit: 50 } }) }
  catch { rangedBlocked = true }
  eq("a different line range is not blocked", rangedBlocked, false)
}

console.log("adapter: per-result cap spills an oversized read and leaves a seq pointer")
{
  const mem = (await import("../../adapters/opencode/plugin_memory.js")).default
  const hooks = await mem()
  // Derive the payload from the CONFIGURED cap via the same resolver the plugin uses (cfgNum:
  // env -> config.yaml -> default), not a frozen magic number. This tests the spill BEHAVIOR at
  // whatever worker_tool_result_cap resolves to. The plugin caps at (cap_tokens * 2.5) chars
  // (estimate.py's divisor); build a payload comfortably above that.
  const { cfgNum } = await import("../../adapters/opencode/bridge.js")
  const capTokens = cfgNum("WORKER_TOOL_RESULT_CAP", "worker_tool_result_cap", 8000)
  const capChars = capTokens * 2.5
  const big = "y".repeat(Math.ceil(capChars * 2)) // 2x the cap: unambiguously oversized
  const output = { title: "read", output: big, metadata: {} }
  await hooks["tool.execute.after"]({ tool: "read", sessionID: "w", callID: "c9", args: {} }, output)
  eq("oversized output is truncated", output.output.length < big.length, true)
  eq("the note carries a recallable seq", /seq=\d+/.test(output.output), true)
  eq("the note points at a scratch file", /tool-c9\.txt/.test(output.output), true)
}

console.log(`\nadapter: ${pass} passed, ${fail} failed`)
process.exit(fail === 0 ? 0 : 1)
