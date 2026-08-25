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
serving() { http_ok "$1/v1/models" || http_ok "$1/health"; }

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
  "$PY" "$EVAL" gsm8k --url "$WORKER_URL" --model "$WORKER_MODEL_ID" --n 40 \
    --out "$BENCH_DIR/eval-worker-$STAMP.jsonl" --md "$BENCH_DIR/eval-$STAMP.md" --logfile "$LOG"
  "$PY" "$EVAL" ruler --url "$WORKER_URL" --model "$WORKER_MODEL_ID" --lengths 4096 8192 16384 --n 20 \
    --out "$BENCH_DIR/eval-worker-$STAMP.jsonl" --md "$BENCH_DIR/eval-$STAMP.md" --logfile "$LOG"
  # Real coding: worker completes HumanEval+ functions, we execute the tests. The workers'
  # actual job -- this is the eval that measures whether they can write correct code.
  "$PY" "$EVAL" code --url "$WORKER_URL" --model "$WORKER_MODEL_ID" --n 15 --timeout 300 \
    --out "$BENCH_DIR/eval-worker-$STAMP.jsonl" --md "$BENCH_DIR/eval-$STAMP.md" --logfile "$LOG"
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
  speed) case "${2:-both}" in worker) speed_worker;; fool) speed_fool;; both|*) speed_worker; speed_fool;; esac ;;
  eval)  case "${2:-both}" in worker) eval_worker;; fool) eval_fool;; both|*) eval_worker; eval_fool;; esac ;;
  e2e)   e2e_run ;;
  all)   speed_worker; speed_fool; eval_worker; eval_fool; e2e_run ;;
  *) die "usage: bench.sh {speed|eval|e2e|all} [worker|fool|both]" ;;
esac

echo; ok "results under $BENCH_DIR (stamp $STAMP)"
