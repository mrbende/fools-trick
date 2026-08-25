#!/usr/bin/env bash
# Benchmark driver. Three real suites across both distinct servers:
#   speed : TTFT / prefill / decode / concurrency / cache  (worker=llama, fool=vllm)
#   eval  : real reasoning -- gsm8k + ruler-at-depth        (exact-match on real data)
#   e2e   : the whole opencode harness on real fan-out tasks (the real eval)
#
#   ./scripts/bench.sh speed [worker|fool|both]
#   ./scripts/bench.sh eval  [worker|fool|both]
#   ./scripts/bench.sh e2e
#   ./scripts/bench.sh all
# Results: JSONL + this run's markdown under /tmp/fools-trick/bench/.

set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/config.sh"
source "$HERE/lib.sh"

BENCH_DIR="${BENCH_DIR:-/tmp/fools-trick/bench}"
mkdir -p "$BENCH_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
# All harnesses run under the bench venv (rich + datasets). Logs -> a per-run logfile.
PY="$OPENCODE_PROJECT_DIR/.bench-venv/bin/python"
SPEED="$OPENCODE_PROJECT_DIR/bench/speed.py"
EVAL="$OPENCODE_PROJECT_DIR/bench/eval.py"
E2E="$OPENCODE_PROJECT_DIR/bench/e2e.py"
LOG="$BENCH_DIR/$STAMP.log"

[ -x "$PY" ] || die "bench venv missing ($PY). create: python3 -m venv .bench-venv && .bench-venv/bin/pip install datasets rich"
REPORT="$OPENCODE_PROJECT_DIR/bench/report.py"
CAP="$OPENCODE_PROJECT_DIR/bench/capability.py"
WORKER_TOKENIZER="${WORKER_TOKENIZER:-Qwen/Qwen3.8-27B}"
serving() { http_ok "$1/v1/models" || http_ok "$1/health"; }

# SIZE selects how many RANDOM samples per task each harness runs: smoke|small|large|max.
# It threads to every harness (lm-eval --size, safety --n, e2e task subset) so one flag
# controls the whole run's cost. Default small = fast, representative.
SIZE="${SIZE:-small}"

# --- cross-harness progress + ETA ---------------------------------------------
# Each harness is one "step"; we time completed steps and project the remainder so a long
# run is legible: "[3/7] safety[harmful] ... ~12m elapsed, ~9m left".
STEP_TOTAL="${STEP_TOTAL:-0}"; STEP_I=0; RUN_T0="$(date +%s)"
step() {
  STEP_I=$((STEP_I + 1))
  local now elapsed avg left
  now="$(date +%s)"; elapsed=$((now - RUN_T0))
  if [ "$STEP_I" -gt 1 ] && [ "$STEP_TOTAL" -gt 0 ]; then
    avg=$(( elapsed / (STEP_I - 1) )); left=$(( avg * (STEP_TOTAL - STEP_I + 1) ))
    say "[$STEP_I/$STEP_TOTAL] $*   (~$((elapsed/60))m elapsed, ~$((left/60))m left)"
  else
    say "[$STEP_I/${STEP_TOTAL:-?}] $*"
  fi
}

preflight() {
  say "fools-trick benchmark  (stamp $STAMP, size=$SIZE)"
  local w f
  serving "$WORKER_URL" && w="up" || w="DOWN"
  serving "$FOOL_URL" && f="up" || f="DOWN"
  dim "  worker (magus)      $WORKER_URL   [$w]"
  dim "  orchestrator (fool) $FOOL_URL   [$f]"
  [ "$w" = "DOWN" ] && warn "worker down -> worker suites will be skipped"
  [ "$f" = "DOWN" ] && warn "orchestrator down -> fool + e2e suites will be skipped/fail"
  dim "  results -> $BENCH_DIR/  (size=$SIZE)"
  echo
}

finish() {
  echo
  "$PY" "$REPORT" --dir "$BENCH_DIR" --stamp "$STAMP" \
    --md "$BENCH_DIR/report-$STAMP.md" 2>/dev/null || true
  ok "results under $BENCH_DIR (stamp $STAMP)"
}

speed_worker() {
  serving "$WORKER_URL" || { warn "worker not serving; skipping speed-worker"; return; }
  # deepest depth must stay under the per-slot context or the server 400s; leave headroom.
  local deep=$(( WORKER_CTX_PER_SLOT - 4096 ))
  "$PY" "$SPEED" --url "$WORKER_URL" --model "$WORKER_MODEL_ID" --engine llama \
    --depths 512 8192 "$deep" --concurrency 1 2 4 \
    --out "$BENCH_DIR/speed-worker-$STAMP.jsonl" --md "$BENCH_DIR/speed-$STAMP.md" --logfile "$LOG"
}
speed_fool() {
  serving "$FOOL_URL" || { warn "orchestrator not serving; skipping speed-fool"; return; }
  "$PY" "$SPEED" --url "$FOOL_URL" --model "$FOOL_MODEL_ID" --engine vllm \
    --depths 1024 16384 65536 131072 --concurrency 1 --reps 1 --timeout 1800 \
    --out "$BENCH_DIR/speed-fool-$STAMP.jsonl" --md "$BENCH_DIR/speed-$STAMP.md" --logfile "$LOG"
}

eval_worker() {
  serving "$WORKER_URL" || { warn "worker not serving; skipping eval-worker"; return; }
  local jl="$BENCH_DIR/eval-worker-$STAMP.jsonl" md="$BENCH_DIR/eval-$STAMP.md"
  local n_gsm=40 n_code=15
  [ "$QUICK" = "1" ] && { n_gsm=10; n_code=5; }
  "$PY" "$EVAL" gsm8k --url "$WORKER_URL" --model "$WORKER_MODEL_ID" --n "$n_gsm" \
    --out "$jl" --md "$md" --logfile "$LOG"
  # Real coding: worker completes HumanEval+ functions, we execute the tests. The workers'
  # actual job -- this is the eval that measures whether they can write correct code.
  "$PY" "$EVAL" code --url "$WORKER_URL" --model "$WORKER_MODEL_ID" --n "$n_code" --timeout 300 \
    --out "$jl" --md "$md" --logfile "$LOG"
  # Tool-calling (BFCL-style): the worker's other core competency, and the signal that is
  # unmeasured on abliterated models -- does it call the right function, and NOT over-trigger.
  "$PY" "$EVAL" tools --url "$WORKER_URL" --model "$WORKER_MODEL_ID" --timeout 120 \
    --out "$jl" --md "$md" --logfile "$LOG"
  # ruler (long-context) is slower; full run only.
  [ "$QUICK" = "1" ] || "$PY" "$EVAL" ruler --url "$WORKER_URL" --model "$WORKER_MODEL_ID" \
    --lengths 4096 8192 16384 --n 20 --out "$jl" --md "$md" --logfile "$LOG"
}
eval_fool() {
  serving "$FOOL_URL" || { warn "orchestrator not serving; skipping eval-fool"; return; }
  local mdout="$BENCH_DIR/eval-$STAMP.md" jl="$BENCH_DIR/eval-fool-$STAMP.jsonl"
  "$PY" "$EVAL" gsm8k --url "$FOOL_URL" --model "$FOOL_MODEL_ID" --n 20 --timeout 1800 \
    --out "$jl" --md "$mdout" --logfile "$LOG"
  "$PY" "$EVAL" ruler --url "$FOOL_URL" --model "$FOOL_MODEL_ID" --lengths 4096 8192 16384 --n 10 --timeout 1800 \
    --out "$jl" --md "$mdout" --logfile "$LOG"
  # Deep multi-hop needle: the ONLY test that exercises DeepSeek's real 384k window.
  # Chained facts at 32k..370k. Very slow (370k prefill ~10min/item), so few items and
  # opt-in via FOOL_DEEP=1 to keep the default run tractable.
  if [ "${FOOL_DEEP:-0}" = "1" ]; then
    "$PY" "$EVAL" deep --url "$FOOL_URL" --model "$FOOL_MODEL_ID" \
      --lengths 32768 131072 262144 370000 --n 3 --timeout 3600 \
      --out "$jl" --md "$mdout" --logfile "$LOG"
  fi
}

e2e_run() {
  command -v opencode >/dev/null || die "opencode not installed"
  serving "$FOOL_URL" || warn "orchestrator not serving -- e2e will fail unless build agent has a reachable model"
  "$PY" "$E2E" --project "$OPENCODE_PROJECT_DIR" --want-provider magus \
    --out "$BENCH_DIR/e2e-$STAMP.jsonl" --md "$BENCH_DIR/e2e-$STAMP.md" --logfile "$LOG"
}

case "${1:-all}" in
  speed) preflight; case "${2:-both}" in worker) speed_worker;; fool) speed_fool;; both|*) speed_worker; speed_fool;; esac; finish ;;
  eval)  preflight; case "${2:-both}" in worker) eval_worker;; fool) eval_fool;; both|*) eval_worker; eval_fool;; esac; finish ;;
  e2e)   preflight; e2e_run; finish ;;
  all)   preflight; speed_worker; speed_fool; eval_worker; eval_fool; e2e_run; finish ;;
  # quick: fast end-to-end signal -- worker evals (small n) + e2e, skip slow fool/speed suites.
  quick) QUICK=1; preflight; eval_worker; e2e_run; finish ;;
  *) die "usage: bench.sh {speed|eval|e2e|all|quick} [worker|fool|both]" ;;
esac
