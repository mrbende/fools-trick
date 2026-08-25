#!/usr/bin/env bash
# Weight management: NAS canonical store, local NVMe fast-copy for serving.
#
#   ./scripts/weights.sh worker        # ensure worker GGUF on NAS, fast-copy active quant to local
#   ./scripts/weights.sh worker-nas    # download to NAS only (no local copy)
#   ./scripts/weights.sh status        # show what's where
#
# The NAS (/mnt/empress/models) is the source of truth, shared over 10G NFS by both
# machines. We serve from a local copy because mmap over NFS has latency spikes that
# hurt cold load and can stall mid-serve. The local copy is disposable cache.

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/config.sh"
source "$HERE/lib.sh"

nas_mounted() { findmnt -rno TARGET "$NAS_MODELS" >/dev/null 2>&1 || mountpoint -q "$NAS_MODELS" 2>/dev/null || [ -d "$NAS_MODELS" ]; }

fetch_to_nas() {
  # download WORKER_FILE into NAS_WORKER_DIR if absent
  local dest="$NAS_WORKER_DIR/$WORKER_FILE"
  if [ -f "$dest" ]; then ok "on NAS: $dest"; return 0; fi
  nas_mounted || die "NAS not mounted at $NAS_MODELS (autofs: 'ls $NAS_MODELS' to trigger, check empress)"
  check_free_local "$NAS_MODELS" "$NAS_MIN_FREE_GIB" || confirm "NAS space is low; download anyway?" || die "aborted"
  mkdir -p "$NAS_WORKER_DIR"
  say "downloading $WORKER_FILE from $WORKER_REPO -> NAS"
  if command -v hf >/dev/null 2>&1; then
    hf download "$WORKER_REPO" "$WORKER_FILE" --local-dir "$NAS_WORKER_DIR"
  elif command -v huggingface-cli >/dev/null 2>&1; then
    huggingface-cli download "$WORKER_REPO" "$WORKER_FILE" --local-dir "$NAS_WORKER_DIR"
  else
    die "need 'hf' or 'huggingface-cli' to download (or place $WORKER_FILE in $NAS_WORKER_DIR)"
  fi
  [ -f "$dest" ] || die "download did not produce $dest"
  ok "downloaded to NAS: $dest"
}

copy_to_local() {
  # rsync the active quant from NAS to local NVMe for fast serving
  local src="$NAS_WORKER_DIR/$WORKER_FILE" dst="$LOCAL_WORKER_DIR/$WORKER_FILE"
  [ -f "$src" ] || die "not on NAS yet: $src (run: weights.sh worker-nas)"
  if [ -f "$dst" ] && [ "$(stat -c%s "$src" 2>/dev/null)" = "$(stat -c%s "$dst" 2>/dev/null)" ]; then
    ok "local fast-copy present: $dst"; return 0
  fi
  local need_gib; need_gib=$(( ($(stat -c%s "$src") / 1073741824) + 5 ))
  if ! check_free_local "$LOCAL_MODELS" "$need_gib"; then
    warn "local disk tight for a $((need_gib-5))G copy. Existing weights in $LOCAL_MODELS are not touched."
    dim "reclaim manually if needed, e.g. old test model: du -sh $LOCAL_MODELS/*"
    confirm "copy to local anyway?" || die "aborted; serve from NAS with WORKER_MODEL_PATH override if you must"
  fi
  mkdir -p "$LOCAL_WORKER_DIR"
  say "fast-copy $WORKER_FILE : NAS -> local NVMe"
  rsync -rltD --info=progress2 "$src" "$dst"
  ok "local ready: $dst"
}

cmd="${1:-status}"
case "$cmd" in
  worker)      fetch_to_nas; copy_to_local ;;
  worker-nas)  fetch_to_nas ;;
  worker-local) copy_to_local ;;
  status)
    say "worker model: $WORKER_FILE ($WORKER_QUANT)"
    if [ -f "$NAS_WORKER_DIR/$WORKER_FILE" ]; then ok "NAS:   $NAS_WORKER_DIR/$WORKER_FILE ($(du -h "$NAS_WORKER_DIR/$WORKER_FILE" | cut -f1))"; else warn "NAS:   absent"; fi
    if [ -f "$LOCAL_WORKER_DIR/$WORKER_FILE" ]; then ok "local: $LOCAL_WORKER_DIR/$WORKER_FILE ($(du -h "$LOCAL_WORKER_DIR/$WORKER_FILE" | cut -f1))"; else warn "local: absent (will fast-copy on serve)"; fi
    nas_mounted && ok "NAS mounted at $NAS_MODELS ($(free_gib "$NAS_MODELS")G free)" || warn "NAS not mounted"
    ;;
  *) die "usage: weights.sh {worker|worker-nas|worker-local|status}" ;;
esac
