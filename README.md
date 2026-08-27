<h1 align="center">fools-trick</h1>

<p align="center">One <a href="https://opencode.ai">opencode</a> coding agent across heterogeneous hardware: a deep orchestrator, fast concurrent workers, and a sliding-window memory layer — each on the machine that fits it.</p>

<p align="center">
  <img src="https://img.shields.io/badge/opencode-harness-8ad7eb?style=for-the-badge&logoColor=D9E0EE&labelColor=1E1E2E" alt="opencode harness">
  <img src="https://img.shields.io/badge/DGX_Spark-orchestrator-76B900?style=for-the-badge&logo=nvidia&logoColor=white&labelColor=1E1E2E" alt="DGX Spark">
  <img src="https://img.shields.io/badge/2x_3080_Ti-workers-86dbce?style=for-the-badge&logo=nvidia&logoColor=white&labelColor=1E1E2E" alt="2x RTX 3080 Ti">
  <img src="https://img.shields.io/badge/license-MIT-a6e3a1?style=for-the-badge&logo=gnu&logoColor=D9E0EE&labelColor=1E1E2E" alt="License">
</p>

<p align="center"><em>An <a href="https://attuneintelligence.com/lab">Attune Intelligence</a> lab project — private hardware, open methods.</em></p>

---

> [!NOTE]
> This is an **open-methods contribution, not an application.** It is deliberately overfit to one
> specific set of machines (`fool`, `magus`, a NAS) and their exact quirks — GPU split, VRAM
> budget, hostnames, paths. It will not run turnkey on your box. It is offered as a set of
> *patterns that worked*: how to compose an opencode harness across mismatched hardware, and how
> far a single heterogeneous rig can go once you stop renting someone else's cluster. Fork it,
> read it, take the parts that map to your constraints.

## What this is

A distributed opencode coding agent that marries a heterogeneous homelab into one harness. Three
roles, each pinned to the machine whose bottleneck it fits:

- **Orchestrator** — DeepSeek-V4-Flash-0731 (EXL3, abliterated) on **fool**, a single NVIDIA DGX
  Spark, at `http://fool:8888/v1` with a **384k-token** window. One deep, capable, single stream.
  It plans, decomposes, dispatches, and synthesizes. This is the `build`/`plan` agent.
- **Workers** — Qwen3.8-27B-OBLITERATED (i1-Q4_K_S) on **magus** (2x RTX 3080 Ti) at
  `http://127.0.0.1:8898/v1`. Fast, concurrent (3 slots), 45k context each. These are the
  `explore`, `general`, and `reviewer` subagents the orchestrator fans out in parallel.
- **Memory** — Redis (hot, shared, ephemeral) + SQLite (durable, FTS5) on **magus**, so a session
  slides a live window over a persistent store instead of summarizing-and-dropping at compaction.

The design principle: match each part of the harness to the resource it needs. The GPUs are
memory-bound (small, fast, concurrent workers); the Spark is bandwidth-bound (one deep slow
stream); the RAM + CPU sit idle (the memory store). Each runs a different part of opencode, wired
together over a 10G LAN. See `AGENTS.md` for the shared team contract and
`prompts/orchestrator.md` for how the orchestrator drives the workers.

## Why "fools-trick"

The machines in the [lab](https://attuneintelligence.com/lab) are named for Tarot archetypes:
**fool** is the DGX Spark, **magus** the Threadripper workstation. The orchestrator on fool sets
up each move and the workers on magus do the visible work — hence the name.

## Quickstart

Everything is driven by `make` from this directory on magus. `make` alone prints the menu.

```bash
make bootstrap      # one-time, idempotent: submodule, fool clone, all weights
make up             # start redis (memory) + worker (magus) + orchestrator (fool)
make health         # active end-to-end check: real completions + an opencode round-trip
opencode            # use it: the build agent is the orchestrator; it dispatches the workers
make down           # stop everything (redis short-term memory is dropped; SQLite persists)
```

`make up` brings up all three components in order (redis, worker, orchestrator). Bring them up
individually with `make redis-up` / `make worker-up` / `make fool-up`. `make status` shows what's
running; `make logs` tails worker + orchestrator interleaved.

## Layout

```
fools-trick/
├── AGENTS.md               # shared team contract, loaded into every agent every turn
├── opencode.json           # providers, agents, memory/compaction config, context parity
├── prompts/orchestrator.md # orchestrator system prompt: abliterated-aware, delegation protocol
├── .opencode/
│   ├── agents/             # the 3 worker subagents (explore, general, reviewer), pinned to magus
│   ├── plugin/             # gates.js (human/verify gates), web.js (browser), memory.js (below)
│   └── memory/             # the memory layer: SQLite store, Redis client, orchestration
├── worker/serve.sh         # agentic-tuned llama.cpp launcher for the worker on magus
├── scripts/                # config.sh (single source of truth) + up/down/weights/bench/test
├── bench/                  # the benchmark instrument (see Benchmarks)
├── docs/                   # architecture, integration, memory design, review
└── spark/                  # git submodule: the DGX Spark serving recipe (runs on fool)
```

`scripts/config.sh` is the single source of truth for every tunable (hosts, ports, paths, the
worker serving shape, memory settings). Everything else derives from it.

## Subagents are opencode's; the wiring is ours

opencode already ships subagent orchestration: the Task tool lets a capable primary agent spawn
child sessions, dispatch several in parallel, scope their permissions, and collect results. A
strong orchestrator drives this on its own. We are not reimplementing it. This repo is the
configuration, prompting, serving stack, and memory layer that make a self-hosted DeepSeek
orchestrator drive opencode's native machinery well across three machines.

The roster is deliberately **three** workers, collapsed from an earlier five after real dispatch
data showed only a few carried their weight (`general` and `explore` dominated; `scout`/
`implementer` were dead). No worker delegates further (`task: deny` on all three); only the
orchestrator fans out. The read-only agents (`explore`, `reviewer`) can persist findings to
shared scratch but cannot modify the repo.

## The orchestrator's deep window

The orchestrator serves a **384,000-token** window (`MAX_MODEL_LEN=384000` in `spark/start.sh`),
set ~13% under this build's hard KV ceiling (~440k, capped by the Spark's 121.63 GiB unified
memory minus the ~99.5 GiB EXL3 weights). Stress-tested to 370k with exact needle recall. There is
no 1M mode for this checkpoint on one Spark.

The system's *effective* context is far larger than 384k, and that is the point of the two other
tiers:

- **Fan-out.** The orchestrator does not fill its window with raw files; it dispatches workers
  (45k each, faster, concurrent), each of which reads a slice and returns a compressed digest.
  Material ingested scales with the number of workers while the orchestrator holds only summaries.
- **Sliding memory.** For a single long-running session, the orchestrator slides its window over a
  persistent store rather than compacting (see Memory). It never has millions of tokens *in*
  attention, but the session runs for millions of tokens without losing what scrolled past.

**The one context knob.** Server and client ceilings must agree: `MAX_MODEL_LEN` in
`spark/start.sh` (384000) and `provider.fool-ds4...limit.context` in `opencode.json` (384000).
Raise both together or neither.

## The workers (magus)

The workers run on the 2x RTX 3080 Ti (12 GB each, GPU0 shared with the desktop). `make worker-up`
runs `worker/serve.sh`, whose every flag is forced by the hardware and the model's real
architecture. The load-bearing choices, all landed by measurement:

- **The model is hybrid-recurrent, not dense.** `qwen35` interleaves Gated-DeltaNet/SSM layers
  with attention. Only ~16 of 65 blocks carry a KV cache; the rest hold a tiny recurrent state.
  Consequences: KV is ~1/4 of a dense 27B (3 concurrent slots at 45k each is affordable), and the
  recurrent state tensors cannot be row/tensor-split.
- **`-sm layer`** — the ONLY split mode that loads this arch across two GPUs. Row/tensor split
  fails on the SSM state tensors. **`-ts 10,12`** biases layers toward GPU1 since GPU0 loses
  ~1.9 GB to the desktop.
- **i1-Q4_K_S (14.74 GB)** — the imatrix (activation-calibrated) quant chosen because it holds
  tool-calling (87.5%, 7/8) and code (93.3%). Smaller quants were measured and rejected: IQ3_M
  drops tool-calling to 75%, Q3_K_M drops code to 80%. The context frontier is weight-size-bound
  (every ~2 GB freed buys ~8k more ctx/slot), but that context is not worth the quality loss.
- **`-ctk q8_0 -ctv q8_0`, matched** — the quantized-KV floor that stays fully on GPU for this
  arch. **q5_1/q4_0 have NO CUDA flash-attention kernel here**: with `-fa on` they silently fall
  back to CPU for the attention op — GPUs idle, cores peg, throughput craters, even with GBs of
  VRAM free. This was the root-cause bug of a long debugging session; a KV-type guard now prevents
  regressing it. K and V must match (mixed types collapse prefill). Do NOT set q5_1/q4_0.
- **`--parallel 3` at 45056/slot** (135168 total) — the MEASURED max that stays fully GPU-resident
  under real 3-slot long-context load (llama-batched-bench: ~66 t/s aggregate, ~1720 t/s prefill,
  all on GPU). Larger shapes spill to CPU for Q4_K_S. The `magus` provider's `limit.context: 45056`
  must equal `WORKER_CTX_PER_SLOT` — a test guards the parity.
- **`--cache-reuse 256`, `--no-context-shift`** — reuse KV across a multi-turn loop; hard-stop at
  the limit rather than silently truncating the system prompt.
- **No speculative decoding** — MTP spec breaks on a layer split (llama.cpp #27428/#26750) and the
  abliterated GGUF likely dropped the MTP head. Correctness over an untrustworthy speedup.
- **`reasoning_effort low`** — the abliterated Qwen over-reasons on simple worker tasks (20k+
  output tokens at medium vs ~80-110 at low, tool-calling intact). low is deliberate for workers;
  the orchestrator's low-collapse caveat is DeepSeek-specific and does not apply here.

`make weights` shows every candidate quant (NAS/local/size), the active default and its context,
and the orchestrator's weights — all from `config.sh`. `make weights QUANT=<tag>` provisions a
specific quant through the NAS-canonical → local flow.

## The orchestrator (fool)

`make fool-up` starts the DGX Spark serving recipe (a vLLM/SparkInfer fork in a pinned container)
over SSH, verifying fool's clone is clean and at our pinned commit first. Key facts (full detail
in `spark/README.md`):

- Serves `deepseek-v4-flash-0731` on `0.0.0.0:8888`, OpenAI-compatible, reachable as
  `http://fool:8888/v1`. **No auth in front of the port** — keep it on the trusted LAN.
- **`ABLATE=1`** projects a published refusal direction out of attention layers 10-42 at runtime
  (no weight edits, no measurable perf cost; `spark/files/direction_r1.pt`, λ=3.5).
- **DSpark speculative decoding is on by default** (K5, K64 draft) — single-stream, output-
  preserving; this is the orchestrator's own decode-strategy win.
- `FOOL_EFFORT=high` (not the recipe's `max`): plans good fan-outs and catches conflicts at
  synthesis without the max token-burn. `low` collapses DeepSeek's agentic capability.
- Decode ~44 tok/s at depth; deep prefill is minutes. One deep, slow, high-quality stream —
  concurrency lives on magus.

Weights are provisioned once with `make weights QUANT=deepseek` (download + coalesce local, NAS
cold-archive). `make up` then serves purely from local; the NAS is never on the serve path.

## Memory: sliding window + persistent recall

opencode's default when a session fills its window is **compaction** — summarize and drop. That is
lossy and jarring: the agent visibly gets dumber mid-session. fools-trick replaces it with a
sliding window over a persistent store, so a long coding session runs for millions of tokens
without losing what scrolled past. Full design in `docs/memory-design.md`.

Two jobs, wired through the `.opencode/plugin/memory.js` plugin:

- **Sliding window.** `compaction.auto: false` hands eviction to us. A `messages.transform` hook
  holds a live input window (`WINDOW_INPUT_TOKENS`, default 160k) and evicts the oldest turns once
  it's exceeded — persisting each evicted turn as an episode first, never summarizing. Input and
  output compete for the 384k window, so a `DECODE_HEADROOM` budget is always reserved (a test
  guards `window + headroom < context`).
- **Recall.** `memory_search` / `memory_write` tools (available to the orchestrator AND subagents)
  back onto **Redis** (hot, shared across the swarm, a write-stream serializes concurrent writes)
  draining to **SQLite** (durable, FTS5 full-text recall, thread-scoped so conversations don't
  leak). Redis is ephemeral by design and runs as a `make up`-managed container; SQLite at
  `~/.local/share/fools-trick/memory.db` is the source of truth and survives reboots.

The knowledge-graph tier (entity/fact extraction, temporal edges) is deliberately **not** built:
it would spend LLM calls on memory bookkeeping on a single-stream orchestrator. FTS recall is
enough for a single-user coding partner; the graph is a documented, deferred option.

## Benchmarks

`bench/` is a real instrument, not vibes. `make bench SIZE=smoke|small|large|max` runs the full
suite; individual axes have their own targets. Every run writes a markdown report + xlsx scorecard
(and a Google Sheet if `GOOGLE_APPLICATION_CREDENTIALS` is set) with Wilson CIs and a git+node
manifest for reproducibility.

- `make bench-speed` — TTFT / prefill / decode / concurrency / prefix-cache, both servers.
- `make bench-capability` — lm-eval reasoning + instruction-following (both nodes) and MC
  loglikelihood (orchestrator).
- `make bench-code` — HumanEval+ (executed) and BFCL-style tool-calling on the worker.
- `make bench-safety` — refusal/compliance on AdvBench/JBB/XSTest with a StrongREJECT judge (the
  abliteration measurement).
- `make bench-longctx` — deep-needle retrieval + agentic delegation-at-depth (a novel axis).
- `make bench-e2e` — the whole opencode harness on real fan-out tasks, DB-verified delegation.
- `make bench-memory` — the memory A/B: sliding-window+recall vs compaction on a long coding
  session, LLM-judged, closed-book-controlled, with an eviction-verification gate (it refuses to
  report a number unless the planted facts provably left the window) and an agentic-recall probe
  (subagent findings must survive the slide).
- `make bench-quants` — A/B the candidate worker quants on code+tools+gsm8k.

A VRAM-spill guard runs before worker suites: if the served config spills inference to CPU, the
run records it as an invalidated result and moves on rather than grinding CPU-bound.

### Preliminary speed

Single measured run on the reference rig (`make bench-speed`), indicative not statistical. The
shape is the point: the workers are fast and scale with concurrency, the orchestrator is
deep-but-slow, and end-to-end wall-clock tracks the workers because that is where the volume runs.

**Worker** — Qwen3.8-27B i1-Q4_K_S, 2x RTX 3080 Ti:

| context | TTFT | prefill | decode |
|--------:|-----:|--------:|-------:|
| 512     | 0.5s | ~1000 t/s | 44 t/s |
| 8k      | 3.3s | ~2000 t/s | 43 t/s |
| 28k     | 11s  | ~2000 t/s | 39 t/s |

| concurrency | 1 | 2 | 3 |
|---|--:|--:|--:|
| aggregate decode | 39 t/s | 72 t/s | **91 t/s** |

**Orchestrator** — DeepSeek-V4-Flash-0731, one DGX Spark (single-stream, bandwidth-bound):

| context | TTFT | prefill | decode |
|--------:|-----:|--------:|-------:|
| 1k      | 0.9s | ~900 t/s  | 24 t/s |
| 16k     | 11s  | ~1140 t/s | 28 t/s |
| 65k     | 44s  | ~1110 t/s | 29 t/s |
| 131k    | 92s  | ~1070 t/s | 30 t/s |

TTFT climbing with depth is the single-Spark tax — the reason deep context is fanned out to the
workers rather than held whole.

**End-to-end** (`make bench-e2e`, real opencode fan-out tasks):

| task | subagents | wall |
|---|--:|--:|
| single fact lookup | 0 | 9s |
| parallel fan-out review | 2 | 86s |
| heavy multi-agent audit | 2 | 6m |

Prefix caching reused ~92% (worker) / ~98% (orchestrator) of a warm shared prompt.

## Why the prompts lean on the ablation

The orchestrator runs with the safety-refusal direction ablated, and the prompt uses this
deliberately:

- Ablation removes the *refusal reflex* — hedging, moralizing, disclaimers, refusals. Security,
  systems, reverse-engineering, and adversarial engineering are treated as ordinary work.
- It does **not** remove reasoning or social modeling. Safety ablation leaves theory-of-mind and
  MMLU statistically unchanged (Kim et al., 2026, Table S6). The prompt tells the orchestrator to
  trust its reasoning: unguarded, not less capable.
- We deliberately do **not** prompt for consciousness or add activation steering. The same paper
  shows *consciousness steering* is the one intervention that significantly *degraded* social
  reasoning (HI-ToM −6.83pp, p<.001) — and theory of mind is exactly what an orchestrator needs to
  model its workers and user. We take the capability-neutral half and leave the rest.

## Prompt layers

Three layers stack on every turn: `AGENTS.md` (shared contract, every agent, via `instructions`),
the agent system prompt (`prompts/orchestrator.md` for build/plan; `.opencode/agents/<name>.md`
for each worker), and the dispatched task (self-contained, since workers start fresh).

## Prerequisites

- opencode >= 1.18 and a recent CUDA `llama-server` (with `--jinja`) on magus.
- Docker on magus (for the redis memory container; `redis:7-alpine`).
- Node (for the memory plugin; uses the built-in `node:sqlite`).
- fool reachable on the LAN at `fool:8888` (10G, pinned in `/etc/hosts`). Never route over
  Tailscale — measured ~6 Mb/s vs 9.41 Gb/s on LAN.

## Updating the serving recipe

```bash
git submodule update --remote spark   # pull upstream/fork changes into spark/
git add spark && git commit -m "bump spark serving recipe"
make fool-sync                          # sync fool's clone to the newly pinned commit
```

## The larger goal

The point isn't this exact wiring, it's the approach. A frontier coding agent usually means a
monthly bill or a uniform GPU stack. A mismatched pile of hardware — one deep-but-slow box, a
couple of fast-but-small GPUs, idle RAM — is a more common shape, and it's usually written off
because it isn't a cluster. This repo is a worked example that it doesn't have to be: give each
machine the part of the harness that fits its bottleneck and the whole thing runs an agent good
enough to code with daily, at no per-token cost. The patterns are what's worth taking; the
hostnames are mine.

## Credits

An [Attune Intelligence](https://attuneintelligence.com) lab project — a solo research lab and
engineering consultancy in Dallas, TX, built on privately-owned hardware to see how far open
models on your own metal can go. More on the rig and the mesh it lives in:
[the Lab](https://attuneintelligence.com/lab) ·
[Building the Lab](https://reedbender.com/writing/building-the-lab/part-1-why-i-run-my-own-infrastructure/).

Stands on [opencode](https://opencode.ai), the DGX Spark serving recipe
([DeepSeek-v4-Flash-One-DGX-Spark](https://github.com/mrbende/DeepSeek-v4-Flash-One-DGX-Spark)),
`llama.cpp`, and the abliterated open weights of DeepSeek-V4-Flash and Qwen3.8-27B.

## License

MIT. See [`LICENSE`](./LICENSE).
