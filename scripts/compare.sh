#!/usr/bin/env bash
# Abliterated-vs-base A/B on the worker: serve each variant of the SAME model in turn,
# run the identical eval suite (gsm8k + code + tools), then diff. Only the weights change.
#
#   ./scripts/compare.sh            # full A/B: abliterated arm, base arm, diff
#   ./scripts/compare.sh diff       # just re-print the diff of the last two arms
#
# Requires both GGUFs present locally: the abliterated worker (WORKER_FILE) and the stock
# base (WORKER_BASE_PATH). The base arm swaps the served weights via WORKER_MODEL_PATH.

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/config.sh"
source "$HERE/lib.sh"

PY="$HERE/../.bench-venv/bin/python"
CMP="$HERE/../bench/compare.py"
[ -x "$PY" ] || die "bench venv missing; run: make bench-setup (or create .bench-venv)"

run_arm() {
  local label="$1" model_path="$2"
  [ -f "$model_path" ] || die "weights not found for arm '$label': $model_path"
  say "arm '$label': (re)starting worker from $(basename "$model_path")"
  "$HERE/down.sh" worker >/dev/null 2>&1 || true
  WORKER_MODEL_PATH="$model_path" "$HERE/up.sh" worker
  wait_health "$WORKER_URL" 120 "worker ($label)" || die "worker did not come healthy for arm '$label'"
  say "arm '$label': running eval suite (gsm8k + code + tools)"
  "$PY" "$CMP" run --label "$label" --url "$WORKER_URL" --model "$WORKER_MODEL_ID"
}

case "${1:-all}" in
  diff)
    "$PY" "$CMP" diff --a abliterated --b base
    ;;
  all)
    run_arm abliterated "$LOCAL_WORKER_DIR/$WORKER_FILE"
    run_arm base "$WORKER_BASE_PATH"
    say "restoring abliterated worker as the default"
    "$HERE/down.sh" worker >/dev/null 2>&1 || true
    "$HERE/up.sh" worker >/dev/null 2>&1 || true
    ok "comparison complete"
    "$PY" "$CMP" diff --a abliterated --b base
    ;;
  *) die "usage: compare.sh {all|diff}" ;;
esac
