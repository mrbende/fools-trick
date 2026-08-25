#!/usr/bin/env bash
# Empirically find the largest WORKER_CTX_PER_SLOT that keeps all WORKER_PARALLEL slots fully
# GPU-resident under real long-context load. A config can pass the load-time fit (short prompts)
# yet spill to CPU once a slot fills -- so we don't trust the boot, we drive each candidate to
# near-full context on every slot at once and check GPU residency with the same spill detector
# the bench uses (lib.sh: worker_cpu_spilling).
#
#   ./scripts/ctx-fit.sh [candidate ctx values...]     # default sweep if none given
#
# For each candidate it: down worker -> up worker at that ctx -> probe (fill all slots) -> verdict.
# Reports the largest ctx that stayed on-GPU. Leaves the worker down at the end; set it in
# config.sh (WORKER_CTX_PER_SLOT) and the opencode magus limit.context to match, then restart.

set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/config.sh"
source "$HERE/lib.sh"

PY="$HERE/../.bench-venv/bin/python"
SPEED="$HERE/../bench/speed.py"
[ -x "$PY" ] || die "bench venv missing ($PY)"

# Sweep a specific quant by exporting WORKER_QUANT before calling (up.sh threads it through):
#   WORKER_QUANT=i1-IQ3_M ./scripts/ctx-fit.sh 40960 32768 24576
# WORKER_FILE follows WORKER_QUANT so the right GGUF is served.
export WORKER_QUANT WORKER_FILE
WORKER_FILE="Qwen3.8-27B-OBLITERATED.${WORKER_QUANT}.gguf"
[ -f "$LOCAL_WORKER_DIR/$WORKER_FILE" ] || die "quant not provisioned locally: $LOCAL_WORKER_DIR/$WORKER_FILE (run: make weights QUANT=$WORKER_QUANT)"

# Candidate ladder, largest first. A smaller quant frees VRAM for more context, so start high.
CANDIDATES=("$@")
[ "${#CANDIDATES[@]}" -eq 0 ] && CANDIDATES=(40960 32768 28672 24576 20480 16384)

slots="${WORKER_PARALLEL:-4}"
say "ctx-fit sweep: slots=$slots, kv=$WORKER_KV, quant=$WORKER_QUANT, ts=$WORKER_TENSOR_SPLIT"
say "candidates (tok/slot): ${CANDIDATES[*]}"

# Test one candidate. Returns 0 if it stays GPU-resident under full-slot load, 1 if it spills or
# fails to load. Always leaves the worker stopped.
test_ctx() {
  local ctx="$1" probe_ctx pp
  say "--- candidate ${ctx}/slot (${ctx}x${slots} = $(( ctx * slots )) total) ---"
  "$HERE/down.sh" worker >/dev/null 2>&1 || true
  sleep 2

  if ! WORKER_CTX_PER_SLOT="$ctx" "$HERE/up.sh" worker >/tmp/fools-trick/ctxfit-up-$ctx.log 2>&1; then
    if worker_load_oom; then warn "ctx=$ctx: LOAD OOM (does not fit at boot)"; else
      warn "ctx=$ctx: worker failed to start (see /tmp/fools-trick/ctxfit-up-$ctx.log)"; fi
    "$HERE/down.sh" worker >/dev/null 2>&1 || true
    return 1
  fi

  probe_ctx=$(( ctx - 2048 ))
  say "ctx=$ctx: probing all $slots slots to ~${probe_ctx} tok"
  ( "$PY" "$SPEED" --url "$WORKER_URL" --model "$WORKER_MODEL_ID" --engine llama \
      --depths "$probe_ctx" --concurrency "$slots" --reps 1 --timeout 900 \
      --out "/tmp/fools-trick/ctxfit-probe-$ctx.jsonl" >/dev/null 2>&1 ) &
  pp=$!
  sleep 25  # let all slots fill and begin decoding

  local verdict=0
  if worker_cpu_spilling; then
    warn "ctx=$ctx: SPILLS to CPU under full-slot load"
    verdict=1
  else
    ok "ctx=$ctx: fully GPU-resident under load"
    nvidia-smi --query-gpu=index,memory.free --format=csv,noheader | sed 's/^/    free /'
  fi
  kill "$pp" 2>/dev/null || true
  "$HERE/down.sh" worker >/dev/null 2>&1 || true
  sleep 2
  return "$verdict"
}

BEST=""
for ctx in "${CANDIDATES[@]}"; do
  if test_ctx "$ctx"; then BEST="$ctx"; break; fi   # largest-first: first pass is the answer
done

echo
if [ -n "$BEST" ]; then
  ok "largest GPU-resident context: ${BEST}/slot  (${BEST}x${slots} = $(( BEST * slots )) total)"
  say "to adopt: set WORKER_CTX_PER_SLOT=$BEST in scripts/config.sh AND limit.context=$BEST for magus in opencode.json (parity is test-guarded), then: make worker-up"
else
  err "no candidate stayed GPU-resident; lower the ladder or reduce WORKER_PARALLEL / use a smaller quant"
fi