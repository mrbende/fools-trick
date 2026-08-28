#!/usr/bin/env bash
# Test runner. Three layers:
#   1. unit    -- python bench parsers + shell lib helpers (no network, always runnable)
#   2. config  -- opencode config parses, all agents resolve to the right nodes
#   3. live    -- real subagent round-trip through opencode (only if servers are up)
#
#   ./scripts/test.sh          # all layers
#   ./scripts/test.sh unit     # just the fast offline units

set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
source "$HERE/env.sh"
source "$HERE/lib.sh"

fail=0

unit_tests() {
  # opencode.json is a generated artifact (from config.yaml); regenerate so the parity checks
  # in test_lib.sh test the CURRENT config, not a stale file.
  ( cd "$ROOT" && python3 -m core.config --opencode > opencode.json 2>/dev/null ) \
    || warn "could not regenerate opencode.json before tests"
  say "unit: python bench parsers"
  local py="$ROOT/.bench-venv/bin/python"; [ -x "$py" ] || py="python3"
  if "$py" -m unittest discover -s "$ROOT/tests" -p "test_*.py" 2>&1 | tail -15; then
    ok "python unit tests passed"
  else
    err "python unit tests FAILED"; fail=1
  fi
  echo
  say "unit: shell lib helpers"
  if bash "$ROOT/tests/test_lib.sh"; then ok "shell unit tests passed"; else err "shell unit tests FAILED"; fail=1; fi
  echo
  say "unit: core primitives (Event Log, context policy, gates, tools -- Python, no harness)"
  if ( cd "$ROOT" && "$py" -m unittest discover -s tests/core -p "test_*.py" 2>&1 | tail -6 ); then
    ok "core primitive tests passed"; else err "core primitive tests FAILED"; fail=1; fi
  echo
  say "adapter: opencode shim + bridge boundary"
  if command -v node >/dev/null 2>&1; then
    if ( cd "$ROOT" && node tests/adapters/test_adapter.mjs ); then
      ok "adapter tests passed"; else err "adapter tests FAILED"; fail=1; fi
  else
    dim "node not found; skipping adapter tests"
  fi
}

config_tests() {
  echo; say "config: opencode wiring"
  local cfg
  cfg="$(opencode debug config 2>/dev/null)"
  if [ -z "$cfg" ] || ! printf '%s' "$cfg" | python3 -c "import sys,json;json.load(sys.stdin)" 2>/dev/null; then
    err "opencode config does not parse"; fail=1; return
  fi
  ok "opencode config parses"
  # Note: pass the checker via -c so the piped config stays on stdin (a heredoc would
  # itself become stdin and shadow the pipe).
  local checker='
import sys, json
d = json.load(sys.stdin); ag = d.get("agent", {})
prim = {"build", "plan"}; sub = {"explore", "general", "reviewer"}  # 3-agent roster (data-driven)
bad = 0
for n in prim | sub:
    a = ag.get(n)
    if not a: print(f"MISSING agent {n}"); bad = 1; continue
    m = a.get("model", "")
    if n in sub and not m.startswith("magus/"): print(f"worker {n} not on magus: {m}"); bad = 1
    if n in prim and not m.startswith("fool-ds4/"): print(f"primary {n} not on fool: {m}"); bad = 1
    # leaf workers must not delegate (only the orchestrator fans out)
    if n in sub and a.get("permission", {}).get("task") != "deny":
        print(f"worker {n} can delegate (task != deny)"); bad = 1
if not d.get("small_model", "").startswith("magus/"): print("small_model not on magus"); bad = 1
sys.exit(bad)
'
  if printf '%s' "$cfg" | python3 -c "$checker"; then
    ok "agents resolve (primaries->fool, workers->magus, small_model->magus)"
  else err "agent routing wrong"; fail=1; fi
  for f in prompts/orchestrator.md AGENTS.md .opencode/agents/explore.md; do
    [ -f "$ROOT/$f" ] && ok "present: $f" || { err "missing: $f"; fail=1; }
  done
}

live_tests() {
  echo; say "live: subagent round-trip (needs worker up)"
  if ! http_ok "$WORKER_URL/v1/models"; then
    dim "worker not serving; skipping live test (make worker-up to enable)"
    return
  fi
  # direct completion on the worker proves the endpoint + reasoning split
  local out
  out="$(curl -fsS --max-time 60 "$WORKER_URL/v1/chat/completions" -H 'Content-Type: application/json' \
    -d "{\"model\":\"$WORKER_MODEL_ID\",\"messages\":[{\"role\":\"user\",\"content\":\"reply exactly: pong\"}],\"max_tokens\":100,\"temperature\":0}" 2>/dev/null \
    | python3 -c 'import sys,json;m=json.load(sys.stdin)["choices"][0]["message"];print((m.get("content") or m.get("reasoning_content") or "").strip())' 2>/dev/null)"
  printf '%s' "$out" | grep -qi pong && ok "worker completion works (said: ${out:0:30})" || { err "worker completion failed"; fail=1; }
}

case "${1:-all}" in
  unit)   unit_tests ;;
  config) config_tests ;;
  live)   live_tests ;;
  all)    unit_tests; config_tests; live_tests ;;
  *) die "usage: test.sh {unit|config|live|all}" ;;
esac

echo
[ "$fail" -ne 0 ] && { err "TESTS FAILED"; exit 1; }
ok "all tests passed"
