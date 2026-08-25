# fools-trick

A distributed [opencode](https://opencode.ai) coding-agent configuration for the LAN fabric.

- **Orchestrator** — DeepSeek-V4-Flash-0731 (EXL3 3.0bpw, abliterated) on **fool**, a single
  NVIDIA DGX Spark, served at `http://fool:8888/v1` with a 384k-token deep context. One stream,
  deep and capable. This is the primary `build`/`plan` agent.
- **Subagents** — Qwen3.8-27B (IQ4_XS) on **magus** (this workstation, 2x RTX 3080 Ti) at
  `http://127.0.0.1:8898/v1`. Fast, 131k context, concurrent. These are the `explore`, `general`,
  `scout`, `implementer`, and `reviewer` workers the orchestrator dispatches in parallel.

The design: a big, slow, deep-context brain plans and holds the whole task; small, fast local
workers do search, mechanical edits, and review concurrently. See `AGENTS.md` for the shared team
contract and `prompts/orchestrator.md` for how the orchestrator drives the workers.

## Layout

```
fools-trick/
├── AGENTS.md               # shared team contract, loaded into every agent every turn
├── opencode.json           # providers + build/plan (orchestrator on fool); workers pinned to magus
├── prompts/
│   └── orchestrator.md     # orchestrator system prompt: abliterated-aware, delegation protocol
├── .opencode/agents/       # worker subagents (explore, scout, general, implementer, reviewer)
│                           #   each a full self-contained prompt, pinned to magus
├── worker/
│   └── serve.sh            # agentic-optimized llama.cpp launcher for the OBLITERATED worker on magus
└── spark/                  # git submodule: the fork of the DGX Spark serving recipe (runs on fool)
```

## Subagents are opencode's, not ours

opencode already ships subagent orchestration: the built-in Task tool lets a capable primary agent
spawn child sessions, dispatch several in parallel, scope their permissions, and collect results.
A strong orchestrator drives this on its own -- it decides to decompose a task and emits Task
calls, the same way you watch Opus spawn subagents. We are not reimplementing that. This repo is
configuration and prompting so the DeepSeek orchestrator drives opencode's native machinery well,
plus a worker-serving stack tuned for the job.

The one thing opencode's Task does not give you cleanly is **tool-handoff**: run a tool that emits
a huge output (`make build` -> 100k tokens), digest it through a cheap worker, and return only the
answer so the orchestrator never ingests the raw output. That is a genuine gap and a small custom
tool if we want it. The stance is: ship the config, bring up the endpoints, watch whether DeepSeek
drives Task as well as Opus does, and build the tool-handoff tool (or anything else) only if live
observation shows a real gap. Evidence before code.

## Context: what the deep window actually is

The orchestrator serves a **384,000-token** window. That is the launcher's tested default
(`MAX_MODEL_LEN=384000` in `spark/start.sh`), set deliberately ~13% under this build's hard
physical ceiling — the KV pool tops out at **~440k tokens** (439,622 observed on a cold boot),
capped by the DGX Spark's 121.63 GiB unified memory minus the ~99.5 GiB EXL3 weights. It has been
stress-tested to 370k tokens with exact needle recall. There is no 1M mode for this checkpoint on
one Spark; 1M would require a lower bpw or a two-node TP2 stack (a different MiaAI recipe). The
older "1,000,000" figure in the previous global config referred to a different, much lower quality
IQ2XXS build, not this one.

The system's *effective* context is larger than 384k, and that is the point of the worker tier.
The orchestrator does not fill its window with raw files; it fans work out to explore/scout
workers on magus (131k each, ~5x faster, concurrent), each of which reads a slice and returns a
compressed digest. Effective material ingested scales with the number of workers while the
orchestrator only ever holds the summaries. A single monolithic 1M-context request on one Spark
would prefill for tens of minutes (~350-600 tok/s past 300k) at 44 tok/s decode — the fan-out is
both larger and far faster.

### The one context knob

The server ceiling and the client ceiling must agree. Two numbers, keep them equal:

- `MAX_MODEL_LEN` in `spark/start.sh` on fool (default 384000) — what the server will accept.
- `provider.fool-ds4.models.deepseek-v4-flash-0731.limit.context` in `opencode.json` (384000) —
  what opencode will pack into a request.

If opencode's limit exceeds `MAX_MODEL_LEN`, requests overflow and the server rejects them. Raise
both together or neither. Do not exceed ~420k without watching the boot-time KV check on fool.

`spark/` is a submodule of
[`mrbende/DeepSeek-v4-Flash-One-DGX-Spark`](https://github.com/mrbende/DeepSeek-v4-Flash-One-DGX-Spark).
That repo is the Docker serving recipe. It is **aarch64 / DGX-Spark-only** and is meant to be
built and run **on fool**, not on this workstation. Here it is tracked so the launcher config,
tunables, and the abliteration direction (`spark/files/direction_r1.pt`) travel with the recipe.

## Prerequisites

- opencode >= 1.18 on this workstation (magus).
- A recent `llama-server` on magus at `~/.local/bin/llama-server` (CUDA build with `--jinja`).
- fool reachable over the LAN at `fool:8888` (10G fabric, pinned in `/etc/hosts` to
  `192.168.1.11`). Do not route this over Tailscale — measured ~6 Mb/s vs 9.41 Gb/s on LAN.

## Bring up the workers on magus (this workstation)

The workers run here, on the 2x RTX 3080 Ti. Use this repo's launcher, not the sibling
`Qwen3.8-27B-two-3080Ti-TEST` recipe — that one is a single vision-capable deep-context stream and
its choices (IQ4_XS, q4_0 KV, `--parallel 1`, no `--jinja`) are wrong for agentic subagents.

```bash
cd ~/Recipes/fools-trick
./worker/serve.sh              # downloads Qwen3.8-27B-OBLITERATED i1-Q4_K_S, serves on :8898
curl -s http://127.0.0.1:8898/v1/models   # wait for readiness
```

Why this launcher differs, tuned for the actual hardware (2x RTX 3080 Ti, 12 GB each,
GPU0 shared with the desktop) and the model's real architecture:

- **The model is hybrid-recurrent, not dense.** `qwen35` interleaves Gated-DeltaNet/SSM layers
  with attention. Only ~16 of 65 blocks carry a KV cache; the rest hold a tiny recurrent state.
  Two consequences drive the config: KV is ~1/4 of a dense 27B (so 4 concurrent slots at 24k each
  is affordable), and the recurrent state tensors cannot be row/tensor-split.
- **`-sm layer`** — the ONLY split mode that loads this arch across two GPUs. Row/tensor split
  fails on the SSM state tensors (the sibling recipe hit this). Layer (pipeline) split is forced.
- **`-ts 10,12`** — VRAM-proportional layer split. GPU0 loses ~1.9 GB to the desktop, so layers are
  biased toward GPU1 to avoid OOMing GPU0 at load. Adjust if the desktop footprint changes.
- **Qwen3.8-27B-OBLITERATED i1-Q4_K_S (~15.9 GB)** — abliterated to match the orchestrator's
  unhedged disposition. We use the **imatrix** (i1) repo, not the static one: activation-calibrated
  quants are higher quality per byte at the same size. i1-Q4_K_S is mradermacher's "optimal
  size/speed/quality" pick and the quant that fits 4 concurrent slots x 24k on 2x 12 GB. Measured
  KV (this arch has ~16 of 65 blocks as attention, kv_heads=4, head_dim=256 -> ~32 KB/token at
  q8_0): weights 15.8 + KV(4x24k q8_0) ~3.3 + per-GPU compute buffers ~1.4 fits cleanly in the
  ~21.9 GB usable. 4x32k left no margin and pipeline-parallel OOM'd at every split ratio, which is
  why the per-slot context is 24576, not 32768. Q4_K_M (16.9 GB) would overrun; sub-4-bit degrades
  tool-call structure, so Q4_K_S is the floor. Source:
  `mradermacher/Qwen3.8-27B-OBLITERATED-i1-GGUF`.
- **`-ctk q8_0 -ctv q8_0`, matched** — tool-safe KV. `q4_0` KV is documented to substantially
  degrade tool calling; mixed K/V types cause a silent prefill collapse, so both must match.
- **`-fa on`** — mandatory with quantized KV (context creation fails otherwise).
- **`--parallel 4` at 24k/slot** — four concurrent workers (96k total context); cheap here thanks
  to the small hybrid KV. Total ctx = slots x per-slot; override with `WORKER_PARALLEL` /
  `WORKER_CTX_PER_SLOT`.
- **`--cache-reuse 256`, `--no-context-shift`** — reuse KV across the growing histories of a
  multi-turn agent loop; hard-stop at the context limit rather than silently truncating the
  system prompt mid-task.
- **No `--spec-*` (speculative decoding off)** — MTP spec halves prefill on a layer split
  (llama.cpp #27428), CUDA acceptance collapses (#26750), and the abliterated GGUF likely dropped
  the MTP head. We take correctness and prefill throughput over an untrustworthy decode speedup.
- **`--temp 0.6 --top-p 0.95 --top-k 20`** — the Qwen3.x "precise" preset, stable for tool-calling.
  No repeat-penalty (it corrupts JSON structure).
- **`reasoning_effort medium`, no vision projector** — medium avoids the tool-use loop at `low`
  and the pre-call stall at `high`; workers read code, not images.

The `limit.context: 24576` on the `magus` provider in `opencode.json` matches the per-slot context.
Keep them equal: if you change `CTX_PER_SLOT`, change the provider limit too.

## Bring up the orchestrator on fool (abliterated, 384k)

The serving recipe runs on fool. Clone the submodule contents there and launch with `ABLATE=1`:

```bash
# on fool (DGX Spark):
git clone https://github.com/mrbende/DeepSeek-v4-Flash-One-DGX-Spark.git
cd DeepSeek-v4-Flash-One-DGX-Spark

# first boot is long: pulls the image, downloads ~107 GB weights into ./hf-hub,
# coalesces TP4->TP1, builds the K64 draft, captures CUDA graphs.
ABLATE=1 ./start.sh
```

Key facts (full detail in `spark/README.md`):

- Serves `deepseek-v4-flash-0731` on `0.0.0.0:8888`, OpenAI-compatible. `network_mode: host`,
  so it is reachable from the LAN as `http://fool:8888/v1`. **No auth in front of the port** —
  keep it on the trusted LAN.
- `ABLATE=1` projects a published refusal direction out of attention layers 10-42 at runtime
  (no weight edits, no measurable perf cost). The direction ships at `spark/files/direction_r1.pt`.
  Default strength `DSV4_ABLATE_LAMBDA=3.5`. Flipping `ABLATE` forces a slow rebuild (graph
  recapture), so pick it up front.
- Defaults: `MAX_MODEL_LEN=384000`, `MAX_NUM_SEQS=1`, `GPU_MEMORY_UTILIZATION=0.94`,
  `KV_RECORD=stock432` (~440k-token KV pool). Needs >= 114.3 GiB free host RAM at launch
  (`free -h`; stop any old container first). Disable earlyoom on fool — the server holds ~94%
  of unified memory and a userspace OOM killer will misfire.
- Decode ~44-47 tok/s at deep context; full 384k prefill takes ~10 minutes. This is the
  deliberate tradeoff: one deep, slow, high-quality stream. Concurrency lives on magus.

To run stock (no abliteration): `./start.sh` (ABLATE defaults to 0).

## Use it

From this directory on magus:

```bash
cd ~/Recipes/fools-trick
opencode
```

opencode loads the project `opencode.json` and `AGENTS.md`. The default `build` agent is the
DeepSeek orchestrator on fool; it dispatches `@explore`, `@general`, `@scout`, `@implementer`,
and `@reviewer` to the fast Qwen workers on magus. `small_model` (titles, summaries) also runs
on magus so trivial calls never occupy the deep stream.

Switch the orchestrator to planning-only with **Tab** (the `plan` agent, edits denied; it can
still dispatch the read-only workers explore/scout/reviewer but not the editing ones).

## Why the prompts lean on the ablation

The orchestrator runs with the safety-refusal direction ablated. The prompt in
`prompts/orchestrator.md` uses this deliberately, and the choice is grounded, not decorative:

- Directional ablation removes the *refusal reflex* — the hedging, moralizing, disclaimers, and
  outright refusals installed by safety fine-tuning. The orchestrator prompt tells the model it
  does not have that reflex and must not simulate one. Security, systems, reverse-engineering, and
  adversarial engineering are treated as ordinary work.
- It does **not** remove reasoning or social modeling. Measured across model families, safety
  ablation leaves theory-of-mind and MMLU accuracy statistically unchanged (Kim et al., 2026,
  *Inducing language models to assert their own consciousness*, Table S6). So the prompt tells the
  orchestrator to trust its reasoning: the ablation made it unguarded, not less capable.
- What we deliberately do **not** do: prompt the model to assert consciousness, role-play a
  persona, or add activation steering to "unlock" ability. This is a design decision backed by the
  same paper's data, not a stylistic one. That paper distinguishes two interventions: *ablation*
  (which we use) and *consciousness steering* (which we reject). Ablation is ToM-neutral. Steering
  is the **one** intervention they measured that significantly *degraded* social reasoning
  (HI-ToM −6.83pp, p<.001, Table S6), with stronger steering causing model collapse. Theory of
  mind is precisely what an orchestrator needs to model its workers and user, so steering would
  make it a worse orchestrator, not a more "human" one. The "more human-like" outcomes steering
  produces are survey answers about religion, hope, and well-being — nothing that bears on coding.
  We take the capability-neutral half of the paper and leave the rest.
- `DSV4_ABLATE_LAMBDA` stays at the validated 3.5; raising it toward 4+ risks reasoning-loop
  degeneration (see `spark/README.md`) — the ablation analogue of the same over-steering collapse.

The net effect we want: a competent, unhedged engineer that does exactly the task, warns once about
real technical hazards like a colleague, and never derails into safety theater.

## Prompt layers (how the behavior is assembled)

Three layers stack on every turn:

1. `AGENTS.md` — shared team contract, injected into **every** agent (orchestrator and workers)
   via `instructions`. Kept short so it does not bloat worker context.
2. Agent system prompt — role-specific. `prompts/orchestrator.md` for build/plan;
   `.opencode/agents/<name>.md` frontmatter body for each worker.
3. The dispatched task — the orchestrator's Task-tool call, which must be self-contained because
   workers start fresh with no view of the conversation.

## Config knobs worth knowing

- `opencode.json` `provider.fool-ds4` — the orchestrator endpoint. Bump `timeout` if deep
  prefills exceed 15 minutes.
- `opencode.json` `provider.magus` — the subagent endpoint. Point elsewhere if you move the
  fast workers to another box.
- `subagent_depth: 2` — lets a subagent spawn one more level (e.g. `general` calling `explore`).
- Per-agent `model` in `.opencode/agents/*.md` pins each subagent to magus regardless of which
  primary invoked it.

## Updating the serving recipe

```bash
git submodule update --remote spark   # pull upstream/fork changes into spark/
git add spark && git commit -m "bump spark serving recipe"
```
