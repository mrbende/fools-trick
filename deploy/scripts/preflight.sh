#!/usr/bin/env bash
# Preflight: verify the environment is ready to run the distributed system.
# Read-only. Exits nonzero if any hard check fails; warns on soft issues.

set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/env.sh"
source "$HERE/lib.sh"

fail=0
hard() { "$@" || fail=1; }

say "fools-trick preflight"
echo

# --- local tooling ---
command -v opencode  >/dev/null 2>&1 && ok "opencode present ($(opencode --version 2>/dev/null))" || { err "opencode not found"; fail=1; }
command -v curl      >/dev/null 2>&1 && ok "curl present" || { err "curl missing"; fail=1; }
command -v rsync     >/dev/null 2>&1 && ok "rsync present" || { err "rsync missing"; fail=1; }
command -v python3   >/dev/null 2>&1 && ok "python3 present" || warn "python3 missing (model-id parsing degraded)"
[ -x "$LLAMA_SERVER" ] && ok "llama-server present ($LLAMA_SERVER)" || { err "llama-server missing at $LLAMA_SERVER"; fail=1; }

# --- config sanity: opencode parses and agents resolve ---
if opencode debug config >/dev/null 2>&1; then ok "opencode config parses"; else err "opencode config failed to parse"; fail=1; fi

echo
# --- storage ---
if findmnt -rno TARGET "$NAS_MODELS" >/dev/null 2>&1 || [ -d "$NAS_MODELS" ]; then
  ok "NAS mounted: $NAS_MODELS ($(free_gib "$NAS_MODELS")G free)"
else
  err "NAS not mounted at $NAS_MODELS"; fail=1
fi
check_free_local "$LOCAL_MODELS" "$LOCAL_MIN_FREE_GIB" || warn "local disk tight; existing weights will not be auto-deleted"

echo
# --- weights ---
[ -f "$NAS_WORKER_DIR/$WORKER_FILE" ]   && ok "worker weights on NAS ($WORKER_QUANT)"   || warn "worker weights not on NAS yet (make weights)"
[ -f "$LOCAL_WORKER_DIR/$WORKER_FILE" ] && ok "worker weights local fast-copy present"    || warn "worker weights not copied local yet (make weights)"

echo
# --- fool reachability + code sync ---
if fool_reachable; then
  ok "$FOOL_HOST reachable on LAN"
  # confirm we are on the wired-LAN path, not a VPN overlay (Tailscale etc). LAN_PREFIX is set in
  # deploy.yaml (topology.lan_prefix) to your subnet.
  conn="$(ssh_fool 'echo $SSH_CONNECTION' 2>/dev/null | awk '{print $3}')"
  case "$conn" in
    "$LAN_PREFIX"*) ok "$FOOL_HOST via LAN ($conn)";;
    "")             warn "could not confirm $FOOL_HOST connection path";;
    *)              warn "$FOOL_HOST NOT on ${LAN_PREFIX}x (got $conn) -- check /etc/hosts, avoid VPN overlays";;
  esac
  if ssh_fool "[ -d '$FOOL_SPARK_DIR/.git' ]" 2>/dev/null; then
    fool_spark_synced || warn "fool spark clone not clean/synced (make fool-sync)"
  else
    warn "no spark clone on $FOOL_HOST (make bootstrap)"
  fi
else
  warn "$FOOL_HOST not reachable (orchestrator checks skipped)"
fi

echo
# --- live endpoints (informational) ---
http_ok "$WORKER_URL/v1/models" && ok "worker serving: $(models_id "$WORKER_URL")" || dim "worker not serving (make worker-up)"
{ http_ok "$FOOL_URL/v1/models" || http_ok "$FOOL_URL/health"; } && ok "orchestrator serving: ${FOOL_URL}" || dim "orchestrator not serving (make fool-up)"

echo
if [ "$fail" -ne 0 ]; then err "preflight FAILED"; exit 1; fi
ok "preflight passed"
