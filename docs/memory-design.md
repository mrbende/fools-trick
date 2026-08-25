# Persistent memory: architecture decision (build deferred)

Status: DESIGNED, not yet built. This records the decision so the next layer is
built against a settled shape, per the layered-growth rule. Implement only after
the verify-gate and human-gate are in place and proven (they are).

## Why this exists

fools-trick is not a pure coding harness. It is a thinking partner plus a coding
agent: it helps the user reason over time, and that requires memory that
survives across sessions -- what the user is building, his standing preferences,
decisions already made and their rationale, and facts learned that should not be
re-derived. opencode gives each session a fresh window; without durable memory
the partner has anterograde amnesia between sessions.

## Decision

Two-tier, markdown-first. Start with the smallest thing that works end to end
and is fully transparent, then add retrieval only if scale demands it.

### Tier 1 (build first): agent-written markdown memory files

- `memory/USER.md`  -- stable facts and preferences about the user and his
  goals. Slow-changing. The standing model of who he is and what he wants.
- `memory/PROJECT.md` -- per-project state: decisions made and why, current
  direction, open threads, things explicitly deferred. This is the working
  continuity file.
- `memory/JOURNAL.md` -- append-only dated entries: what happened each session,
  in one or two lines. The audit trail the other two are distilled from.

Mechanics:
- The orchestrator reads these at session start (wire via `instructions` in
  opencode.json, or a `chat.message` hook that injects them on the first turn).
- It updates them through normal edits. A memory update is a deliberate act, not
  a side effect: "record this decision", "update your model of me".
- Flush before compaction. opencode exposes `experimental.session.compacting`;
  a hook there can remind the model to persist anything session-only into the
  files before the window is summarized, so nothing load-bearing is lost.
- Everything is plain markdown in the repo: greppable, diffable, versioned,
  and correctable by hand. No opaque store. This is the transparency the whole
  system is built on.

Why markdown first: it is the leanest design that fully meets the current
requirement (continuity of a single user across sessions on a handful of
projects). It needs no new infrastructure, it is auditable, and it is the
Hermes-proven MEMORY.md pattern minus the machinery we do not yet need.

### Tier 2 (add only when Tier 1 hurts): SQLite + retrieval

Add if and only if the markdown files grow past what fits in context, or the
user works across enough projects that "load all memory" stops being free:

- SQLite with FTS5 for full-text recall over dated journal entries and
  distilled facts.
- A `memory_search` tool (same shape as the web tools) the orchestrator calls
  to pull only the relevant slice into context, instead of loading everything.
- The markdown files remain the human-facing source of truth; the DB is an
  index over them, rebuildable from them. Never the reverse.

Explicitly NOT adopting from Hermes now: the background curator (an aux-model
loop that consolidates/patches/archives memory autonomously). It is heavy,
depends on a second model running on a schedule, and it makes memory mutate
without the user in the loop -- which is exactly wrong for a system whose point
is an honest, inspectable second mind. If autonomous consolidation is ever
wanted, it comes after Tier 2 and stays archive-only (never auto-delete),
mirroring Hermes's own "pinned skills bypass, nothing is deleted" rule.

## Open question for the user (resolve before building Tier 1)

- Scope of memory writes: should the orchestrator update USER.md/PROJECT.md
  autonomously when it judges something worth remembering, or only on explicit
  instruction? Autonomy is more useful and more in the spirit of a real partner;
  explicit-only is safer and keeps the user fully in control of his own model.
  Leaning autonomous-with-transparency: it writes when warranted, but every
  write is a visible diff he can revert -- consistent with the markdown-first,
  nothing-opaque principle.
