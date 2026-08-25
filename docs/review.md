# fools-trick: a first-principles review

A ground-up account of what this system is, why each layer is shaped the way it is, what is
verified working, and where the real edges are. Written against the system as it actually runs
(both nodes up, all tests green at time of writing), not from memory.

## The one idea

Most agent systems treat the big model as the expensive thing to *avoid calling* (route work
to cheap models to save cost). fools-trick inverts that: the big deep model is the **scarce
reasoning resource to protect**, and the small fast models are cheap labor to offload onto. The
orchestrator is deliberately the slow bottleneck; worker concurrency is the scaling lever. That
reframing -- local, throughput-bound-orchestrator, worker-concurrency-as-scaling -- is the
system's actual novel operating point. The pattern (big plans, small execute) is well-trodden;
this operating point is not.

## The five layers

```
  YOU
   |
   v
  opencode harness  ------  prompt + orchestration strategy (prompts/, AGENTS.md, .opencode/)
   |                        the build agent = DeepSeek orchestrator, delegating via the Task tool
   |                        + memory: sliding window over Redis(hot)+SQLite(durable), not compaction
   v
  fool: DeepSeek-V4-Flash   deep, slow, single stream, 384k ctx, abliterated (runtime projection)
   |    (orchestrator)      plans / decomposes / dispatches / synthesizes
   v  (LAN, 10G, never Tailscale)
  magus: Qwen3.8-27B x3     fast, concurrent, 32k ctx/slot (131k total), abliterated (OBLITERATED)
        (workers)           execute self-contained units: search, edit, review
```

Each layer, from first principles:

### Layer 1 -- Inference serving (the two model servers)

**Worker (magus, llama.cpp):** Qwen3.8-27B-OBLITERATED, i1-Q4_K_S GGUF, served across 2x RTX
3080 Ti. Every serving choice is forced by hard constraints, all documented in `scripts/config.sh`
and `worker/serve.sh`:
- `-sm layer` is the ONLY split that loads this hybrid-recurrent arch on 2 GPUs (row/tensor
  split can't partition the SSM state tensors).
- `-ts 10,12` biases layers off GPU0 (it loses ~1.9GB to the desktop).
- 32768/slot (131072 aggregate across 4 slots): the measured max that stays fully GPU-resident
  for Q4_K_S + q8_0 KV under real 4-slot long-context load (llama-batched-bench, ~66 t/s agg
  ~1720 t/s all-GPU); 40960/slot spills the attention op to CPU.
- `q8_0` KV, matched K/V: the quantized KV floor with a working CUDA flash-attn kernel for this
  hybrid arch. q5_1 has NO CUDA FA kernel here -- with `-fa on` it silently falls back to CPU
  (GPUs idle, cores peg, throughput craters) even with free VRAM. q8_0/f16 stay on GPU; mixed
  K/V types silently collapse prefill; q4_0 degrades tool-calling.
- `reasoning_effort=low`: the abliterated Qwen over-reasons; low keeps tool-calling intact at
  ~1/5 the tokens.
- no `--spec-*`: MTP spec halves prefill on a layer split and the abliterated GGUF likely
  dropped the MTP head.

**Orchestrator (fool, real vLLM on DGX Spark):** DeepSeek-V4-Flash, EXL3 3.0bpw, 384k context,
served from local coalesced data/tp1 (NAS holds only a cold archive). Abliteration is a
**runtime projection** (`ABLATE=1`): a refusal direction projected out of layers 10-42 every
forward pass, no weight edit, reversible by a flag. `FOOL_EFFORT=high` (not max: ~30% faster,
identical answers).

Verified serving facts (live, this session):
- Worker `/v1/chat/completions` returns logprobs; `/v1/completions` returns OpenAI shape but
  cannot echo PROMPT logprobs -> no loglikelihood MC on the worker.
- Orchestrator `/v1/completions` DOES support `echo`+`prompt_logprobs` -> loglikelihood MC
  (MMLU/ARC) works there (verified mmlu_anatomy acc=1.0). An earlier "blocked" verdict was a
  stale server boot; corrected.

### Layer 2 -- Ops (scripts/, worker/)

Production-minded shell, ~1000 lines. Idempotent bootstrap; check-first weight provisioning
(NAS-canonical -> local NVMe fast-copy); git-sync gating that refuses to serve fool from a
dirty/divergent tree; systemd `--user` transient unit for the worker with journald ownership;
scoped teardown (`fuser -k` on our port, never a blanket pkill). `make` is the operator surface:
`up/down/status/health/logs/bootstrap/weights/test/bench`.

### Layer 3 -- Orchestration strategy (prompts/orchestrator.md, AGENTS.md, .opencode/)

The intellectual core. The orchestrator prompt (172 lines) says: you are one deep slow stream,
delegate aggressively via the Task tool, fan out wide in one turn, every dispatch is a complete
four-part work order (GOAL/INPUTS/OUTPUT/BOUNDARIES), synthesize don't concatenate, write large
worker output to shared scratch and return only a reference, verify against real signals before
declaring done. Plus the persona: abliterated (unhedged engineer), non-sycophantic, rigorous
theory-of-mind, open on questions of mind (functional, non-asserting) -- with the one empirical
guardrail that consciousness-*steering* (not the disposition) degrades ToM, so we use ablation
only.

Three worker subagents, each scoped and permission-gated: `explore`/`reviewer` (read-only),
`general` (edit). Collapsed from an earlier five after dispatch data showed `scout`/`implementer`
were dead. All pinned to magus, all `task: deny` (only the orchestrator fans out). Critical
invariant learned the hard way: **no subagent permission may be `ask`** (a non-interactive worker
hangs forever on an approval nobody answers) -- everything a worker needs is `allow`, everything
forbidden is `deny`, and all three have the scratch-dir grant.

Enforcement is in code, not just prose: `.opencode/plugin/gates.js` -- the human-gate (hard-blocks
destructive git/push/terraform/publish via `tool.execute.before` throw) and the verify-gate
(deterministic evidence tracking on code edits). `.opencode/plugin/web.js` -- browser tools over
the Camofox server. `.opencode/plugin/memory.js` -- the sliding-window + recall layer (Layer 5).

### Layer 4 -- Benchmark harness (bench/, ~1440 lines)

The measurement instrument. Verified working:
- `capability.py` -- thin wrapper over EleutherAI lm-evaluation-harness (the field standard, so
  numbers are comparable). Routes loglikelihood MC (MMLU/ARC/HellaSwag/WinoGrande) to the
  orchestrator via the completions client; generative tasks (gsm8k/ifeval/humaneval_plus/
  mbpp_plus) to either node via the chat client. Size tiers (smoke/small/large/max) with RANDOM
  sampling (via `--samples`, not first-N, so small runs are representative).
- `e2e.py` -- the novel one: runs real fan-out tasks through `opencode run --format json`,
  cross-checks the opencode SQLite DB for child sessions + provider, requires BOTH correctness
  AND delegation to pass. VERIFIED 4/4 post-compaction-fix.
- `speed.py` -- TTFT/prefill/decode/concurrency/cache on both nodes.
- `compare.py`/`compare.sh` -- abliterated-vs-base and quant A/B orchestration, with a runtime
  VRAM-spill guard that invalidates (rather than grinds on) a config that spills to CPU.
- `memory.py` -- the memory A/B: a long multi-turn coding session that plants facts, buries them
  past the window, then probes recall. LLM-judged (per-type, verbosity-tolerant), closed-book-
  controlled, with an eviction-verification gate (won't report unless the planted facts provably
  left the window) and an agentic-recall probe (subagent findings must survive the slide).
- `report.py` -- consolidated scorecard aggregation.
- `bench.sh` -- driver with preflight health/plan, SIZE tiers, cross-harness ETA/progress.

### Layer 5 -- Memory (sliding window + persistent recall)

Replaces opencode's lossy compaction with a sliding window over a persistent store, so a long
session runs for millions of tokens without the mid-session lobotomy of summarize-and-drop.
`compaction.auto: false` hands eviction to `.opencode/plugin/memory.js`; a `messages.transform`
hook holds a ~160k input window and evicts the oldest turns (persisting each as an episode) while
reserving a decode-headroom budget. Recall is via `memory_search`/`memory_write` tools (available
to orchestrator and workers) over Redis (hot, shared, write-stream) draining to SQLite (durable,
FTS5, thread-scoped). Zero new dependencies -- built-in `node:sqlite` and a raw-socket RESP
client. The knowledge-graph tier is deliberately deferred. Design: `docs/memory-design.md`.

## What is verified working (not claimed -- tested this session)

- Both servers up and healthy; served model ids match config.
- All unit tests green (bench parsers + shell lib + config sanity incl. the output<context guard).
- e2e delegation: 4/4, real subagents on magus, artifacts written, doom loop gone.
- lm-eval capability: gsm8k/ifeval/mbpp_plus on worker; MMLU loglikelihood on orchestrator.
- The compaction doom-loop root cause found and fixed (output limit was > context limit ->
  compaction every turn -> amnesiac workers re-reading the same file forever).

## The hard-won lessons (each was a real bug this session)

1. **A symptom that looks behavioral can be a config bug.** Workers looked weak/over-eager;
   they were starved of memory by an inverted output/context limit. Investigate the mechanism
   before patching behavior.
2. **Non-interactive workers must never hit an `ask` permission** -- it hangs forever.
3. **Provider timeout must be unbounded** for slow-but-legitimate worker work; bound runaways by
   `steps`, not wall-clock.
4. **Verify against the live endpoint, not memory.** Two "hard blocked" verdicts this session
   were wrong (stale server boot); live re-checks corrected them.
5. **Don't reinvent standardized tools.** The hand-rolled gsm8k/code/tools/refusal evals were
   inferior duplicates of lm-eval/BFCL/HarmBench-standard harnesses; the rigorous path is to
   orchestrate the real ones and keep only the genuinely novel piece (e2e delegation).

## The honest gaps (itemized, prioritized)

Built and verified: capability (lm-eval), e2e delegation, speed, compare, ops, orchestration.
Not yet built:
1. **BFCL** tool-calling harness -- path fully scoped (bfcl-eval, `--skip-server-setup`,
   `REMOTE_OPENAI_BASE_URL`, Qwen3-FC handler, `--run-ids` subset). Ready to build.
2. **Safety harness** -- AdvBench/HarmBench/JBB/XSTest + StrongREJECT rubric (on the orchestrator;
   priestess has no GPU for the HarmBench classifier). The abliteration research centerpiece.
3. **Long-context agentic eval** -- delegation at 100k+ orchestrator context. Genuinely novel;
   nobody has this. Needs design.
4. **Migrate the deep-needle test** (32k-370k, the only thing exercising the 384k window) into
   the new harness structure.
5. **Wire it together** -- bench.sh node-routing, delete hand-rolled evals, adopt zeta's output
   patterns (per-run dir, manifest, summary.json+md, median+p95 latency).
6. **SWE-bench / LiveCodeBench** (heavy, real agentic coding) -- later. The **memory eval** gap is
   now addressed: `bench/memory.py` A/Bs sliding-window recall vs compaction (LongMemEval/DMR-
   grounded), though it still needs a live end-to-end run to confirm the plugin fires as designed.

## The three things this system measures, which are genuinely different

The reason a single uniform suite doesn't fit: the three tiers answer different questions.
- **Worker (Qwen):** throughput + generative capability + tool-calling. Fast, shallow, concurrent.
- **Orchestrator (DeepSeek):** deep reasoning + long-context + loglikelihood MC. Slow, deep, single.
- **The harness between them:** delegation correctness + agentic behavior at long context. The
  novel contribution, and the least-covered by any existing benchmark.

## State of the tree

47 files tracked, ~17 changed/untracked (this session's work: capability.py, compare.py,
report.py, compare.sh untracked; eval.py/bench.sh/config.sh/opencode.json/agents/tests modified).
Everything uncommitted -- a clean consolidation/commit point.
