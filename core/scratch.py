"""Scratch tier: ephemeral, RAM-backed worker artifacts, scoped per task and bounded.

The scratch dir holds worker artifacts too large for a reply. Two rules keep it from being a
silent leak (the playbook's 'memory without cleanup' failure): writes are scoped to the root
session (a per-task subdir), and a cleanup expires entries older than a TTL so it can't grow
unbounded or serve stale artifacts.
"""

from __future__ import annotations

import os
import time

DEFAULT_TTL_S = 86400  # artifacts older than a day are stale


def task_dir(scratch_dir: str, session_id: str) -> str:
    """The per-task scratch subdir for a root session."""
    d = os.path.join(scratch_dir, session_id or "scratch")
    os.makedirs(d, exist_ok=True)
    return d


def cleanup(scratch_dir: str, ttl_s: int = DEFAULT_TTL_S) -> int:
    """Remove scratch entries older than the TTL. Returns the count removed."""
    now = time.time()
    removed = 0
    try:
        entries = os.listdir(scratch_dir)
    except OSError:
        return 0
    for name in entries:
        p = os.path.join(scratch_dir, name)
        try:
            if now - os.path.getmtime(p) > ttl_s:
                if os.path.isdir(p):
                    import shutil
                    shutil.rmtree(p)
                else:
                    os.remove(p)
                removed += 1
        except OSError:
            pass
    return removed
