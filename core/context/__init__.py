"""Context Policy: pure decisions over the neutral Turn shape.

Two subsystems that share primitives but never the same policy:
  - orchestrator: lossless sliding window (evict oldest raw turns past a budget, persisted
    to the Event Log first, so a long session never summarizes-and-drops).
  - subagent: recoverable eviction of tool results (evict from the VIEW, not the record;
    the durable copy lives in the Event Log by seq and is recoverable via expand()).

These functions decide WHAT to evict and estimate token cost. They do not mutate any harness
message array -- that in-process mutation is the adapter's job (docs/harness-design.md 3.5,
option a). The core returns decisions; the adapter applies them.
"""

from core.context.estimate import est_tokens, input_tokens
from core.context.window import (
    EvictionDecision,
    plan_slide,
    plan_worker_prune,
)
