# fools-trick: a local distributed coding harness -- a deep orchestrator + fast concurrent
# workers, with owned Python primitives (core/) plugged into opencode via thin adapters.
# Config is one source of truth: config.yaml (the method) + deploy.yaml (the rig). Run `make`.

SHELL := /bin/bash
S := ./deploy/scripts

# Skills are allowlist-only in this repo: load ONLY .opencode/skills, never the
# global (~/.config/opencode/skills) or external (~/.claude, ~/.agents) scans.
# This keeps worker requests inside the 32k slot; the global catalog alone
# overran it. Exported so every opencode invocation from the harness matches
# the interactive sessions.
export OPENCODE_DISABLE_EXTERNAL_SKILLS := 1
export OPENCODE_DISABLE_CLAUDE_CODE_SKILLS := 1

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
bootstrap: ## one-time (idempotent): adapter deps, submodule, fool clone, all weights. Re-run to verify.
	@$(S)/bootstrap.sh
.PHONY: config
config: ## generate opencode.json from config.yaml + opencode.base.json (single source of truth)
	@python3 -m core.config --check
	@python3 -m core.config --opencode > opencode.json && echo "wrote opencode.json from config.yaml"
.PHONY: config-show
config-show: ## print the fully-resolved config (config.yaml + config.local + deploy merged)
	@python3 -m core.config --json
.PHONY: preflight
preflight: ## read-only readiness check: tools, disk, mounts, weights, fool sync, endpoints
	@$(S)/preflight.sh

##@ Run
.PHONY: start
start: ## ONE COMMAND: load .env, regen config, start redis, launch interactive opencode (cloud rig)
	@$(S)/start.sh
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
.PHONY: observe
observe: ## per-task rollup (tokens, delegation, wall) + trip-wires vs recent baseline
	@python3 -m core.observe

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
.PHONY: bench-memory
bench-memory: ## memory A/B: sliding-window+recall vs compaction on a long coding session (LLM-judged)
	@SIZE=$(SIZE) $(S)/bench.sh memory
.PHONY: bench-e2e
bench-e2e: ## delegation: whole opencode harness on real fan-out tasks (DB-verified)
	@$(S)/bench.sh e2e
.PHONY: bench-prune
bench-prune: ## subagent prune: worker reads past its budget, must stay correct (outcome-verified)
	@SIZE=$(SIZE) $(S)/bench.sh prune
.PHONY: bench-cap
bench-cap: ## per-result cap: a worker reads past the cap, must recover the spilled tail by seq
	@SIZE=$(SIZE) $(S)/bench.sh cap
.PHONY: bench-xagent
bench-xagent: ## cross-agent memory: subagent writes, a fresh session recalls (thread-root proof)
	@$(S)/bench.sh xagent
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
	.bench-venv/bin/python bench/export_sheets.py --dir /tmp/fools-trick/bench --stamp $$stamp $${BENCH_SHARE_WITH:+--share-with $$BENCH_SHARE_WITH}

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
.PHONY: redis-up
redis-up: ## start only the redis memory container (magus)
	@$(S)/up.sh redis
.PHONY: redis-down
redis-down: ## stop the redis memory container (short-term memory is ephemeral; SQLite persists)
	@$(S)/down.sh redis

##@ Autonomous loop
.PHONY: loop
loop: ## start the self-continuation loop (re-prompts the live session on an interval until stop/budget)
	@$(S)/loop.sh
.PHONY: loop-stop
loop-stop: ## stop the autonomous loop (touch the stop file)
	@touch "$$(python3 -c 'import sys;sys.path.insert(0,".");import core.config as c;print(c.load().scratch_dir)')/loop-stop" && echo "stop file set; the loop ends at the next interval"
