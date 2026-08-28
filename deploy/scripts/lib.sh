#!/usr/bin/env bash
# fools-trick shared helpers. Source this after env.sh (which loads config from core/config.py).
# All scripts use these for consistent logging, safety prompts, and checks.

set -euo pipefail

_here() { cd "$(dirname "${BASH_SOURCE[0]}")" && pwd; }
# shellcheck source=./env.sh
[ -n "${WORKER_URL:-}" ] || source "$(_here)/env.sh"

# --- logging ---
_c_reset=$'\033[0m'; _c_red=$'\033[31m'; _c_grn=$'\033[32m'; _c_yel=$'\033[33m'; _c_blu=$'\033[34m'; _c_dim=$'\033[2m'
say()  { printf '%s>>%s %s\n' "$_c_blu" "$_c_reset" "$*"; }
ok()   { printf '%s ok%s %s\n' "$_c_grn" "$_c_reset" "$*"; }
warn() { printf '%swarn%s %s\n' "$_c_yel" "$_c_reset" "$*" >&2; }
err()  { printf '%sERR%s %s\n' "$_c_red" "$_c_reset" "$*" >&2; }
die()  { err "$*"; exit 1; }
dim()  { printf '%s%s%s\n' "$_c_dim" "$*" "$_c_reset"; }

# --- confirmation (respects FT_YES=1 for non-interactive) ---
confirm() {
  local prompt="${1:-Proceed?}"
  if [ "${FT_YES:-0}" = "1" ]; then say "$prompt -> auto-yes (FT_YES=1)"; return 0; fi
  local ans
  read -r -p "$(printf '%s?? %s [y/N] ' "$_c_yel" "$prompt")$_c_reset" ans || true
  case "$ans" in [yY]|[yY][eE][sS]) return 0 ;; *) return 1 ;; esac
}

# --- ssh to fool (LAN, never Tailscale) ---
ssh_fool() { ssh -o ConnectTimeout=8 -o BatchMode=yes "$FOOL_HOST" "$@"; }
fool_reachable() { ping -c1 -W2 "$FOOL_HOST" >/dev/null 2>&1; }

# --- disk: free GiB at a path (local or, with ssh_fool wrapper, remote) ---
free_gib() { df -PB1G "$1" 2>/dev/null | awk 'NR==2{print $4}'; }
check_free_local() {
  local path="$1" need="$2" have
  have="$(free_gib "$path")" || { warn "cannot stat $path"; return 1; }
  if [ "${have:-0}" -lt "$need" ]; then
    warn "low disk at $path: ${have}G free, want >=${need}G"
    return 1
  fi
  ok "disk $path: ${have}G free (>= ${need}G)"
}

# --- port in use locally? ---
port_in_use() { ss -ltn 2>/dev/null | grep -q ":$1 "; }

# --- HTTP health ---
http_ok()   { curl -fsS --max-time "${2:-5}" "$1" >/dev/null 2>&1; }
models_id() { curl -fsS --max-time "${2:-5}" "$1/v1/models" 2>/dev/null | python3 -c 'import sys,json;print(",".join(m["id"] for m in json.load(sys.stdin).get("data",[])))' 2>/dev/null; }

wait_health() {
  # wait_health <base_url> <timeout_s> <label>
  local url="$1" timeout="${2:-120}" label="${3:-server}" deadline
  deadline=$(( $(date +%s) + timeout ))
  say "waiting up to ${timeout}s for ${label} at ${url} ..."
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if http_ok "${url}/v1/models" 5 || http_ok "${url}/health" 5; then
      ok "${label} healthy: $(models_id "$url" || echo "$url")"
      return 0
    fi
    sleep 3
  done
  err "${label} did not become healthy within ${timeout}s"
  return 1
}

# --- git sync verification (used for fool's clone) ---
# Verify a remote-or-local git tree is clean and at the expected SHA.
# Usage: fool_spark_synced   (checks fool's clone against our submodule pin)
fool_spark_synced() {
  local want dir="$FOOL_SPARK_DIR"
  want="$(spark_pinned_sha)" || die "cannot read submodule pin from $SPARK_DIR_LOCAL"
  [ -n "$want" ] || die "empty submodule pin"

  local have dirty
  have="$(ssh_fool "git -C '$dir' rev-parse HEAD 2>/dev/null" || true)"
  [ -n "$have" ] || { err "no git clone on ${FOOL_HOST}:${dir} (run: make bootstrap)"; return 2; }
  dirty="$(ssh_fool "git -C '$dir' status --porcelain 2>/dev/null | wc -l" || echo 0)"

  if [ "${dirty:-0}" -ne 0 ]; then
    err "${FOOL_HOST} spark clone has ${dirty} uncommitted change(s) -- refusing to serve stale/modified code"
    ssh_fool "git -C '$dir' status --short 2>/dev/null | head -20" || true
    return 1
  fi
  if [ "$have" != "$want" ]; then
    err "${FOOL_HOST} spark clone is at $have but this recipe pins $want"
    dim "sync it: make fool-sync   (or on fool: git -C $dir fetch && git -C $dir checkout $want)"
    return 1
  fi
  ok "${FOOL_HOST} spark clone clean and synced at ${want:0:12}"
}

# --- local worker process ---
# Worker runs as a systemd --user transient unit; journald owns its log/lifecycle.
worker_active() { systemctl --user is-active --quiet "$WORKER_UNIT" 2>/dev/null; }
worker_state()  { systemctl --user is-active "$WORKER_UNIT" 2>/dev/null || echo inactive; }

# --- VRAM-fit / CPU-spill detection -------------------------------------------
# A worker config is only VALID if inference stays fully on the two GPUs. With -ngl 999 the
# hybrid arch has two failure modes we must catch instead of waiting on:
#   1. LOAD OOM  : the model/KV/compute buffer does not fit at boot. llama.cpp prints
#      "failed to allocate CUDAx buffer" / "failed to allocate compute buffers" and aborts.
#      wait_health would then spin the full timeout -- we detect the abort in the journal
#      and fail in seconds.
#   2. RUNTIME SPILL : it fits at boot (short prompts) but once a slot fills under real long
#      context the overflow silently falls to CPU -- GPUs go idle while ~32 cores peg and
#      throughput decays. This is worse than an abort because it RUNS, slowly, forever.
# The reliable runtime signal is: worker is actively processing (a slot busy) yet BOTH GPUs
# report near-zero compute utilization. That is only possible if the math moved to the CPU.

# Did the just-attempted worker boot ABORT with a CUDA/compute allocation failure?
# Returns 0 (true) if a load-time OOM abort is present in the recent journal. Scoped to the last
# 90s because callers only ask right after a start attempt; the OOM aborts within ~2s of launch,
# so a fresh failure is always recent and we never match an aged-out earlier boot.
worker_load_oom() {
  journalctl --user -u "$WORKER_UNIT" --since "90 seconds ago" --no-pager -o cat 2>/dev/null \
    | grep -qiE "failed to allocate (CUDA[0-9]+ buffer|compute buffers)|ggml_gallocr_reserve"
}

# Whole CPU cores the worker burned over the interval (utime+stime delta / CLK_TCK / seconds).
# High core count with idle GPUs is the signature of a KV/attention CPU spill.
worker_cpu_cores() {
  local pid="$1" interval="${2:-2}" j0 j1 hz
  hz=$(getconf CLK_TCK 2>/dev/null || echo 100)
  j0="$(awk '{print $14+$15}' /proc/$pid/stat 2>/dev/null)" || return 1
  sleep "$interval"
  j1="$(awk '{print $14+$15}' /proc/$pid/stat 2>/dev/null)" || return 1
  awk -v d=$(( j1 - j0 )) -v hz="$hz" -v t="$interval" 'BEGIN{printf "%d", (d/hz)/t}'
}

# Median GPU compute utilization (max across both cards) over many quick samples. Median, not max,
# so a transient blip can't clear a spill verdict -- we want sustained GPU activity as the signal.
gpu_util_median() {
  local samples="${1:-10}" vals=() u
  for _ in $(seq "$samples"); do
    u="$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits 2>/dev/null | sort -rn | head -1)"
    vals+=("${u:-0}")
    sleep 0.3
  done
  printf '%s\n' "${vals[@]}" | sort -n | awk '{a[NR]=$1} END{print a[int((NR+1)/2)]}'
}

# Is the worker currently spilling inference to CPU?
# Precondition: the caller is driving an active long-context probe so a slot is COMPUTING.
# Signature of a spill: the worker process is burning multiple CPU cores (real compute on CPU)
# WHILE the GPUs sit idle (median util low across a sustained window). On a healthy config the
# GPUs carry the decode and median GPU util is clearly nonzero; on a spill they flatline near 0
# and ~20-32 cores peg. We key on both halves and require them to AGREE, sampled over a window so
# a single GPU flicker cannot produce a false negative.
# Returns 0 (true) == SPILLING (invalid config).
worker_cpu_spilling() {
  local pid cores gmed
  pid="$(pgrep -x llama-server | head -1)"
  [ -n "$pid" ] || return 1  # no worker -> a different failure, not a spill
  cores="$(worker_cpu_cores "$pid" 2)"          # cores the worker burns over 2s
  gmed="$(gpu_util_median 10)"                   # median GPU util over ~3s window
  # Spill == worker is clearly computing on CPU (>= 4 cores) AND GPUs are idle (median <= 10%).
  # A healthy long-context decode keeps a GPU busy (median well above 10) even though llama's
  # host threads also spin; the discriminator is the GPU being IDLE while CPU is hot.
  if [ "${cores:-0}" -ge 4 ] && [ "${gmed:-100}" -le 10 ]; then
    return 0
  fi
  return 1
}

# The commit fool must run: the SHA this repo's spark submodule pins, resolved at runtime so it
# can never drift from what we track. Rig tooling (DeepSeek/spark deploy on the orchestrator).
spark_pinned_sha() { git -C "${SPARK_DIR_LOCAL:-$(_here)/../../spark}" rev-parse HEAD 2>/dev/null; }
