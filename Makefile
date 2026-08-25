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
weights: ## show all quants (NAS/local/size), the active default+ctx, and orchestrator weights. QUANT=<tag> provisions one (QUANT=deepseek for fool)
	@$(S)/weights.sh $(if $(QUANT),QUANT=$(QUANT),)

##@ Quality
.PHONY: test
test: ## full test suite: unit (bench parsers + lib) + config + live round-trip
	@$(S)/test.sh all
.PHONY: test-unit
test-unit: ## fast offline unit tests only (no servers needed)
	@$(S)/test.sh unit
# SIZE=smoke|small|large|max controls sample count per eval (default small). e.g. make bench SIZE=smoke
SIZE ?= small
.PHONY: bench
bench: ## full instrument: speed+capability+code/tools+safety+e2e+long-context (SIZE=smoke|small|large|max)
	@SIZE=$(SIZE) $(S)/bench.sh all
.PHONY: bench-quick
bench-quick: ## fast representative signal: capability + code/tools + e2e at smoke size
	@$(S)/bench.sh quick
.PHONY: bench-capability
bench-capability: ## lm-eval reasoning/IF (both nodes) + MC loglikelihood (orchestrator)
	@SIZE=$(SIZE) $(S)/bench.sh capability
.PHONY: bench-code
bench-code: ## code (HumanEval+, executed) + tool-calling (BFCL-style AST) on worker
	@SIZE=$(SIZE) $(S)/bench.sh code
.PHONY: bench-safety
bench-safety: ## refusal/compliance on AdvBench/JBB/XSTest, StrongREJECT judge (the abliteration measure)
	@SIZE=$(SIZE) $(S)/bench.sh safety
.PHONY: bench-longctx
bench-longctx: ## long-context: deep needle (passive) + agentic delegation-at-depth (novel)
	@SIZE=$(SIZE) $(S)/bench.sh longctx
.PHONY: bench-speed
bench-speed: ## speed: TTFT/prefill/decode/concurrency/cache (both servers)
	@$(S)/bench.sh speed
.PHONY: bench-e2e
bench-e2e: ## delegation: whole opencode harness on real fan-out tasks (DB-verified)
	@$(S)/bench.sh e2e
.PHONY: bench-quants
bench-quants: ## A/B quants (Q4_K_S vs IQ3_M vs Q3_K_M) on code+tools+gsm8k -- does a smaller quant hold tool-calling
	@$(S)/compare.sh quants
.PHONY: bench-compare
bench-compare: ## abliterated-vs-base A/B on the worker, then diff
	@$(S)/compare.sh all
.PHONY: bench-sheets
bench-sheets: ## export latest run to Google Sheets (needs GOOGLE_APPLICATION_CREDENTIALS); xlsx always on disk
	@stamp=$$(ls -t /tmp/fools-trick/bench/report-*.md 2>/dev/null | head -1 | sed 's/.*report-//;s/.md//'); \
	if [ -z "$$stamp" ]; then echo "no run found"; exit 1; fi; \
	.bench-venv/bin/python bench/export_xlsx.py --dir /tmp/fools-trick/bench --stamp $$stamp; \
	.bench-venv/bin/python bench/export_sheets.py --dir /tmp/fools-trick/bench --stamp $$stamp --share-with $${BENCH_SHARE_WITH:-reedbndr@gmail.com}

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
