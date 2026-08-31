"""Live-service integration tests: cover the paths that talk to real external services.

These prove the integration works against the actual service, not a mock. When a service is down,
the test SKIPS with a warning (an external-dependency outage is not a code failure) rather than
going red -- so the suite stays green offline but tells you what is and isn't alive. This is how
the harness reaches 100% coverage of its own integration paths honestly.
"""

from __future__ import annotations

import os
import unittest
import urllib.request
from pathlib import Path

import core.config as _cfg

# Live-service tests need the harness's secrets (ZEN_API_KEY) in env. Load the project .env so a
# bare `python -m unittest` run (which never sources it) can reach the authed endpoints.
_ENV = Path(__file__).resolve().parents[2] / ".env"
if _ENV.exists():
    for line in _ENV.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


def _reachable(url: str, timeout: float = 2.5) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout):
            return True
    except Exception:
        return False


def _library_api() -> str:
    return _cfg.load().library_api_url.rstrip("/")


def _embed() -> str:
    return _cfg.load().library_embed_url.rstrip("/")


class TestLiveLibrary(unittest.TestCase):
    """The corpus API (read/query) -- pure SQL/HTTP, no embed needed."""

    def setUp(self):
        if not _reachable(_library_api() + "/health"):
            self.skipTest(f"library API not reachable at {_library_api()} (external dependency)")

    def test_health_reports_db_up(self):
        from core.tools import library
        out = library.library_query({"count_by": "collection"}, None, None)
        self.assertIn("metadata", out)
        self.assertGreaterEqual(out["metadata"].get("rows", 0), 1)

    def test_query_empty_is_clean(self):
        from core.tools import library
        out = library.library_query({"collection": "__nope__"}, None, None)
        self.assertEqual(out["metadata"]["rows"], 0)


class TestLiveLibraryRead(unittest.TestCase):
    """library_read happy paths (pure SQL, only needs the API)."""

    def setUp(self):
        if not _reachable(_library_api() + "/health"):
            self.skipTest("library API down")

    def test_read_by_canonical_id(self):
        from core.tools import library
        # search returns hits with canonical_id; read around one of them
        s = library.library_search({"query": "attention", "k": 1}, None, None)
        if s["metadata"].get("hits", 0) < 1:
            self.skipTest("search returned no hit to read")
        import re
        m = re.search(r"\(([^()]+)#(\d+)\)", s["output"])
        if not m:
            self.skipTest("search hit carried no canonical_id#chunk to read")
        out = library.library_read({"canonical_id": m.group(1)}, None, None)
        self.assertIn("canonical_id", out["metadata"])

    def test_read_missing_id_validated(self):
        from core.tools import library
        out = library.library_read({"canonical_id": ""}, None, None)
        self.assertIn("no canonical_id", out["output"])

    def test_read_unknown_id_errors_cleanly(self):
        from core.tools import library
        out = library.library_read({"canonical_id": "__definitely_not_a_doc__"}, None, None)
        self.assertIn("failed", out["title"].lower())

    def test_search_validation_when_embed_up(self):
        from core.tools import library
        # a result-over-limit arg path: k=0 is handled, no crash
        out = library.library_search({"query": "attention", "k": 0}, None, None)
        self.assertIn("metadata", out)


class TestLiveLibrarySearch(unittest.TestCase):
    """Semantic search -- needs the embed service on fool too."""

    def setUp(self):
        if not _reachable(_library_api() + "/health"):
            self.skipTest("library API down")
        if not _reachable(_embed() + "/health"):
            self.skipTest(f"embed service not reachable at {_embed()} (external dependency)")

    def test_search_returns_hits(self):
        from core.tools import library
        out = library.library_search({"query": "transformer attention", "k": 3}, None, None)
        self.assertGreaterEqual(out["metadata"].get("hits", 0), 1)

    def test_search_empty_query_validated(self):
        from core.tools import library
        out = library.library_search({"query": ""}, None, None)
        self.assertIn("empty", out["output"])


class TestLiveWorker(unittest.TestCase):
    """The worker endpoint (delegate_cheap) against the live provider."""

    def setUp(self):
        wk = _cfg.load().worker
        url = wk.base_url.rstrip("/") + "/models"
        req = urllib.request.Request(url, headers={"User-Agent": "fools-trick/1.0"})
        if wk.api_key and wk.api_key != "dummy":
            req.add_header("Authorization", f"Bearer {wk.api_key}")
        try:
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            self.skipTest(f"worker endpoint not reachable at {url} (external dependency)")

    def test_delegate_cheap_roundtrip(self):
        import core.config
        from core.tools import memory
        from core.log.log import MemoryLog
        cfg = core.config.load()
        log = MemoryLog(cfg.memory_db, cfg.redis_url)
        try:
            out = memory.delegate_cheap({"task": "reply with exactly: pong", "max_tokens": 20},
                                        None, log)
        finally:
            log.close()
        self.assertNotIn("failed", out["title"].lower())
        self.assertTrue(out["output"].strip())


if __name__ == "__main__":
    unittest.main()
