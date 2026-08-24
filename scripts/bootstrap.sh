#!/usr/bin/env bash
# Bootstrap: one-time setup. Init submodule, clone+sync spark on fool, provision worker weights.
# Idempotent: safe to re-run.

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/config.sh"
source "$HERE/lib.sh"

say "bootstrap: local submodule"
git -C "$OPENCODE_PROJECT_DIR" submodule update --init --recursive spark 2>/dev/null || warn "submodule init skipped (not a git repo yet?)"
[ -f "$SPARK_DIR_LOCAL/start.sh" ] && ok "spark submodule present ($(spark_pinned_sha | cut -c1-12))" || die "spark submodule missing"

echo
say "bootstrap: spark clone on $FOOL_HOST"
if fool_reachable; then
  local_sha="$(spark_pinned_sha)"
  if ssh_fool "[ -d '$FOOL_SPARK_DIR/.git' ]" 2>/dev/null; then
    ok "clone exists on $FOOL_HOST"
    "$HERE/fool-sync.sh"
  else
    say "cloning $SPARK_REMOTE_URL -> $FOOL_HOST:$FOOL_SPARK_DIR"
    ssh_fool "git clone '$SPARK_REMOTE_URL' '$FOOL_SPARK_DIR' && git -C '$FOOL_SPARK_DIR' checkout '$local_sha'" \
      || die "clone/checkout failed on $FOOL_HOST"
    ok "cloned and checked out ${local_sha:0:12} on $FOOL_HOST"
  fi
else
  warn "$FOOL_HOST not reachable; skipping remote clone (run 'make fool-sync' later)"
fi

echo
say "bootstrap: worker weights (NAS canonical + local fast-copy)"
if confirm "provision worker weights now (download to NAS if needed, copy to local)?"; then
  "$HERE/weights.sh" worker
else
  dim "skipped; run 'make weights' before first serve"
fi

echo
ok "bootstrap complete. Next: make preflight, then make up"
