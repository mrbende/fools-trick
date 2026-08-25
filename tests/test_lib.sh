#!/usr/bin/env bash
# Unit tests for the shell helpers in scripts/lib.sh. Pure logic only -- no network,
# no servers. A tiny assert harness (no bats dependency).

set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
# shellcheck source=../scripts/config.sh
source "$ROOT/scripts/config.sh"
# shellcheck source=../scripts/lib.sh
source "$ROOT/scripts/lib.sh"

pass=0; fail=0
check() {  # check <name> <expected> <actual>
  if [ "$2" = "$3" ]; then pass=$((pass+1)); printf '  ok   %s\n' "$1"
  else fail=$((fail+1)); printf '  FAIL %s: expected [%s] got [%s]\n' "$1" "$2" "$3"; fi
}
checktrue()  { if "$@"; then pass=$((pass+1)); printf '  ok   %s\n' "$*"; else fail=$((fail+1)); printf '  FAIL %s (expected success)\n' "$*"; fi; }
checkfalse() { if "$@"; then fail=$((fail+1)); printf '  FAIL %s (expected failure)\n' "$*"; else pass=$((pass+1)); printf '  ok   ! %s\n' "$*"; fi; }

echo "logging helpers do not crash and are quiet-safe"
check "say output"  ">> hi"   "$(say hi | sed 's/\x1b\[[0-9;]*m//g')"
check "ok output"   " ok done" "$(ok done | sed 's/\x1b\[[0-9;]*m//g')"

echo "confirm respects FT_YES"
check "FT_YES=1 auto-yes" "0" "$(FT_YES=1 confirm 'proceed?' >/dev/null; echo $?)"

echo "port_in_use detects a listening port"
# start a throwaway listener on a random high port
PORT=$(( (RANDOM % 5000) + 20000 ))
python3 -c "import socket,time,sys; s=socket.socket(); s.bind(('127.0.0.1',$PORT)); s.listen(1); open('/tmp/.ft_port_ready','w').close(); time.sleep(5)" &
LISTENER=$!
for _ in $(seq 1 20); do [ -f /tmp/.ft_port_ready ] && break; sleep 0.1; done
checktrue  port_in_use "$PORT"
checkfalse port_in_use 1     # port 1 is not ours / not listening
kill "$LISTENER" 2>/dev/null; rm -f /tmp/.ft_port_ready

echo "free_gib returns an integer for a real path"
gib="$(free_gib /tmp)"
check "free_gib numeric" "yes" "$([[ "$gib" =~ ^[0-9]+$ ]] && echo yes || echo no)"

echo "spark_pinned_sha reads the submodule HEAD"
sha="$(spark_pinned_sha)"
check "sha is 40 hex" "yes" "$([[ "$sha" =~ ^[0-9a-f]{40}$ ]] && echo yes || echo no)"

echo "config sanity: worker context = slots x per-slot, matches opencode limit"
total=$(( WORKER_PARALLEL * WORKER_CTX_PER_SLOT ))
oc_limit="$(python3 -c "import json;print(json.load(open('$ROOT/opencode.json'))['provider']['magus']['models']['$WORKER_MODEL_ID']['limit']['context'])" 2>/dev/null)"
check "per-slot ctx matches opencode limit" "$WORKER_CTX_PER_SLOT" "$oc_limit"

echo "config sanity: tensor-split has two comma-separated values"
check "ts is N,N" "yes" "$([[ "$WORKER_TENSOR_SPLIT" =~ ^[0-9]+,[0-9]+$ ]] && echo yes || echo no)"

echo
echo "shell lib: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
