"""Emitters: the shell and the opencode adapter consume the loader, not the YAML directly.

The shell and the opencode adapter both ask this loader (`python -m core.config --shell/--env/
--json/--opencode`), so nothing is kept in sync by hand. Values are derived and validated once in
core/config.py (load), then handed to the shell or rendered into opencode.json here.
"""

from __future__ import annotations

import os
from dataclasses import asdict

from core.config import Config, _ROOT

# Stable opencode provider keys. The agent defs reference these (fool-ds4/..., magus/...), so
# they are fixed adapter identifiers; only the URL/model/limits inside are config-derived.
_OPENCODE_ORCH_PROVIDER = "fool-ds4"
_OPENCODE_WORKER_PROVIDER = "magus"

# --------------------------------------------------------------------------------------
# Emitters: the shell and the opencode adapter consume the loader, not the YAML directly.
# --------------------------------------------------------------------------------------
def _shell_exports(cfg: Config) -> str:
    """Emit `export KEY=value` lines for scripts that opt in with `eval $(python -m core.config --shell)`.

    This replaces sourcing a ball of shell assignments and the hand-maintained export list:
    the values are derived and validated once, in one place, then handed to the shell.
    """
    pairs = {
        "ORCHESTRATOR_URL": cfg.orchestrator.base_url.removesuffix("/v1"),
        "ORCHESTRATOR_MODEL_ID": cfg.orchestrator.model_id,
        "ORCHESTRATOR_API_KEY": cfg.orchestrator.api_key,
        "ORCHESTRATOR_CONTEXT": cfg.orchestrator.context,
        "ORCHESTRATOR_MAX_OUTPUT": cfg.orchestrator.max_output,
        "WORKER_URL": cfg.worker.base_url.removesuffix("/v1"),
        "WORKER_MODEL_ID": cfg.worker.model_id,
        "WORKER_API_KEY": cfg.worker.api_key,
        "WORKER_MAX_OUTPUT": cfg.worker.max_output,
        "WORKER_PARALLEL": cfg.worker_parallel,
        "WORKER_CTX_PER_SLOT": cfg.worker_ctx_per_slot,
        "WORKER_KV": cfg.serving_worker.kv,
        "WORKER_SPLIT_MODE": cfg.serving_worker.split_mode,
        "WORKER_TENSOR_SPLIT": cfg.serving_worker.tensor_split,
        "WORKER_REASONING": cfg.serving_worker.reasoning,
        "WORKER_TEMP": cfg.serving_worker.temp,
        "WORKER_TOP_P": cfg.serving_worker.top_p,
        "WORKER_TOP_K": cfg.serving_worker.top_k,
        "WORKER_REPO": cfg.weights.repo,
        "WORKER_QUANT": cfg.weights.quant,
        "WORKER_FILE": cfg.weights.file,
        "WORKER_QUANTS": " ".join(cfg.weights.candidates),
        "WINDOW_INPUT_TOKENS": cfg.window_input_tokens,
        "DECODE_HEADROOM": cfg.decode_headroom,
        "WORKER_INPUT_TOKENS": cfg.worker_input_tokens,
        "WORKER_DECODE_HEADROOM": cfg.worker_decode_headroom,
        "WORKER_KEEP_RECENT": cfg.worker_keep_recent,
        "WORKER_TOOL_RESULT_CAP": cfg.worker_tool_result_cap,
        "MEMORY_DB": cfg.memory_db,
        "MEMORY_ENABLED": "1" if cfg.memory_enabled else "0",
        "REDIS_URL": cfg.redis_url,
        "SCRATCH_DIR": cfg.scratch_dir,
    }
    lines = [f"export {k}={_sh_quote(str(v))}" for k, v in pairs.items()]
    return "\n".join(lines)


def _env_exports(cfg: Config) -> str:
    """Everything the ops scripts need: the method's runtime config PLUS the rig's deploy vars.

    Scripts opt in with `eval "$(python3 -m core.config --env)"`, replacing the retired
    config.sh entirely. One loader, one source, no shell config file.
    """
    d = cfg.deploy
    dep_pairs = {
        "FOOL_HOST": d.fool_host,
        "FOOL_PORT": d.fool_port,
        "WORKER_PORT": d.worker_port,
        "LAN_PREFIX": d.lan_prefix,
        "NAS_MODELS": d.nas_models,
        "LOCAL_MODELS": d.local_models,
        "LLAMA_SERVER": d.llama_server,
        "LOCAL_MIN_FREE_GIB": d.local_min_free_gib,
        "NAS_MIN_FREE_GIB": d.nas_min_free_gib,
        "WORKER_UNIT": d.worker_unit,
        "REDIS_CONTAINER": d.redis_container,
        "REDIS_IMAGE": d.redis_image,
        "SPARK_REMOTE_URL": d.spark_remote_url,
        "FOOL_SPARK_DIR": d.spark_fool_dir,
        "FOOL_ABLATE": d.fool_ablate,
        "FOOL_EFFORT": d.fool_effort,
        # derived rig paths the scripts use
        "NAS_WORKER_DIR": f"{d.nas_models}/Qwen3.8-27B-OBLITERATED",
        "LOCAL_WORKER_DIR": f"{d.local_models}/qwen3.8-27b-obliterated",
        "FOOL_HF_CACHE": f"{d.spark_fool_dir}/hf-hub",
        "NAS_DEEPSEEK_ARCHIVE": f"{d.nas_models}/deepseek-v4-flash-spark-hfcache",
        "SPARK_DIR_LOCAL": str(_ROOT / "spark"),
        "OPENCODE_PROJECT_DIR": str(_ROOT),
        # derived / operational values the scripts read
        "REDIS_PORT": _redis_port(cfg.redis_url),
        "WORKER_MODEL_PATH": f"{d.local_models}/qwen3.8-27b-obliterated/{cfg.weights.file}",
        "WORKER_BASE_PATH": f"{d.local_models}/qwen3.8-27b/Qwen3.8-27B-IQ4_XS.gguf",
        "FOOL_STARTUP_WAIT": os.environ.get("FOOL_STARTUP_WAIT", "3600"),
        "BENCH_DIR": os.environ.get("BENCH_DIR", "/tmp/fools-trick/bench"),
        "BENCH_SHEETS": os.environ.get("BENCH_SHEETS", "0"),
        "BENCH_SHARE_WITH": os.environ.get("BENCH_SHARE_WITH", ""),
    }
    lines = [_shell_exports(cfg)]
    lines += [f"export {k}={_sh_quote(str(v))}" for k, v in dep_pairs.items()]
    return "\n".join(lines)


def _redis_port(redis_url: str) -> int:
    from urllib.parse import urlparse
    return urlparse(redis_url).port or 6379


def _sh_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def _as_dict(cfg: Config) -> dict:
    d = asdict(cfg)
    d["weights"]["candidates"] = list(cfg.weights.candidates)
    return d


def sync_worker_agent_models(cfg: Config) -> None:
    """Retarget the worker subagents' .md frontmatter model to the config-resolved worker model.

    opencode merges opencode.json's agent block with .opencode/agents/<name>.md, and the .md
    frontmatter model WINS (verified). So a base-json retarget cannot reach the worker agents; the
    only canonical sync is to rewrite the frontmatter from the resolved worker model. Without this,
    flipping deploy.yaml's worker backend leaves the workers pinned to a model the provider no
    longer serves (the exact stale-pin failure mode the retarget was built to kill).
    """
    wk_ref = f"{_OPENCODE_WORKER_PROVIDER}/{cfg.worker.model_id}"
    agents_dir = _ROOT / ".opencode" / "agents"
    if not agents_dir.is_dir():
        return
    import re
    for md in agents_dir.glob("*.md"):
        text = md.read_text()
        if not text.startswith("---"):
            continue
        # only retarget agents whose frontmatter model already uses the worker provider
        m = re.search(r"^model:\s*(" + re.escape(_OPENCODE_WORKER_PROVIDER) + r"/\S+)\s*$", text, re.M)
        if m and m.group(1) != wk_ref:
            md.write_text(text[: m.start(1)] + wk_ref + text[m.end(1):])


def render_opencode(cfg: Config, base: dict) -> dict:
    """Inject the config-derived provider block + model refs into the static opencode base.

    opencode reads static JSON at startup and cannot call this loader inline, so opencode.json
    is a GENERATED artifact: config.yaml stays the single source, `make config` writes the file.
    Everything opencode-specific (agents, permissions, compaction) lives in opencode.base.json
    and is passed through untouched.
    """
    out = dict(base)
    orch, wk = cfg.orchestrator, cfg.worker
    # {env:NAME} keeps the secret out of the generated file; opencode resolves it at runtime.
    orch_key = f"{{env:{orch.api_key_env}}}" if orch.api_key_env else orch.api_key
    wk_key = f"{{env:{wk.api_key_env}}}" if wk.api_key_env else wk.api_key
    out["provider"] = {
        _OPENCODE_ORCH_PROVIDER: {
            "npm": "@ai-sdk/openai-compatible",
            "name": "orchestrator",
            "options": {"baseURL": orch.base_url, "apiKey": orch_key, "timeout": False},
            "models": {
                orch.model_id: {
                    "name": orch.model_id,
                    "tool_call": True,
                    "reasoning": True,
                    "limit": {"context": orch.context, "output": orch.max_output},
                }
            },
        },
        _OPENCODE_WORKER_PROVIDER: {
            "npm": "@ai-sdk/openai-compatible",
            "name": f"worker (x{cfg.worker_parallel})",
            "options": {"baseURL": wk.base_url, "apiKey": wk_key, "timeout": False},
            "models": {
                wk.model_id: {
                    "name": f"{wk.model_id} {cfg.weights.quant}",
                    "tool_call": True,
                    "attachment": True,
                    "reasoning": True,
                    "limit": {"context": cfg.worker_ctx_per_slot, "output": wk.max_output},
                }
            },
        },
    }
    out["model"] = f"{_OPENCODE_ORCH_PROVIDER}/{orch.model_id}"
    out["small_model"] = f"{_OPENCODE_WORKER_PROVIDER}/{wk.model_id}"

    # Retarget agents by provider key so their model tracks deploy.yaml instead of a stale base pin.
    orch_ref = f"{_OPENCODE_ORCH_PROVIDER}/{orch.model_id}"
    wk_ref = f"{_OPENCODE_WORKER_PROVIDER}/{wk.model_id}"
    for agent in out.get("agent", {}).values():
        m = agent.get("model", "")
        if m.startswith(f"{_OPENCODE_ORCH_PROVIDER}/"):
            agent["model"] = orch_ref
        elif m.startswith(f"{_OPENCODE_WORKER_PROVIDER}/"):
            agent["model"] = wk_ref

    # Absolutize file refs so the config resolves from any cwd (launched via OPENCODE_CONFIG).
    root = str(_ROOT)
    out["instructions"] = [
        p if os.path.isabs(p) else os.path.join(root, p) for p in out.get("instructions", [])
    ]
    for agent in out.get("agent", {}).values():
        pr = agent.get("prompt", "")
        if pr.startswith("{file:./"):
            agent["prompt"] = "{file:" + os.path.join(root, pr[len("{file:./"):-1]) + "}"
    return out
