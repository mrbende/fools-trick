<h1 align="center">fools-trick</h1>

<p align="center">A self-hosted distributed coding agent: one deep orchestrator driving fast concurrent workers over an owned, lossless, recoverable memory layer — plugged into <a href="https://opencode.ai">opencode</a> through a thin adapter.</p>

<p align="center">
  <img src="https://img.shields.io/badge/opencode-harness-8ad7eb?style=for-the-badge&logoColor=D9E0EE&labelColor=1E1E2E" alt="opencode harness">
  <img src="https://img.shields.io/badge/DGX_Spark-orchestrator-76B900?style=for-the-badge&logo=nvidia&logoColor=white&labelColor=1E1E2E" alt="DGX Spark">
  <img src="https://img.shields.io/badge/2x_3080_Ti-workers-86dbce?style=for-the-badge&logo=nvidia&logoColor=white&labelColor=1E1E2E" alt="2x RTX 3080 Ti">
  <img src="https://img.shields.io/badge/license-MIT-a6e3a1?style=for-the-badge&logo=gnu&logoColor=D9E0EE&labelColor=1E1E2E" alt="License">
</p>

<p align="center"><em>An <a href="https://attuneintelligence.com/lab">Attune Intelligence</a> lab project — private hardware, open methods.</em></p>

---

> [!NOTE]
> **Open methods, not an application.** The *recipe* — an owned harness-agnostic core plugged into
> a TUI, a deep orchestrator + concurrent workers, and a recoverable memory layer — generalizes to
> any OpenAI-compatible inference, local or cloud. The *rig* (the specific machines and serving
> physics under `deploy/`) is one worked example. Fork the method; the hostnames are mine.

## The idea

`Agent = Model + Harness`. The model reasons; the harness is everything else — context, memory,
permissions, recovery, observability. The evidence that the harness is the lever is empirical: a
44-point GAIA swing and a 25-rank Terminal Bench jump on an *unchanged* model. fools-trick builds
that harness from scratch: the primitives (memory, context management, gates, observability) are
**owned Python** in `core/`, plugged into opencode's TUI through a thin adapter. opencode supplies
the agent loop and delegation; we own the substrate.

Three roles, each on the machine whose bottleneck it fits:

- **Orchestrator** — a deep, single, slow stream (DeepSeek-V4-Flash, 384k context). Plans,
  decomposes, dispatches, synthesizes. Holds the whole task and the durable memory.
- **Workers** — fast, concurrent, shallow (Qwen3.8-27B, 4 slots × 32k each). Execute bounded units:
  search, edit, review. Ephemeral; they escalate to the orchestrator rather than loop.
- **Memory** — a durable, addressable Event Log (SQLite + FTS5) with a Redis write-queue, so a
  session never summarizes-and-drops; it slides and recalls.

## The architecture

```
                          ┌─────────── you ───────────┐
                          │         opencode          │
                          └─────────────┬─────────────┘
                                        │  (plugins: .opencode/plugin/*.js -- one-line re-exports)
                          ┌─────────────▼─────────────┐
            THE ADAPTER   │   adapters/opencode/      │   the only opencode-speaking code
            (thin, JS)    │   bridge · shape · gates · memory · web
                          └───────┬───────────────┬───┘
                  per-turn hooks  │               │  per-tool call (subprocess to the core)
         (slide/prune decisions,  │               │  memory_* · note · promote · trace · skills ·
          system-context inject)  │               │  delegate_cheap · library_* · pdf_read
                          ┌───────▼──────┐   ┌────▼─────────────────────┐
                          │ core/context │   │   core/tools · core/log  │   THE CORE (Python,
                          │  slide+prune │   │   the Event Log + tools  │   harness-agnostic,
                          └───────┬──────┘   └────┬───────────┬─────────┘   no opencode import)
                                  │               │           │
                          ┌───────▼───────────┐   │     ┌─────▼──────────┐
                          │ the live message  │   │     │ the Event Log  │
                          │ view (evicted =   │   │     │ Redis stream   │ write-serialize
                          │ recoverable, seq) │   │     │ → SQLite (FTS5)│ durable, addressable
                          └───────────────────┘   │     └────────────────┘
                                                  │
   ┌────────────────────────┬─────────────────────┴───────────────┬────────────────────────┐
   │      orchestrator      │       the inference backbone        │      workers           │
   │  fool · DGX Spark      │   (deploy/<tier>/<backend>.yaml)    │  magus · 2x 3080 Ti      │
   │  DeepSeek · 384k ctx   │                                   │  Qwen · 4 slots × 32k    │
   │  plan · dispatch ·     │                                   │  explore·general·reviewer│
   │  synthesize · remember │                                   │  execute · note · promote│
   └────────────────────────┘                                   └──────────────────────────┘

   the library plane (attune-library owns it; the harness is a thin client over its API)
   ┌──────────────────────────────────────────────────────────────────────────────────────┐
   │  magus: library API :8082 (systemd user unit) · embedding llama-server :8001 (CPU,   │
   │  GGUF -- search never touches fool's GPUs)   priestess: postgres :5432 (~50k docs,   │
   │  3.1M vectorized chunks)   empress: NAS corpus store + inbox                          │
   └──────────────────────────────────────────────────────────────────────────────────────┘
```

A fan-out: the orchestrator plans, emits parallel `task` calls; each worker runs on a small slot,
reads ranged, distills to `note`, returns a digest; evicted or oversized tool results persist to the
Event Log (recoverable by `seq`); a worker that outgrows its slot calls `promote` to hand off with
its findings attached; the orchestrator synthesizes from digests and the log, never from raw dumps.

## The memory layer (the load-bearing piece)

opencode's default at a full window is *compaction* — summarize and drop, which is lossy and visibly
dumbs the agent mid-session. fools-trick replaces it with a lossless, addressable store and two
context policies over it:

- **Orchestrator — sliding window.** The oldest raw turns past the live-input cap are persisted
  verbatim to the Event Log, then dropped — never summarized. `memory_search` (BM25) recalls them.
- **Worker — recoverable eviction.** Past its budget, a worker evicts completed tool results from
  its *view*; each stays verbatim in the log under a `seq`, the view carries a one-line
  `[evicted ... recover it: seq=N]`, and `recall(N)` gets it back. Eviction is a cheap round-trip,
  not a lobotomy. A single oversized read is capped at ingestion (preview + seq pointer + scratch
  spill), so one big file can never overflow the slot between prune evaluations. The eviction index
  is bounded: the newest few notes stay in full, the older collapse into a rolled-up seq span.

The three tiers each do one job: **context** (the live window), **Redis** (write-serialization for
the shared store — many workers across separate processes XADD, one drainer moves them to SQLite in
order), **SQLite** (the durable source of truth). If Redis is down a write falls back to SQLite and
logs it loudly once, not silently.

## The tool surface (what the agents can call)

Registered through the adapter, gated by health (a down backend errors cleanly, never hangs):

| Tool | Role |
|---|---|
| `memory_write` / `memory_search` | persist / recall durable memory (scoped by role, agent, seq range) |
| `note` | a worker distills a finding so its raw tool result can be safely evicted |
| `recall` | recover an evicted/truncated result verbatim by its `seq` address |
| `promote` | a worker hands off to the orchestrator with its findings, when out of depth |
| `trace` | reconstruct a session's trajectory (tools, tokens, errors, where it stopped) |
| `delegate_cheap` | run a cheap shallow sub-task on a worker slot instead of deep-stream tokens |
| `skills` | list/inspect the skill surface (the reusable capabilities, with metadata) |
| `scratch_write` | spill an oversized artifact to the per-task scratch dir, return its path |
| `web_search` / `browse_open` / `browse_click` / `browse_type` | the live web: search, and drive a real browser (camofox) past interstitials |
| `pdf_read` | download a PDF from the web and read it now -- extracted to scratch, paged with `read`. Ephemeral; never touches the library |
| `library_search` / `library_read` / `library_query` | the agent's own corpus (attune-library): hybrid content search, chunk-window reads, structured metadata/stats |
| `library_fetch` | acquire a reference (doi/arxiv/url/title) INTO the corpus -- the library's ETL owns convert/OCR/index. Permanence, not reading |

The two PDF paths are deliberately distinct: `pdf_read` puts a paper's text in the agent's context
for one read; `library_fetch` sends a reference into the permanent indexed corpus (slow, runs the
full ETL). Reading a paper is not ingesting it.

## Observability

`make observe` reads the opencode session DB into per-task rollups (tokens, delegation count, wall
clock) and fires trip-wires when a run drifts from the recent baseline (token-spike, duration-spike,
delegation-vanished, over-reasoning). For a local rig the metric is tokens + wall, not dollars.

## Configuration

Two files, edited in place; one loader (`core/config.py`) reads both; env vars override for CI.

- **`config.yaml` — the method.** Memory budgets, the agent roster, the weights A/B set. The
  model-agnostic behavior the recipe assumes.
- **`deploy.yaml` + `deploy/<tier>/<backend>.yaml` — the inference backbone.** A menu of deployable
  backends (a local GPU, the DGX Spark, a cloud endpoint). `deploy.yaml` is a thin pointer selecting
  which is live per tier; each backend file supplies the endpoint, model, context, concurrency, and
  serving physics. Toggle a model or a cloud target by changing the pointer and running
  `make config && make up`. A `kind: cloud` backend skips local serving and points the harness at
  its `base_url`.

`make config` regenerates `opencode.json` (a derived, gitignored artifact) from the config; a test
guards the server/client context parity. `make config-show` prints the fully-resolved config.

## Layout

```
fools-trick/
├── ── THE METHOD (generalizes: any OpenAI-compatible endpoint, local or cloud) ──
├── config.yaml             # the method: memory budgets, agent roster, weights. Edit this.
├── AGENTS.md               # the shared team contract every agent loads each turn
├── prompts/orchestrator.md # the orchestrator's system prompt
├── core/                   # OWNED PRIMITIVES (Python, harness-agnostic, no opencode import)
│   ├── log/                #   the Event Log: store (SQLite+FTS5) · redis queue · thread · log
│   ├── context/            #   slide + recoverable-prune decisions (pure), estimation, the CLI
│   ├── gates/              #   human-gate patterns + verify-state machine
│   ├── tools/              #   tool bodies + CLI + the toolset registry (health)
│   ├── observe/            #   per-task rollups + trip-wires + trace + trajectory
│   ├── skills/             #   the skill loader (rich frontmatter: version, platforms, tags, prereqs)
│   └── config.py           #   the one config loader (config.yaml + deploy.yaml + backends)
├── adapters/opencode/      # THIN ADAPTER: the only opencode-speaking code
├── bench/                  # the benchmark instrument (imports core/ directly)
├── tests/                  # core/ (no harness present) + adapters/ + bench parsers
├── docs/                   # harness-design · hardware · memory · review · integration
│
├── ── THE RIG (this hardware's deployment tooling; reference, not the artifact) ──
├── deploy.yaml             # thin pointer: which backend per tier + rig topology
├── deploy/
│   ├── orchestrator/       # one backend per orchestrator (spark-gb10, cloud-*, ...)
│   ├── worker/             # one backend per worker (3080ti-qwen27, cloud-*, ...)
│   ├── scripts/            # ops tooling: up/down/weights/bench/test (reads config via env.sh)
│   └── worker/serve.sh     # the llama.cpp launcher for the local worker backend
└── spark/                  # git submodule: the DGX Spark serving recipe (orchestrator node)
```

## Usage

```bash
make bootstrap      # one-time: adapter deps, opencode.json, spark submodule, weights
make config         # (re)generate opencode.json after editing config
make up             # start redis + worker + orchestrator
make health         # real completions on both nodes + an opencode round-trip
opencode            # use it: the build agent is the orchestrator; it dispatches the workers
make observe        # per-task rollups (tokens, delegation, wall) + trip-wires
make test           # the full suite: core + adapters + shell + config parity
make bench          # the formal instrument (see bench/): capability, e2e, memory, prune, cap, xagent, ...
make down           # stop everything (SQLite persists; Redis is ephemeral)
```

## Benchmarks

`bench/` is a real instrument. `make bench-e2e` drives real `opencode run` tasks with DB-verified
delegation — a task passes only if the answer matches AND the subagents provably ran on the worker
endpoint, with the per-task rollup reported alongside. The other arms measure capability (lm-eval),
code + tool-calling, safety (the abliteration measure), long-context delegation-at-depth, the memory
A/B (sliding-window recall vs compaction), the worker prune-under-pressure (outcome), the per-result
cap recovery, and cross-agent memory (a subagent writes, a fresh session recalls). Every run writes
a markdown report + xlsx scorecard with a provenance manifest, so runs are comparable over time.

## Lineage

Each design decision traces to a measured result in the literature; nothing is folklore.

| Decision | Basis |
|---|---|
| Lossless addressable Event Log, never summarize-and-drop | **Scroll** (arXiv:2608.21690) — lossy ablation collapses 73→20; BM25+log beats graph-first |
| Recoverable eviction w/ seq + recall (not lossy prune) | **Scroll** — eviction changes the view, never the record |
| Bounded, tiered eviction index | **Scroll** Algorithm 1 step 7 — a flat index grows linearly |
| Decomposition amplifies weak models (orchestrator+workers) | **Rakuten enterprise MAS** (arXiv:2608.18740) — a pipeline of weaker models beats a stronger monolith |
| Independent verification (producer ≠ verifier) | **Rakuten enterprise MAS** — 93% hallucination-free rests on it |
| The harness must not manufacture a model failure | **Prime Agent** (arXiv:2608.23552) |
| Grounded feedback only; self-judgment is worse than nothing | **Evo-Harness** (arXiv:2608.15071) |
| Gains come from richer *input* to a fixed improver | **Metan** (arXiv:2608.24735) |
| Ablate the refusal direction; never consciousness-*steer* | **Kim et al.** (arXiv:2607.28607) — ToM stays intact under ablation, degrades under steering |
| The six-layer harness + the ratchet (every failure → a permanent fix) | **Production Agent Engineering Practice 2026** (`harness_final.pdf`) |

The full reasoning, with the papers' numbers, is `docs/harness-design.md`; the hardware derivation
is `docs/hardware.md`.

## Credits

An [Attune Intelligence](https://attuneintelligence.com) lab project. Stands on
[opencode](https://opencode.ai), the
[DeepSeek-v4-Flash-One-DGX-Spark](https://github.com/mrbende/DeepSeek-v4-Flash-One-DGX-Spark)
serving recipe, `llama.cpp`, and the abliterated open weights of DeepSeek-V4-Flash and Qwen3.8-27B.
The design lineage is in `docs/harness-design.md`.

## License

MIT. See [`LICENSE`](./LICENSE).
