#!/usr/bin/env bash
# Status: concise view of both servers, weights, and opencode wiring. Read-only.

set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/env.sh"
source "$HERE/lib.sh"

say "orchestrator (fool / DeepSeek-V4-Flash)"
if fool_reachable; then
  if http_ok "$ORCHESTRATOR_URL/v1/models" || http_ok "$ORCHESTRATOR_URL/health"; then
    ok "up @ $ORCHESTRATOR_URL  models=[$(models_id "$ORCHESTRATOR_URL" || echo '?')]"
  else
    warn "reachable but not serving on :$FOOL_PORT"
  fi
  if ssh_fool "[ -d '$FOOL_SPARK_DIR/.git' ]" 2>/dev/null; then
    have="$(ssh_fool "git -C '$FOOL_SPARK_DIR' rev-parse HEAD" 2>/dev/null || echo '?')"
    dirty="$(ssh_fool "git -C '$FOOL_SPARK_DIR' status --porcelain 2>/dev/null | wc -l" || echo '?')"
    want="$(spark_pinned_sha)"
    if [ "$have" = "$want" ] && [ "${dirty:-1}" -eq 0 ]; then
      ok "spark clone synced @ ${want:0:12} (clean)"
    else
      warn "spark clone DRIFT: fool @ ${have:0:12}, pin ${want:0:12}, ${dirty} dirty -- run: make fool-sync"
    fi
  else
    warn "no spark clone on fool (make bootstrap)"
  fi
else
  warn "not reachable"
fi

echo
say "worker (magus / Qwen3.8-27B-OBLITERATED)"
if http_ok "$WORKER_URL/v1/models"; then
  ok "up @ $WORKER_URL  models=[$(models_id "$WORKER_URL")]  unit=$(worker_state)"
elif worker_active; then
  warn "unit $WORKER_UNIT active but not answering on :$WORKER_PORT (still loading?)"
else
  warn "not running (unit: $(worker_state))"
fi

echo
say "weights"
"$HERE/weights.sh"

echo
say "opencode"
if opencode debug config >/dev/null 2>&1; then
  ok "config parses; default_agent=$(opencode debug config 2>/dev/null | python3 -c 'import sys,json;print(json.load(sys.stdin).get("default_agent"))' 2>/dev/null)"
else
  warn "config does not parse"
fi
