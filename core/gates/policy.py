"""Gate policy: pure predicates and a verify-state machine. Port of gates.js policy.

The gate errs toward blocking: a false block costs one hand-back to the human; a false allow
can be an irreversible push. The human-gate list mirrors AGENTS.md's human-gated actions.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional

# (regex source, reason). Kept as source strings so export_blocked_json can hand them to JS.
BLOCKED: list[tuple[str, str]] = [
    (r"\bgit\s+push\b", "git push publishes commits. Hand the exact command to the human."),
    (r"\bgit\s+push\s+.*--force\b|\bgit\s+push\s+.*-f\b",
     "force-push rewrites remote history. Human-gated."),
    (r"\bgit\s+push\s+.*(--delete|:\S)", "deleting a remote branch is human-gated."),
    (r"\bgit\s+reset\s+--hard\b", "reset --hard can destroy work. Confirm with the human first."),
    (r"\bgit\s+(push\s+.*)?--tags\b|\bgit\s+tag\s+-d\b",
     "publishing or deleting tags is human-gated."),
    (r"\bgit\s+rebase\b.*\b(-i|--interactive)\b",
     "interactive rebase rewrites history. Human-gated."),
    (r"\bgit\s+filter-branch\b|\bgit\s+filter-repo\b", "history rewrite is human-gated."),
    (r"\b(terraform|terragrunt|tofu)\b",
     "all terraform/infra-as-code operations are human-run. Hand the exact command back to the human to run."),
    # AWS: read-only (describe/list/get/ls) is allowed -- the agent needs it to understand infra
    # state. Only clearly-mutating verbs are hard-blocked; the gray area is covered by the guide
    # (AGENTS.md), which tells the agent to hand back anything that creates/changes/destroys.
    (r"\baws\s+\S+\s+(create|delete|put|modify|update|terminate|run-instances|start|stop|reboot|"
     r"attach|detach|associate|disassociate|authorize|revoke|deploy|destroy|remove|register|"
     r"deregister|enable|disable|reset|restore|import|apply|set-|tag-|untag-)\S*",
     "this AWS command creates/changes/destroys a resource. Hand the exact command back to the human to run."),
    (r"\b(kubectl|helm)\s+(apply|delete|destroy|uninstall)\b",
     "cluster mutation is human-gated."),
    (r"\b(pulumi\s+up|pulumi\s+destroy)\b",
     "pulumi up/destroy changes real infra. Human-gated."),
    (r"\bDROP\s+(DATABASE|TABLE|SCHEMA)\b", "dropping a database object is human-gated."),
    (r"\bTRUNCATE\s+TABLE\b", "TRUNCATE is destructive and human-gated."),
    (r"\bnpm\s+publish\b|\byarn\s+publish\b|\bpnpm\s+publish\b",
     "publishing a package is human-gated."),
    (r"\bcargo\s+publish\b|\btwine\s+upload\b|\bpoetry\s+publish\b",
     "publishing a package is human-gated."),
    (r"\bdocker\s+push\b", "pushing an image is human-gated."),
    (r"\bgh\s+(release\s+create|pr\s+merge)\b",
     "creating a release or merging a PR is human-gated."),
]

_BLOCKED_RE = [(re.compile(src, re.IGNORECASE), reason) for src, reason in BLOCKED]

# Always-protected branches: the agent must never commit to or push these directly. Work happens on
# feature branches; integration into a protected branch is a human-gated action (PR/merge), never a
# direct commit or push by the agent. Matched case-insensitively against the current branch name.
PROTECTED_BRANCHES: tuple[str, ...] = ("master", "main", "staging")


def is_protected_branch(branch: str) -> bool:
    return bool(branch) and branch.strip().lower() in PROTECTED_BRANCHES


def export_protected_branches_json() -> str:
    """Emit the protected-branch list for the in-process JS gate to load once."""
    return json.dumps(list(PROTECTED_BRANCHES))


def export_gate_patterns_json() -> str:
    """Emit the code-file and verify-command patterns for the in-process JS gate, so the Python
    policy is the single source of truth (not a hand-mirrored copy in plugin_gates.js)."""
    return json.dumps({"code_ext": _CODE_EXT.pattern, "verify_cmd": _VERIFY_CMD.pattern})

# Docs/data deliberately excluded so editing a README never demands a test run.
_CODE_EXT = re.compile(
    r"\.(py|js|ts|jsx|tsx|mjs|cjs|go|rs|c|h|cc|cpp|hpp|java|rb|sh|bash|lua|zig|swift|kt|scala|clj)$"
)
_VERIFY_CMD = re.compile(
    r"\b(make\s+(test|check-quality|check|bench|lint|build)|pytest|npm\s+test|"
    r"npm\s+run\s+(test|build|lint|typecheck)|go\s+test|"
    r"cargo\s+(test|check|build|clippy)|ruff|eslint|tsc|mypy|shellcheck|bats)\b"
)


def classify_command(cmd: str) -> Optional[str]:
    """Return the block reason if cmd is human-gated, else None."""
    if not cmd:
        return None
    for rx, reason in _BLOCKED_RE:
        if rx.search(cmd):
            return reason
    return None


def export_blocked_json() -> str:
    """Emit the blocked patterns as JSON for the in-process JS before-hook to load once.

    JS RegExp uses the same syntax for these patterns; the flag is case-insensitive.
    """
    return json.dumps([{"source": src, "reason": reason} for src, reason in BLOCKED])



