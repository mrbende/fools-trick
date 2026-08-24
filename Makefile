# fools-trick: distributed opencode coding system.
# Orchestrator (DeepSeek-V4-Flash, abliterated) on fool; workers (Qwen3.8-27B-OBLITERATED) on magus.
# This Makefile drives both machines: magus targets run locally, fool targets over SSH.

SHELL := /bin/bash
S := ./scripts

.DEFAULT_GOAL := help

.PHONY: help
help: ## show this help
	@grep -E '^[a-zA-Z0-9_-]+:.*## ' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN{FS=":.*## "}{printf "  \033[36m%-16s\033[0m %s\n",$$1,$$2}'
	@echo
	@echo "  typical first run:  make bootstrap && make preflight && make up && make health"

# --- lifecycle ---
.PHONY: bootstrap
bootstrap: ## one-time: init submodule, clone+sync spark on fool, provision weights
	@$(S)/bootstrap.sh

.PHONY: preflight
preflight: ## read-only readiness check (tools, disk, mounts, weights, fool sync, endpoints)
	@$(S)/preflight.sh

.PHONY: up
up: ## start both servers (worker on magus, orchestrator on fool)
	@$(S)/up.sh all

.PHONY: worker-up
worker-up: ## start the worker on magus only
	@$(S)/up.sh worker

.PHONY: fool-up
fool-up: ## start the orchestrator on fool only (verifies git sync first)
	@$(S)/up.sh fool

.PHONY: down
down: ## stop both servers
	@$(S)/down.sh all

.PHONY: worker-down
worker-down: ## stop the worker on magus
	@$(S)/down.sh worker

.PHONY: fool-down
fool-down: ## stop the orchestrator on fool
	@$(S)/down.sh fool

.PHONY: restart
restart: down up ## stop then start both

# --- inspection ---
.PHONY: status
status: ## concise status of both servers, weights, opencode
	@$(S)/status.sh

.PHONY: health
health: ## active end-to-end health: real completions + opencode round-trip
	@$(S)/health.sh

.PHONY: logs
logs: ## tail the local worker log
	@tail -n 60 -f worker/logs/worker.log

.PHONY: fool-logs
fool-logs: ## tail the orchestrator container logs on fool
	@source $(S)/config.sh && ssh -o ConnectTimeout=8 "$$FOOL_HOST" "cd '$$FOOL_SPARK_DIR' && ./start.sh logs"

# --- weights (NAS canonical, local fast-copy) ---
.PHONY: weights
weights: ## ensure worker weights on NAS + fast-copied to local NVMe
	@$(S)/weights.sh worker

.PHONY: weights-status
weights-status: ## show where weights live (NAS / local) and sizes
	@$(S)/weights.sh status

# --- code sync (fool must match our submodule pin before serving) ---
.PHONY: fool-sync
fool-sync: ## sync fool's spark clone to this repo's pinned commit
	@$(S)/fool-sync.sh

# --- quality ---
.PHONY: bench
bench: ## run benchmarks (stub: smoke timings for now)
	@$(S)/bench.sh

.PHONY: test
test: ## config integrity, agent resolution, live subagent round-trip
	@$(S)/test.sh
