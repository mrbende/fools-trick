#!/usr/bin/env bash
# Logs, from each platform's native facility -- no custom logfile, no plumbing:
#   worker (magus)      -> journald  (systemd --user unit)
#   orchestrator (fool) -> docker    (compose logs over ssh)
#
#   ./scripts/logs.sh          # both, interleaved, node-prefixed
#   ./scripts/logs.sh worker   # magus only (plain journalctl)
#   ./scripts/logs.sh fool     # fool only (plain docker logs)

set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/env.sh"
source "$HERE/lib.sh"

follow_worker() { journalctl --user -u "$WORKER_UNIT" -n 40 -f --no-hostname; }
follow_fool()   { ssh -o ConnectTimeout=8 "$FOOL_HOST" "cd '$FOOL_SPARK_DIR' && ./start.sh logs"; }

case "${1:-all}" in
  worker) exec journalctl --user -u "$WORKER_UNIT" -n 100 -f ;;
  fool)   exec ssh -o ConnectTimeout=8 "$FOOL_HOST" "cd '$FOOL_SPARK_DIR' && ./start.sh logs" ;;
  all)
    say "unified logs (Ctrl-C to stop).  ${_c_dim}[magus]=worker  [fool]=orchestrator${_c_reset}"
    trap 'kill 0' EXIT INT TERM
    follow_worker 2>&1 | sed "s/^/$(printf '\033[36m[magus]\033[0m ')/" &
    follow_fool   2>&1 | sed "s/^/$(printf '\033[35m[fool] \033[0m')/" &
    wait
    ;;
  *) die "usage: logs.sh {all|worker|fool}" ;;
esac
