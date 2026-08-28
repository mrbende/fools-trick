#!/usr/bin/env bash
# Bootstrap: one-time setup, fully idempotent. Re-running is safe and doubles as a
# check: every step reports whether it was already done (ok) or had to act (say),
# and only does work that is actually missing. Nothing is redone if it is in place.

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/env.sh"
source "$HERE/lib.sh"

want_sha="$(spark_pinned_sha)"

# 0. harness setup: adapter JS deps + generated opencode.json (method-level, not rig).
say "0/4 harness: adapter deps + opencode.json"
if [ -d "$OPENCODE_PROJECT_DIR/node_modules/@opencode-ai/plugin" ]; then
  ok "adapter deps present"
elif command -v npm >/dev/null 2>&1; then
  ( cd "$OPENCODE_PROJECT_DIR" && npm install --no-audit --no-fund >/dev/null 2>&1 ) \
    && ok "installed adapter deps (npm)" || warn "npm install failed; opencode plugins may not load"
else
  warn "npm not found; install Node to build the opencode adapter deps"
fi
if ( cd "$OPENCODE_PROJECT_DIR" && python3 -m core.config --opencode > opencode.json 2>/dev/null ); then
  ok "generated opencode.json from config.yaml"
else
  warn "could not generate opencode.json (run: python3 -m core.config --check)"
fi

# 1. local submodule -- checked out at the pinned commit?
echo; say "1/4 local spark submodule"
if [ -f "$SPARK_DIR_LOCAL/start.sh" ] && [ "$(git -C "$SPARK_DIR_LOCAL" rev-parse HEAD 2>/dev/null)" = "$want_sha" ]; then
  ok "present at ${want_sha:0:12}"
else
  git -C "$OPENCODE_PROJECT_DIR" submodule update --init --recursive spark 2>/dev/null \
    || warn "submodule init skipped (not a git repo yet?)"
  [ -f "$SPARK_DIR_LOCAL/start.sh" ] && ok "initialized at $(spark_pinned_sha | cut -c1-12)" || die "spark submodule missing"
fi

# 2. spark clone on fool -- present, clean, at the pinned commit?
echo; say "2/4 spark clone on $FOOL_HOST"
if ! fool_reachable; then
  warn "$FOOL_HOST not reachable; skipping (run 'make bootstrap' again when it is up)"
elif ssh_fool "[ -d '$FOOL_SPARK_DIR/.git' ]" 2>/dev/null; then
  fool_spark_synced || { warn "clone present but not synced -- syncing"; "$HERE/fool-sync.sh"; }
else
  say "cloning $SPARK_REMOTE_URL -> $FOOL_HOST:$FOOL_SPARK_DIR"
  ssh_fool "git clone '$SPARK_REMOTE_URL' '$FOOL_SPARK_DIR' && git -C '$FOOL_SPARK_DIR' checkout '$want_sha'" \
    || die "clone/checkout failed on $FOOL_HOST"
  ok "cloned and checked out ${want_sha:0:12}"
fi

# 3. worker weights -- on NAS + local fast-copy? (weights.sh is itself check-first)
echo; say "3/4 worker weights (Qwen, magus)"
if [ -f "$LOCAL_WORKER_DIR/$WORKER_FILE" ]; then
  ok "local fast-copy present ($WORKER_FILE)"
elif [ -f "$NAS_WORKER_DIR/$WORKER_FILE" ] || confirm "worker weights not present; download now (~16 GB to NAS + local)?"; then
  "$HERE/weights.sh" QUANT="$WORKER_QUANT"
else
  dim "skipped; run 'make weights' before first worker-up"
fi

# 4. DeepSeek weights on fool -- coalesced serving checkpoint present? (fool-weights.sh is check-first)
echo; say "4/4 DeepSeek weights (orchestrator, fool)"
if ! fool_reachable || ! ssh_fool "[ -d '$FOOL_SPARK_DIR/.git' ]" 2>/dev/null; then
  dim "fool clone not ready; provision later with 'make weights QUANT=deepseek'"
elif ssh_fool "[ -f '$FOOL_SPARK_DIR/data/tp1/rank-sliced-tp1-manifest.json' ]" 2>/dev/null; then
  ok "TP1 serving checkpoint present on $FOOL_HOST (serves local, no rebuild)"
else
  "$HERE/fool-weights.sh"
fi

echo; ok "bootstrap complete / verified. Next: make preflight, then make up"
