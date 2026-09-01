"""Tool contracts: harness-neutral tool bodies over a ToolContext we own.

The same function runs under opencode's tool() wrapper (via the CLI in cli.py, invoked by
the JS adapter as a subprocess) or a Prime Agent kernel binding. No harness shape here.
"""

from core.tools.memory import (
    delegate_cheap,
    scratch_write,
    note,
    promote,
    recall,
    record_contract,
    incident,
    report,
    memory_search,
    memory_write,
)
