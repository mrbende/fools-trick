#!/usr/bin/env bash
# The autonomous-loop runner: re-prompt the live session on an interval until the stop file
# exists or the budget is spent. The agent keeps working the current task instead of stopping
# for confirmation. Toggled by the autonomous-loop skill; stopped by touching the stop file.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/env.sh"
source "$HERE/lib.sh"

INTERVAL="${LOOP_INTERVAL:-300}"     # seconds between re-prompts
BUDGET="${LOOP_BUDGET:-48}"          # max re-prompts (default: 48 x interval)
STOP="${SCRATCH_DIR:-/tmp/fools-trick/scratch}/loop-stop"
PROMPT="${LOOP_PROMPT:-Continue the current task. If it is fully done and verified, say so and stop.}"

say "autonomous loop: every ${INTERVAL}s, up to ${BUDGET} prompts. Stop: touch $STOP"
i=0
while [ $i -lt "$BUDGET" ]; do
  [ -f "$STOP" ] && { say "stop file present; loop ending"; rm -f "$STOP"; exit 0; }
  i=$((i+1))
  say "loop $i/$BUDGET"
  opencode run -c "$PROMPT" >/dev/null 2>&1 || warn "re-prompt $i failed"
  sleep "$INTERVAL"
done
say "budget spent ($BUDGET prompts); loop ending"
