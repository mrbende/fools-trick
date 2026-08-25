#!/usr/bin/env bash
# Provision the DeepSeek EXL3 orchestrator weights on fool, ONCE.
#
# Flow (chosen for local-fast serving with a NAS cold backup):
#   1. download + coalesce on fool LOCAL (hf-hub -> data/tp1). Hardlink-coalesce needs
#      one filesystem, so the raw download must be local.
#   2. archive the raw ~107 GB hf-hub to the NAS as cold backup.
#   3. delete the local hf-hub, leaving fool with only the ~99 GB data/tp1 it serves.
#
# Idempotent: the recipe's download.sh no-ops if data/tp1's manifest exists, and we skip
# the whole thing if data/tp1 is already built. After this, every `make fool-up` serves
# straight from local data/tp1 -- the NAS is never on the serve path.

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/config.sh"
source "$HERE/lib.sh"

fool_reachable || die "$FOOL_HOST not reachable"
ssh_fool "[ -d '$FOOL_SPARK_DIR/.git' ]" 2>/dev/null || die "no spark clone on $FOOL_HOST (run: make bootstrap)"

# Already coalesced? nothing to do.
if ssh_fool "[ -f '$FOOL_SPARK_DIR/data/tp1/rank-sliced-tp1-manifest.json' ]" 2>/dev/null; then
  ok "DeepSeek TP1 serving weights already present on $FOOL_HOST:$FOOL_SPARK_DIR/data/tp1"
  exit 0
fi

say "DeepSeek weights not yet coalesced on $FOOL_HOST. This is the one-time heavy step."
dim "  download ~107 GB -> fool local hf-hub, coalesce -> local data/tp1 (~99 GB), archive raw -> NAS"
confirm "run the full download + coalesce + NAS-archive now (long, ~107 GB)?" || { dim "skipped"; exit 0; }

# 1. download + coalesce, entirely on fool local (HF_CACHE defaults to the clone's ./hf-hub).
say "step 1/3: download + coalesce on $FOOL_HOST (local)"
ssh_fool "cd '$FOOL_SPARK_DIR' && HF_CACHE='$FOOL_HF_CACHE' ./download.sh" \
  || die "download/coalesce failed on $FOOL_HOST"
ssh_fool "[ -f '$FOOL_SPARK_DIR/data/tp1/rank-sliced-tp1-manifest.json' ]" 2>/dev/null \
  || die "coalesce did not produce data/tp1 manifest on $FOOL_HOST"
ok "coalesced serving checkpoint ready at $FOOL_SPARK_DIR/data/tp1"

# 2. archive the raw hf-hub to NAS (cold backup), 3. free it locally.
if ssh_fool "[ -d '$FOOL_HF_CACHE' ]" 2>/dev/null; then
  say "step 2/3: archive raw hf-hub -> NAS ($NAS_DEEPSEEK_ARCHIVE)"
  if confirm "archive the raw 107 GB hf-hub to NAS and free it from fool local?"; then
    ssh_fool "[ -d '$NAS_DEEPSEEK_ARCHIVE' ] || mkdir -p '$NAS_DEEPSEEK_ARCHIVE'" \
      || die "cannot create NAS archive dir $NAS_DEEPSEEK_ARCHIVE (is empress mounted on $FOOL_HOST?)"
    # NFS export rejects chown/chgrp/chmod, so drop owner/group/perm preservation
    # (-a would include -pgo and spam "Operation not permitted"). -rltD keeps content,
    # symlinks, and mtimes -- all that matters for a weight archive.
    ssh_fool "rsync -rltD --info=progress2 '$FOOL_HF_CACHE/' '$NAS_DEEPSEEK_ARCHIVE/'" \
      || die "archive rsync to NAS failed"
    say "step 3/3: free local hf-hub on $FOOL_HOST"
    ssh_fool "rm -rf '$FOOL_HF_CACHE'" && ok "local hf-hub removed; fool now carries only data/tp1"
  else
    dim "kept raw hf-hub on fool local ($FOOL_HF_CACHE); NAS archive skipped"
  fi
fi

ok "DeepSeek weights provisioned. make fool-up now serves from local data/tp1 (no rebuild, no NAS on serve path)."
