#!/usr/bin/env bash
# fools-trick shared configuration. Sourced by every script and the Makefile.
# Single source of truth for hosts, ports, paths, and the active worker model.
# Everything here is overridable from the environment.

# --- topology ---
FOOL_HOST="${FOOL_HOST:-fool}"                 # DGX Spark orchestrator (resolves via /etc/hosts to 192.168.1.11)
FOOL_PORT="${FOOL_PORT:-8888}"                 # DeepSeek-V4-Flash OpenAI endpoint
FOOL_MODEL_ID="${FOOL_MODEL_ID:-deepseek-v4-flash-0731}"
FOOL_URL="${FOOL_URL:-http://${FOOL_HOST}:${FOOL_PORT}}"

WORKER_PORT="${WORKER_PORT:-8898}"             # local Qwen worker endpoint on magus
WORKER_MODEL_ID="${WORKER_MODEL_ID:-qwen3.8-27b-obliterated}"
WORKER_URL="${WORKER_URL:-http://127.0.0.1:${WORKER_PORT}}"
WORKER_UNIT="${WORKER_UNIT:-fools-worker}"     # systemd --user transient unit name (journald)

# --- weight storage: NAS canonical, local fast-copy for serving ---
NAS_MODELS="${NAS_MODELS:-/mnt/empress/models}"                       # persistent, shared over 10G NFS
NAS_WORKER_DIR="${NAS_WORKER_DIR:-${NAS_MODELS}/Qwen3.8-27B-OBLITERATED}"
LOCAL_MODELS="${LOCAL_MODELS:-$HOME/Models}"                          # fast NVMe serving cache
LOCAL_WORKER_DIR="${LOCAL_WORKER_DIR:-${LOCAL_MODELS}/qwen3.8-27b-obliterated}"

# --- worker model selection ---
# imatrix (i1) quants: activation-calibrated, higher quality per byte than the static
# GGUFs at the same size. i1-Q4_K_S (14.74 GB GGUF) is mradermacher's "optimal size/speed/quality"
# pick, chosen because it holds tool-calling (87.5%, 7/8) and code (93.3%) while fitting 4
# concurrent slots x 32768 with q8_0 KV on 2x 12 GB (see serving shape below for the KV math).
# Smaller quants were rejected: IQ3_M drops tool-calling to 75%, Q3_K_M drops code to 80%.
# Q4_K_M (16.9) would overrun; sub-4-bit degrades tool-call structure, so Q4_K_S is the floor.
WORKER_REPO="${WORKER_REPO:-mradermacher/Qwen3.8-27B-OBLITERATED-i1-GGUF}"
WORKER_QUANT="${WORKER_QUANT:-i1-Q4_K_S}"
WORKER_FILE="${WORKER_FILE:-Qwen3.8-27B-OBLITERATED.${WORKER_QUANT}.gguf}"
# Candidate quants under evaluation (the A/B set). Q4_K_S is the current default and the
# tool-calling floor from prior runs; IQ3_M/Q3_K_M are the smaller arms being tested for whether
# they hold tool-calling while freeing VRAM for more context. `weights.sh` reports presence of
# each; `compare.sh quants` A/Bs them. Space-separated quant tags.
WORKER_QUANTS="${WORKER_QUANTS:-i1-Q4_K_S i1-IQ3_M i1-Q3_K_M}"

# Stock (non-abliterated) base of the SAME model, for the abliteration A/B comparison.
# IQ4_XS (15.7 GB) is the closest size match to the abliterated i1-Q4_K_S (15.8 GB) -- the
# fair arm, since quant size is held ~constant so the only variable is the abliteration.
WORKER_BASE_PATH="${WORKER_BASE_PATH:-$LOCAL_MODELS/qwen3.8-27b/Qwen3.8-27B-IQ4_XS.gguf}"

# --- serving shape (worker) ---
# Qwen3.8-27B is a HYBRID-recurrent arch (qwen35: Gated-DeltaNet/SSM + attention),
# not dense. Consequences, all load-bearing:
#   - Only ~16 of 65 blocks carry a KV cache; the rest hold a tiny recurrent state.
#     So KV is ~1/4 of a dense 27B and 4 concurrent slots at high context is affordable.
#   - Row/tensor split cannot partition the recurrent state tensors -> they FAIL to
#     load. --split-mode layer is the ONLY working mode across 2 GPUs.
WORKER_PARALLEL="${WORKER_PARALLEL:-4}"        # concurrent slots
# 32768/slot (131072 total across 4 slots) is the MEASURED max that stays fully GPU-resident for
# Q4_K_S weights + q8_0 KV under real long-context load on all 4 slots. Verified with
# llama-batched-bench (npl=4): 32768/slot decodes ~66 t/s aggregate ~1720 t/s, all on GPU; 40960
# does NOT fit for Q4_K_S (KV overflow spills the attention op to CPU). Smaller quants reach more
# context (Q3_K_M 40960, IQ3_M 49152+) but IQ3_M drops tool-calling to 75% and Q3_K_M drops code
# to 80%; Q4_K_S holds both (tools 87.5%, code 93.3%) -- context is not worth the quality loss.
# Frontier is weight-size-bound: every ~2 GB of weights freed buys ~8k more ctx/slot.
WORKER_CTX_PER_SLOT="${WORKER_CTX_PER_SLOT:-32768}"
# q8_0 KV: the ONLY quantized KV with a working CUDA flash-attention kernel for this hybrid
# (qwen35 / GatedDeltaNet) arch on Ampere. q5_1 has NO CUDA FA kernel here: with -fa on it
# silently falls back to CPU for the attention op -- GPUs go idle, ~30 cores peg, throughput
# craters -- even with GBs of VRAM free. Isolated with llama-batched-bench (npl=4): q8_0 and f16
# run fully on GPU (~1790 / ~1830 t/s aggregate); q5_1 hangs/CPU-spills. f16 is fastest but 2x the
# KV bytes; q8_0 is the quantized floor that stays on-GPU. Do NOT set q5_1/q4_0 here.
WORKER_KV="${WORKER_KV:-q8_0}"
WORKER_SPLIT_MODE="${WORKER_SPLIT_MODE:-layer}"  # only mode that loads the hybrid arch on 2 GPUs
# Layer split. GPU0 loses ~1.9 GB to the desktop, so give GPU1 slightly more of the
# model to keep GPU0's free memory (which also holds a compute buffer) comfortable.
# With 32768/slot at q8_0 KV this fits cleanly (fully GPU-resident on all 4 slots). If the
# desktop footprint grows, shift toward 9,12; if GPU1 ever OOMs, shift toward 11,10.
WORKER_TENSOR_SPLIT="${WORKER_TENSOR_SPLIT:-10,12}"
# low, not medium: the abliterated Qwen over-reasons on simple worker tasks -- measured 20k+
# output tokens on a "list the make targets" dispatch at medium, vs a correct answer in ~80-110
# at low, with tool-calling fully intact (verified: read/write tool_calls emit correctly at low).
# The orchestrator's low-collapse caveat is DeepSeek-specific; it does not apply to these workers.
WORKER_REASONING="${WORKER_REASONING:-low}"
# Qwen3.x "precise" sampling preset; stable for tool-calling. No repeat-penalty (corrupts JSON).
WORKER_TEMP="${WORKER_TEMP:-0.6}"
WORKER_TOP_P="${WORKER_TOP_P:-0.95}"
WORKER_TOP_K="${WORKER_TOP_K:-20}"
LLAMA_SERVER="${LLAMA_SERVER:-$HOME/.local/bin/llama-server}"

# --- disk safety thresholds (GiB) ---
LOCAL_MIN_FREE_GIB="${LOCAL_MIN_FREE_GIB:-24}" # worker i1-Q4_K_S ~15.9G + headroom
NAS_MIN_FREE_GIB="${NAS_MIN_FREE_GIB:-120}"    # DeepSeek weights ~107G if fool downloads to NAS

# --- shared scratch (RAM-backed, wiped on reboot) ---
SCRATCH_DIR="${SCRATCH_DIR:-/tmp/fools-trick/scratch}"

# --- memory: sliding window + persistent recall (see docs/memory-design.md) ---
# Two jobs: (1) slide a live input window instead of compacting (lossy summarize-drop),
# (2) persist everything that slides out for recall. Two stores: Redis (hot, shared, ephemeral
# short-term + write-queue for the swarm) and SQLite (durable episode store, source of truth).
MEMORY_ENABLED="${MEMORY_ENABLED:-1}"
# Redis runs as a make-up-managed Docker container (redis:7-alpine, already local). Ephemeral by
# design -- short-term memory; make down can drop it. SQLite is what persists.
REDIS_CONTAINER="${REDIS_CONTAINER:-fools-redis}"
REDIS_PORT="${REDIS_PORT:-6379}"
REDIS_URL="${REDIS_URL:-redis://127.0.0.1:${REDIS_PORT}}"
REDIS_IMAGE="${REDIS_IMAGE:-redis:7-alpine}"
# Durable episode store. NOT in /tmp -- must survive reboot. Episodes are keyed by conversation
# thread (opencode sessionID + its root) so recall is correctly scoped per conversation.
MEMORY_DB="${MEMORY_DB:-$HOME/.local/share/fools-trick/memory.db}"
# Sliding-window budget for the orchestrator (384k context). Hold this many INPUT tokens live and
# slide; reserve DECODE_HEADROOM for output. Invariant: WINDOW_INPUT_TOKENS + DECODE_HEADROOM well
# under 384k, so decode always has room (input and output compete for the same window).
WINDOW_INPUT_TOKENS="${WINDOW_INPUT_TOKENS:-160000}"
DECODE_HEADROOM="${DECODE_HEADROOM:-32000}"
MEMORY_RECENT_TTL="${MEMORY_RECENT_TTL:-3600}"   # Redis recent-cache expiry (seconds)

# --- spark serving recipe (submodule here; a matching clone runs on fool) ---
SPARK_DIR_LOCAL="${SPARK_DIR_LOCAL:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/spark}"
SPARK_REMOTE_URL="${SPARK_REMOTE_URL:-https://github.com/mrbende/DeepSeek-v4-Flash-One-DGX-Spark.git}"
# Dedicated clone of OUR fork on fool, isolated from the pre-existing
# AttuneIntelligence clone and library-inference-recipe already in ~/Recipes.
FOOL_SPARK_DIR="${FOOL_SPARK_DIR:-$HOME/Recipes/fools-trick-spark}"
FOOL_ABLATE="${FOOL_ABLATE:-1}"                # 1 = abliterated orchestrator
# Reasoning effort for the orchestrator. The recipe defaults to `max`, which burns huge
# reasoning-token budgets every turn -- wasteful for a model whose job is delegation and
# synthesis (the workers do the deep thinking). `high` still plans good fan-outs and
# catches conflicts during synthesis without the max token-burn. Values: max|high|low|false.
FOOL_EFFORT="${FOOL_EFFORT:-high}"

# DeepSeek EXL3 weights (~107 GB). Three tiers in the recipe; we place them so serving
# is always local-fast and the NAS holds only a cold archive:
#   HF_CACHE (raw TP4 download, ~107 GB)  -> fool LOCAL during bootstrap. Hardlink-coalesce
#       into data/tp1 only works within one filesystem, so the download must be local.
#   data/tp1 (coalesced TP1, ~99 GB, mmap'd every serve) -> fool LOCAL. The serve hot path.
#   NAS archive -> after data/tp1 is built, the raw 107 GB hf-hub is pushed to the NAS as
#       cold backup and deleted from fool local, so fool carries only the ~99 GB it serves.
# make up then serves purely from local data/tp1; the NAS is never on the boot/serve path.
FOOL_HF_CACHE="${FOOL_HF_CACHE:-${FOOL_SPARK_DIR}/hf-hub}"                 # local download cache on fool
NAS_DEEPSEEK_ARCHIVE="${NAS_DEEPSEEK_ARCHIVE:-${NAS_MODELS}/deepseek-v4-flash-spark-hfcache}"  # cold backup

# The commit fool must run: the SHA this repo's submodule pins. fool's clone must be
# clean and at this exact commit before we start the server there. Resolved at runtime
# from the submodule so it can never drift from what we track.
spark_pinned_sha() { git -C "$SPARK_DIR_LOCAL" rev-parse HEAD 2>/dev/null; }

# --- opencode ---
OPENCODE_PROJECT_DIR="${OPENCODE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

# --- benchmark reporting ---
# Every run writes an .xlsx scorecard to the bench dir (on-disk, no cloud needed). If a Google
# service-account key is present (GOOGLE_APPLICATION_CREDENTIALS) and BENCH_SHEETS=1, it also
# creates a Google Sheet shared to this address.
BENCH_SHARE_WITH="${BENCH_SHARE_WITH:-reedbndr@gmail.com}"
