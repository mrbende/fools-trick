#!/usr/bin/env bash
# Benchmarks (stub). Placeholder for the benchmark suite we will build:
#   - worker: decode tok/s, concurrent-slot throughput, tool-call reliability rate
#   - orchestrator: prefill time vs depth, decode tok/s, DSpark acceptance
#   - end-to-end: wall-clock on a fan-out task vs single-stream baseline
# For now it just confirms endpoints and prints a single-shot decode-rate estimate.

set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/config.sh"
source "$HERE/lib.sh"

warn "bench suite not built yet -- running smoke measurement only"

smoke() {
  local label="$1" url="$2" model="$3"
  http_ok "$url/v1/models" || { warn "$label not serving; skipping"; return; }
  say "$label: 128-token decode timing"
  local t0 t1
  t0=$(date +%s.%N)
  curl -fsS --max-time 300 "$url/v1/chat/completions" -H 'Content-Type: application/json' \
    -d "{\"model\":\"$model\",\"messages\":[{\"role\":\"user\",\"content\":\"Count from 1 to 40 in words, one per line.\"}],\"max_tokens\":128,\"temperature\":0}" \
    >/dev/null 2>&1 || { warn "$label request failed"; return; }
  t1=$(date +%s.%N)
  awk -v a="$t0" -v b="$t1" 'BEGIN{printf "   ~%.1f s for up to 128 tokens (rough)\n", b-a}'
}

smoke "worker" "$WORKER_URL" "$WORKER_MODEL_ID"
smoke "orchestrator" "$FOOL_URL" "$FOOL_MODEL_ID"
dim "TODO: real suite (tool-call reliability, concurrency scaling, fan-out wall-clock)"
