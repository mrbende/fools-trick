#!/usr/bin/env bash
# Stop servers. Targets: worker (magus, local), fool (over ssh), or both.
#   ./scripts/down.sh worker | fool | all

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/config.sh"
source "$HERE/lib.sh"

down_redis() {
  docker ps -a --format '{{.Names}}' 2>/dev/null | grep -qx "$REDIS_CONTAINER" || { ok "redis already stopped"; return 0; }
  say "stopping redis ($REDIS_CONTAINER) -- short-term memory is ephemeral; SQLite store persists"
  docker rm -f "$REDIS_CONTAINER" >/dev/null 2>&1 || warn "docker rm returned nonzero"
  ok "redis stopped"
}

down_worker() {
  if ! worker_active && ! port_in_use "$WORKER_PORT"; then ok "worker already stopped"; return 0; fi
  say "stopping worker unit ($WORKER_UNIT)"
  systemctl --user stop "$WORKER_UNIT" 2>/dev/null || true
  systemctl --user reset-failed "$WORKER_UNIT" 2>/dev/null || true
  # Scoped fallback for a stray process: kill only what holds OUR port, never a
  # sibling llama-server on another port (a blanket pkill -x llama-server would).
  if port_in_use "$WORKER_PORT"; then
    fuser -k "${WORKER_PORT}/tcp" 2>/dev/null || true
  fi
  ok "worker stopped"
}

down_fool() {
  fool_reachable || { warn "$FOOL_HOST not reachable; nothing to stop"; return 0; }
  say "stopping orchestrator container on $FOOL_HOST"
  ssh_fool "cd '$FOOL_SPARK_DIR' && ./start.sh stop" || warn "stop returned nonzero (may already be down)"
  ok "orchestrator stop requested on $FOOL_HOST"
}

case "${1:-all}" in
  redis)  down_redis ;;
  worker) down_worker ;;
  fool)   down_fool ;;
  all)    down_worker; down_fool; down_redis ;;
  *) die "usage: down.sh {redis|worker|fool|all}" ;;
esac
