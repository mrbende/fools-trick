#!/usr/bin/env bash
# A/B the worker across weight variants: serve each in turn, run the identical eval suite
# (gsm8k + code + tools), then diff. Only the weights change, so differences are the weights.
#
#   ./scripts/compare.sh                  # abliterated-vs-base A/B, then diff
#   ./scripts/compare.sh quants           # quant A/B: Q4_K_S vs IQ3_M vs Q3_K_M, then diff
#   ./scripts/compare.sh diff a b         # re-print the diff of two named arms
#
# Each arm swaps the served weights via WORKER_MODEL_PATH. The quant A/B tests whether a smaller
# quant (which frees VRAM for more context) holds tool-calling -- the tools eval is the risk axis.

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/env.sh"
source "$HERE/lib.sh"

PY="$HERE/../../.bench-venv/bin/python"
CMP="$HERE/../../bench/compare.py"
SPEED="$HERE/../../bench/speed.py"
export COMPARE_DIR="${COMPARE_DIR:-/tmp/fools-trick/compare}"
mkdir -p "$COMPARE_DIR"
[ -x "$PY" ] || die "bench venv missing; run: make bench-setup (or create .bench-venv)"

# Record an invalidated arm as a real RESULT (not a crash): a config that OOMs at load or spills
# inference to CPU is a measured negative outcome. Write it to the compare results dir so the
# scorecard shows WHY it was rejected, then let the caller move to the next arm.
invalidate_arm() {
  local label="$1" reason="$2"
  warn "arm '$label' INVALID: $reason -- recording and skipping"
  mkdir -p "${COMPARE_DIR:-/tmp/fools-trick/compare}"
  printf '{"test":"_config","label":"%s","summary":true,"valid":false,"reason":"%s"}\n' \
    "$label" "$reason" >> "${COMPARE_DIR:-/tmp/fools-trick/compare}/${label}.jsonl"
}

# Start an arm and validate it stays fully on the GPUs. Returns 0 if valid+benched, 1 if
# invalidated (OOM at load, or CPU-spill under a probe). Never dies -- an invalid arm is a result.
run_arm() {
  local label="$1" model_path="$2"
  [ -f "$model_path" ] || { invalidate_arm "$label" "weights missing: $model_path"; return 1; }
  say "arm '$label': (re)starting worker from $(basename "$model_path")"
  "$HERE/down.sh" worker >/dev/null 2>&1 || true
  : > "${COMPARE_DIR:-/tmp/fools-trick/compare}/${label}.jsonl" 2>/dev/null || true

  # up.sh returns 3 on a load-time VRAM OOM (fast-fail, no long wait).
  if ! WORKER_MODEL_PATH="$model_path" "$HERE/up.sh" worker; then
    if worker_load_oom; then invalidate_arm "$label" "load OOM: does not fit in VRAM"; else
      invalidate_arm "$label" "worker failed to start"; fi
    return 1
  fi

  # Fits at load. Now probe under real load and confirm it does not spill to CPU: drive a
  # long-context request (fills a slot toward capacity), and while it runs, check GPU residency.
  say "arm '$label': probing VRAM residency under long-context load"
  local probe_ctx=$(( WORKER_CTX_PER_SLOT - 2048 ))
  ( "$PY" "$SPEED" --url "$WORKER_URL" --model "$WORKER_MODEL_ID" --engine llama \
      --depths "$probe_ctx" --concurrency 4 --reps 1 --timeout 900 \
      --out "${COMPARE_DIR:-/tmp/fools-trick/compare}/probe-${label}.jsonl" >/dev/null 2>&1 ) &
  local probe_pid=$!
  sleep 20  # let the probe fill slots and start decoding before we sample
  if worker_cpu_spilling; then
    # A spilled worker keeps grinding CPU even after the client cancels -- killing the probe is not
    # enough, the wedged slot must be torn down. Down the worker so the next arm starts clean.
    kill "$probe_pid" 2>/dev/null || true
    "$HERE/down.sh" worker >/dev/null 2>&1 || true
    invalidate_arm "$label" "CPU-spill at ctx=$probe_ctx x $WORKER_PARALLEL slots (GPU idle under load)"
    return 1
  fi
  wait "$probe_pid" 2>/dev/null || true
  ok "arm '$label': fully GPU-resident under load"

  say "arm '$label': running eval suite (gsm8k + code + tools)"
  "$PY" "$CMP" run --label "$label" --url "$WORKER_URL" --model "$WORKER_MODEL_ID"
}

restore_default() {
  say "restoring default worker ($WORKER_FILE)"
  "$HERE/down.sh" worker >/dev/null 2>&1 || true
  "$HERE/up.sh" worker >/dev/null 2>&1 || true
}

case "${1:-all}" in
  diff)
    "$PY" "$CMP" diff --a "${2:-abliterated}" --b "${3:-base}"
    ;;
  all)
    run_arm abliterated "$LOCAL_WORKER_DIR/$WORKER_FILE"
    run_arm base "$WORKER_BASE_PATH"
    restore_default
    ok "comparison complete"
    "$PY" "$CMP" diff --a abliterated --b base
    ;;
  quants)
    # A/B the candidate quants from config (WORKER_QUANTS), each PROVISIONED through the canonical
    # NAS->local flow (weights.sh) before benching. arm label = quant tag lowercased, i1- dropped.
    # Baseline is the active default quant; every other arm is diffed against it.
    label_of() { local l="${1#i1-}"; echo "${l,,}"; }
    base_label="$(label_of "$WORKER_QUANT")"
    valid=(); invalid=()
    for q in $WORKER_QUANTS; do
      label="$(label_of "$q")"
      say "provisioning $label ($q) via NAS-canonical flow"
      if ! bash "$HERE/weights.sh" QUANT="$q" >/dev/null 2>&1; then
        warn "skip $label: $q not available on NAS/HF yet"; continue
      fi
      # run_arm invalidates (and records) an arm that OOMs or spills to CPU, returning nonzero.
      if run_arm "$label" "$LOCAL_WORKER_DIR/Qwen3.8-27B-OBLITERATED.$q.gguf"; then
        valid+=("$label")
      else
        invalid+=("$label")
      fi
    done
    restore_default
    ok "quant comparison complete (valid: ${valid[*]:-none}; invalidated: ${invalid[*]:-none})"
    for label in "${valid[@]}"; do
      [ "$label" = "$base_label" ] && continue
      echo; say "=== $label vs $base_label ==="
      "$PY" "$CMP" diff --a "$label" --b "$base_label"
    done
    ;;
  *) die "usage: compare.sh {all|quants|diff [a] [b]}" ;;
esac
