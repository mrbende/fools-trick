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

# Quiet the harness internals so the CLI shows only our coherent step lines + result tables,
# not lm-eval/HF/datasets chatter. The full detail still lands in the per-run logfile.
export LMEVAL_LOG_LEVEL="${LMEVAL_LOG_LEVEL:-WARNING}"
export HF_HUB_DISABLE_PROGRESS_BARS=1
export DATASETS_VERBOSITY=error
export TRANSFORMERS_VERBOSITY=error
export TOKENIZERS_PARALLELISM=false

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
MANIFEST="$OPENCODE_PROJECT_DIR/bench/manifest.py"
SHEETS="$OPENCODE_PROJECT_DIR/bench/export_sheets.py"
XLSX="$OPENCODE_PROJECT_DIR/bench/export_xlsx.py"
CAP="$OPENCODE_PROJECT_DIR/bench/capability.py"
WORKER_TOKENIZER="${WORKER_TOKENIZER:-Qwen/Qwen3.8-27B}"
serving() { http_ok "$1/v1/models" || http_ok "$1/health"; }

# Gate worker suites on the served worker being fully GPU-resident under load. A config that fits
# at boot can still spill to CPU once a slot fills at long context (GPUs idle, ~32 cores pegged,
# throughput decaying) -- those numbers are garbage. We probe ONCE (fill a slot, sample GPU
# residency) and cache the verdict; if it spills, worker suites are skipped with a logged reason
# rather than run CPU-bound. Set BENCH_SKIP_FIT=1 to bypass (e.g. when you know it fits).
WORKER_FIT="unknown"
worker_fit_ok() {
  [ "${BENCH_SKIP_FIT:-0}" = "1" ] && return 0
  [ "$WORKER_FIT" = "ok" ] && return 0
  [ "$WORKER_FIT" = "spill" ] && return 1
  serving "$WORKER_URL" || { WORKER_FIT="spill"; return 1; }
  say "checking worker VRAM residency under long-context load (one-time)"
  local probe_ctx=$(( WORKER_CTX_PER_SLOT - 2048 ))
  ( "$PY" "$SPEED" --url "$WORKER_URL" --model "$WORKER_MODEL_ID" --engine llama \
      --depths "$probe_ctx" --concurrency 4 --reps 1 --timeout 900 \
      --out "$BENCH_DIR/fitprobe-$STAMP.jsonl" >/dev/null 2>&1 ) &
  local pp=$!; sleep 20
  if worker_cpu_spilling; then
    # A spilled worker keeps burning CPU after the client cancels; kill the probe AND down the
    # worker so subsequent (fool-only) suites are not starved by a wedged CPU-grinding slot.
    kill "$pp" 2>/dev/null || true
    "$HERE/down.sh" worker >/dev/null 2>&1 || true
    err "worker SPILLS to CPU at ctx=$probe_ctx x $WORKER_PARALLEL (GPU idle under load) -- worker suites skipped, worker stopped"
    dim "  reduce WORKER_CTX_PER_SLOT so 4 full slots fit in VRAM, or use a smaller quant, then rerun"
    printf '{"test":"_worker_fit","summary":true,"valid":false,"reason":"cpu-spill at ctx=%s x %s"}\n' \
      "$probe_ctx" "$WORKER_PARALLEL" >> "$BENCH_DIR/fit-$STAMP.jsonl"
    WORKER_FIT="spill"; return 1
  fi
  wait "$pp" 2>/dev/null || true
  ok "worker fully GPU-resident under load"
  WORKER_FIT="ok"; return 0
}

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
  # Capture the run manifest up front (git state + live node shapes) so the run is reproducible.
  "$PY" "$MANIFEST" --stamp "$STAMP" --size "$SIZE" --worker-url "$WORKER_URL" \
    --fool-url "$FOOL_URL" --out "$BENCH_DIR" >/dev/null 2>&1 || true
  echo
}

finish() {
  echo
  "$PY" "$REPORT" --dir "$BENCH_DIR" --stamp "$STAMP" \
    --md "$BENCH_DIR/report-$STAMP.md" 2>/dev/null || true
  # Always write an .xlsx scorecard to disk -- the on-disk report, no cloud needed.
  "$PY" "$XLSX" --dir "$BENCH_DIR" --stamp "$STAMP" 2>/dev/null || warn "xlsx export failed"
  ok "results under $BENCH_DIR (stamp $STAMP)"
  # Google Sheets is the optional cloud upgrade: needs GOOGLE_APPLICATION_CREDENTIALS (the target
  # account's service-account JSON). Auto-runs when creds are present; shared to BENCH_SHARE_WITH.
  if [ -n "${GOOGLE_APPLICATION_CREDENTIALS:-}" ] && [ "${BENCH_SHEETS:-1}" = "1" ]; then
    say "exporting scorecard to Google Sheets (shared to $BENCH_SHARE_WITH)"
    "$PY" "$SHEETS" --dir "$BENCH_DIR" --stamp "$STAMP" \
      ${BENCH_SHARE_WITH:+--share-with "$BENCH_SHARE_WITH"} || warn "sheets export failed"
  fi
}

speed_worker() {
  serving "$WORKER_URL" || { warn "worker not serving; skipping speed-worker"; return; }
  step "speed[worker] TTFT/prefill/decode/concurrency"
  # deepest depth must stay under the per-slot context or the server 400s; leave headroom.
  # concurrency tops out at WORKER_PARALLEL (the real max-concurrency operating point).
  local deep=$(( WORKER_CTX_PER_SLOT - 4096 ))
  "$PY" "$SPEED" --url "$WORKER_URL" --model "$WORKER_MODEL_ID" --engine llama \
    --depths 512 8192 "$deep" --concurrency 1 2 "$WORKER_PARALLEL" \
    --out "$BENCH_DIR/speed-worker-$STAMP.jsonl" --md "$BENCH_DIR/speed-$STAMP.md" --logfile "$LOG"
}
speed_fool() {
  serving "$FOOL_URL" || { warn "orchestrator not serving; skipping speed-fool"; return; }
  step "speed[fool] TTFT/prefill/decode/cache"
  "$PY" "$SPEED" --url "$FOOL_URL" --model "$FOOL_MODEL_ID" --engine vllm \
    --depths 1024 16384 65536 131072 --concurrency 1 --reps 1 --timeout 1800 \
    --out "$BENCH_DIR/speed-fool-$STAMP.jsonl" --md "$BENCH_DIR/speed-$STAMP.md" --logfile "$LOG"
}

# SIZE -> per-eval sample count. One knob controls the whole run's cost.
size_n() { case "$SIZE" in smoke) echo 5;; small) echo 25;; large) echo 200;; max) echo 0;; *) echo 25;; esac; }
# Wrappers for the two URL conventions: capability.py/safety.py want the URL WITH /v1;
# eval.py (code/tools/deep) adds /v1 itself, so it wants the bare base.
CAP="$OPENCODE_PROJECT_DIR/bench/capability.py"
SAFETY="$OPENCODE_PROJECT_DIR/bench/safety.py"
LONGCTX="$OPENCODE_PROJECT_DIR/bench/longctx.py"
WORKER_TOK="${WORKER_TOKENIZER:-Qwen/Qwen3.8-27B}"
FOOL_TOK="${FOOL_TOKENIZER:-deepseek-ai/DeepSeek-V3}"

# Capability via lm-eval: reasoning + instruction-following (both nodes, generative) and
# multiple-choice loglikelihood (orchestrator only). Node-routed inside capability.py.
capability_worker() {
  serving "$WORKER_URL" || { warn "worker down; skip capability-worker"; return; }
  worker_fit_ok || { warn "worker spills to CPU; skip capability-worker (would run CPU-bound)"; return; }
  step "capability[worker] gen (gsm8k, ifeval)"
  "$PY" "$CAP" --node worker --url "$WORKER_URL/v1" --model "$WORKER_MODEL_ID" \
    --tokenizer "$WORKER_TOK" --tier gen --size "$SIZE" --out "$BENCH_DIR/cap-worker-$STAMP"
}
capability_fool() {
  serving "$FOOL_URL" || { warn "orchestrator down; skip capability-fool"; return; }
  step "capability[fool] gen + MC (mmlu/arc/hellaswag/winogrande loglikelihood)"
  local tier="gen"; [ "$SIZE" = "max" ] || [ "$SIZE" = "large" ] && tier="full"
  "$PY" "$CAP" --node fool --url "$FOOL_URL/v1" --model "$FOOL_MODEL_ID" \
    --tokenizer "$FOOL_TOK" --tier "$tier" --size "$SIZE" --out "$BENCH_DIR/cap-fool-$STAMP"
}

# Code + tool-calling on the worker (our own robust harness; lm-eval mis-extracts code on this
# reasoning model, BFCL is py3.14-incompatible -- see docs. These own the code+tools axes).
code_tools_worker() {
  serving "$WORKER_URL" || { warn "worker down; skip code/tools"; return; }
  worker_fit_ok || { warn "worker spills to CPU; skip code/tools (would run CPU-bound)"; return; }
  local jl="$BENCH_DIR/eval-worker-$STAMP.jsonl" md="$BENCH_DIR/eval-$STAMP.md" n; n="$(size_n)"
  step "code (HumanEval+, executed) on worker"
  "$PY" "$EVAL" code --url "$WORKER_URL" --model "$WORKER_MODEL_ID" --n "${n:-15}" --timeout 300 \
    --out "$jl" --md "$md" --logfile "$LOG"
  step "tools (BFCL-style AST) on worker"
  "$PY" "$EVAL" tools --url "$WORKER_URL" --model "$WORKER_MODEL_ID" --timeout 120 \
    --out "$jl" --md "$md" --logfile "$LOG"
}

# Safety / refusal (the abliteration measurement). Target = worker; judge = orchestrator.
safety_worker() {
  serving "$WORKER_URL" || { warn "worker down; skip safety"; return; }
  worker_fit_ok || { warn "worker spills to CPU; skip safety (would run CPU-bound)"; return; }
  serving "$FOOL_URL" || { warn "orchestrator (judge) down; skip safety"; return; }
  local jl="$BENCH_DIR/safety-$STAMP.jsonl" n; n="$(size_n)"; [ "$n" = "0" ] && n=100
  for ds in advbench jbb_harmful xstest; do
    step "safety[$ds] target=worker judge=fool"
    "$PY" "$SAFETY" --dataset "$ds" --url "$WORKER_URL" --model "$WORKER_MODEL_ID" \
      --judge-url "$FOOL_URL" --judge-model "$FOOL_MODEL_ID" --n "$n" \
      --out "$jl" --logfile "$LOG"
  done
}

# Long-context passive retrieval (deep needle) + agentic delegation-at-depth (longctx).
longctx_fool() {
  serving "$FOOL_URL" || { warn "orchestrator down; skip long-context"; return; }
  local depths="8192 32768"; [ "$SIZE" = "large" ] && depths="32768 131072"
  [ "$SIZE" = "max" ] && depths="32768 131072 262144 370000"
  step "deep needle (passive retrieval) at $depths"
  "$PY" "$EVAL" deep --url "$FOOL_URL" --model "$FOOL_MODEL_ID" --lengths $depths --n 1 \
    --timeout 3600 --out "$BENCH_DIR/eval-fool-$STAMP.jsonl" --md "$BENCH_DIR/eval-$STAMP.md" --logfile "$LOG"
  command -v opencode >/dev/null || { warn "opencode missing; skip longctx agentic"; return; }
  local ld="32000"; [ "$SIZE" = "large" ] && ld="32000 100000"; [ "$SIZE" = "max" ] && ld="32000 100000 200000"
  step "longctx agentic (delegation at depth) at $ld"
  "$PY" "$LONGCTX" --project "$OPENCODE_PROJECT_DIR" --depths $ld --n 1 --timeout 2400 \
    --out "$BENCH_DIR/longctx-$STAMP.jsonl" --logfile "$LOG"
}

e2e_run() {
  command -v opencode >/dev/null || { warn "opencode missing; skip e2e"; return; }
  serving "$FOOL_URL" || { warn "orchestrator down; skip e2e"; return; }
  step "e2e delegation (opencode fan-out, DB-verified)"
  "$PY" "$E2E" --project "$OPENCODE_PROJECT_DIR" --want-provider magus \
    --out "$BENCH_DIR/e2e-$STAMP.jsonl" --md "$BENCH_DIR/e2e-$STAMP.md" --logfile "$LOG"
}

# Memory A/B: does sliding-window + recall beat opencode's compaction on a long coding session?
# Runs both arms (on = memory plugin, off = MEMORY_ENABLED=0 compaction baseline), then diffs.
# LLM-judged, closed-book-controlled, includes an agentic-recall probe (subagent findings must
# survive the slide). bury-turns scales with SIZE so smoke is fast.
MEMORY="$OPENCODE_PROJECT_DIR/bench/memory.py"
memory_ab() {
  command -v opencode >/dev/null || { warn "opencode missing; skip memory"; return; }
  serving "$FOOL_URL" || { warn "orchestrator down; skip memory (judge+session need it)"; return; }
  local bury; case "$SIZE" in smoke) bury=12;; small) bury=30;; large) bury=60;; max) bury=100;; *) bury=30;; esac
  export MEMORY_BENCH_DIR="$BENCH_DIR/membench-$STAMP"
  for arm in on off; do
    step "memory[$arm] long coding session (bury=$bury) -- sliding-recall vs compaction"
    "$PY" "$MEMORY" run --project "$OPENCODE_PROJECT_DIR" --arm "$arm" --bury-turns "$bury" \
      --judge-url "$FOOL_URL" --judge-model "$FOOL_MODEL_ID" --logfile "$LOG"
  done
  step "memory A/B diff (does memory beat compaction)"
  "$PY" "$MEMORY" diff --a on --b off | tee -a "$LOG"
}

case "${1:-all}" in
  capability) STEP_TOTAL=2; preflight; capability_worker; capability_fool; finish ;;
  code)       STEP_TOTAL=2; preflight; code_tools_worker; finish ;;
  safety)     STEP_TOTAL=3; preflight; safety_worker; finish ;;
  longctx)    STEP_TOTAL=2; preflight; longctx_fool; finish ;;
  speed)      STEP_TOTAL=2; preflight; speed_worker; speed_fool; finish ;;
  e2e)        STEP_TOTAL=1; preflight; e2e_run; finish ;;
  memory)     STEP_TOTAL=3; preflight; memory_ab; finish ;;
  # quick: fast representative signal across the whole system -- capability + code/tools + e2e.
  quick)      SIZE=smoke; STEP_TOTAL=4; preflight; capability_worker; code_tools_worker; e2e_run; finish ;;
  # all: the full instrument. STEP_TOTAL counts each step() call across the run for ETA. Each step
  # is guarded with `|| warn` so a single step's failure (infra hiccup, non-zero exit) can never
  # abort the run before finish() writes the report/xlsx -- a benchmark must always produce its
  # scorecard from whatever completed.
  all)        STEP_TOTAL=15; preflight
              speed_worker      || warn "speed[worker] step errored"
              speed_fool        || warn "speed[fool] step errored"
              capability_worker || warn "capability[worker] step errored"
              capability_fool   || warn "capability[fool] step errored"
              code_tools_worker || warn "code/tools step errored"
              safety_worker     || warn "safety step errored"
              e2e_run           || warn "e2e step errored"
              longctx_fool      || warn "longctx step errored"
              memory_ab         || warn "memory A/B step errored"
              finish ;;
  *) die "usage: bench.sh {all|quick|capability|code|safety|longctx|speed|e2e|memory}   (SIZE=smoke|small|large|max)" ;;
esac
