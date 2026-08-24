#!/usr/bin/env bash
# Tests: config integrity + agent resolution + (when live) a subagent round-trip.
# The config/wiring checks run without any server; the round-trip needs both up.

set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/config.sh"
source "$HERE/lib.sh"

fail=0

say "config + wiring"
opencode debug config >/dev/null 2>&1 && ok "opencode config parses" || { err "config parse failed"; fail=1; }

# every agent must resolve to a model, and workers must point at magus
agents_json="$(opencode debug config 2>/dev/null)"
if [ -n "$agents_json" ]; then
  printf '%s' "$agents_json" | python3 - <<'PY' || fail=1
import sys,json
d=json.load(sys.stdin); ag=d.get("agent",{})
need_primary={"build","plan"}; need_sub={"explore","scout","general","implementer","reviewer"}
bad=0
for n in need_primary|need_sub:
    a=ag.get(n)
    if not a: print(f"MISSING agent {n}"); bad=1; continue
    m=a.get("model","")
    if n in need_sub and not m.startswith("magus/"): print(f"worker {n} not on magus: {m}"); bad=1
    if n in need_primary and not m.startswith("fool-ds4/"): print(f"primary {n} not on fool: {m}"); bad=1
sm=d.get("small_model","")
if not sm.startswith("magus/"): print(f"small_model not on magus: {sm}"); bad=1
sys.exit(bad)
PY
  [ $? -eq 0 ] && ok "agents resolve (primaries->fool, workers->magus, small_model->magus)"
else
  err "could not read config"; fail=1
fi

# prompt files referenced exist
[ -f "$OPENCODE_PROJECT_DIR/prompts/orchestrator.md" ] && ok "orchestrator prompt present" || { err "orchestrator prompt missing"; fail=1; }
[ -f "$OPENCODE_PROJECT_DIR/AGENTS.md" ] && ok "AGENTS.md present" || { err "AGENTS.md missing"; fail=1; }

echo
say "live subagent round-trip (needs worker up)"
if http_ok "$WORKER_URL/v1/models"; then
  if out="$(cd "$OPENCODE_PROJECT_DIR" && timeout "${TEST_TIMEOUT:-300}" opencode run "@explore what files are in the repo root? answer in one line." 2>/dev/null)"; then
    [ -n "$out" ] && ok "explore worker answered via opencode" || warn "explore returned empty"
  else
    warn "subagent round-trip did not complete"
  fi
else
  dim "worker not up; skipping round-trip (make worker-up to enable)"
fi

echo
[ "$fail" -ne 0 ] && { err "tests FAILED"; exit 1; }
ok "tests passed"
