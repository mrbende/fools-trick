# fools-trick log formatter. Reads a raw log stream on stdin, emits a distilled,
# color-coded stream. Modes via -v mode=default|raw|quiet.
#
# What it does with llama.cpp (worker) lines:
#   - errors (E)   -> red, always
#   - warnings (W) -> yellow, always
#   - startup/model milestones -> bright, always
#   - per-request timing block  -> collapsed into ONE stats line per request (default mode)
#   - the raw slot firehose      -> dropped in default/quiet, shown verbatim in raw
# vLLM/docker (fool) lines: pass through with severity coloring; drop pure-noise heartbeats.

BEGIN {
  R="\033[31m"; Y="\033[33m"; G="\033[32m"; C="\033[36m"; B="\033[1m"; D="\033[2m"; X="\033[0m"
  if (mode=="") mode="default"
}

# accumulate a request's timing fields keyed by task id, flush on release
function flush(task,   s) {
  if (!(task in seen)) return
  s = sprintf("req %s", task)
  if (task in pp) s = s sprintf("  prompt %st@%st/s", pptok[task], pp[task])
  if (task in ev) s = s sprintf("  gen %st@%st/s", evtok[task], ev[task])
  if (task in acc) s = s sprintf("  accept %s", acc[task])
  if (task in tot) s = s sprintf("  (%ss)", tot[task])
  print C s X
  delete seen[task]; delete pp[task]; delete pptok[task]; delete ev[task]
  delete evtok[task]; delete acc[task]; delete tot[task]
}

{
  line=$0

  # --- raw mode: everything, lightly colored by severity ---
  if (mode=="raw") {
    if (line ~ / E /) print R line X
    else if (line ~ / W /) print Y line X
    else print line
    next
  }

  # --- errors and warnings: always ---
  if (line ~ / E /)                 { print R line X; next }
  if (line ~ / W /)                 { print Y line X; next }

  # --- startup / lifecycle milestones: always, bright ---
  if (line ~ /load_model|model loaded|listening on|n_slots|n_ctx_slot|threadpool|MTP draft|Started|Uvicorn|Application startup|Route|EngineCore|serving/) {
    print G line X; next
  }

  # --- per-request timing: capture into a summary, drop the raw lines ---
  if (line ~ /slot print_timing/ && match(line, /task ([0-9]+)/, m)) {
    t=m[1]; seen[t]=1
    if (match(line, /prompt eval time =.*\/ *([0-9]+) tokens.* ([0-9.]+) tokens per second/, a)) { pptok[t]=a[1]; pp[t]=a[2] }
    else if (match(line, /eval time =.*\/ *([0-9]+) tokens.* ([0-9.]+) tokens per second/, a)) { evtok[t]=a[1]; ev[t]=a[2] }
    if (match(line, /total time = *([0-9.]+) ms/, a)) tot[t]=sprintf("%.1f", a[1]/1000)
    if (match(line, /draft acceptance = ([0-9.]+)/, a)) acc[t]=a[1]
    next
  }
  if (line ~ /slot      release/ && match(line, /task ([0-9]+)/, m)) { flush(m[1]); next }

  # --- other slot noise: drop in default/quiet ---
  if (line ~ /slot (launch_slot_|get_availabl|update_slots|kv cache)/) next

  # --- quiet mode drops anything else that is not E/W/startup ---
  if (mode=="quiet") next

  # --- default: pass remaining lines dim (context without spam) ---
  print D line X
}
