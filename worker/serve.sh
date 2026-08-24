#!/usr/bin/env bash
# fools-trick worker: Qwen3.8-27B-OBLITERATED as an agentic subagent backend on magus.
#
# Serves from the LOCAL fast-copy (NVMe). Weight provisioning (NAS canonical ->
# local copy) is handled by scripts/weights.sh, invoked automatically if the local
# copy is missing. See scripts/config.sh for all tunables.
#
# This is NOT the sibling Qwen3.8-27B-two-3080Ti-TEST recipe (a single vision stream).
# A subagent worker does agentic tool-calling, so the priorities invert:
#   --jinja                 : model's tool-aware chat template (the #1 tool-call fix)
#   Q4_K_M weights          : tool-safe 4-bit floor, leaves room for KV + parallel slots
#   -ctk/-ctv q8_0          : tool-safe KV (q4_0 degrades tool calling)
#   --parallel N            : N concurrent slots; per-slot ctx = total ctx / N
#   reasoning_effort medium : low LOOPS on tool use, high stalls before calls

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/../scripts/config.sh"
source "$HERE/../scripts/lib.sh"

MODEL_PATH="${WORKER_MODEL_PATH:-$LOCAL_WORKER_DIR/$WORKER_FILE}"
CTX=$(( WORKER_PARALLEL * WORKER_CTX_PER_SLOT ))

FOREGROUND=0
for arg in "$@"; do
  case "$arg" in
    --foreground) FOREGROUND=1 ;;
    -h|--help) echo "usage: worker/serve.sh [--foreground]"; exit 0 ;;
    *) die "unknown arg: $arg" ;;
  esac
done

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
  -ngl 99
  -fa on
  -c "$CTX"
  -ctk "$WORKER_KV" -ctv "$WORKER_KV"
  -ub 512 -b 2048
  --parallel "$WORKER_PARALLEL"
  --jinja
  --reasoning-format deepseek
  --chat-template-kwargs "{\"reasoning_effort\":\"${WORKER_REASONING}\"}"
  --no-mmproj
  --host 0.0.0.0 --port "$WORKER_PORT"
)

say "worker: $WORKER_FILE  slots=$WORKER_PARALLEL  ctx/slot=$WORKER_CTX_PER_SLOT  kv=$WORKER_KV  reasoning=$WORKER_REASONING  -> 0.0.0.0:$WORKER_PORT"

if [ "$FOREGROUND" = 1 ]; then
  exec "$LLAMA_SERVER" "${args[@]}"
else
  mkdir -p "$HERE/logs"
  logfile="$HERE/logs/worker.log"
  setsid nohup "$LLAMA_SERVER" "${args[@]}" > "$logfile" 2>&1 &
  ok "started in background, pid $!, log: $logfile"
  dim "readiness: make worker-health"
fi
