// fools-trick enforcement gates: human-gate (hard block on irreversible
// external side-effects) and verify-gate (evidence tracking on code edits).
// Auto-discovered by opencode from .opencode/plugin/.
//
// The gate errs toward blocking: a false block costs one hand-back to the
// human, a false allow can be an irreversible push.

const BLOCKED = [
  [/\bgit\s+push\b/, "git push publishes commits. Hand the exact command to the human."],
  [/\bgit\s+push\s+.*--force\b|\bgit\s+push\s+.*-f\b/, "force-push rewrites remote history. Human-gated."],
  [/\bgit\s+push\s+.*(--delete|:\S)/, "deleting a remote branch is human-gated."],
  [/\bgit\s+reset\s+--hard\b/, "reset --hard can destroy work. Confirm with the human first."],
  [/\bgit\s+(push\s+.*)?--tags\b|\bgit\s+tag\s+-d\b/, "publishing or deleting tags is human-gated."],
  [/\bgit\s+rebase\b.*\b(-i|--interactive)\b/, "interactive rebase rewrites history. Human-gated."],
  [/\bgit\s+filter-branch\b|\bgit\s+filter-repo\b/, "history rewrite is human-gated."],
  [/\bterraform\s+(apply|destroy)\b/, "terraform apply/destroy changes real infra. Human-gated."],
  [/\b(kubectl|helm)\s+(apply|delete|destroy|uninstall)\b/, "cluster mutation is human-gated."],
  [/\b(pulumi\s+up|pulumi\s+destroy)\b/, "pulumi up/destroy changes real infra. Human-gated."],
  // destructive data ops
  [/\bDROP\s+(DATABASE|TABLE|SCHEMA)\b/i, "dropping a database object is human-gated."],
  [/\bTRUNCATE\s+TABLE\b/i, "TRUNCATE is destructive and human-gated."],
  [/\bnpm\s+publish\b|\byarn\s+publish\b|\bpnpm\s+publish\b/, "publishing a package is human-gated."],
  [/\bcargo\s+publish\b|\btwine\s+upload\b|\bpoetry\s+publish\b/, "publishing a package is human-gated."],
  [/\bdocker\s+push\b/, "pushing an image is human-gated."],
  [/\bgh\s+(release\s+create|pr\s+merge)\b/, "creating a release or merging a PR is human-gated."],
]

// Docs/data deliberately excluded so editing a README or SKILL.md never demands a test run.
const CODE_EXT = /\.(py|js|ts|jsx|tsx|mjs|cjs|go|rs|c|h|cc|cpp|hpp|java|rb|sh|bash|lua|zig|swift|kt|scala|clj)$/
const VERIFY_CMD = /\b(make\s+(test|check|bench|lint|build)|pytest|npm\s+test|npm\s+run\s+(test|build|lint|typecheck)|go\s+test|cargo\s+(test|check|build|clippy)|ruff|eslint|tsc|mypy|shellcheck|bats)\b/

const dirty = new Map() // sessionID -> { files: Set<string>, verifiedSince: bool }

function mark(sessionID, file) {
  let s = dirty.get(sessionID)
  if (!s) { s = { files: new Set(), verifiedSince: true }; dirty.set(sessionID, s) }
  s.files.add(file)
  s.verifiedSince = false
}

function clearVerified(sessionID) {
  const s = dirty.get(sessionID)
  if (s) { s.verifiedSince = true; s.files.clear() }
}

export default async () => {
  return {
    "tool.execute.before": async (input, output) => {
      if (input.tool !== "bash") return
      const cmd = String(output?.args?.command ?? "")
      if (!cmd) return
      for (const [re, reason] of BLOCKED) {
        if (re.test(cmd)) {
          throw new Error(
            `[human-gate] Blocked: ${reason}\n` +
            `Command: ${cmd}\n` +
            `This action is irreversible and gated to the human. Do not retry ` +
            `it another way. State the exact command and hand it back.`
          )
        }
      }
    },

    "tool.execute.after": async (input) => {
      const sid = input.sessionID
      if (input.tool === "edit" || input.tool === "write") {
        const file = String(input?.args?.filePath ?? "")
        if (CODE_EXT.test(file)) mark(sid, file)
      } else if (input.tool === "bash") {
        const cmd = String(input?.args?.command ?? "")
        if (VERIFY_CMD.test(cmd)) clearVerified(sid)
      }
    },

    "experimental.text.complete": async (input, output) => {
      const s = dirty.get(input.sessionID)
      if (!s || s.verifiedSince || s.files.size === 0) return
      const files = [...s.files].slice(0, 6).join(", ")
      output.text +=
        `\n\n---\n[verify-gate] Code was edited (${files}) but no test/build/lint ` +
        `has run since. Ground "done" in a real signal: run the canonical check ` +
        `(make test / make bench-e2e / the repo's own suite), read the result, ` +
        `and fix if red. Do not report done on intent.`
      // Reset so the reminder is not repeated every completion in the turn.
      s.verifiedSince = true
    },
  }
}
