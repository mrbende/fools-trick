# Hardware and serving shape, from first principles

The serving shape is not inherited lore -- it is derived here from measured facts, so a future
reader (or a future you) can re-check the reasoning instead of trusting a number. Re-run the
measurements when the hardware or the model changes; do not copy the conclusions forward blind.

## The two nodes (measured)

| | orchestrator | workers |
|---|---|---|
| host | `fool` (DGX Spark) | `magus` |
| GPU | NVIDIA GB10, aarch64 | 2x RTX 3080 Ti, 12 GB each |
| memory | 121 GB unified (~116 GB free) | 24 GB VRAM + 93 GB system RAM, Threadripper 9970X (64 threads) |
| link | 192.168.1.11, wired LAN, ~7 ms | local |

## Orchestrator: DeepSeek-V4-Flash, EXL3 3.0bpw, 384k context

Settled and unchanged. The orchestrator's value is holding the whole task in a deep window, so we
maximize context, not quality-per-token: 3.0bpw weights (~99 GB) on 116 GB free unified memory
leave ~17 GB for KV, which is where the 384k ceiling comes from. A 4.0bpw checkpoint exists on disk
but trades context for quality -- the wrong trade for the deep-reasoning seat. Served by the spark
submodule recipe; not managed by this repo's worker serving.

## Worker: the KV math (derived from the GGUF, not assumed)

The whole shape hangs on one number: KV bytes per token. Read from the `Qwen3.8-27B-OBLITERATED`
GGUF metadata directly:

- arch `qwen35`, **65 blocks**, of which **exactly 17 carry a KV cache** (attention); the other 48
  are recurrent/SSM and cost ~nothing per token. This is why KV is ~1/4 of a dense 27B.
- `head_count_kv = 4`, `key_length = value_length = 256`.

Per token, across the 17 attention layers: `2 x head_count_kv x (key_len + val_len) x bytes/elem`.

| KV quant | KB/token | 32768 ctx/slot | x4 slots |
|---|---|---|---|
| f16 | 68.0 | 2.13 GB | 8.5 GB |
| **q8_0** | **36.1** | **1.13 GB** | **4.5 GB** |
| q5_1 | 25.5 | 0.80 GB | 3.2 GB |
| q4_0 | 19.1 | 0.60 GB | 2.4 GB |

q8_0 is the floor we use: q5_1/q4_0 have **no working CUDA flash-attention kernel for this hybrid
arch** on Ampere, so with `-fa on` they silently spill the attention op to CPU (GPUs idle, throughput
craters). Verified in a prior debugging session; do not set them.

## Worker: the VRAM budget (the shape decision)

Measured desktop footprint on GPU0 is **1.5 GB** (steady, with browser + Slack + Chromium open) --
not the 1.9 GB the earlier config assumed. So usable VRAM with the desktop up is ~22.2 GB.

```
usable (desktop up):        ~22.2 GB
  Q4_K_S weights:            -15.0 GB   (the tool-calling floor; smaller quants drop it)
  q8_0 KV, 4 slots x 32768:   -4.5 GB
  compute buffers / overhead: ~-1.5 GB  (estimate -- the one figure to confirm live)
                             ─────────
  headroom:                   ~1.2 GB
```

### Why 4 x 32768 (not the old 3 x 45056)

The inherited `3 x 45056` fit, but at the edge: ~5.0 GB KV left only ~0.7 GB slack, and it was tuned
to the stale 1.9 GB desktop figure and to a pre-eviction-fix world where a smaller per-slot context
caused a doom-loop. Two things changed the calculus:

1. **Recoverable eviction exists now** (`core/context`, `core/tools recall`). A worker past its
   window evicts a tool result to the Event Log and recovers it by `expand(seq)`. So a smaller
   per-slot context is graceful, not fatal -- the old reason to fight for every token of context is
   gone.
2. **Concurrency is the actual throughput lever.** The orchestrator scales by fanning out parallel
   workers; more slots > deeper per-slot context for that pattern.

So we spend the same ~5 GB KV budget on **4 slots at 32768** instead of 3 at 45056: +33% concurrency,
same VRAM, and 32768 sits comfortably above the 18000 prune trigger (see below), so the eviction
layer rarely even engages.

### Tensor split

`-sm layer` is the only mode that loads the SSM/recurrent tensors across two GPUs (row/tensor split
cannot partition them). `-ts 10,12` biases weights off GPU0 (the desktop card). At 4 x 32768 the
tighter card is GPU1; of the candidate ratios, only `10,12` keeps both under budget (paper margins
~0.6 GB GPU0 / ~0.2 GB GPU1). The GPU1 margin is thin -- **confirm resident live, and run headless
if the desktop VRAM footprint grows.**

## Worker: prune budgets follow the context

The context management invariant (test-guarded): `worker_input_tokens + worker_decode_headroom <=
ctx_per_slot`. At 32768:

- `worker_decode_headroom = 14000` -- clears the 12288 opencode output floor with slack for the
  over-reasoning worker.
- `worker_input_tokens = 18000` -- the prune trigger. 18000 + 14000 = 32000, margin 768 under 32768.

This is lower than the old 26000 trigger, so the prune fires earlier -- which is **safe now** because
eviction is recoverable. A result evicted early is one `expand(seq)` away, not lost. The smaller
context and the recoverable-eviction fix are the same design decision working together.

## Speculative decoding: off, by necessity (not assumption)

An MTP draft head exists on disk only for the *base* Qwen (`mtp-Qwen3.8-27B-Q4_0.gguf`), not the
abliterated worker. You cannot spec-decode the abliterated model without a matching abliterated
draft, and MTP spec also breaks on a layer split (llama.cpp #27428/#26750). So `--spec-*` stays off
for the worker -- a settled fact, not a guess.

## The idle-RAM lever (noted, not taken)

magus has 93 GB system RAM and 64 threads nearly idle (used only by the SQLite/Redis memory store).
Unexplored options if the GPU shape proves too tight or a bigger worker is wanted: partial CPU
offload of a larger model, or the smaller-active-param MoE class (Scroll validated Qwen3.6-35B-A3B
at 88.8 on LongMemEval). Deferred: the current 27B-on-GPU shape is the known-good baseline to
measure against first.

## What still needs a live measurement

The paper math is exact except the CUDA compute-buffer size (~1.5 GB estimate). Bring the worker up
and confirm the 4 x 32768 shape stays fully GPU-resident under real 4-way load (`make bench-quants`
+ the VRAM-spill guard). If GPU1 spills, the fallback is 3 x 32768 (KV 3.4 GB, comfortable) or
4 x 28672. The A/B quants (IQ3_M, Q3_K_M) are already on disk if freeing weight VRAM proves worth
the tool-calling cost -- but Q4_K_S is the measured tool-calling floor, so treat a smaller quant as a
last resort, not a default.
