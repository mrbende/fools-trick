"""The one configuration loader. Single source of truth for the whole system.

Reads config.yaml (the method) and deploy.yaml (the rig), applies code-owned defaults for
anything not set, and validates the design invariants. The shell and the opencode adapter both
ask this loader (`python -m core.config --shell/--env/--json/--opencode`), so nothing is kept in
sync by hand. Precedence: code defaults < YAML < environment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

import yaml

_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------------------
# Typed config
# --------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Endpoint:
    name: str
    base_url: str          # full OpenAI-compatible base, e.g. http://host:port/v1
    model_id: str
    api_key: str = "dummy"
    context: int = 32768
    max_output: int = 8192
    api_key_env: str = ""  # when set, render {env:NAME} instead of the literal key


@dataclass(frozen=True)
class ServingWorker:
    """GPU serving physics for the worker. Code-owned; overridable but not in the edit surface.

    The reasoning for every value (why -sm layer, why q8_0 KV, why no MTP) is in
    docs/review.md and worker/serve.sh; it is deliberately not duplicated here as prose.
    """

    kv: str = "q8_0"
    split_mode: str = "layer"
    tensor_split: str = "10,12"
    reasoning: str = "low"
    temp: float = 0.6
    top_p: float = 0.95
    top_k: int = 20
    llama_server: str = field(default_factory=lambda: os.path.expanduser("~/.local/bin/llama-server"))


@dataclass(frozen=True)
class Weights:
    repo: str
    quant: str
    candidates: tuple[str, ...]
    file: str = ""  # derived from repo/quant if empty


@dataclass(frozen=True)
class Deploy:
    """Rig-specific deployment values (deploy.yaml). Read by the ops scripts, not the method."""

    fool_host: str = "orchestrator"
    fool_port: int = 8888
    worker_port: int = 8898
    lan_prefix: str = "10.0.0."
    nas_models: str = "/mnt/nas/models"
    local_models: str = field(default_factory=lambda: os.path.expanduser("~/Models"))
    llama_server: str = field(default_factory=lambda: os.path.expanduser("~/.local/bin/llama-server"))
    local_min_free_gib: int = 24
    nas_min_free_gib: int = 120
    worker_unit: str = "fools-worker"
    redis_container: str = "fools-redis"
    redis_image: str = "redis:7-alpine"
    spark_remote_url: str = "https://github.com/mrbende/DeepSeek-v4-Flash-One-DGX-Spark.git"
    spark_fool_dir: str = field(default_factory=lambda: os.path.expanduser("~/Recipes/fools-trick-spark"))
    fool_ablate: int = 1
    fool_effort: str = "high"


@dataclass(frozen=True)
class Config:
    orchestrator: Endpoint
    worker: Endpoint
    worker_parallel: int
    worker_ctx_per_slot: int

    window_input_tokens: int
    decode_headroom: int
    worker_input_tokens: int
    worker_decode_headroom: int
    worker_keep_recent: int

    memory_db: str
    redis_url: str
    scratch_dir: str
    memory_enabled: bool
    worker_tool_result_cap: int

    serving_worker: ServingWorker
    weights: Weights
    deploy: Deploy

    library_api_url: str = "http://127.0.0.1:8080"
    library_embed_url: str = "http://fool:8001"
    library_inbox_dir: str = "/mnt/empress/library/inbox"

    primaries: tuple[str, ...] = ("build", "plan")
    workers: tuple[str, ...] = ("explore", "general", "reviewer")

    def validate(self) -> None:
        assert self.window_input_tokens + self.decode_headroom < self.orchestrator.context, (
            "orchestrator window+headroom must fit its context"
        )
        assert self.decode_headroom >= self.orchestrator.max_output, (
            "decode headroom must cover the orchestrator output limit"
        )
        assert (
            self.worker_input_tokens + self.worker_decode_headroom <= self.worker_ctx_per_slot
        ), "worker input+headroom must fit a slot"
        assert self.worker_parallel >= 1
        assert self.worker_ctx_per_slot >= 1


# --------------------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------------------
def _read_yaml(path: Path) -> dict:
    if not path.is_file():
        return {}
    with path.open() as fh:
        return yaml.safe_load(fh) or {}


def _base_url(url: str) -> str:
    """Normalize an endpoint to an OpenAI-compatible base ending in /v1."""
    url = str(url).rstrip("/")
    return url if url.endswith("/v1") else url + "/v1"


def _get(d: dict, path: str, env: str, default: Any) -> Any:
    """Resolve a value: env override wins, then the merged YAML at dotted `path`, then default."""
    if env in os.environ:
        raw = os.environ[env]
        if isinstance(default, bool):
            return raw not in ("0", "false", "False", "")
        if isinstance(default, int):
            try:
                return int(raw)
            except ValueError:
                return default
        return raw
    node: Any = d
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def _load_backend(root: Path, tier: str, name: str) -> dict:
    """Read deploy/<tier>/<name>.yaml. A backend def supplies the endpoint + serving physics."""
    path = root / "deploy" / tier / f"{name}.yaml"
    if not path.is_file():
        raise FileNotFoundError(
            f"deploy.yaml selects {tier}: {name}, but deploy/{tier}/{name}.yaml does not exist"
        )
    return _read_yaml(path)


def _endpoint(b: dict, tier: str, env_url: str, env_model: str, env_key: str) -> Endpoint:
    """Build an Endpoint from a backend def, honoring env overrides."""
    model = _get(b, "model", env_model, b.get("model", ""))
    key_env = b.get("api_key_env", "") or ""
    api_key = b.get("api_key") or os.environ.get(key_env or "\0", "") or \
        os.environ.get(env_key, "dummy")
    return Endpoint(
        name=model,
        base_url=_base_url(_get(b, "base_url", env_url, b.get("base_url", ""))),
        model_id=model,
        api_key=api_key,
        context=int(b.get("context", b.get("ctx_per_slot", 32768))),
        max_output=int(b.get("max_output", 8192)),
        api_key_env=key_env,
    )


def load(config_dir: Optional[Path] = None) -> Config:
    root = config_dir or _ROOT
    merged = _read_yaml(root / "config.yaml")     # the method: behavior knobs
    dep = _read_yaml(root / "deploy.yaml")         # the rig: backend pointers + shared topology

    # Backend selection: deploy.yaml points at deploy/<tier>/<name>.yaml for each tier.
    orch_backend = _load_backend(root, "orchestrator", _get(dep, "orchestrator", "", "spark-gb10"))
    worker_backend = _load_backend(root, "worker", _get(dep, "worker", "", "3080ti-qwen27"))

    orchestrator = _endpoint(orch_backend, "orchestrator", "ORCHESTRATOR_URL", "ORCHESTRATOR_MODEL_ID", "ORCHESTRATOR_API_KEY")
    worker = _endpoint(worker_backend, "worker", "WORKER_URL", "WORKER_MODEL_ID", "WORKER_API_KEY")

    # Worker serving physics + concurrency come from the selected worker backend.
    serve = worker_backend.get("serve", {})
    def sw(k: str, env: str, default):  # noqa: E731 -- small local getter over the backend's serve block
        return _get(serve, k, env, default)

    w_quant = _get(worker_backend, "serve.quant", "WORKER_QUANT",
                   worker_backend.get("serve", {}).get("quant", "i1-Q4_K_S"))
    w_repo = _get(worker_backend, "serve.repo", "WORKER_REPO",
                  worker_backend.get("serve", {}).get("repo", ""))
    w_candidates = worker_backend.get("serve", {}).get("candidates", [w_quant])
    if isinstance(w_candidates, str):
        w_candidates = w_candidates.split()
    weights = Weights(
        repo=w_repo,
        quant=w_quant,
        candidates=tuple(w_candidates),
        file=worker_backend.get("serve", {}).get("file", f"{worker.model_id}.{w_quant}.gguf"),
    )

    serving_worker = ServingWorker(
        kv=sw("kv", "WORKER_KV", "q8_0"),
        split_mode=sw("split_mode", "WORKER_SPLIT_MODE", "layer"),
        tensor_split=sw("tensor_split", "WORKER_TENSOR_SPLIT", "10,12"),
        reasoning=sw("reasoning", "WORKER_REASONING", "low"),
        temp=float(sw("temp", "WORKER_TEMP", 0.6)),
        top_p=float(sw("top_p", "WORKER_TOP_P", 0.95)),
        top_k=int(sw("top_k", "WORKER_TOP_K", 20)),
    )

    # env overrides the backend file (CI / one-off experiments).
    worker_parallel = int(os.environ.get("WORKER_PARALLEL", worker_backend.get("parallel", 3)))
    worker_ctx = int(os.environ.get("WORKER_CTX_PER_SLOT", worker_backend.get("ctx_per_slot", worker.context)))

    # Rig-shared topology (from deploy.yaml), backend-agnostic.
    deploy = Deploy(
        fool_host=_get(orch_backend, "host", "FOOL_HOST", "orchestrator"),
        fool_port=int(_get(orch_backend, "port", "FOOL_PORT", 8888)),
        worker_port=int(_get(worker_backend, "port", "WORKER_PORT", 8898)),
        lan_prefix=_get(dep, "topology.lan_prefix", "LAN_PREFIX", "10.0.0."),
        nas_models=_get(dep, "weights.nas_models", "NAS_MODELS", "/mnt/nas/models"),
        local_models=os.path.expanduser(_get(dep, "weights.local_models", "LOCAL_MODELS", "~/Models")),
        llama_server=os.path.expanduser(_get(dep, "weights.llama_server", "LLAMA_SERVER", "~/.local/bin/llama-server")),
        local_min_free_gib=int(_get(dep, "weights.local_min_free_gib", "LOCAL_MIN_FREE_GIB", 24)),
        nas_min_free_gib=int(_get(dep, "weights.nas_min_free_gib", "NAS_MIN_FREE_GIB", 120)),
        worker_unit=_get(worker_backend, "unit", "WORKER_UNIT", "fools-worker"),
        redis_container=_get(dep, "redis.container", "REDIS_CONTAINER", "fools-redis"),
        redis_image=_get(dep, "redis.image", "REDIS_IMAGE", "redis:7-alpine"),
        spark_remote_url=_get(orch_backend, "remote_url", "SPARK_REMOTE_URL",
                              "https://github.com/mrbende/DeepSeek-v4-Flash-One-DGX-Spark.git"),
        spark_fool_dir=os.path.expanduser(_get(orch_backend, "fool_dir", "FOOL_SPARK_DIR", "~/Recipes/fools-trick-spark")),
        fool_ablate=int(_get(orch_backend, "ablate", "FOOL_ABLATE", 1)),
        fool_effort=_get(orch_backend, "effort", "FOOL_EFFORT", "high"),
    )

    cfg = Config(
        orchestrator=orchestrator,
        worker=worker,
        worker_parallel=worker_parallel,
        worker_ctx_per_slot=worker_ctx,
        window_input_tokens=_get(merged, "memory.window_input_tokens", "WINDOW_INPUT_TOKENS", 160000),
        decode_headroom=_get(merged, "memory.decode_headroom", "DECODE_HEADROOM", 96000),
        worker_input_tokens=_get(merged, "memory.worker_input_tokens", "WORKER_INPUT_TOKENS", 26000),
        worker_decode_headroom=_get(merged, "memory.worker_decode_headroom", "WORKER_DECODE_HEADROOM", 16000),
        worker_keep_recent=_get(merged, "memory.worker_keep_recent", "WORKER_KEEP_RECENT", 3),
        # Default: half the worker input budget, so one read can never alone approach the slot.
        worker_tool_result_cap=_get(merged, "memory.worker_tool_result_cap", "WORKER_TOOL_RESULT_CAP", 8000),
        memory_db=_get(merged, "memory.db", "MEMORY_DB",
                       os.path.expanduser("~/.local/share/fools-trick/memory.db")),
        redis_url=_get(merged, "memory.redis_url", "REDIS_URL", "redis://127.0.0.1:6379"),
        scratch_dir=_get(merged, "memory.scratch_dir", "SCRATCH_DIR", "/tmp/fools-trick/scratch"),
        memory_enabled=_get(merged, "memory.enabled", "MEMORY_ENABLED", True),
        library_api_url=_get(merged, "library.api_url", "LIBRARY_API_URL", "http://127.0.0.1:8080"),
        library_embed_url=_get(merged, "library.embed_url", "LIBRARY_EMBED_URL", "http://fool:8001"),
        library_inbox_dir=_get(merged, "library.inbox_dir", "LIBRARY_INBOX_DIR", "/mnt/empress/library/inbox"),
        serving_worker=serving_worker,
        weights=weights,
        deploy=deploy,
        primaries=tuple(_get(merged, "agents.primaries", "", ["build", "plan"])),
        workers=tuple(_get(merged, "agents.workers", "", ["explore", "general", "reviewer"])),
    )
    return cfg


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


# Stable opencode provider keys. The agent defs reference these (fool-ds4/..., magus/...), so
# they are fixed adapter identifiers; only the URL/model/limits inside are config-derived.
_OPENCODE_ORCH_PROVIDER = "fool-ds4"
_OPENCODE_WORKER_PROVIDER = "magus"


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


def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(prog="core.config")
    parser.add_argument("--shell", action="store_true", help="emit method-config export lines")
    parser.add_argument("--env", action="store_true", help="emit method + deploy exports (for ops scripts)")
    parser.add_argument("--json", action="store_true", help="emit the resolved config as JSON")
    parser.add_argument("--opencode", action="store_true",
                        help="render opencode.json from opencode.base.json + the config")
    parser.add_argument("--check", action="store_true", help="validate and print OK")
    args = parser.parse_args(argv)

    cfg = load()
    cfg.validate()
    if args.opencode:
        sync_worker_agent_models(cfg)   # keep .md worker-model frontmatter in step with deploy.yaml
        base = json.loads((_ROOT / "opencode.base.json").read_text())
        print(json.dumps(render_opencode(cfg, base), indent=2))
    elif args.env:
        print(_env_exports(cfg))
    elif args.shell:
        print(_shell_exports(cfg))
    elif args.json:
        print(json.dumps(_as_dict(cfg), indent=2))
    else:
        print("config OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
