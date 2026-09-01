#!/usr/bin/env bash
# One-command entrypoint: regenerate config, start redis, verify auth, launch opencode with the
# harness config in the caller's cwd. Cloud rigs serve nothing locally, so this does not run up.sh;
# for a self-hosted rig, `make up` first.

set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
# env.sh and `make -C` both cd into the recipe, so the caller passes its launch dir in FOOLS_CWD.
LAUNCH_CWD="${FOOLS_CWD:-$PWD}"
source "$HERE/env.sh"
source "$HERE/lib.sh"

say "regenerating opencode.json from config.yaml"
if ( cd "$ROOT" && python3 -m core.config --opencode > opencode.json 2>/dev/null ); then
  ok "opencode.json written (orchestrator: $ORCHESTRATOR_MODEL_ID, worker x$WORKER_PARALLEL: $WORKER_MODEL_ID)"
else
  die "config render failed (run: python3 -m core.config --check)"
fi

"$HERE/up.sh" redis || die "redis failed to start"
"$HERE/up.sh" camofox   # web/research layer; warns (does not die) if unavailable

if [ -n "${ZEN_API_KEY:-}" ]; then
  probe_url="${ORCHESTRATOR_URL%/v1}/v1/models"   # env.sh strips /v1; the OpenAI-compatible path needs it
  say "checking orchestrator endpoint ($probe_url)"
  if curl -fsS --max-time 30 "$probe_url" -H "Authorization: Bearer $ZEN_API_KEY" >/dev/null 2>&1; then
    ok "orchestrator endpoint reachable and authed"
  else
    warn "orchestrator endpoint check failed (bad key, network, or provider down) -- launching anyway"
  fi
fi

# The library corpus is a default agent capability; probe the API + embed service so a partial or
# not-yet-booted library-inference stack (make up on fool) surfaces here, not mid-task.
read -r LIB_API LIB_EMBED < <(cd "$ROOT" && python3 -c \
  'import core.config as c; g=c.load(); print(g.library_api_url, g.library_embed_url)' 2>/dev/null)
if [ -n "${LIB_API:-}" ]; then
  say "checking library ($LIB_API + embed $LIB_EMBED)"
  if curl -fsS --max-time 5 "${LIB_API%/}/health" >/dev/null 2>&1; then
    if curl -fsS --max-time 5 "${LIB_EMBED%/}/health" >/dev/null 2>&1; then
      ok "library corpus + embeddings live (search, read, query, fetch all available)"
    else
      warn "library API up but embed ($LIB_EMBED) down -- library_search unavailable until it boots (read/query/fetch work)"
    fi
  else
    warn "library API ($LIB_API) unreachable -- library_* tools will gate off (start it: make up on fool)"
  fi
fi

echo
say "launching opencode (harness config active; working dir: $LAUNCH_CWD)"
# opencode discovers config from the cwd's own opencode.json + opencode/ -- it does NOT read an
# OPENCODE_CONFIG path env var (only OPENCODE_CONFIG_CONTENT, an inline-JSON SDK override). So link
# the harness config + dir into the launch dir, and opencode's normal discovery finds it.
cd "$LAUNCH_CWD" || die "cannot cd to launch dir: $LAUNCH_CWD"
[ -e opencode.json ] || ln -s "$ROOT/opencode.json" opencode.json
[ -e opencode ] || ln -s "$ROOT/opencode" opencode
exec opencode "$@"
