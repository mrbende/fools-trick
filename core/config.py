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


def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    import json

    from core import config_emit as emit

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
        emit.sync_worker_agent_models(cfg)   # keep .md worker-model frontmatter in step with deploy.yaml
        base = json.loads((_ROOT / "opencode.base.json").read_text())
        print(json.dumps(emit.render_opencode(cfg, base), indent=2))
    elif args.env:
        print(emit._env_exports(cfg))
    elif args.shell:
        print(emit._shell_exports(cfg))
    elif args.json:
        print(json.dumps(emit._as_dict(cfg), indent=2))
    else:
        print("config OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
