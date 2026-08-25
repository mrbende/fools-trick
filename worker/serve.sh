#!/usr/bin/env bash
# fools-trick worker: Qwen3.8-27B-OBLITERATED as an agentic subagent backend on magus.
#
# Serves from the LOCAL fast-copy (NVMe). Weight provisioning (NAS canonical ->
# local copy) is handled by scripts/weights.sh, invoked automatically if the local
# copy is missing. See scripts/config.sh for all tunables.
#
# A subagent worker does agentic tool-calling across 2x RTX 3080 Ti, so the choices are:
#   -sm layer            : ONLY split mode that loads this hybrid-recurrent arch on 2 GPUs
#                          (row/tensor split cannot partition the SSM state tensors)
#   -ts 10,12            : VRAM-proportional split; biases layers off GPU0 (desktop ~2GB)
#   -ctk/-ctv q8_0       : tool-safe KV, MATCHED (mixed K/V types cause silent prefill collapse)
#   -fa on               : mandatory with quantized KV (context creation fails otherwise)
#   --parallel 4         : cheap here -- hybrid arch keeps KV small (~16 of 65 blocks)
#   --cache-reuse 256    : reuse KV across turns; big win for multi-turn agent loops
#   --no-context-shift   : hard-stop at limit, not silent truncation of the system prompt
#   no --spec-* flags    : MTP spec halves prefill on a layer split (bug #27428), CUDA
#                          acceptance collapses (#26750), and the abliterated GGUF likely
#                          dropped the MTP head. Correctness + prefill over decode speed.

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/../scripts/config.sh"
source "$HERE/../scripts/lib.sh"

MODEL_PATH="${WORKER_MODEL_PATH:-$LOCAL_WORKER_DIR/$WORKER_FILE}"
CTX=$(( WORKER_PARALLEL * WORKER_CTX_PER_SLOT ))

[ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ] && { echo "usage: worker/serve.sh   (foreground; run via systemd-run from up.sh)"; exit 0; }

# Ensure a local fast-copy exists (provisions from NAS, or downloads to NAS then copies).
if [ ! -f "$MODEL_PATH" ]; then
  say "local weights missing; provisioning via weights.sh"
  "$HERE/../scripts/weights.sh" worker
fi
[ -f "$MODEL_PATH" ] || die "worker weights not found at $MODEL_PATH"
[ -x "$LLAMA_SERVER" ] || die "missing llama-server at $LLAMA_SERVER"

mkdir -p "$SCRATCH_DIR"

if port_in_use "$WORKER_PORT"; then
  die "port $WORKER_PORT already in use -- stop the old worker first (make worker-down)"
fi

args=(
  -m "$MODEL_PATH"
  -a "$WORKER_MODEL_ID"
  -ngl 999
  -sm "$WORKER_SPLIT_MODE"
  -ts "$WORKER_TENSOR_SPLIT"
  -fa on
  -c "$CTX"
  -ctk "$WORKER_KV" -ctv "$WORKER_KV"
  -ub 512 -b 2048
  --parallel "$WORKER_PARALLEL"
  --cache-reuse 256
  --no-context-shift
  --jinja
  --reasoning-format deepseek
  --reasoning-preserve
  --chat-template-kwargs "{\"reasoning_effort\":\"${WORKER_REASONING}\"}"
  --temp "$WORKER_TEMP" --top-p "$WORKER_TOP_P" --top-k "$WORKER_TOP_K"
  --no-mmproj
  --host 0.0.0.0 --port "$WORKER_PORT"
)

say "worker: $WORKER_FILE  sm=$WORKER_SPLIT_MODE ts=$WORKER_TENSOR_SPLIT  slots=$WORKER_PARALLEL x ${WORKER_CTX_PER_SLOT}ctx  kv=$WORKER_KV  -> 0.0.0.0:$WORKER_PORT"

# Foreground exec. Backgrounding, logging, and lifecycle are systemd's job:
# up.sh launches this via `systemd-run --user --unit fools-worker`, so stdout/stderr
# go straight to journald (timestamps, rotation, persistence, `journalctl --user`).
exec "$LLAMA_SERVER" "${args[@]}"
