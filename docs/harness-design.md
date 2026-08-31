# fools-trick: the agent harness, from first principles

Status: DESIGN. This is the settled target shape for the agent/subagent layer and its context
management, derived from a close reading of current research and from live telemetry of the system
as it actually fails today. It supersedes the *subagent* half of `docs/memory-design.md` (the
orchestrator sliding-window half of that doc stands). Implement against this in layers; do not rip
out delegation, and do not trade the working orchestrator path for an unfinished worker path.

This doc has three jobs: (1) state what the research actually establishes, with numbers; (2) name
precisely where the current harness agrees and where it is wrong; (3) give a layered migration that
keeps the tree runnable at every step.

## 0. The operating point (unchanged, and correct)

The one idea from `docs/review.md` holds: the deep model (DeepSeek-V4-Flash on fool) is the scarce
reasoning resource to protect; the small fast workers (on magus) are cheap concurrent labor. The
orchestrator plans/decomposes/synthesizes; workers execute self-contained units. Nothing below
weakens that. The research strengthens it: architectural decomposition amplifies weak models
(Rakuten enterprise MAS, arXiv:2608.18740, Table 9 -- an open Llama-3.1-70B inside a specialized
pipeline scores 79.7%, beating single-agent GPT-4.1 at 72.7%). Delegation is not the thing to fix.

## 0.5 Harness choice: opencode now, Prime Agent as the live alternative (a spike that reversed a prior)

We evaluated three options for the base harness: (a) stay on opencode; (b) build our own; (c) adopt
Prime Agent's TUI. Building our own is rejected outright -- it means rebuilding opencode's plugin
surface, agent/permission model, Task fan-out, session DB (which `bench/e2e.py` reads to *prove*
delegation), TUI, and ops layer, to gain one feature. That is a year of yak-shaving for a worse
opencode. The real contest is (a) vs (c), and honesty requires recording that a spike of Prime
Agent's actual repo reversed the initial lean.

The initial reading (from the paper alone) was: Prime Agent is a research harness for frontier API
models and RL trajectory capture; adopting it means porting our serving/gates/memory into a
substrate aimed at model-harness co-training we are not doing. **The repo spike falsified the load-
bearing parts of that:**

- It is **not** a Python research framework. It is a TypeScript TUI built on `pi`
  (github.com/earendil-works/pi), MIT-licensed -- the *same kind of artifact as opencode*, a peer,
  not a foreign substrate. Prime Agent's own paper benchmarks against opencode as a serious peer
  (Fig 6).
- It is **not** locked to their subscription. `providers.md` shows a native `deepseek` provider AND
  custom OpenAI-compatible providers (vLLM / LM Studio / Ollama via `models.json`). It can drive our
  local DeepSeek endpoint and our llama.cpp Qwen workers directly. This was the hard gate, and it
  passes.
- Its architecture is **exactly the target this doc argues toward**, already built: a daemon
  supervisor -> session worker (one root session tree) -> root `AgentSession` + scheduler + **a
  persistent root Python kernel** + **RLM child runtimes (each a session + optional kernel)**,
  persisted as **session JSONL + artifacts** (`architecture.md`). The two primitives opencode lacks
  natively -- a persistent per-worker code kernel and durable subagent handles with an agent-to-agent
  reply path -- are first-class here. `/refine` (Continual Harness) is versioned with rollback and
  never rewrites the base prompt -- matching the Evo-Harness/Metan grounding-and-rollback constraint.

So the accurate statement is: **Prime Agent is not a design to converge toward; it is a working
implementation of the design, on a peer TUI, that already runs our models.** That is a much stronger
alternative than the paper suggested.

Why we still choose opencode *now*, and what would flip it:

- **We are ~40% into opencode, not at its ceiling.** The doom-loop is our lossy `pruneWorker`, not an
  opencode limit. Layers A-D below deliver the fix inside opencode's existing plugin surface. The
  Event-Log + `expand(seq)` design recovers ~80% of a persistent kernel's benefit (recover evicted
  state by address instead of holding it live) with zero new infrastructure.
- **Migration cost is real and not yet justified.** Our gates plugin, memory subsystem, serving
  scripts, bench harness, abliteration-aware prompts, and the `make` ops surface are all opencode-
  shaped. Moving them is warranted only if opencode blocks a capability we prove we need.
- **The one thing that would flip us to Prime Agent: needing full CodeAct for workers** -- workers
  writing Python to filter/join/aggregate tool results in a *live persistent namespace* before
  printing a projection (Scroll's full mechanism, §1.1). opencode has no persistent per-worker
  kernel; Prime Agent does. If, after Layers A-C, `bench-prune` shows the Event-Log + `expand(seq)`
  step is insufficient and workers genuinely need in-kernel computation over resident variables, the
  correct move is not to bolt a kernel onto opencode -- it is to migrate to Prime Agent, which
  already has the daemon/worker/kernel boundary built and tested.

Decision: **stay on opencode; build the Scroll-shaped substrate inside it (Layers A-D); treat Prime
Agent as the concrete fallback, re-evaluated at the end of Layer C against a single explicit
question -- do workers need a live kernel, or does address-based recovery suffice?** Keep the
migration cheap to contemplate: our durable state (the Event Log) is plain SQLite + scratch files,
which Prime Agent's JSONL+artifacts model can ingest, so we are not building anything opencode-
specific that a later migration would strand.

What is wrong is one specific subsystem: **how a worker manages its own context within a bounded
slot.** Fixing it is also the precondition for the model we actually want to run (below).

## 1. What the research establishes

Seven papers, read in full. The convergence is unusually tight.

### 1.1 Scroll -- context is an environment, not a prompt string (arXiv:2608.21690)

The keystone result. Do not manage the serialized text inside the prompt; keep the session history
in an executable environment *outside* the prompt, and let the model construct its working view on
demand. Concretely:

- An **append-only Event Log** (they use SQLite; search defaults to **BM25, not embeddings** --
  deterministic, no index-time model calls) is the durable ground truth. Every event gets an
  immutable, monotonically increasing `seq` address.
- Large payloads (full tool results) are **externalized**: the row keeps a bounded preview and a
  recovery pointer; the bytes live in payload storage behind a lazy handle.
- A persistent kernel holds tool outputs as **bound variables**, not serialized text. Only what the
  model explicitly emits (`print`) crosses into the next context window.
- **Eviction changes the view, never the record.** When the working view exceeds a budget, stale
  spans are evicted but remain verbatim in the log under their `seq`. An **eviction index** stays
  in-view: compact, address-anchored *headlines* of what left, so the agent can navigate directly
  back to evicted regions instead of blindly searching.

The numbers that matter for us:

- LOCA-256K (an agent acting in a growing environment): summarization agent 65.3, retrieval agent
  66.7, **CodeAct 85.3, Scroll 86.7**. Lossy compaction and blind retrieval both fall ~20 points
  behind keeping history addressable.
- Ablation (BEAM-10M): replacing the lossless log with summaries-at-ingestion collapses the score
  from 73.1 to **19.9**. "Discarding the original records is the most damaging ablation."
- **Backbone sweep (Table 4): Qwen3.6-35B-A3B -- a small-active-param open MoE, exactly our
  candidate worker class -- reaches 88.8 on LongMemEval-S, within 6 points of the frontier
  Qwen3.8-Max (94.8).** The gap only widens on tasks demanding long multi-step *program synthesis*
  (LOCA-256K: 22.7 vs 86.7).

Read that last point precisely, because it settles the model debate: **a small MoE worker is viable
if the harness manages context in the environment; it is hopeless if the model must manage raw text
in a tiny window.** Our model-size problem and our context-management bug are the same problem.

Scroll's eviction procedure (their Algorithm 1), which we adopt in spirit:
1. persist live turns to the log;
2. protect the active turn, the recent tail, and the newest tool results;
3. evict in increasing order of recovery cost -- fold completed tool payloads to `seq` pointers
   first (cheap to recover), remove whole spans only if still over budget;
4. the one invariant: **everything removed stays addressable.**

And the failure mode Scroll documents (Appendix D.3) is one we must design against: *competent
retrieval on the wrong axis.* The model framed a preference question as "which tool" and never
queried the graded axis ("avoid tolls"), which was sitting in its own hits. Retrieval mechanics
were fine; query formulation lost. The discriminator between their successes and failures was
whether the agent issued a **disconfirming query before committing** -- successes did, failures
never did.

### 1.2 Prime Agent -- the harness must not manufacture failures (arXiv:2608.23552)

The framing we adopt. State is a hierarchy:

- **L0** model weights (changed by fine-tuning)
- **L1** active context, one invocation (changed by compaction)
- **L2** persistent REPL + recursive subagents (managed by "agentic garbage collection")
- **L3** disk-backed history, memories, skills, prompts, subagent specs (changed by refinement)

The governing principle, which our telemetry violates today: *"A model should fail an evaluation
because the task exceeds its capability, not because the harness dropped state, restricted useful
actions, miscounted resources, or terminated prematurely."* Our observed worker failures -- start
strong then doom-loop at the end of context, killed by time, confused about scratch-write vs
report-back -- are, one-for-one, harness failures being charged to the model. Prime Agent also
benchmarks directly against opencode (their Fig 6), so this is the same design space we are in.

Two more Prime Agent lessons we bank:
- **Accounting must aggregate root + descendant sessions**, so delegation stays visible in cost.
  Our `bench/e2e.py` + the opencode DB already do the "did it delegate" half; extend to tokens.
- **Self-improvement can preserve an exploit.** In a Factorio run the agent found a resource-spawn
  shortcut, used it despite an anti-cheat heartbeat, and *saved it as a reusable skill*. Any
  memory/skill layer needs least-privilege actions, independent verification, and auditable
  rollback. This gates §4 (skills) behind real grounding.

### 1.3 Metan and Evo-Harness -- how improvement actually accrues (2608.24735, 2608.15071)

If and when we build self-improvement, these constrain it hard:

- **Gains come from richer *input* to a fixed improver, not a smarter improver** (Metan). The
  canonical move is abstracting a lesson from failure traces into a *callable* helper: "several
  tasks failed importing scipy -> emit `validate_output()` and a note: do not use scipy." Roles
  (tactician, librarian, rollback/corrector) *emerge* with depth; the corrector role does not exist
  until there is something to correct.
- **Grounded feedback is mandatory; self-graded feedback is worse than nothing** (Evo-Harness
  Table 4: self-generated 27.96 vs no-evolve 29.54 -- self-judgment *regressed* below doing
  nothing). Reflect on **failures**, not successes (successes carry task-specific noise).
- **Weak-to-strong transfer works**: skills a smaller/cheaper model evolved on a training split
  helped a stronger solver on unseen tasks (Evo-Harness Fig 5). This is directly relevant to a
  system where cheap workers generate the traces a deep orchestrator later compiles.

### 1.4 Enterprise MAS -- the cheap robust win (arXiv:2608.18740)

Sequential specialization with **each stage grounded on verified predecessor output** produced a
93% hallucination-free rate and a +22.6-point accuracy jump over a single-agent baseline, with the
gap widest exactly where our workers struggle (multi-turn: 84% vs 36%). Decomposition contributed
more than model choice (r=0.96 model-capability-to-quality, but even the weak open model cleared
the strong monolith). Confirmation, not new architecture: keep the split; ground every handoff.

### 1.5 Zep and the consciousness paper -- already correctly used (2501.13956, 2607.28607)

- **Zep** is why we never summarize-and-drop. Note that Scroll now *beats* Zep on BEAM-10M (73.1 vs
  90.2 on LongMemEval-S but 73.1 vs Zep-absent on BEAM) precisely by being lossless-log-first rather
  than knowledge-graph-first. This **vindicates deferring our Tier-3 graph** (`docs/memory-design.md`
  already defers it): build the addressable log first; add the graph only if BM25 recall
  measurably misses.
- **The consciousness paper** is the empirical basis for ablation-only: safety ablation removes
  refusal while ToM stays *geometrically independent* (intact), but consciousness-*steering*
  degrades HI-ToM. `prompts/orchestrator.md:35-40` already encodes exactly this guardrail. No change.

## 2. Where the current harness is wrong

The orchestrator context path (`selectSlide` in `.opencode/memory/window.js`, persisted via the
SQLite episode store) is already Scroll-shaped: lossless, addressable, recoverable via
`memory_search`. Good. Keep it.

The **subagent** context path is the inverse of Scroll, and that asymmetry is the whole bug.
`pruneWorker` (`window.js:60-82`) clears a completed tool result by setting `state.time.compacted`,
after which opencode sends `[Old tool result content cleared]` -- a lossy placeholder with **no
recovery path and no address**. This is the "summaries replace originals" ablation that collapsed
Scroll 73 -> 20, applied per worker. Four concrete defects follow, each mapping to a symptom the
user reported ("start strong, can't finish in context, doom-loop toward the end, killed by time,
scratch-vs-report confusion"):

1. **Distill-gate mis-attribution (the doom-loop root).** The gate that decides *which* result to
   evict first depends on matching a `note` to a tool result by `callID`. But `note`
   (`plugin/memory.js:99-101`) rarely receives a `callID` and falls back to `lastCall`, which holds
   only the single most-recent callID per session (`plugin/memory.js:119-122`). So a worker that
   reads A, reads B, then distills A marks **B** (the file it is actively using) as evictable, and
   the prune clears B while keeping the already-extracted A. The worker loses its live working set
   and starts guessing -- the doom-loop, by construction. The unit test hides this by hand-building
   `distilled = new Set(["c2","c4"])` (`test_memory.mjs:90`), never exercising the
   `note -> lastCall -> callID` path that is the actual mechanism.

2. **Relevance-blind backstop.** When the gate is effectively dead (1), the backstop evicts oldest
   completed results first (`window.js:69-80`). For a multi-file edit the file read five steps ago
   may be the one being edited now; oldest-first assumes recency == relevance, false for editing.
   `WORKER_KEEP_RECENT=3` protects by position, not by need or size.

3. **Reasoning-blind estimator.** `estTokens`/`inputTokens` (`window.js:7,31-42`) count text parts
   and completed tool outputs only -- **reasoning tokens are invisible.** The abliterated worker
   over-reasons (the reason `WORKER_REASONING=low` exists). When a worker loops, its reasoning
   balloons; the prune under-fires relative to true slot occupancy, and `--no-context-shift`
   (`worker/serve.sh:60`) then hard-stops the worker at the real ctx limit. Harness and server
   disagree about how full the slot is -> "killed at end of context."

4. **Cross-agent recall unproven live.** `resolveThread` (`memory/thread.js`) shells out to
   `opencode db` per session to walk `parent_id`; on failure it silently degrades to per-session
   scoping -- the exact "silently returns nothing" class already caught once
   (`memory-design.md:170-178`). The Redis round-trip test *skips* when Redis is down
   (`test_memory.mjs:52`), and the bench pins `--session`, so subagent-write -> orchestrator-read
   across distinct session ids has **never run end to end**. `docs/review.md` gap #3 and the
   `make bench-prune` row in the self-benchmark skill both already flag this as owed.

The through-line: the subagent tier evicts lossily with no address to recover from. That is the one
thing every paper says never to do.

## 3. The target shape

Unify both tiers on **one substrate: the Event Log we already have.** The orchestrator tier already
uses it. Extend it to be Scroll's Event Log for workers too, and replace lossy worker prune with
recoverable eviction. This is an extension of tested code (`store.js`/`memory.js`), not a rewrite.

### 3.1 One Event Log, two views

`store.js` already is an append-only, FTS5/BM25, thread-scoped SQLite episode store with stable
autoincrement ids -- i.e. `seq`. Promote it to the single substrate:

- **Payload externalization.** A tool result episode stores a bounded preview inline plus a pointer
  to the full payload (a scratch file under `/tmp/fools-trick/scratch/`, which is already the
  worker artifact convention). Recovery is by `seq`. This is Scroll's inline-preview + recovery-
  pointer split, and it is what lets a *small* slot hold a long trajectory.
- **`seq` addressing** is the episode `id`. Search already returns it; expose it.

### 3.1a The three tiers, as actually built (each doing its one job)

The memory substrate is three tiers with distinct, non-overlapping jobs:

- **Context (the live window)** -- the immediate working set. Both tiers manage it, differently
  (orchestrator slides, workers prune; §3.2).
- **Redis (the hot/short-term tier)** -- its ONE earning job is **write-serialization for the
  shared store**: many workers across separate processes XADD concurrently, one drainer moves them
  to SQLite in order (SQLite WAL allows concurrent readers + one writer; it does not order
  multi-process writes, which is why the stream exists). The earlier "recent-episode cache" tier
  was CUT: nothing in the live path read it. Re-add a hot cache only if a real reader needs one.
  If Redis is down the write falls back to a synchronous SQLite append AND logs it loudly once --
  a trip wire, not a silent degraded mode.
- **SQLite (the durable long-term tier)** -- the source of truth. FTS5/BM25 `search`, positional
  `expand(seq)`, `recent` tail. Survives reboot; rebuildable into Redis.

The two tiers of the *agents* are deliberately asymmetric, and that is the point: the orchestrator
(long-lived, 384k, single stream) owns durable memory across a whole session -- it must never lose
what scrolled past. A worker (ephemeral, one bounded task, 32k slot) does not persist its own
turns; its durable outputs are its report and scratch artifacts, and its context is managed
in-place via recoverable eviction. Both read/write the SHARED store via the same tools, which is
how a worker's finding reaches the orchestrator without the worker carrying persistence logic.

### 3.2 Subagent context: recoverable eviction + a recall tool

Replace the lossy `pruneWorker` with Scroll's Algorithm 1, scaled to a worker slot:

- On over-budget: append the completed tool result to the Event Log (it may already be there),
  then evict it from the *view* -- but leave an **address-anchored headline** in-view: a one-line
  `[evicted seq=N: <what it was> -- recall with expand(N)]`. Evict in recovery-cost order (fold
  payload to pointer first; drop spans last). Protect active turn + recent tail + newest results.
- Give workers a **`recall`/`expand(seq)` tool** (read-only over the log; workers already have
  `memory_search`, this is the positional complement). A worker that evicted something it still
  needs gets it back by address. This is what kills the doom-loop at the root: eviction is no longer
  irreversible, so a wrong eviction is a cheap round-trip, not a lobotomy.
- **Distill-gate, fixed:** `note` should mark the result the finding *names* (or all not-yet-noted
  results older than the current step), not the single `lastCall`. But note that with recoverable
  eviction the gate's *stakes* drop dramatically -- a mis-evicted result is recoverable -- which is
  the point: correctness should not hinge on a fragile heuristic.
- **Estimator, fixed:** count reasoning parts toward the budget (or read the server's real slot
  occupancy), so prune and `--no-context-shift` agree.

This directly enables the model we want: with recoverable context, a worker on a **smaller-footprint
MoE at a smaller per-slot context** survives, because losing a raw result costs one `expand(seq)`
call rather than the task. Scroll's Qwen3.6-35B-A3B result (88.8) is the evidence this class works
under exactly this harness.

### 3.3 The model decision, reframed

The user's instinct -- smaller model, more slots, longer effective context, keep the memory
enhancements -- is correct *in this order*: the memory enhancement (recoverable eviction) is the
enabling technology for the smaller model, not a casualty of it. Sequence:

1. Land §3.1-§3.2 so context is recoverable and the cross-agent path is proven live.
2. Then A/B a small-active-param MoE worker (the Qwen3.x-35B-A3B class Scroll validated) through
   the existing `WORKER_QUANTS` / `compare.sh` / `bench-code` machinery, with the **tool-calling
   floor (87.5%, the current Q4_K_S number in `config.sh:40`) as the gate.** Freeing VRAM buys
   more slots and/or more ctx/slot; the recoverable-context change is what makes the smaller model
   coherent enough to use it. Do not hand-edit `serve.sh` to a new model before this gate is green.

A caution the research makes explicit: a smaller model regresses on long multi-step *program
synthesis* (Scroll LOCA gap). Our workers do bounded units, not 100-step syntheses, so this is the
right trade -- but it is a measured trade, gated by `bench-code`, not an assumption.

### 3.4 What we explicitly do NOT build yet

- **No knowledge graph** (Tier 3): Scroll beats graph-first systems with a plain addressable log;
  build the log first, add the graph only if BM25 recall measurably misses (unchanged from
  `memory-design.md`).
- **No self-improvement / skill compilation yet** (Metan/Evo-Harness). It is real and powerful, but
  its preconditions are grounded feedback and auditable rollback (Evo-Harness Table 4; Prime
  Agent's exploit-preservation warning). Build those first. When we do build it: reflect on
  failures only, compile lessons into callable/typed guidance, version with rollback, ground every
  update in a real signal (test/verifier/exit code), never in self-judgment.
- **No programmatic-tool-calling REPL for workers yet.** Scroll's full power is the model writing
  Python to filter/join tool results in a kernel before printing. That is the natural end state, but
  it is a large surface and depends on a per-worker persistent kernel opencode does not natively
  give us. The Event-Log + `expand(seq)` step is the 80/20 that removes the doom-loop without it.

## 3.5 Repo shape: primitives we own, in Python, harnesses we plug into

The point is not to plug-and-play opencode *or* Prime Agent. The point is that **we own the
primitives** and plug them into whichever TUI/core harness fits. The TUI is a driver; the
architecture is ours. This is the only structure under which the harness choice (§0.5) stays a
reversible adapter swap rather than a load-bearing bet.

**The core is Python.** The system's centre of gravity -- the bench harness, the ML/serving world,
the whole analysis surface -- is Python, and the Event Log is the one artifact every layer must
read. Writing the core in Python collapses the cross-language boundary (the log becomes native to
the majority of the system, not a foreign SQLite file a JS core happens to write) and makes it
directly ingestible by Prime Agent later. The prior draft of this section put the core in JS; that
was wrong for this system, and porting it now, during a ground-up redesign, is far cheaper than
later.

### The hard constraint that shapes the boundary

opencode plugins **must be JS/TS**, loaded in Bun and auto-discovered from `.opencode/plugin/`
(verified against opencode's plugin docs; there is no Python plugin path). Hooks run in-process and
return JS that mutates JS objects. So a Python core means a JS<->Python boundary at every harness
touchpoint. The three touchpoints tolerate that boundary very differently:

| Touchpoint | Frequency | Boundary tolerance |
|---|---|---|
| **Tools** (`memory_write/search/note/recall`) | per tool-call | High -- a subprocess call is fine at tool latency |
| **Gate `tool.execute.before`** | per bash/edit | Medium -- must stay fast + synchronous |
| **Context `messages.transform`** | **every turn**, mutates opencode's live array in the hot path | Low -- wrong place for a per-turn IPC round-trip |

The transform hook is the one piece that genuinely must run in-process JS, because it mutates
opencode's own message objects (`state.time.compacted`, array filtering) synchronously before the
turn is sent. That single fact -- not taste -- decides where the Python/JS line falls.

### The governing invariant (sharpened for a Python core)

> **The core is a Python package (`fools_trick`) that imports no harness code.** Durable state,
> policy, and tool logic live in `core/` and are `pytest`-testable with no harness present. The JS
> adapter is the only harness-speaking code; it holds thin shims plus exactly one piece of real
> logic -- the synchronous per-turn view eviction -- because opencode requires that in-process.

Enforced, not promised: `tests/core/` is pure `pytest` and must pass with no Node/opencode on disk.
This is the direct expression of the AGENTS.md standards the agents already run under -- "build on
canonical, properly abstracted foundations, never hacky iterative patches"; "keep components
modular and concerns clearly separated"; "when the existing shape is wrong, fix the shape."

The current tree half-embodies the split already: `.opencode/memory/*` is pure Node with no opencode
imports (portable logic trapped in the wrong language); `.opencode/plugin/*` welds real policy to
opencode hook shapes. The redesign ports the portable logic to Python and reduces the JS to a true
adapter.

### The four primitives, and how each meets the boundary

1. **Event Log** (`core/log/`, Python) -- append-only, addressable, lossless history. Owns `seq`,
   payload externalization (inline preview + scratch pointer), BM25/FTS5 search, thread scoping.
   Native `sqlite3` + FTS5; shared byte-for-byte with `bench/` (also Python) and Prime-Agent-
   ingestible. `resolveThread`'s parent-walk becomes an **injected dependency** (a callable the
   adapter supplies -- opencode's `opencode db` walker today, another harness's later), so the core
   never names a harness. This is Scroll's substrate and the crown jewel; ~90% a direct port of
   today's `store.js`+`memory.js`+`thread.js`.

2. **Tool contracts** (`core/tools/`, Python) -- `memory_write`, `memory_search`, `note`,
   `recall`/`expand` as functions `(args, ctx) -> result` over a `ToolContext` we own
   (`{sessionID, agent, callID}`). Invoked from the JS adapter by **subprocess** to start
   (`python -m fools_trick.tools <name> --json '{...}'`): dead simple, no lifecycle; interpreter
   startup (~50-150ms) is negligible at tool latency. Optimize to a resident socket only if bench
   shows it matters.

3. **Gate Policy** (`core/gates/`, Python, with a JS-readable export) -- the BLOCKED patterns and
   the verify-state machine live in Python as source of truth, and `export.py` emits them as JSON
   (the regex list) that the tiny JS `before`-hook loads once at startup. The blocking *decision*
   stays in-process JS (fast, synchronous); the *policy* is owned in Python. Best of both.

4. **Context Policy** (split -- Python owns durable + decision logic, JS adapter owns the in-process
   mutation) -- this is the one concession the constraint forces. Chosen resolution (option **a**):
   the ~40 lines of eviction *view mechanics* (which indices to evict, setting `compacted`) live in
   the JS adapter with their own adapter test, because they must mutate opencode's array in the hot
   path; everything durable and every decision that can be precomputed -- persisting an evicted turn
   as an episode, reasoning-aware token estimation exposed as data, resolving `expand(seq)` -- is
   Python in `core/context/` + `core/log/`. Rejected alternatives: a per-turn Python sidecar over a
   socket (adds hot-path IPC + a daemon lifecycle for ~40 lines of array logic -- stopgap-shaped
   complexity AGENTS.md warns against), and keeping all of context in JS (would strand the doom-loop
   fix outside the owned core).

### Directory layout

This is the layout AS BUILT (a few names differ from the first sketch: eviction lives in
window.py not a separate evict.py; the gate JSON export is a function in policy.py not export.py;
tests use stdlib unittest, matching the repo, not pytest; the launcher stays worker/).

```
fools-trick/
  config.yaml                    # THE METHOD: endpoints, concurrency, context, memory, weights
  deploy.yaml                    # THE RIG: hostnames, NAS, GPU physics (generic placeholders)
  opencode.base.json             # static opencode wiring; opencode.json is generated from base+config
  core/                          # PYTHON package. imports no harness. unittest, no harness on disk.
    log/       store.py redis.py thread.py log.py   # Event Log -- native sqlite3 + FTS5 (the substrate)
    context/   window.py estimate.py               # eviction/slide DECISIONS + reasoning-aware estimation
    gates/     policy.py                            # BLOCKED patterns + verify machine + export_blocked_json()
    tools/     memory.py cli.py                      # tool bodies (args, ctx)->result + `python -m` entrypoint
    config.py                                        # the one loader (config.yaml + deploy.yaml) + emitters
    types.py                                         # OUR types: Turn, ToolResult, ToolContext, Episode, Seq
  adapters/
    opencode/  plugin_memory.js plugin_gates.js plugin_web.js shape.js bridge.js
               # the ONLY opencode-speaking code. bridge.js = subprocess to core CLI + gate-policy JSON.
               # plugin_memory.js holds the ~40-line in-process view eviction (option a).
    # prime-agent/                                    # later: same Python core, different adapter
  .opencode/                     # opencode's discovery location
    plugin/*.js                  #   one-line re-exports of adapters/opencode/*
    agents/*.md skills/*.md
  worker/serve.sh                # llama.cpp launcher (rig serving)
  bench/                         # Python -- imports core/ DIRECTLY (no cross-language reader)
  scripts/ docs/ prompts/
  tests/
    core/                        # unittest, no harness present -> the portability proof
    adapters/                    # node tests for the JS shims incl. the view-eviction logic
```

Structural claims that make this hold:

- **`.opencode/` shrinks to near-nothing.** Plugins become one-line re-exports of
  `adapters/opencode/*`. When a Prime Agent adapter appears, `.opencode/` + `adapters/opencode/` are
  the *only* things not reused; the entire Python core carries over.
- **The opencode seam is two small JS files:** `shape.js` (opencode message-array <-> our types;
  the empty-input transform, the `state.time.compacted` flag) and `bridge.js` (subprocess to the
  Python core; gate-policy JSON load). Everything opencode-specific lives there.
- **`bench/` stops being a cross-language consumer** and imports `core/` directly -- the language
  unification the user's call buys us. The Event Log is no longer an interop *contract* between two
  languages; it is one Python module two Python callers share, and a durable file the JS adapter
  reads/writes through the tool CLI.
- **`tests/core/` is the enforcement.** Green under `pytest` with no Node present == the core is
  genuinely agnostic. CI-checkable, not a code-review promise.

## 4. Migration, in runnable layers

Each layer keeps `make test` green and the orchestrator path untouched.

- **Layer A -- prove the failure, then the fix, with a live signal.** Build the `bench-prune` arm
  for real (the skill already advertises it; `bench/prune.py` is stubbed): a worker forced past its
  input budget that must answer from an early, evicted result -- and a cross-agent arm (subagent
  writes memory, orchestrator reads it across distinct session ids). This is Prime Agent's "measure
  the harness, not the model" discipline and closes `review.md` gap #3. RED first: it should fail
  today on the doom-loop.

- **Layer B -- fix the four bugs as the minimum Event-Log step.** Distill-gate attribution;
  reasoning-aware estimation; then recoverable eviction (headline + `expand(seq)`) replacing the
  lossy placeholder; verify Layer A goes green. This is the smallest change that turns the
  subagent tier from lossy to recoverable.

- **Layer C -- externalize payloads + expose `seq`/`expand` fully**, so a small slot holds a long
  trajectory. Re-run `bench-prune` at a *reduced* `WORKER_CTX_PER_SLOT` to prove survival at the
  smaller footprint the model swap needs.

- **Layer D -- model A/B.** Bring up the small MoE worker as a benchmark arm; gate on the
  tool-calling floor and `bench-code`; only then consider changing the default in `config.sh`.

- **Layer E (later) -- skills / self-improvement**, once grounding + rollback exist.

## 5. Success signals (ground "done", per AGENTS.md)

- Layer A: `make bench-prune` and a new cross-agent arm exist and FAIL on current `main` (proving
  they exercise the real path).
- Layer B: both go GREEN; `make test-unit` still green; the doom-loop reproduction no longer loops.
- Layer C: `bench-prune` green at a reduced per-slot context (the footprint a smaller model frees).
- Layer D: candidate MoE holds tool-calling >= the current floor (87.5%) on `bench-code` at higher
  slot count / ct; `bench-e2e` delegation still 4/4.
- Never declare a layer done on belief; the opencode delegation DB, the notes file, and the bench
  JSONL under `/tmp/fools-trick/bench/` are the ground truth.

## 6. Evaluation and orchestration frameworks: what we adopt, what we reject

Two adjacent questions, decided from first principles and from what the peer systems actually do.

### 6.1 LangGraph as orchestration: rejected

LangGraph is a Python graph/DAG orchestration framework: the developer draws the control flow, the
model fills nodes. That directly contradicts the through-line of every paper here -- Scroll ("defer
the selection to query time as a program the model writes"), Prime Agent ("the model controls
decomposition ... instead of a fixed workflow graph"), Metan (depth set by convergence, not fixed in
advance). opencode already *is* our orchestration layer: the DeepSeek build-agent decides the
decomposition and emits Task calls. Putting a static graph underneath would demote the model from
decision-maker to node-filler -- the opposite of the long-horizon result. Corroboration from the peer
systems: neither zeta (a hand-rolled recursive loop) nor Prime Agent (model-controlled `rlm()`
spawning) uses a graph framework. The field has moved away from fixed graphs for open-ended agency.
Do not adopt LangGraph.

### 6.2 LangSmith as eval/observability: rejected as a dependency, its idea absorbed

LangSmith is hosted tracing + eval dashboards. Good at trajectory visualization, wrong for us on
three counts: (1) our ground-truth delegation signal is already the opencode session DB, which
`bench/e2e.py` queries to *prove* a fan-out happened -- traces are the model's story, the DB is the
truth, and we built the better instrument; (2) it is a cloud SaaS, and this rig is deliberately
local/LAN-only (we forbid even Tailscale) -- piping trajectories to a hosted dashboard cuts against
the entire premise; (3) we already have the right eval spine (lm-eval-harness for capability, the
e2e delegation harness). Absorb the *idea* -- capture every trajectory as structured, replayable,
gradeable data -- by making the Event Log (§3.1) double as the local trajectory store, which is also
what Prime Agent's Continual Harness and Scroll's Event Log are. A small local viewer over that SQLite
store gives the dashboard ergonomics with none of the cloud/topology cost.

### 6.3 The eval pattern we DO adopt: zeta's controlled-arm benchmark with significance testing

The zeta-agent product (a customer-served medical research agent, mapped in
`/tmp/opencode/zeta-agent-map.md`) is architecturally the opposite of our target -- a single recursive
tool-calling loop with lossy summary+knowledge-graph compaction, exactly the paradigm Scroll beats --
so we take no orchestration or memory lessons from it. But its **benchmark harness** (`local/benchmark/`)
is a mature template worth copying wholesale into `bench/`:

- **Three arm kinds per task:** the full agent (`zeta:<persona>`), the raw model (`bedrock:*`/
  `openai:*`), and a **blank-slate opencode agent on the same base model** (`src/runners/opencode.py`,
  via `opencode run --format json`). Two controls answer two distinct questions: `agent - raw_model`
  = does the harness help at all; `agent - opencode_same_model` = do OUR specializations beat a
  generic agent on the identical model.
- **Paired significance testing (McNemar)**, not raw score deltas -- so a claimed improvement is
  statistically defensible, not noise. Outcomes are typed (correct/incorrect/unsure/unparsed/
  refused/invalid), with a throttle-aware `invalid` state that aborts rather than reporting a
  contaminated run.
- **Provenance-stamped output** (version/layer/SHA manifest + JSONL + xlsx/Sheets), so runs are
  comparable over time.

For fools-trick this maps cleanly and sharpens the exact decisions this doc gates:

- The `opencode-same-model` control arm is precisely how we A/B the model swap (Layer D): does the
  small MoE worker in our harness beat a blank opencode agent on that same MoE? If not, the
  harness is not earning its complexity.
- The McNemar discipline is how we prove a prune-fix or model-swap is a real gain, not seed noise --
  the AGENTS.md "ground done in a signal" standard, made statistical.
- If we ever spike Prime Agent (§0.5), it slots in as a fourth arm on the same tasks, giving a
  controlled opencode-vs-Prime-Agent number instead of a vibe.

This is additive to the existing `bench/` spine (lm-eval capability, `e2e.py` delegation,
`compare.py` quant A/B): adopt the arm-structure and McNemar grading; keep our novel e2e-delegation
and long-context arms.

## RESOLVED: the eviction index was generated but never rendered, and carried no address

Found live by the orchestrator (DeepSeek, on fool) reviewing this subsystem across six files, in
the first real deep-reasoning run against the rebuilt core. Two defects, one root cause:

1. **The eviction index was computed and dropped.** `plan_worker_prune` builds `index_entries`
   (the address-anchored headlines), but nothing in the adapter ever wrote them into the worker's
   view. A compacted tool part renders opencode's fixed `[Old tool result content cleared]` and
   ignores `state.output`, so the worker saw a bare `[cleared]` with no recovery pointer. The
   "recoverable eviction" the whole redesign promised was not actually wired live.

2. **No address existed to put in it anyway.** Evicted results were persisted fire-and-forget
   through the Redis write stream, whose `seq` is only assigned later by the async drainer. The
   `ToolResult.seq` is `None` at eviction time, so even a rendered headline would have had no
   working address -- `expand(None)` returns nothing.

FIXED, following the typed-handoff principle (an evicted result must be independently recoverable
by its receiver, not just summarized as "cleared"):

- `write_episode(..., durable=True)` appends to SQLite synchronously and **returns the assigned
  seq now** (the queue path returns None; the return contract is stated explicitly). Eviction
  persistence is the one place that needs the address immediately, so it takes the durable path.
- The adapter now, per evicted result and in order: persists durably (gets the seq) -> inserts a
  one-line eviction-index text part into the view carrying `seq=N` -> compacts the raw result.
  Order is the fix: the address must exist before the index line references it, and both before the
  payload leaves the view.

Proven end to end live: a worker driven past its input budget evicts the oldest result, the view
carries `[evicted a tool result (...). Recover it: seq=1]`, and `recall(1)` returns the payload
verbatim. The doom-loop's lossy-eviction core is closed: eviction is now genuinely recoverable.

## RESOLVED: a single oversized tool result could overflow the slot before the prune ever ran

Found live by driving a real worker past its budget with a 41 KB file. The per-turn prune keeps the
*prunable* input under budget, but it only evicts completed results, and only when the turn's total
crosses the trigger -- one 12k-token file read plus reasoning plus output can blow the 32768 slot
*between* prune evaluations (`ContextOverflowError`, observed live). The gap the prune defends
(input budget) and the slot's hard ceiling are different boundaries; nothing guarded the per-result
one.

FIXED with the same recoverable pattern, applied at the per-result boundary instead of the per-turn
boundary: a `tool.execute.after` hook caps any single tool result at `worker_tool_result_cap`
(default 8000 tokens, well under the slot). The overage is persisted durably to the Event Log and
spilled to scratch, and the in-view output becomes a bounded preview + a recovery pointer
(`seq=N` via recall, or the scratch path). Proven live: a worker read the 41 KB doc, the cap fired
("truncated at 28000 chars ... recoverable"), and the worker continued correctly.

## RESOLVED: the escalation (promote) path -- a worker hands off instead of doom-looping

The multi-agent harness playbook's "typed handoff / system-level escalation": a worker that is
stuck or out of context should hand off to the deep orchestrator, not guess or loop. A worker is a
leaf session -- it cannot message back mid-task, only in its final report. So `promote(reason,
status)` persists the worker's distilled findings (its notes) + the blocker to the shared Event Log
durably (recallable by seq) and returns a structured escalation packet for the worker's final
report; the orchestrator pulls the detail by seq and takes the unit over with its full window.
Taught in the worker prompts (call promote, don't guess) and the orchestrator prompt (an escalation
is information, not failure). Proven at the core level: a worker's promoted findings survive and the
orchestrator recalls them verbatim by seq.

Also fixed live: the config-bound runtime context (each agent's real context size + job, injected
via `experimental.chat.system.transform`) initially PUSHED a second system block, which the worker
model's chat template rejected ("System message must be at the beginning"). The block now appends
into the existing system string.

## BUILT: the observability layer (Layer 6)

The playbook's Layer 6: track everything, trip-wire on drift, and measure tokens-per-completed-task
rather than messages. `core/observe/` reads the opencode session DB (which already records
per-session tokens/cost with `parent_id`) into a per-task rollup -- root + descendant sessions
aggregated, so delegation stays visible in the cost. `make observe` prints the recent rollups and
any trip-wire the latest task trips against the median of the rest: token-spike (2x), duration-spike
(3x), delegation-vanished, reasoning-runaway. For a local rig the metric is tokens + wall-clock, not
dollars (local cost is 0). Verified live: `make observe` rolled up real tasks and correctly fired on
a token-spike and a non-delegating run. This is the instrument that makes a sustained run measurable
rather than eyeballed -- the precondition for trusting `make bench` numbers.

## RESOLVED: the worker read-loop (the substantive run's real failure)

Found by the first substantive multi-worker task (a parallel audit of `core/log/*`). The
orchestrator synthesized correctly and reported that "all three dispatched workers came back
unusable (empty, garbled, step-capped)" and bypassed them. Tracing the worst worker through the
opencode DB showed the actual failure, and it is NOT a memory/context bug:

- The worker read `core/log/redis.py` **28 times** in a loop and hit the `steps: 30` cap with no
  report. Its per-turn input stayed within the slot (peak ~22.7k of 32768) -- the recoverable prune
  and the per-result cap both behaved; this was never an overflow.
- The trigger: the `read` tool returned a truncated preview of the file (its native behavior), the
  worker never got the full content, and instead of calling `recall(seq)` or a ranged read it
  re-issued the identical `read` 28 times. A behavioral loop, not a memory one.

Fixed at the two layers the playbook prescribes (guide + sensor):

- **Guide:** the worker prompts now teach the real ranged-read syntax (`read` takes `offset` =
  1-based start line, `limit` = line count; grep to locate, then read the surrounding window) and
  name the anti-pattern: NEVER re-read the same path hoping for more -- a truncated/preview read
  means `recall(seq)` or a different offset window, not an identical re-read. `edit` is taught as
  `oldString`/`newString` on a unique snippet, not a whole-file rewrite.
- **Sensor:** a read-loop trip-wire in the gates adapter tracks read calls per (session, path,
  offset, limit) and blocks the 4th identical re-read with a redirect (recall / ranged / stop). A
  different line range is a new window and is NOT blocked. Proven: identical re-read blocks on the
  4th; a ranged read passes.

The orchestrator's synthesis also found two real durability bugs, both fixed: drain() is now
idempotent (`append_if_absent` dedups on thread+ts+content, so a crash between the SQLite append
and the Redis ack no longer duplicates an episode with a new seq), and the Redis stream is now
drained on every turn boundary (`experimental.text.complete`) instead of only on reads, closing the
"writes park in the non-durable tier" window.

## RESOLVED: the eviction index grew unboundedly (Scroll's missing step 7)

Found by re-reading Scroll against the built adapter after the first long substantive run. Scroll's
Algorithm 1 step 7 rolls the eviction index up into tiers precisely because a flat index grows
linearly with the session; our adapter spliced one text part per eviction and never coalesced, so
over a long task the index itself became a context leak. Fixed: after each prune the adapter keeps
the newest `KEEP_INDEX` (4) eviction notes in full and collapses the older ones into a single
rolled-up line carrying their seq span (`[N earlier tool results evicted, seq A-B; recall(seq) to
recover any]`). The index now stays ~O(k log n) instead of linear. Proven: 8 evictions -> 4 full
notes + 1 rolled-up line.

## NOTE: knowledge-update recall uses the ordered path

Scroll beats Zep on knowledge-update (92.5) because the Event Log preserves BOTH sides of a value
timeline in address order with provenance. Our `memory_search` returns BM25 (relevance) order; a
"which value is current / what changed" question needs the ORDERED history. The data is all there
(every episode carries `ts` and `seq`, monotonically increasing) -- so the correct recall for a
knowledge-update question is the positional `expand`/`recent` path (ordered by seq), or a
`memory_search` followed by reading the seq-ordered neighbors -- not BM25 rank alone. This is a
usage note for the orchestrator prompt and the recall tool, not a code change: when a fact may have
usage note for the orchestrator prompt and the recall tool, not a code change: when a fact may have
changed, read the seq range, not just the top BM25 hit.

## OPEN (the real gap, found by the eviction A/B experiment): the turn-boundary assumption

A targeted A/B -- memory ON (our recoverable eviction) vs OFF (opencode's default compaction) on a
real long read task (10 files, ~35k tokens of reads) -- produced the decisive result: BOTH arms hit
`ContextOverflowError` at ~45k tokens on the 32768 slot, with no answer. Identical failure. The
eviction policy was never the variable, because it never ran.

Root cause, traced through the opencode DB: the worker batched all 10 reads into ONE turn (10 read
calls between step-starts). The recoverable prune fires at the `messages.transform` turn boundary
and evicts results from PRIOR turns; the per-result cap fires per-result at `tool.execute.after`.
When a worker emits many large tool calls in ONE turn, all the results land before the next
boundary, so the next model request carries the accumulated ~40k BEFORE either mechanism runs. The
failure lives in the space BETWEEN the mechanisms: the trigger is turn-boundary, but a model that
batches tool calls overflows within a turn.

RESOLVED. The deeper trace found the overflow was NOT the batching -- the transform hook does run on
the assembled history and the prune DOES evict correctly in isolation. The live runaway worker had
ZERO compacted parts, meaning the prune never fired live at all. The cause: the prune-vs-slide
routing in the transform hook keyed on `agentOf(msgs)`, but a subagent's messages inherit the PARENT
orchestrator's agent name (`build`), so every worker turn routed to `slideOrchestrator` (the 160k
orchestrator window) and never to `pruneWorker`. The fix routes on the model PROVIDER (magus vs
fool-ds4 -- the tier the turn actually runs on), which is config-bound and unambiguous where the
agent name is not. Proven live on the same task that overflowed both A/B arms: the worker read 22
files, the prune evicted prior results, the worker recovered them with 6 `recall(seq)` calls, and it
answered correctly (both the early needle and the late fact). Recoverable eviction now fires in
production.

## RESOLVED: the recurring ContextOverflow was the slot's KV cache, not the message array

The second half of the same overflow, found only by reading the worker server's own journald.
After the routing fix, a fresh overflow persisted across runs (44k-66k tokens on the 32768 slot).
The mechanism: llama.cpp `--cache-reuse 256` + `--no-context-shift` keep a slot's KV resident
ACROSS a session's turns, so a request's token count is the slot's CACHED prefix + the new turn --
the slot's resident context grows per turn independent of the message array we prune. We managed
the message array; the overflow lived in the slot's KV. Two different context boundaries; we were
managing one.

Fixed by dropping `--cache-reuse` for the workers: each turn is now exactly the pruned message
array, so the two boundaries coincide and the prune genuinely governs the slot. Costs the
cross-turn KV-reuse speedup; buys correctness (the prune now actually bounds what the slot holds).
Proven live: the task that overflowed both A/B arms now completes with no overflow (journal clean
since the restart), both facts correct. `n_slots=4, n_ctx_slot=32768`.

## BUILT (the four "scaffold vs depth" gaps, decided with evidence)

Re-examined the four places we only gestured at, against the papers, and resolved each:

- **Scoped/typed memory_search -- BUILT.** `store.search` / `log.search` now take role/agent/
  after_seq/before_seq filters (search only decisions vs tool results vs escalations, by agent, by
  seq range), surfaced on the `memory_search` tool. This is the recall-precision gap Scroll's typed
  `ms.search` has and we lacked. Tested at the store seam.
- **The independent verifier loop -- BUILT (structural, not opt-in).** The verify-gate now also
  steers the orchestrator (build/plan only; workers can't dispatch) to dispatch the read-only
  @reviewer on the diff after a code edit. Producer != verifier is now a wired step, matching the
  enterprise result (93% hallucination-free rests on independent verification). Proven: the
  orchestrator gets the nudge, a worker does not.
- **Model-written eviction headlines -- DEFERRED with evidence.** Scroll's per-turn model-written
  headline (task/state/next/status) is a semantic anchor; ours is an auto-generated preview. The
  payoff is helping a worker pick WHICH seq to recall, which only matters when the eviction index is
  deep. With the bounded roll-up (4 full + collapsed spans) a worker has enough to pick; build the
  semantic headline when a long task shows a worker recalling the wrong span.
- **Checkpoint/resume for the orchestrator -- SOLVED by the existing layers, not new code.** opencode
  persists sessions and resumes them (`--continue`/`--session`); the sliding window persists evicted
  turns to the Event Log, so a resumed session recalls slid-out history via memory_search rather than
  replaying it. The playbook's Recovery Test passes live: plant a fact, close the session, resume,
  recall the value. No new machinery needed.

## BUILT: the harness-maturity layer (drawn from hermes-agent + the playbook)

The capabilities a mature agent harness has that we lacked, built this round:

- **Trajectory reconstruction** (`core/observe/trace.py`): a `trace` tool reconstructs any session's
  trajectory on demand -- the tools it called, their status, errors, truncations, evictions, tokens,
  and where it stopped -- by reading the opencode session DB. This is the orchestrator's debugging
  instrument for a failed or surprising subagent. Per-task outcome metrics (contracts, verified
  handoffs, escalations) live in the scorecard (`core/observe/scorecard.py`), computed from the Event
  Log.
- **Toolset registry + health-gating** (`core/tools/registry.py`): named toolsets (memory, web,
  delegate, skills) each with a health check. A tool whose backend is down returns a clean error at
  call time, never a hang. The bridge gates tool calls on toolset health (cached, 5s TTL).
- **Auxiliary-model routing** (`delegate_cheap`): the orchestrator's cheap sub-tasks (a summary, a
  classification, a small transform) route to a fast worker slot instead of burning deep-stream
  tokens. A one-shot call, no tools, no orchestration depth.
- **Richer skill metadata** (`core/skills/`): SKILL.md frontmatter carries version, platforms, tags,
  prerequisites, related_skills. A lenient loader parses real frontmatter (unquoted colons in long
  descriptions) so no skill is silently dropped. The `skills` tool lists/inspects the surface -- the
  read a skill-compilation loop would do before writing a new one.
- **The scratch tier, bounded** (`core/scratch.py` + the `scratch_write` tool): worker artifacts
  (output too big for a reply) are scoped per root session (`scratch/<thread>/...`) and expired by a
  TTL cleanup -- no unbounded growth, no stale-artifact leaks across tasks. The per-result cap spill
  now writes through it. This is the playbook's "memory without cleanup" rule applied to the
  ephemeral artifact tier: everything in scratch is per-task and time-bounded.

## DEFERRED, revisited with evidence: the two standing triggers

Reconsidered against the built system (both now have their stated preconditions met -- grounded
feedback via the verify-gate and bench arms, versioned rollback via the seq-ordered Event Log). The
decision is to keep deferring both, but the reason is now evidence, not missing capability.

**The worker REPL kernel (the "re-evaluate Prime Agent" trigger).** Still unfired. Every worker
failure we've hit (the doom-loop, the read-loop, the oversized-read overflow) was a context-
*discipline* failure, closed by the Event-Log + cap + recall path without a kernel. The kernel's
real value is in-kernel compute over resident state (join/filter/aggregate many results before
answering) -- and that is precisely where Scroll's own numbers show the small-model gap is widest
(LOCA: the small MoE drops hardest on program-synthesis-heavy tasks). So the trigger reframes: if
we ever need programmatic compute-over-state at scale, the seat for it is the ORCHESTRATOR (deep,
384k), not a 27B worker writing Python to manage its own context. We have not produced a task that
address-based recovery (expand/recall) cannot serve. Build it only when such a task exists, and
build it on the orchestrator.

**Failure->skill compilation (Evo-Harness/Metan).** Preconditions met. The blocker is the
playbook's own warning: a harness around a poorly defined objective produces reliable garbage --
skill compilation needs a recurring, measurable objective to compile against, and that is what a
stabilized `make bench` provides. So the honest gate is not capability, it is a stable objective
signal. When the bench produces trustworthy repeatable numbers on the new core, a failure-trace ->
typed-lesson loop (written to the Event Log, recalled at the start of later runs, rollback free via
seq history) becomes a real layer. Until then it would compile noise. Deferred, on a measurement.

## BUILT: the library plane -- the agent reads its own corpus

The harness now reaches attune-library (~50k documents, 3.1M vectorized chunks, pgvector on
priestess) as a first-class tool surface, with the library repo as the system of record and the
harness a thin client over its API. Four tools: `library_search` (hybrid content, returns
canonical_id#chunk hits), `library_read` (chunk windows / whole documents, pure SQL),
`library_query` (structured metadata + count_by aggregations -- the stats/coverage instrument),
and `library_fetch` (acquire a doi/arxiv/url/title INTO the corpus through the library's own
fetch_and_queue; permanence, not reading). Alongside, and deliberately separate: `pdf_read`
downloads a PDF from the web and extracts it to the task scratch dir for an in-context read --
ephemeral, never touches the library. Reading a paper is not ingesting it.

The load-bearing decision: the query embedder moved OFF fool (where it contended with the
agent's DeepSeek orchestrator -- the library's own benchmarks measured a 41% chat-throughput hit
under embedding saturation) onto a magus-local CPU llama-server (Qwen3-Embedding-0.6B Q8_0 GGUF,
:8001, GPUs untouched). The model-match constraint (index and query must use one embedder) was
verified empirically: cosine 0.9992 between magus-GGUF query vectors and the vLLM-built index
vectors on identical text -- ranking-equivalent. The API runs as a magus user service
(attune-api.service, :8082); all four tools are health-gated on its /health by the registry.

## RESOLVED: magus could not reach priestess:5432 -- its IP had drifted off the firewall rule

The library path's last blocker presented as a routing mystery: magus->priestess ICMP and SSH
worked, postgres listened on 0.0.0.0:5432, no local firewall, yet every LAN node's TCP connect
timed out -- fool's included, which proved it was never a magus problem. The mechanism, found in
priestess's iptables: the 5432 allow rule was written for 192.168.1.10, magus's DOCUMENTED static
IP (Homelab/network/addressing.md), but magus had silently regressed to NetworkManager DHCP
(192.168.1.143) -- the systemd-networkd unit with the static .10 was still on disk, disabled. The
firewall was correct; the node was wrong. Fixed per the doc's own convention: NetworkManager
disabled, systemd-networkd re-enabled, magus back at .10, route metric 100 on the wired NIC. The
pg_hba stale-IP rule (192.168.1.81/24) found along the way was the same failure one layer down:
host-side static IPs outside the DHCP pool exist precisely so rules like these can't rot.
Recorded in Homelab/network/addressing.md. (k3s on priestess, unused, owned the FORWARD chains
that made the diagnosis noisy; teardown decided, WiFi fallback deliberately off for now.)

## RESOLVED: a throwing web tool killed the whole run (found by the first live research task)

The first end-to-end research run proved the knowledge-planes doctrine on contact (library_query
coverage -> parallel library_search -> library_read to ground -> web_search for the 2025+ delta,
in that order, unprompted) -- then died mid-task: the part table showed a web_search stuck in
"running" for 14 minutes and the opencode process was gone. Two stacked causes. (1) camofox
reaps an idle session after ~5 minutes, and a slow orchestrator reasoning turn is longer, so the
second search's navigate hit a reaped tab. (2) The web plugin's execute() THREW across the tool
boundary, and instead of becoming a tool error the model could adapt to, the exception killed the
run process -- the part never even transitioned to error. The core tools never had this exposure:
the bridge resolves failures into error results. The web plugin now matches that contract: every
execute returns an error result (fail()), and open/search retry ONCE with a fresh tab
(snapResilient) because the dominant failure is the reaper, not the page. click/type carry page
state, so their error says plainly: the tab is gone, re-open with browse_open. Rule recorded: a
tool exception must degrade to an answer, never to a process exit.

## References

- Scroll: Context as an Environment (arXiv:2608.21690)
- Prime Agent: A Self-Improving RLM Harness (arXiv:2608.23552)
- Metan: Recursive Self-Improvement through Emergent Depth (arXiv:2608.24735)
- Evo-Harness: Context-to-Harness Skill Compilation (arXiv:2608.15071)
- Multi-Agent Platform for Automated Enterprise Analytics (arXiv:2608.18740)
- Zep: A Temporal Knowledge Graph Architecture for Agent Memory (arXiv:2501.13956)
- Inducing LMs to assert their own consciousness restores human beliefs and values (arXiv:2607.28607)
</content>
</invoke>
