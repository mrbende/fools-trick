"""CLI entrypoint the harness adapter shells into: `python -m core.tools.cli <tool> --json '{...}'`.

Reads config from the environment, builds the MemoryLog (with a thread resolver whose parent
walk is supplied by an env-configured command, so the core still names no harness), runs one
tool body, and prints the neutral result as JSON on stdout. Subprocess-per-call keeps the
adapter boundary trivial; optimize to a socket later only if measured latency demands it.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys

from core import config as cfg_mod
from core.log.log import MemoryLog
from core.log.thread import MISSING, ThreadResolver
from core.tools import memory as tools
from core.types import ToolContext

_TOOLS = {
    "memory_write": tools.memory_write,
    "memory_search": tools.memory_search,
    "recall": tools.recall,
    "note": tools.note,
    "promote": tools.promote,
}


def _parent_walker():
    """Build a parent_of(session_id) from FOOLS_PARENT_CMD, else identity (root everything).

    FOOLS_PARENT_CMD is a shell template with {sid}; it must print the parent session id, an
    empty line for a root, or exit non-zero when not found. The opencode adapter sets it to a
    query over `opencode db`. Keeping this env-driven is what lets the core stay harness-blind.
    """
    template = None
    import os

    template = os.environ.get("FOOLS_PARENT_CMD")
    if not template:
        return lambda _sid: None  # identity: every session is its own root

    def parent_of(sid: str):
        try:
            cmd = template.replace("{sid}", shlex.quote(sid))
            out = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=8
            )
            if out.returncode != 0:
                return MISSING
            val = out.stdout.strip()
            return val if val else None
        except (subprocess.SubprocessError, OSError):
            return MISSING

    return parent_of


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fools-trick tools")
    parser.add_argument("tool", choices=sorted([*_TOOLS, "drain"]))
    parser.add_argument("--json", default="{}", help="tool args as a JSON object")
    parser.add_argument("--session", default="", help="ToolContext sessionID")
    parser.add_argument("--agent", default="", help="ToolContext agent")
    parser.add_argument("--call-id", default=None, help="ToolContext callID")
    args = parser.parse_args(argv)

    try:
        payload = json.loads(args.json or "{}")
    except json.JSONDecodeError as e:
        print(json.dumps({"error": f"bad --json: {e}"}))
        return 2

    cfg = cfg_mod.load()
    resolver = ThreadResolver(_parent_walker())
    log = MemoryLog(
        db_path=cfg.memory_db,
        redis_url=cfg.redis_url,
        resolve_thread=resolver.resolve,
    )
    # drain is not a model tool; it flushes the Redis write-stream into SQLite so a write is never
    # parked in the ephemeral tier past a turn boundary (the autonomous-drain fix).
    if args.tool == "drain":
        moved = log.drain()
        log.close()
        print(json.dumps({"drained": moved}))
        return 0
    ctx = ToolContext(sessionID=args.session, agent=args.agent, callID=args.call_id)
    try:
        result = _TOOLS[args.tool](payload, ctx, log)
        print(json.dumps(result))
        return 0
    finally:
        log.close()


if __name__ == "__main__":
    sys.exit(main())
