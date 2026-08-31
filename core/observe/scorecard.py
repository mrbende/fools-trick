"""The harness health scorecard: the real metric, computed from existing data.

Not raw tokens (what the paper says NOT to measure alone) but outcomes: did the orchestrator define
an objective (contract), did workers return verified typed handoffs, and did the work reach a
commit only after the contract's SIGNAL ran. Everything here is a query over the Event Log
(contracts/handoffs) and the opencode session DB (task trees) -- no new runtime recording.
"""

from __future__ import annotations

import os

from core.log.store import EpisodeStore


def _db_path() -> str:
    import core.config as _cfg
    return _cfg.load().memory_db


def scorecard(limit: int = 100) -> dict:
    """Compute the Layer-6 scorecard from the Event Log.

    Verified-task signals:
      - contracts: tasks where the orchestrator recorded a success-contract (goal-direction exists)
      - handoffs: worker returns, split by whether they carried verification evidence
      - escalations: promote events (the system handing off rather than guessing)
    A task that records a contract AND gets verified handoffs AND commits only after its SIGNAL ran
    is a verified, intervention-free completion -- the real metric.
    """
    if not os.path.exists(_db_path()):
        return {"available": False}
    store = EpisodeStore(_db_path())
    try:
        contracts = store.recent_by_role("contract", k=limit)
        handoffs = store.recent_by_role("handoff", k=limit)
        escalations = store.recent_by_role("escalation", k=limit)
    finally:
        store.close()

    verified = 0
    for h in handoffs:
        c = h.content or ""
        # a handoff is "verified" if its EVIDENCE line is present and not the unverified sentinel
        if "EVIDENCE:" in c and "(unverified)" not in c:
            verified += 1
    return {
        "available": True,
        "contracts": len(contracts),
        "handoffs": len(handoffs),
        "handoffs_verified": verified,
        "escalations": len(escalations),
        "verification_rate": (verified / len(handoffs)) if handoffs else None,
    }
