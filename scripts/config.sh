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

# --- weight storage: NAS canonical, local fast-copy for serving ---
NAS_MODELS="${NAS_MODELS:-/mnt/empress/models}"                       # persistent, shared over 10G NFS
NAS_WORKER_DIR="${NAS_WORKER_DIR:-${NAS_MODELS}/Qwen3.8-27B-OBLITERATED}"
LOCAL_MODELS="${LOCAL_MODELS:-$HOME/Models}"                          # fast NVMe serving cache
LOCAL_WORKER_DIR="${LOCAL_WORKER_DIR:-${LOCAL_MODELS}/qwen3.8-27b-obliterated}"

# --- worker model selection ---
# imatrix (i1) quants: activation-calibrated, higher quality per byte than the static
# GGUFs at the same size. i1-Q4_K_S is mradermacher's "optimal size/speed/quality" pick
# and, at 15.9 GB, is the quant that fits 4 concurrent slots x 32k on 2x 12 GB with a
# real cushion. Measured KV math (this arch: ~16 of 65 blocks attention, kv_heads=4,
# head_dim=256 -> ~32 KB/token q8_0): 4x32k KV+state ~4.3 GB, +weights 15.9 +overhead
# ~1.5 = ~21.7 GB against ~21.7 GB usable. Q4_K_M (16.9) would overrun 4x32k.
WORKER_REPO="${WORKER_REPO:-mradermacher/Qwen3.8-27B-OBLITERATED-i1-GGUF}"
WORKER_QUANT="${WORKER_QUANT:-i1-Q4_K_S}"
WORKER_FILE="${WORKER_FILE:-Qwen3.8-27B-OBLITERATED.${WORKER_QUANT}.gguf}"

# --- serving shape (worker) ---
# Qwen3.8-27B is a HYBRID-recurrent arch (qwen35: Gated-DeltaNet/SSM + attention),
# not dense. Consequences, all load-bearing:
#   - Only ~16 of 65 blocks carry a KV cache; the rest hold a tiny recurrent state.
#     So KV is ~1/4 of a dense 27B and 4 concurrent slots at 32k each is affordable.
#   - Row/tensor split cannot partition the recurrent state tensors -> they FAIL to
#     load. --split-mode layer is the ONLY working mode across 2 GPUs.
WORKER_PARALLEL="${WORKER_PARALLEL:-4}"        # concurrent slots
WORKER_CTX_PER_SLOT="${WORKER_CTX_PER_SLOT:-32768}"
WORKER_KV="${WORKER_KV:-q8_0}"                 # tool-safe KV quant; q4_0 degrades tool calling
WORKER_SPLIT_MODE="${WORKER_SPLIT_MODE:-layer}"  # only mode that loads the hybrid arch on 2 GPUs
# VRAM-proportional layer split. GPU0 carries the desktop (~2GB), so bias layers to
# GPU1 to avoid OOMing GPU0. Free VRAM ~9.8G (GPU0) vs ~11.9G (GPU1) -> ~9,12 with a
# cushion on GPU0. Override if the desktop footprint changes.
WORKER_TENSOR_SPLIT="${WORKER_TENSOR_SPLIT:-9,12}"
WORKER_REASONING="${WORKER_REASONING:-medium}"
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

# --- spark serving recipe (submodule here; a matching clone runs on fool) ---
SPARK_DIR_LOCAL="${SPARK_DIR_LOCAL:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/spark}"
SPARK_REMOTE_URL="${SPARK_REMOTE_URL:-https://github.com/mrbende/DeepSeek-v4-Flash-One-DGX-Spark.git}"
# Dedicated clone of OUR fork on fool, isolated from the pre-existing
# AttuneIntelligence clone and library-inference-recipe already in ~/Recipes.
FOOL_SPARK_DIR="${FOOL_SPARK_DIR:-$HOME/Recipes/fools-trick-spark}"
FOOL_ABLATE="${FOOL_ABLATE:-1}"                # 1 = abliterated orchestrator

# The commit fool must run: the SHA this repo's submodule pins. fool's clone must be
# clean and at this exact commit before we start the server there. Resolved at runtime
# from the submodule so it can never drift from what we track.
spark_pinned_sha() { git -C "$SPARK_DIR_LOCAL" rev-parse HEAD 2>/dev/null; }

# --- opencode ---
OPENCODE_PROJECT_DIR="${OPENCODE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
