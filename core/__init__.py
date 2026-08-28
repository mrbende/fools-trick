"""fools_trick core: harness-agnostic primitives for the distributed coding harness.

This package imports NO harness code (no opencode, no Prime Agent). It is the owned
core -- Event Log, context policy, gate policy, and tool bodies -- that a thin harness
adapter plugs into. See docs/harness-design.md section 3.5.

Invariant: everything here is importable and testable under pytest with no harness on
disk. If a module cannot be, it belongs in an adapter, not in core.
"""
