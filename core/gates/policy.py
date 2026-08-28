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
    (r"\bterraform\s+(apply|destroy)\b",
     "terraform apply/destroy changes real infra. Human-gated."),
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

# Docs/data deliberately excluded so editing a README never demands a test run.
_CODE_EXT = re.compile(
    r"\.(py|js|ts|jsx|tsx|mjs|cjs|go|rs|c|h|cc|cpp|hpp|java|rb|sh|bash|lua|zig|swift|kt|scala|clj)$"
)
_VERIFY_CMD = re.compile(
    r"\b(make\s+(test|check|bench|lint|build)|pytest|npm\s+test|"
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


def is_code_file(path: str) -> bool:
    return bool(path and _CODE_EXT.search(path))


def is_verify_command(cmd: str) -> bool:
    return bool(cmd and _VERIFY_CMD.search(cmd))


def export_blocked_json() -> str:
    """Emit the blocked patterns as JSON for the in-process JS before-hook to load once.

    JS RegExp uses the same syntax for these patterns; the flag is case-insensitive.
    """
    return json.dumps([{"source": src, "reason": reason} for src, reason in BLOCKED])


@dataclass
class VerifyState:
    """Per-session dirty-file / verified-since tracker for the verify-gate.

    Marking an edit sets verified_since False; running a verify command clears it. The gate
    nudges when a session ends a turn with code edited but no verification since.
    """

    files: set[str] = field(default_factory=set)
    verified_since: bool = True

    def mark_edit(self, file: str) -> None:
        self.files.add(file)
        self.verified_since = False

    def mark_verified(self) -> None:
        self.verified_since = True
        self.files.clear()

    def needs_verify(self) -> bool:
        return bool(self.files) and not self.verified_since
