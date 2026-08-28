#!/usr/bin/env bash
# Sync fool's spark clone to the exact commit this repo's submodule pins.
# Refuses to discard uncommitted work without confirmation.

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/env.sh"
source "$HERE/lib.sh"

fool_reachable || die "$FOOL_HOST not reachable"
ssh_fool "[ -d '$FOOL_SPARK_DIR/.git' ]" 2>/dev/null || die "no clone on $FOOL_HOST (run: make bootstrap)"

want="$(spark_pinned_sha)"; [ -n "$want" ] || die "cannot read submodule pin"
have="$(ssh_fool "git -C '$FOOL_SPARK_DIR' rev-parse HEAD" 2>/dev/null || true)"
dirty="$(ssh_fool "git -C '$FOOL_SPARK_DIR' status --porcelain 2>/dev/null | wc -l" || echo 0)"

if [ "$have" = "$want" ] && [ "${dirty:-0}" -eq 0 ]; then
  ok "already clean and synced at ${want:0:12}"; exit 0
fi

if [ "${dirty:-0}" -ne 0 ]; then
  warn "$FOOL_HOST clone has ${dirty} uncommitted change(s):"
  ssh_fool "git -C '$FOOL_SPARK_DIR' status --short" || true
  confirm "discard these local changes on $FOOL_HOST and sync to ${want:0:12}?" || die "aborted"
  ssh_fool "git -C '$FOOL_SPARK_DIR' reset --hard && git -C '$FOOL_SPARK_DIR' clean -fd" || die "reset failed"
fi

say "fetching and checking out ${want:0:12} on $FOOL_HOST"
ssh_fool "git -C '$FOOL_SPARK_DIR' fetch --all --quiet && git -C '$FOOL_SPARK_DIR' checkout '$want'" \
  || die "checkout failed"
ok "synced $FOOL_HOST to ${want:0:12}"
