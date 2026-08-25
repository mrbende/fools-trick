#!/usr/bin/env bash
# Stop servers. Targets: worker (magus, local), fool (over ssh), or both.
#   ./scripts/down.sh worker | fool | all

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/config.sh"
source "$HERE/lib.sh"

down_worker() {
  if ! worker_active && ! port_in_use "$WORKER_PORT"; then ok "worker already stopped"; return 0; fi
  say "stopping worker unit ($WORKER_UNIT)"
  systemctl --user stop "$WORKER_UNIT" 2>/dev/null || true
  systemctl --user reset-failed "$WORKER_UNIT" 2>/dev/null || true
  # --collect usually cleans up; belt-and-suspenders for a stray process on the port.
  pkill -x llama-server 2>/dev/null || true
  ok "worker stopped"
}

down_fool() {
  fool_reachable || { warn "$FOOL_HOST not reachable; nothing to stop"; return 0; }
  say "stopping orchestrator container on $FOOL_HOST"
  ssh_fool "cd '$FOOL_SPARK_DIR' && ./start.sh stop" || warn "stop returned nonzero (may already be down)"
  ok "orchestrator stop requested on $FOOL_HOST"
}

case "${1:-all}" in
  worker) down_worker ;;
  fool)   down_fool ;;
  all)    down_worker; down_fool ;;
  *) die "usage: down.sh {worker|fool|all}" ;;
esac
