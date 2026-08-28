"""Resolve a session id to its conversation thread: the root of the session tree, shared by
the orchestrator and every subagent it spawns. Episodes key on this root so a worker's write
and the orchestrator's read land under the same thread.

The parent lookup is INJECTED, not hardcoded to a harness. The core is given a callable
parent_of(session_id) -> parent_id | None | MISSING; it walks the chain to the root and
caches. opencode's adapter supplies a walker over `opencode db`; another harness supplies
its own; tests supply a dict. This is what keeps thread resolution harness-agnostic.
"""

from __future__ import annotations

from typing import Callable, Optional

# Sentinel: the lookup could not resolve this id (distinct from None = "this is a root").
MISSING = object()

# parent_of returns: a parent id (str), None (this id is a root), or MISSING (lookup failed).
ParentOf = Callable[[str], object]


class ThreadResolver:
    def __init__(self, parent_of: ParentOf, max_hops: int = 16):
        self._parent_of = parent_of
        self._max_hops = max_hops
        self._cache: dict[str, str] = {}

    def resolve(self, session_id: str) -> str:
        if not session_id:
            return "default"
        if session_id in self._cache:
            return self._cache[session_id]
        cur = session_id
        chain = [cur]
        for _ in range(self._max_hops):  # depth guard against cycles
            parent = self._parent_of(cur)
            if parent is MISSING:
                cur = session_id  # lookup failed: fall back to self-scoping
                break
            if parent is None:
                break  # reached root
            cur = str(parent)
            chain.append(cur)
        for s in chain:
            self._cache[s] = cur
        return cur


def identity_resolver() -> ThreadResolver:
    """A resolver that scopes every session to itself. The standalone/default behavior."""
    return ThreadResolver(lambda _sid: None)


def dict_resolver(parents: dict) -> ThreadResolver:
    """A resolver backed by an in-memory {child: parent} map. For tests and simple harnesses."""

    def parent_of(sid: str) -> Optional[object]:
        if sid not in parents:
            return MISSING
        return parents[sid]  # None means root

    return ThreadResolver(parent_of)
