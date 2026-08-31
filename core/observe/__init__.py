"""Observability: read the opencode session DB into a per-task rollup + trip-wire signals.

For a local rig, "cost" is not dollars -- it is tokens and wall-clock. The metric that matters
(playbook Layer 6) is tokens per completed task and per delegation, tracked over time so a
regression is a trip-wire, not a vibe. The opencode DB already records per-session tokens/cost
with parent_id, so root+descendant roll-up is a query, not new instrumentation.
"""

from core.observe.rollups import TaskRollup, task_rollup, task_rollups
from core.observe.scorecard import scorecard
from core.observe.tripcheck import TripWire, check
from core.observe.trace import format_trace, trace_recent_subagents, trace_session
