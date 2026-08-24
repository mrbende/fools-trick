#!/usr/bin/env bash
# Stop servers. Targets: worker (magus, local), fool (over ssh), or both.
#   ./scripts/down.sh worker | fool | all

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/config.sh"
source "$HERE/lib.sh"

down_worker() {
  local pid; pid="$(worker_pid || true)"
  if [ -z "$pid" ] && ! port_in_use "$WORKER_PORT"; then ok "worker already stopped"; return 0; fi
  say "stopping worker (llama-server pid ${pid:-?}) on :$WORKER_PORT"
  pkill -x llama-server 2>/dev/null || true
  for _ in $(seq 1 20); do worker_pid >/dev/null 2>&1 || { ok "worker stopped"; return 0; }; sleep 1; done
  warn "worker still alive after 20s, sending SIGKILL"
  pkill -9 -x llama-server 2>/dev/null || true
  ok "worker killed"
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
