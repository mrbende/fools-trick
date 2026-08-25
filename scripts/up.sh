#!/usr/bin/env bash
# Start servers. Targets: worker (magus, local), fool (DGX Spark, over ssh), or both.
#   ./scripts/up.sh worker | fool | all
# Refuses to serve fool from a dirty/diverged git tree. Confirms before killing anything.

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/config.sh"
source "$HERE/lib.sh"

up_worker() {
  say "starting worker on magus"
  if http_ok "$WORKER_URL/v1/models"; then
    ok "worker already up and healthy on :$WORKER_PORT"; return 0
  fi
  if port_in_use "$WORKER_PORT" || worker_active; then
    warn ":$WORKER_PORT busy or fools-worker unit present but not healthy"
    confirm "restart the worker?" || die "aborted"
    "$HERE/down.sh" worker
  fi
  # Ensure weights are present (check-first; provisions from NAS or downloads if missing).
  if [ ! -f "$LOCAL_WORKER_DIR/$WORKER_FILE" ]; then
    say "worker weights not local; provisioning"
    "$HERE/weights.sh" worker
  fi
  # Launch as a transient systemd user service: journald handles logging + lifecycle,
  # args come from serve.sh (config-driven), no unit file to maintain.
  systemd-run --user --unit "$WORKER_UNIT" --description "fools-trick worker (Qwen)" \
    --collect "$HERE/../worker/serve.sh" \
    || die "systemd-run failed to start $WORKER_UNIT"
  wait_health "$WORKER_URL" 300 "worker"
}

up_fool() {
  say "starting orchestrator on $FOOL_HOST"
  fool_reachable || die "$FOOL_HOST not reachable on the LAN"

  # Never serve stale/modified code: fool's clone must be clean and at our pinned SHA.
  fool_spark_synced || die "fool spark clone not synced (make fool-sync) -- refusing to start"

  # Already healthy?
  if http_ok "$FOOL_URL/v1/models" || http_ok "$FOOL_URL/health"; then
    ok "orchestrator already up and healthy at $FOOL_URL"; return 0
  fi

  # Something on the port but unhealthy? confirm before the recipe recreates the container.
  local busy; busy="$(ssh_fool "ss -ltn 2>/dev/null | grep -c ':$FOOL_PORT '" || echo 0)"
  if [ "${busy:-0}" -ne 0 ]; then
    warn "$FOOL_HOST has something on :$FOOL_PORT but it is not healthy"
    confirm "let the spark recipe stop/recreate the container on $FOOL_HOST?" || die "aborted"
    ssh_fool "cd '$FOOL_SPARK_DIR' && ./start.sh stop" || warn "stop returned nonzero"
  fi

  # Serve from the local coalesced data/tp1. The recipe's entrypoint skips download/coalesce
  # when data/tp1's manifest exists, so this is fast and never touches the NAS. If the weights
  # were never provisioned, warn -- don't trigger a surprise 107 GB download inside serve.
  if ! ssh_fool "[ -f '$FOOL_SPARK_DIR/data/tp1/rank-sliced-tp1-manifest.json' ]" 2>/dev/null; then
    warn "DeepSeek weights not coalesced on $FOOL_HOST yet"
    confirm "run 'make fool-weights' first? (recommended; otherwise serve will download ~107 GB)" \
      && { "$HERE/fool-weights.sh"; } || dim "proceeding; first boot will download+coalesce (slow)"
  fi
  say "launching spark recipe on $FOOL_HOST (ABLATE=$FOOL_ABLATE, effort=$FOOL_EFFORT); serves from local data/tp1"
  ssh_fool "cd '$FOOL_SPARK_DIR' && HF_CACHE='$FOOL_HF_CACHE' ABLATE=$FOOL_ABLATE \
    DEFAULT_CHAT_TEMPLATE_KWARGS_EFFORT='$FOOL_EFFORT' ./start.sh --no-wait" \
    || die "spark start.sh failed on $FOOL_HOST"
  wait_health "$FOOL_URL" "${FOOL_STARTUP_WAIT:-3600}" "orchestrator"
}

case "${1:-all}" in
  worker) up_worker ;;
  fool)   up_fool ;;
  all)    up_worker; up_fool ;;
  *) die "usage: up.sh {worker|fool|all}" ;;
esac
