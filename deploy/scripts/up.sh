#!/usr/bin/env bash
# Start servers. Targets: worker (magus, local), fool (DGX Spark, over ssh), or both.
#   ./scripts/up.sh worker | fool | all
# Refuses to serve fool from a dirty/diverged git tree. Confirms before killing anything.

set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/env.sh"
source "$HERE/lib.sh"

up_redis() {
  [ "${MEMORY_ENABLED:-1}" = "1" ] || { dim "memory disabled; skipping redis"; return 0; }
  say "starting redis (short-term memory + write-queue) as $REDIS_CONTAINER"
  # Ephemeral by design: short-term memory. The durable episode store is SQLite ($MEMORY_DB),
  # which persists across make down. So the container needs no volume -- if it dies, short-term
  # memory is gone but the source of truth is intact and Redis rewarms from it.
  if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$REDIS_CONTAINER"; then
    ok "redis already running ($REDIS_CONTAINER)"; return 0
  fi
  docker rm -f "$REDIS_CONTAINER" >/dev/null 2>&1 || true
  docker run -d --name "$REDIS_CONTAINER" -p "127.0.0.1:${REDIS_PORT}:6379" \
    "$REDIS_IMAGE" redis-server --save "" --appendonly no >/dev/null \
    || die "failed to start redis container (is docker running?)"
  for _ in $(seq 1 20); do
    docker exec "$REDIS_CONTAINER" redis-cli ping 2>/dev/null | grep -q PONG && { ok "redis healthy on :$REDIS_PORT"; return 0; }
    sleep 0.5
  done
  die "redis did not become healthy"
}

up_camofox() {
  # The web/research layer: an anti-detection Firefox server. Real-time web is a first-class agent
  # capability, so it is brought up with the harness rather than lazy-started mid-task (which added
  # latency and a fragile failure path). Backgrounded; journald-free, logs to the scratch dir.
  local base="${CAMOFOX_URL:-http://localhost:9377}"
  local dir="${CAMOFOX_DIR:-$HOME/Source/camofox-browser}"
  local ua='{"userId":"fools-trick","sessionKey":"health"}'
  if curl -fsS --max-time 3 -X POST "$base/tabs" -H 'Content-Type: application/json' -d "$ua" >/dev/null 2>&1; then
    ok "camofox already up ($base)"; return 0
  fi
  [ -f "$dir/server.js" ] || { warn "camofox server not found at $dir; web tools will be unavailable"; return 0; }
  say "starting camofox web server ($base)"
  mkdir -p /tmp/fools-trick
  # Fully detach: redirect all three fds so the background node process does not hold this script's
  # stdout open (which would make the caller block until camofox exits). setsid + </dev/null is the
  # clean detach; the loop below polls for health.
  ( cd "$dir" && setsid node server.js </dev/null >/tmp/fools-trick/camofox.log 2>&1 & ) >/dev/null 2>&1
  for _ in $(seq 1 20); do
    sleep 1
    curl -fsS --max-time 2 -X POST "$base/tabs" -H 'Content-Type: application/json' -d "$ua" >/dev/null 2>&1 \
      && { ok "camofox healthy ($base)"; return 0; }
  done
  warn "camofox did not become healthy in 20s (see /tmp/fools-trick/camofox.log); web tools will lazy-start on first use"
}

up_worker() {
  say "starting worker on magus"
  if http_ok "$WORKER_URL/v1/models"; then
    ok "worker already up and healthy on :$WORKER_PORT"; return 0
  fi
  if port_in_use "$WORKER_PORT" || worker_active; then
    warn ":$WORKER_PORT busy or fools-worker unit present but not healthy"
    confirm "restart the worker?" || die "aborted"
    "$HERE/down.sh" worker
  fi
  # Ensure weights are present (check-first; provisions from NAS or downloads if missing).
  if [ ! -f "$LOCAL_WORKER_DIR/$WORKER_FILE" ]; then
    say "worker weights not local; provisioning"
    "$HERE/weights.sh" QUANT="$WORKER_QUANT"
  fi
  # Launch as a transient systemd user service: journald handles logging + lifecycle,
  # args come from serve.sh (config-driven), no unit file to maintain.
  # systemd-run starts serve.sh in a FRESH env (units don't inherit the caller's shell env), so
  # pass through any WORKER_* / model overrides explicitly -- otherwise `WORKER_CTX_PER_SLOT=... make
  # worker-up` is silently ignored (a real bug we hit). --setenv threads the tunables in.
  setenv_args=()
  for v in WORKER_CTX_PER_SLOT WORKER_PARALLEL WORKER_KV WORKER_TENSOR_SPLIT WORKER_SPLIT_MODE \
           WORKER_REASONING WORKER_MODEL_PATH WORKER_QUANT WORKER_FILE; do
    [ -n "${!v:-}" ] && setenv_args+=(--setenv="$v=${!v}")
  done
  systemd-run --user --unit "$WORKER_UNIT" --description "fools-trick worker (Qwen)" \
    "${setenv_args[@]}" --collect "$HERE/../worker/serve.sh" \
    || die "systemd-run failed to start $WORKER_UNIT"

  # Fast-fail on a load-time VRAM OOM instead of waiting the full health timeout: with -ngl 999
  # a non-fitting config aborts within seconds ("failed to allocate CUDAx buffer"). Poll for both
  # health and the abort marker; whichever comes first decides.
  local deadline=$(( $(date +%s) + 300 ))
  say "waiting for worker health (or fast-fail on VRAM OOM) ..."
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if http_ok "$WORKER_URL/v1/models" || http_ok "$WORKER_URL/health"; then
      ok "worker healthy: $(models_id "$WORKER_URL" || echo "$WORKER_URL")"; return 0
    fi
    if ! worker_active && worker_load_oom; then
      err "worker aborted at load: config does not fit in VRAM (n_ctx=$(( WORKER_PARALLEL * WORKER_CTX_PER_SLOT )), KV=$WORKER_KV, ts=$WORKER_TENSOR_SPLIT)"
      dim "  reduce WORKER_CTX_PER_SLOT or WORKER_PARALLEL, or use a smaller quant; see journalctl --user -u $WORKER_UNIT"
      return 3
    fi
    sleep 3
  done
  err "worker did not become healthy within 300s"; return 1
}

up_fool() {
  say "starting orchestrator on $FOOL_HOST"
  fool_reachable || die "$FOOL_HOST not reachable on the LAN"

  # Never serve stale/modified code: fool's clone must be clean and at our pinned SHA.
  fool_spark_synced || die "fool spark clone not synced (make fool-sync) -- refusing to start"

  # Already healthy?
  if http_ok "$ORCHESTRATOR_URL/v1/models" || http_ok "$ORCHESTRATOR_URL/health"; then
    ok "orchestrator already up and healthy at $ORCHESTRATOR_URL"; return 0
  fi

  # Something on the port but unhealthy? confirm before the recipe recreates the container.
  local busy; busy="$(ssh_fool "ss -ltn 2>/dev/null | grep -c ':$FOOL_PORT '" || echo 0)"
  if [ "${busy:-0}" -ne 0 ]; then
    warn "$FOOL_HOST has something on :$FOOL_PORT but it is not healthy"
    confirm "let the spark recipe stop/recreate the container on $FOOL_HOST?" || die "aborted"
    ssh_fool "cd '$FOOL_SPARK_DIR' && ./start.sh stop" || warn "stop returned nonzero"
  fi

  # Serve from the local coalesced data/tp1. The recipe's entrypoint skips download/coalesce
  # when data/tp1's manifest exists, so this is fast and never touches the NAS. If the weights
  # were never provisioned, warn -- don't trigger a surprise 107 GB download inside serve.
  if ! ssh_fool "[ -f '$FOOL_SPARK_DIR/data/tp1/rank-sliced-tp1-manifest.json' ]" 2>/dev/null; then
    warn "DeepSeek weights not coalesced on $FOOL_HOST yet"
    confirm "run 'make weights QUANT=deepseek' first? (recommended; otherwise serve will download ~107 GB)" \
      && { "$HERE/fool-weights.sh"; } || dim "proceeding; first boot will download+coalesce (slow)"
  fi
  say "launching spark recipe on $FOOL_HOST (ABLATE=$FOOL_ABLATE, effort=$FOOL_EFFORT); serves from local data/tp1"
  ssh_fool "cd '$FOOL_SPARK_DIR' && HF_CACHE='$FOOL_HF_CACHE' ABLATE=$FOOL_ABLATE \
    DEFAULT_CHAT_TEMPLATE_KWARGS_EFFORT='$FOOL_EFFORT' ./start.sh --no-wait" \
    || die "spark start.sh failed on $FOOL_HOST"
  wait_health "$ORCHESTRATOR_URL" "${FOOL_STARTUP_WAIT:-3600}" "orchestrator"
}

case "${1:-all}" in
  redis)   up_redis ;;
  camofox) up_camofox ;;
  worker)  up_worker ;;
  fool)    up_fool ;;
  all)     up_redis; up_camofox; up_worker; up_fool ;;
  *) die "usage: up.sh {redis|camofox|worker|fool|all}" ;;
esac
