#!/usr/bin/env bash
# fools-trick shared helpers. Source this after config.sh.
# All scripts use these for consistent logging, safety prompts, and checks.

set -euo pipefail

_here() { cd "$(dirname "${BASH_SOURCE[0]}")" && pwd; }
# shellcheck source=./config.sh
[ -n "${FOOL_HOST:-}" ] || source "$(_here)/config.sh"

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
