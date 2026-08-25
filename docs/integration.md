# How fools-trick integrates with the rest of the machine

This records how fools-trick composes with the two other config layers on magus, so the
seams do not rot. Three layers, cleanly separated by concern:

| Layer | Path | Owns | Scope |
|---|---|---|---|
| Desktop/dotfiles | `~/.magus-config` (symlinked into `~/.config`) | Omarchy/Hyprland overrides, terminals, nvim, tmux, GPU fan daemon, project launcher | the machine |
| Global opencode | `~/.config/opencode/` | machine-wide agent defaults (AGENTS.md standards, global skills) | every opencode session |
| Project harness | `~/Recipes/fools-trick/` | the distributed orchestrator+worker recipe | sessions run in this dir |

## Config composition (the merge)

opencode deep-merges config: global `~/.config/opencode/opencode.json` first, then the
project `opencode.json` overrides and adds. Running opencode inside fools-trick gives the
UNION of both. Consequences to keep in mind:

- The global AGENTS.md (the shared engineering standards) loads in every session, including
  here. The project AGENTS.md is a superset -- it adds the distributed-system contract,
  human-gate, validation standard, and comment/cruft rules. No conflict; the project extends.
- All global skills load here too. Most are irrelevant to this project (trading, frontend,
  paper-writing); `camofox-browser`, `postgresql`, and `timescaledb` are the ones that
  actually apply (the latter two to attune-library). This is harmless noise, not a bug.
- The project defines its own providers (`fool-ds4`, `magus`) with current, correct specs.
  These are the source of truth for this recipe; do not rely on global provider definitions.

The global opencode config was cleaned of dead providers (`fool` Qwen3.6 on :8000, a stale
`fool-ds4` deepseek-v4-flash IQ2XXS/1M, and `workstation` Qwen3.8 on :8898 asserting 131072
context). Those servers are gone or superseded, and leaving them risked a global-scoped
session routing to a model spec that no longer matches what magus serves. The global config
now carries only machine-wide preferences; providers live in the projects that use them.

## Provider / port map (the single source of truth)

| Provider (project) | Endpoint | Model | Context | Role |
|---|---|---|---|---|
| `fool-ds4` | `http://fool:8888/v1` | `deepseek-v4-flash-0731` (abliterated, EXL3) | 384000 | orchestrator (deep, slow) |
| `magus` | `http://127.0.0.1:8898/v1` | `qwen3.8-27b-obliterated` (i1-Q4_K_S) | 32768/slot, 131072 total | workers (fast, concurrent) |

`fool` resolves via `/etc/hosts` to the LAN address (192.168.1.11), NEVER Tailscale. The
worker is local on magus. Both `apiKey` are `dummy` (local servers, no auth). Both provider
timeouts are `false` (unbounded) -- a slow worker doing substantial work must not be
cancelled mid-generation; runaway workers are bounded by each subagent's `steps` cap, not
by wall-clock.

## Subagent permission model (the non-interactive invariant)

This is the subtlest integration point and the source of a real class of bug. A subagent
runs NON-INTERACTIVELY -- there is no human to answer a permission prompt. Therefore:

- No subagent permission may be `ask`. An `ask` on a non-interactive worker hangs forever
  waiting for an approval that never comes. Every worker permission is `allow` or `deny`.
  A denied command returns an error the worker can reason about; a hung one returns nothing.
- Every worker needs the shared scratch dir: `external_directory: { "/tmp/fools-trick/scratch/**": allow }`.
  Writers (`general`, `implementer`) create artifacts there; read-only workers
  (`explore`, `scout`, `reviewer`) read artifacts other workers wrote. Missing this grant
  makes a scratch access hit the default `ask` and hang -- the failure that silently broke
  artifact-passing before it was fixed.
- The human-gate (destructive git / infra / publish) is enforced by the `gates` plugin
  (`.opencode/plugin/gates.js`), which throws in `tool.execute.before`. This binds every
  agent including workers, and belt-and-suspenders `ask` rules on the primary `build` agent
  cover the interactive path.

Rule of thumb: a worker's config must guarantee that everything it legitimately needs is
`allow` and everything forbidden is `deny`, with nothing left at `ask`.

## Launching fools-trick from the magus project picker

`~/.magus-config/home/.config/projects.sh` drives the `tp` tmux project picker. fools-trick
is registered there so `tp` opens a session with preset windows (editor, opencode, worker
logs, status). The two repos stay decoupled otherwise: fools-trick is a self-contained
recipe with its own `make` targets; `.magus-config` only knows how to open a window in it.

## The compaction / doom-loop invariant (hard-won)

A model's `output` limit in opencode.json MUST be less than its `context` limit. opencode
computes compaction headroom roughly as `context - output` (it reserves the output budget as
response headroom). If `output >= context`, that headroom is non-positive and opencode compacts
the session on almost every turn.

For a worker window this was catastrophic: `output: 32768 = context: 32768` (headroom zero,
non-positive) made each worker compact away its own working memory every turn, so it forgot files it had just
read and re-read them forever -- a doom loop that looked like the worker "taking forever" or
"over-generating" (measured: one explore worker read the same config.sh 4 times, 65 compaction
events, 132 turns, 10-23k tokens for a two-line summary). The fix was `output: 8192` (< 32768).
After: 1 read, 0 compactions, 3 turns, 69 tokens.

This is guarded by a test (`tests/test_lib.sh`, "output < context for all models"). Do not set a
model's output limit at or above its context limit.
