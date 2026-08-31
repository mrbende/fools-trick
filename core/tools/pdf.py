"""pdf_read: download a PDF from the web and read it directly, in-context.

The ephemeral counterpart to library_fetch: this never touches the library. The PDF lands in the
per-task scratch dir, pdftotext (poppler) extracts beside it, and the tool returns the text path
plus a first window -- the agent pages the rest with the ordinary read tool. A scanned PDF has no
text layer; when extraction yields nothing, the OCR pipeline is the library's job (library_fetch),
not this tool's.
"""

from __future__ import annotations

import os
import re
import subprocess
import urllib.request

import core.config as _cfg
from core.scratch import task_dir
from core.types import ToolContext

MAX_BYTES = 100 * 1024 * 1024
FIRST_WINDOW = 6000


def _err(msg: str) -> dict:
    return {"title": "pdf_read failed", "output": msg, "metadata": {}}


def pdf_read(args: dict, ctx: ToolContext, log) -> dict:
    url = str(args.get("url", "")).strip()
    if not url:
        return _err("no url")
    cfg = _cfg.load()
    d = task_dir(cfg.scratch_dir, log.resolve_thread(ctx.sessionID or ""))
    name = os.path.basename(re.sub(r"[?#].*$", "", url).rstrip("/")) or "document.pdf"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    dest = os.path.join(d, name)

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "fools-trick/pdf_read"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read(MAX_BYTES + 1)
    except Exception as e:
        return _err(f"download failed: {e}")
    if len(data) > MAX_BYTES:
        return _err(f"over {MAX_BYTES // (1024*1024)}MB -- too large for the direct path")
    if not data.startswith(b"%PDF-"):
        return _err("not a PDF (no %PDF- magic). For an HTML landing page, browse_open it; "
                    "for a publisher page with a PDF link, pass the PDF url here.")
    with open(dest, "wb") as fh:
        fh.write(data)

    txt = dest[:-4] + ".txt"
    p = subprocess.run(["pdftotext", "-layout", dest, txt], capture_output=True, text=True)
    if p.returncode != 0:
        return _err(f"pdftotext failed: {p.stderr.strip()[:200]}")
    with open(txt, errors="replace") as fh:
        text = fh.read()
    # A scanned PDF extracts to nothing but form feeds; any real text layer leaves characters.
    if not text.strip(" \t\n\r\f"):
        return _err("no text layer -- this is a scanned PDF. OCR is the library ETL's job: "
                    "library_fetch(url=...) acquires it permanently with OCR, or find another copy.")
    pages = len(text.rstrip("\f").split("\f"))
    return {
        "title": f"pdf {name} ({pages}p, {len(text)} chars)",
        "output": f"extracted: {txt}\npages: {pages}  chars: {len(text)}\n"
                  f"page through the rest with the read tool on that path.\n\n"
                  f"{text[:FIRST_WINDOW]}",
        "metadata": {"path": txt, "pdf": dest, "pages": pages, "chars": len(text)},
    }
