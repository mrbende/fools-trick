# fools-trick: distributed opencode coding system.
# Orchestrator (DeepSeek-V4-Flash, abliterated) on fool; workers (Qwen3.8-27B-OBLITERATED) on magus.
# magus targets run locally; fool targets run over SSH. Run `make` for the menu.

SHELL := /bin/bash
S := ./scripts

.DEFAULT_GOAL := help

.PHONY: help
help:
	@printf '\n\033[1mfools-trick\033[0m  distributed coding: DeepSeek orchestrator (fool) + Qwen workers (magus)\n'
	@awk 'BEGIN{FS=":.*## "} \
		/^##@ /{printf "\n\033[1m%s\033[0m\n", substr($$0,5); next} \
		/^[a-zA-Z0-9_-]+:.*## /{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@printf '\n\033[2mfirst run:\033[0m  make bootstrap  ->  make preflight  ->  make up  ->  make health\n'
	@printf '\033[2mre-run bootstrap anytime; it checks what is done and only fills gaps.\033[0m\n\n'

##@ Setup
.PHONY: bootstrap
bootstrap: ## one-time (idempotent): submodule, fool clone, all weights. Re-run to verify.
	@$(S)/bootstrap.sh
.PHONY: preflight
preflight: ## read-only readiness check: tools, disk, mounts, weights, fool sync, endpoints
	@$(S)/preflight.sh

##@ Run
.PHONY: up
up: ## start both: worker on magus + orchestrator on fool
	@$(S)/up.sh all
.PHONY: down
down: ## stop both servers
	@$(S)/down.sh all
.PHONY: restart
restart: down up ## stop then start both

##@ Inspect
.PHONY: status
status: ## concise status of both servers, weights, opencode
	@$(S)/status.sh
.PHONY: health
health: ## active end-to-end: real completions + opencode round-trip
	@$(S)/health.sh
.PHONY: logs
logs: ## unified logs: worker (magus) + orchestrator (fool) interleaved, node-prefixed
	@$(S)/logs.sh all

##@ Weights
.PHONY: weights
weights: ## worker weights: ensure on NAS + fast-copy to local NVMe (magus)
	@$(S)/weights.sh worker
.PHONY: fool-weights
fool-weights: ## DeepSeek weights: one-time download+coalesce local, archive raw to NAS (fool)
	@$(S)/fool-weights.sh
.PHONY: weights-status
weights-status: ## show where weights live (NAS / local) and sizes
	@$(S)/weights.sh status

##@ Quality
.PHONY: test
test: ## full test suite: unit (bench parsers + lib) + config + live round-trip
	@$(S)/test.sh all
.PHONY: test-unit
test-unit: ## fast offline unit tests only (no servers needed)
	@$(S)/test.sh unit
.PHONY: bench
bench: ## all benchmarks: speed + eval on both servers, then e2e harness
	@$(S)/bench.sh all
.PHONY: bench-speed
bench-speed: ## speed: TTFT/prefill/decode/concurrency/cache (both servers)
	@$(S)/bench.sh speed both
.PHONY: bench-eval
bench-eval: ## quality: real gsm8k + ruler reasoning-at-depth (both servers)
	@$(S)/bench.sh eval both
.PHONY: bench-e2e
bench-e2e: ## the real eval: whole opencode harness on real fan-out tasks
	@$(S)/bench.sh e2e
.PHONY: bench-quick
bench-quick: ## fast signal: worker evals (small n) + e2e, skip slow fool/speed suites
	@$(S)/bench.sh quick
.PHONY: bench-compare
bench-compare: ## abliterated-vs-base A/B on the worker (gsm8k+code+tools), then diff
	@$(S)/compare.sh all

##@ Per-node (advanced)
.PHONY: worker-up
worker-up: ## start only the worker (magus)
	@$(S)/up.sh worker
.PHONY: worker-down
worker-down: ## stop only the worker (magus)
	@$(S)/down.sh worker
.PHONY: worker-logs
worker-logs: ## tail only the worker log (magus)
	@$(S)/logs.sh worker
.PHONY: fool-up
fool-up: ## start only the orchestrator (fool); verifies git sync first
	@$(S)/up.sh fool
.PHONY: fool-down
fool-down: ## stop only the orchestrator (fool)
	@$(S)/down.sh fool
.PHONY: fool-logs
fool-logs: ## tail only the orchestrator log (fool)
	@$(S)/logs.sh fool
.PHONY: fool-sync
fool-sync: ## sync fool's spark clone to this repo's pinned commit
	@$(S)/fool-sync.sh
