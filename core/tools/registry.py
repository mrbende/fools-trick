"""Toolset registry: the named tool surface + health.

A tool whose BACKEND is down should error cleanly at call time, not hang the worker. Each toolset
declares its tools and a health check; the adapter consults it before executing. This is the
hermes-agent pattern (named toolsets + requirement validation) adapted to our two backends (the
Python core over subprocess, and the camofox browser server).
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Toolset:
    name: str
    tools: tuple[str, ...]
    # health(): None if healthy, else a one-line reason it's down.
    health: object = field(default=None, repr=False)


def _core_health() -> str | None:
    """The Python core tools are healthy if the CLI answers a no-op (python + core imports work)."""
    try:
        p = subprocess.run(
            ["python3", "-m", "core.tools.cli", "drain"],
            capture_output=True, text=True, timeout=10,
        )
        return None if p.returncode == 0 else f"core CLI exited {p.returncode}"
    except (OSError, subprocess.SubprocessError) as e:
        return f"core unreachable: {e}"


def _worker_health() -> str | None:
    """delegate_cheap is healthy if the worker endpoint answers /v1/models (with auth for cloud)."""
    import urllib.request
    import core.config as _cfg
    wk = _cfg.load().worker
    url = wk.base_url.rstrip("/") + "/models"
    req = urllib.request.Request(url, headers={"User-Agent": "fools-trick/1.0"})
    if wk.api_key and wk.api_key != "dummy":
        req.add_header("Authorization", f"Bearer {wk.api_key}")
    try:
        with urllib.request.urlopen(req, timeout=2.5):
            return None
    except Exception as e:
        return f"worker unreachable at {url}: {e}"


def _web_health() -> str | None:
    """The web tools are healthy if the camofox server answers /tabs."""
    import urllib.request
    import json as _json
    import os
    base = os.environ.get("CAMOFOX_URL", "http://localhost:9377")
    try:
        req = urllib.request.Request(
            f"{base}/tabs", method="POST",
            data=_json.dumps({"userId": "fools-trick", "sessionKey": "health"}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=2.5):
            return None
    except Exception as e:
        return f"camofox server down at {base}: {e}"


def _library_health() -> str | None:
    """SQL-backed library tools (read/query/fetch) are healthy if the attune-library API is up."""
    import json as _json
    import urllib.request
    import core.config as _cfg
    url = _cfg.load().library_api_url.rstrip("/") + "/health"
    try:
        with urllib.request.urlopen(url, timeout=2.5) as r:
            d = _json.loads(r.read())
        return None if d.get("status") == "ok" and d.get("db") == "up" else f"library API degraded: {d}"
    except Exception as e:
        return f"library API down at {url}: {e}"


def _library_search_health() -> str | None:
    """library_search is healthy only if the corpus API can actually search. Probe a real (empty)
    /search, not the embed service's /health -- the API embeds via the embed service internally, so a
    503 from /search is the truthful signal that the search path (incl. embeddings) is down."""
    api = _library_health()
    if api is not None:
        return api
    import urllib.request
    import urllib.error
    import core.config as _cfg
    url = _cfg.load().library_api_url.rstrip("/") + "/search?q=healthcheck&k=1"
    try:
        with urllib.request.urlopen(url, timeout=4):
            return None
    except urllib.error.HTTPError as e:
        if e.code == 503:
            return "library_search unavailable (embedding path down): HTTP 503 from /search"
        return None  # a non-503 (e.g. 4xx on the probe) still means the search path is reachable
    except Exception as e:
        return f"library_search unreachable at {url}: {e}"


def _pdf_health() -> str | None:
    """pdf_read is healthy if poppler's pdftotext is on PATH (its one real dependency)."""
    import shutil
    return None if shutil.which("pdftotext") else "pdftotext (poppler) not on PATH"


TOOLSETS = {
    "memory": Toolset(
        name="memory",
        tools=("memory_write", "memory_search", "recall", "note", "promote", "record_contract", "report", "incident", "trace", "tripcheck"),
        health=_core_health,
    ),
    "delegate": Toolset(
        name="delegate",
        tools=("delegate_cheap",),
        health=_worker_health,
    ),
    "scratch": Toolset(
        name="scratch",
        tools=("scratch_write",),
        health=_core_health,
    ),
    "web": Toolset(
        name="web",
        tools=("browse_open", "web_search", "browse_click", "browse_type"),
        health=_web_health,
    ),
    "library": Toolset(
        name="library",
        tools=("library_read", "library_query", "library_fetch"),
        health=_library_health,
    ),
    "library_search": Toolset(
        name="library_search",
        tools=("library_search", "library_prior"),
        health=_library_search_health,
    ),
    "pdf": Toolset(
        name="pdf",
        tools=("pdf_read",),
        health=_pdf_health,
    ),
}


def toolset_for(tool: str) -> str | None:
    for ts in TOOLSETS.values():
        if tool in ts.tools:
            return ts.name
    return None


def health() -> dict:
    """{toolset: ok-or-reason}. The adapter reads this to gate tool availability."""
    out = {}
    for name, ts in TOOLSETS.items():
        reason = ts.health() if ts.health else None
        out[name] = {"ok": reason is None, "reason": reason, "tools": list(ts.tools)}
    return out
