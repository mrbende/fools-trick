"""Gate Policy: the human-gate (block irreversible external side-effects) and the verify-gate
(track code edits vs verification) as pure predicates.

Source of truth is Python; export_blocked_json() emits the blocked-pattern list as JSON for
the tiny in-process JS before-hook to load once at startup, so the fast synchronous decision
runs in-process while the policy is owned here.
"""

from core.gates.policy import (
    BLOCKED,
    VerifyState,
    classify_command,
    export_blocked_json,
    is_code_file,
    is_verify_command,
)
