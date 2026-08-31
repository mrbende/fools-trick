#!/usr/bin/env bash
# Weights: one command for the whole picture and for provisioning any quant.
#
#   ./scripts/weights.sh                     # default: show everything (below)
#   ./scripts/weights.sh QUANT=i1-IQ3_M      # provision a worker quant: NAS -> local NVMe
#   ./scripts/weights.sh QUANT=deepseek      # provision the DeepSeek orchestrator weights on fool
#
# The default view shows, in one place:
#   - every candidate worker quant, where each lives (NAS / local) and its size
#   - which quant is the active default, and the context length it serves at (worker)
#   - the orchestrator (DeepSeek) weights state and its context, on fool
# so you can see at a glance what is available, what is set, and on which machine.
#
# The NAS ($NAS_MODELS) is the source of truth, shared over the LAN by both machines.
# We serve from a local copy because mmap over NFS has latency spikes that hurt cold load and
# can stall mid-serve; the local copy is a disposable cache. ALL weights -- every quant, for
# serving or A/B -- go through this NAS-canonical -> local flow. Never dump a GGUF straight to
# local NVMe.

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/env.sh"
source "$HERE/lib.sh"

nas_mounted() { findmnt -rno TARGET "$NAS_MODELS" >/dev/null 2>&1 || mountpoint -q "$NAS_MODELS" 2>/dev/null || [ -d "$NAS_MODELS" ]; }
quant_file() { printf 'Qwen3.8-27B-OBLITERATED.%s.gguf' "$1"; }

fetch_to_nas() {
  # download the given quant file into NAS_WORKER_DIR if absent
  local file="$1" dest="$NAS_WORKER_DIR/$1"
  if [ -f "$dest" ]; then ok "on NAS: $dest"; return 0; fi
  nas_mounted || die "NAS not mounted at $NAS_MODELS (autofs: 'ls $NAS_MODELS' to trigger; check the NAS)"
  check_free_local "$NAS_MODELS" "$NAS_MIN_FREE_GIB" || confirm "NAS space is low; download anyway?" || die "aborted"
  mkdir -p "$NAS_WORKER_DIR"
  say "downloading $file from $WORKER_REPO -> NAS"
  if command -v hf >/dev/null 2>&1; then
    hf download "$WORKER_REPO" "$file" --local-dir "$NAS_WORKER_DIR"
  elif command -v huggingface-cli >/dev/null 2>&1; then
    huggingface-cli download "$WORKER_REPO" "$file" --local-dir "$NAS_WORKER_DIR"
  else
    die "need 'hf' or 'huggingface-cli' to download (or place $file in $NAS_WORKER_DIR)"
  fi
  [ -f "$dest" ] || die "download did not produce $dest"
  ok "downloaded to NAS: $dest"
}

copy_to_local() {
  # rsync the given quant file from NAS to local NVMe for fast serving
  local file="$1" src="$NAS_WORKER_DIR/$1" dst="$LOCAL_WORKER_DIR/$1"
  [ -f "$src" ] || die "not on NAS yet: $src"
  if [ -f "$dst" ] && [ "$(stat -c%s "$src" 2>/dev/null)" = "$(stat -c%s "$dst" 2>/dev/null)" ]; then
    ok "local fast-copy present: $dst"; return 0
  fi
  local need_gib; need_gib=$(( ($(stat -c%s "$src") / 1073741824) + 5 ))
  if ! check_free_local "$LOCAL_MODELS" "$need_gib"; then
    warn "local disk tight for a $((need_gib-5))G copy. Existing weights in $LOCAL_MODELS are not touched."
    dim "reclaim manually if needed, e.g.: du -sh $LOCAL_MODELS/*"
    confirm "copy to local anyway?" || die "aborted"
  fi
  mkdir -p "$LOCAL_WORKER_DIR"
  say "fast-copy $file : NAS -> local NVMe"
  rsync -rltD --info=progress2 "$src" "$dst"
  ok "local ready: $dst"
}

provision_worker_quant() {
  local quant="$1" file; file="$(quant_file "$quant")"
  fetch_to_nas "$file"
  copy_to_local "$file"
}

provision_deepseek() { exec "$HERE/fool-weights.sh"; }

# where-does-this-file-live cell for the status table
where() {
  local path="$1"
  if [ -f "$path" ]; then printf '%s%6s%s' "$_c_grn" "$(du -h "$path" 2>/dev/null | cut -f1)" "$_c_reset"
  else printf '%s%6s%s' "$_c_dim" "--" "$_c_reset"; fi
}

show_status() {
  say "worker quants (magus)   local serving dir: $LOCAL_WORKER_DIR"
  printf '  %-14s  %-6s  %-6s  %s\n' "quant" "NAS" "local" ""
  local q file mark
  for q in $WORKER_QUANTS; do
    file="$(quant_file "$q")"
    [ "$q" = "$WORKER_QUANT" ] && mark="  ${_c_blu}<- active, serving n_ctx=${WORKER_CTX_PER_SLOT} x ${WORKER_PARALLEL} slots (KV ${WORKER_KV})${_c_reset}" || mark=""
    printf '  %-14s  %s  %s%s\n' "$q" "$(where "$NAS_WORKER_DIR/$file")" "$(where "$LOCAL_WORKER_DIR/$file")" "$mark"
  done
  nas_mounted && ok "NAS mounted at $NAS_MODELS ($(free_gib "$NAS_MODELS")G free)" || warn "NAS not mounted at $NAS_MODELS"

  echo
  say "orchestrator (fool)     DeepSeek $ORCHESTRATOR_MODEL_ID"
  local tp1="$FOOL_SPARK_DIR/data/tp1/rank-sliced-tp1-manifest.json"
  if fool_reachable && ssh_fool "[ -f '$tp1' ]" 2>/dev/null; then
    local sz; sz="$(ssh_fool "du -sh '$FOOL_SPARK_DIR/data/tp1' 2>/dev/null | cut -f1" || echo '?')"
    ok "coalesced TP1 serving weights present on $FOOL_HOST ($sz), effort=$FOOL_EFFORT ablate=$FOOL_ABLATE"
  elif fool_reachable; then
    warn "DeepSeek weights not coalesced on $FOOL_HOST (provision: make weights QUANT=deepseek)"
  else
    dim "  $FOOL_HOST not reachable; cannot check DeepSeek weights"
  fi
}

# arg parse: bare -> status; QUANT=<tag> -> provision that quant (or 'deepseek')
QARG=""
for a in "$@"; do case "$a" in QUANT=*) QARG="${a#QUANT=}" ;; esac; done

if [ -z "$QARG" ]; then
  show_status
else
  case "$QARG" in
    deepseek|fool) provision_deepseek ;;
    *)             provision_worker_quant "$QARG" ;;
  esac
fi
