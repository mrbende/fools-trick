"""Library tools: query the agent's own corpus (attune-library) and fetch/ingest new documents.

Calls the library's FastAPI surface over the LAN -- the library repo owns the corpus, the ETL,
and the fetch/acquire cascade; this module is a thin client, not a reimplementation. Separation of
concerns: the library is the system of record; the harness is the agent that reads it.

The corpus API serves search/read/query/fetch; embeddings for search are served on fool's GPU
(library-inference-recipe). library_read/query/fetch are pure SQL behind the API and gate on the
API's /health; library_search additionally gates on the embed service.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

import core.config as _cfg
from core.types import ToolContext


def _api(path: str, params: dict | None = None, timeout: float = 30,
         method: str = "GET") -> dict:
    cfg = _cfg.load()
    url = cfg.library_api_url.rstrip("/") + path
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    req = urllib.request.Request(url, method=method, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _err(name: str, msg: str) -> dict:
    return {"title": f"{name} failed", "output": msg, "metadata": {}}


def library_search(args: dict, ctx: ToolContext, log) -> dict:
    """Hybrid content search over the library (semantic + lexical, fused). Returns hits with
    canonical_id + chunk_index + snippet. Embeds the query on magus's CPU service, so it works
    alongside the agent; if the API is down the registry gates this tool before it is called."""
    q = str(args.get("query", "")).strip()
    if not q:
        return _err("library_search", "empty query")
    try:
        d = _api("/search", {"q": q, "k": int(args.get("k") or 10),
                             "collection": args.get("collection")})
    except Exception as e:
        return _err("library_search",
                    f"the library API is unreachable ({e}). For a metadata query (authors/years/"
                    f"journals) try library_query; to read a known document, library_read.")
    hits = d if isinstance(d, list) else d.get("results", [])
    if not hits:
        return {"title": f"search: {q}", "output": "(no results)", "metadata": {"hits": 0}}
    lines = []
    for h in hits:
        cid = h.get("canonical_id", "?")
        idx = h.get("chunk_index", "?")
        lines.append(f"- [{h.get('score', 0):.3f}] {h.get('title') or cid}  ({cid}#{idx})  "
                     f"{(h.get('snippet') or '')[:180]}")
    return {"title": f"search: {q} ({len(hits)})", "output": "\n".join(lines),
            "metadata": {"hits": len(hits)}}


def library_read(args: dict, ctx: ToolContext, log) -> dict:
    """Read a document from the library: a chunk window around a hit (around + radius), or the
    whole document reconstructed. Pure SQL -- always available, no embedding service needed."""
    cid = str(args.get("canonical_id", "")).strip()
    if not cid:
        return _err("library_read", "no canonical_id")
    try:
        if args.get("around") is not None:
            d = _api(f"/documents/{urllib.parse.quote(cid)}/chunks",
                     {"around": int(args["around"]), "radius": int(args.get("radius") or 1)})
        else:
            d = _api(f"/documents/{urllib.parse.quote(cid)}")
    except Exception as e:
        return _err("library_read", f"document not found or the API is down: {e}")
    if "text" in d:
        return {"title": f"read {cid}", "output": d["text"], "metadata": {"canonical_id": cid}}
    return {"title": f"document {cid}", "output": json.dumps(d, indent=1),
            "metadata": {"canonical_id": cid}}


def library_query(args: dict, ctx: ToolContext, log) -> dict:
    """Metadata query over the documents table (zeta's document_library_query pattern): filter by
    author/title/year/collection/lang/doi, or aggregate with count_by. Pure SQL over the library's
    /query endpoint -- always available, no inference service needed. Give a structured filter."""
    try:
        d = _api("/query", _clean(args))
    except Exception as e:
        return _err("library_query", f"the library /query endpoint is unreachable: {e}")
    rows = d.get("rows", [])
    if not rows:
        return {"title": "query", "output": "(no rows)", "metadata": {"rows": 0}}
    if "aggregate" in d:
        lines = [f"  {r.get('k')}: {r.get('n')}" for r in rows]
    else:
        lines = [f"- [{r.get('year') or '?'}] {r.get('title') or r.get('canonical_id')}  "
                 f"({r.get('authors') or '?'})  [{r.get('collection') or '?'}]" for r in rows]
    return {"title": f"query ({len(rows)})", "output": "\n".join(lines),
            "metadata": {"rows": len(rows), "aggregate": d.get("aggregate")}}


def _clean(args: dict) -> dict:
    return {k: v for k, v in args.items() if v is not None and v != ""}


def library_fetch(args: dict, ctx: ToolContext, log) -> dict:
    """Acquire a document INTO the permanent library by reference (doi/arxiv/url/title): the
    library resolves it, downloads from the open-access cascade, and queues it for the normal
    ETL (convert, OCR if needed, index). This is PERMANENCE, not reading -- to read a paper once
    without ingesting it, use pdf_read. Slow by nature (external sources); the reply is the
    acquire report, including 'not found' when nothing open carries it."""
    params = _clean({k: args.get(k) for k in ("url", "doi", "arxiv", "title", "authors",
                                              "collection")})
    if not any(params.get(k) for k in ("url", "doi", "arxiv", "title")):
        return _err("library_fetch", "one of url, doi, arxiv, title is required")
    try:
        d = _api("/fetch", params, timeout=180, method="POST")
    except Exception as e:
        return _err("library_fetch", f"the library /fetch endpoint is unreachable: {e}")
    if d.get("ok"):
        out = (f"acquired via {d.get('method')}: {d.get('path')}\n"
               f"queued for ETL: {d.get('queued')} -- searchable after the next pipeline pass.")
    else:
        out = (f"not acquired: {d.get('reason') or 'nothing open-access carries this reference'}. "
               f"identifiers tried: {d.get('identifiers')}")
    return {"title": f"fetch {'ok' if d.get('ok') else 'miss'}", "output": out,
            "metadata": {"ok": bool(d.get("ok")), "path": d.get("path")}}
