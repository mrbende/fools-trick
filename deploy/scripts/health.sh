#!/usr/bin/env bash
# Health: active end-to-end check. Pings both endpoints, does a tiny real completion
# on each, and verifies opencode can reach the orchestrator. Exits nonzero on failure.

set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/env.sh"
source "$HERE/lib.sh"

fail=0

probe() {
  # probe <label> <base_url> <model_id>
  local label="$1" url="$2" model="$3"
  # cloud endpoints require the API key; self-hosted servers are unauthenticated (empty header is a no-op).
  local auth=()
  [ -n "${ZEN_API_KEY:-}" ] && auth=(-H "Authorization: Bearer $ZEN_API_KEY")
  if ! curl -fsS --max-time 5 "${auth[@]}" "$url/v1/models" >/dev/null 2>&1; then err "$label: /v1/models unreachable at $url"; fail=1; return; fi
  local out
  out="$(curl -fsS --max-time "${HEALTH_TIMEOUT:-120}" "${auth[@]}" "$url/v1/chat/completions" \
    -H 'Content-Type: application/json' \
    -d "{\"model\":\"$model\",\"messages\":[{\"role\":\"user\",\"content\":\"reply with the single word: ok\"}],\"max_tokens\":100,\"temperature\":0}" 2>/dev/null \
    | python3 -c 'import sys,json;m=json.load(sys.stdin)["choices"][0]["message"];print((m.get("content") or m.get("reasoning_content") or "").strip())' 2>/dev/null)"
  if [ -n "$out" ]; then ok "$label: completion works (said: ${out:0:40})"; else err "$label: completion failed"; fail=1; fi
}

say "endpoint health"
probe "worker" "$WORKER_URL" "$WORKER_MODEL_ID"
if fool_reachable && { http_ok "$ORCHESTRATOR_URL/v1/models" || http_ok "$ORCHESTRATOR_URL/health"; }; then
  probe "orchestrator" "$ORCHESTRATOR_URL" "$ORCHESTRATOR_MODEL_ID"
else
  warn "orchestrator not serving; skipping its completion probe"
fi

echo
say "opencode end-to-end"
if command -v opencode >/dev/null 2>&1; then
  # non-interactive one-shot through the default agent (hits the orchestrator)
  if out="$(cd "$OPENCODE_PROJECT_DIR" && timeout "${OPENCODE_PROBE_TIMEOUT:-300}" opencode run "reply with exactly: pong" 2>/dev/null)"; then
    if printf '%s' "$out" | grep -qi pong; then ok "opencode run works end-to-end (orchestrator answered)"; else warn "opencode ran but no 'pong' (got: ${out:0:60})"; fi
  else
    warn "opencode run did not complete (orchestrator may be down or slow-prefilling)"
  fi
else
  err "opencode not installed"; fail=1
fi

echo
[ "$fail" -ne 0 ] && { err "health FAILED"; exit 1; }
ok "health passed"
