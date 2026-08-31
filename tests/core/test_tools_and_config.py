"""Tool bodies + config + the CLI subprocess boundary. stdlib unittest, no harness on disk."""

import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

from core import config as cfg_mod  # noqa: E402
from core import config_emit as emit_mod  # noqa: E402 -- emitters live here after the config split
from core.log.log import MemoryLog  # noqa: E402
from core.log.thread import identity_resolver  # noqa: E402
from core.tools import memory as tools  # noqa: E402
from core.types import ToolContext  # noqa: E402


class TestToolBodies(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.log = MemoryLog(
            db_path=os.path.join(self._tmp.name, "m.db"),
            redis_url="redis://127.0.0.1:6399",
            resolve_thread=identity_resolver().resolve,
        )
        self.ctx = ToolContext(sessionID="s1", agent="build")

    def tearDown(self):
        self.log.close()
        self._tmp.cleanup()

    def test_write_then_search(self):
        tools.memory_write({"content": "picked q8_0 KV for the hybrid arch"}, self.ctx, self.log)
        out = tools.memory_search({"query": "q8_0 KV"}, self.ctx, self.log)
        self.assertGreaterEqual(out["metadata"]["hits"], 1)
        self.assertIn("q8_0", out["output"])

    def test_recall_by_seq(self):
        tools.memory_write({"content": "anchor fact for recall"}, self.ctx, self.log)
        hit = self.log.search(thread="s1", query="anchor")[0]
        out = tools.recall({"seq": hit.seq}, self.ctx, self.log)
        self.assertIn("anchor fact", out["output"])

    def test_note_records_and_returns_call_id(self):
        scratch = os.path.join(self._tmp.name, "scratch")
        out = tools.note({"finding": "auth lives in session.py:42", "callID": "c7"},
                         self.ctx, self.log, scratch=scratch)
        self.assertEqual(out["metadata"]["call_id"], "c7")
        self.assertTrue(os.path.exists(os.path.join(scratch, "notes-s1.md")))

    def test_promote_persists_findings_and_returns_recallable_seq(self):
        # the escalation path: a worker's distilled findings must survive to the log and be
        # recallable by the returned seq (the typed handoff).
        scratch = os.path.join(self._tmp.name, "scratch")
        tools.note({"finding": "auth lives in session.py:42"}, self.ctx, self.log, scratch=scratch)
        out = tools.promote({"reason": "needs whole-repo context", "status": "blocked"},
                            self.ctx, self.log, scratch=scratch)
        seq = out["metadata"]["seq"]
        self.assertIsNotNone(seq)
        self.assertIn(f"seq={seq}", out["output"])
        # the orchestrator (a different session) recalls the escalation verbatim
        rec = tools.recall({"seq": seq}, ToolContext(sessionID="root", agent="build"), self.log)
        self.assertIn("ESCALATION", rec["output"])
        self.assertIn("auth lives in session.py:42", rec["output"])

    def test_empty_memory_write_is_safe(self):
        out = tools.memory_write({"content": "  "}, self.ctx, self.log)
        self.assertIn("not saved", out["title"])


class TestConfig(unittest.TestCase):
    def test_defaults_validate(self):
        cfg_mod.load().validate()  # must not raise on shipped config.yaml

    def test_yaml_is_source(self):
        # values come from config.yaml and validate; assert properties, not magic numbers that
        # legitimately change when the serving shape is re-tuned (docs/hardware.md).
        cfg = cfg_mod.load()
        self.assertGreaterEqual(cfg.worker_parallel, 1)
        self.assertGreaterEqual(cfg.worker_ctx_per_slot, cfg.worker_input_tokens)
        self.assertTrue(cfg.orchestrator.base_url.endswith("/v1"))
        self.assertTrue(cfg.worker.base_url.endswith("/v1"))
        cfg.validate()  # the window/headroom invariants hold at whatever shape is set

    def test_env_override_wins(self):
        os.environ["WORKER_PARALLEL"] = "7"
        try:
            self.assertEqual(cfg_mod.load().worker_parallel, 7)
        finally:
            del os.environ["WORKER_PARALLEL"]

    def test_serving_physics_come_from_selected_backend(self):
        # the worker's serving physics live in the selected backend def (deploy/worker/<name>.yaml)
        sw = cfg_mod.load().serving_worker
        self.assertEqual(sw.kv, "q8_0")
        self.assertEqual(sw.split_mode, "layer")

    def test_backend_menu_selects_and_supplies_endpoint(self):
        # the whole point: deploy.yaml picks a backend; its def supplies endpoint + concurrency;
        # config.yaml supplies behavior. Build a minimal fake rig to prove the wiring.
        import tempfile
        from pathlib import Path
        # isolate every env var the loader can read, so the test exercises the files only
        env_keys = [k for k in os.environ if k.startswith((
            "WORKER_", "FOOL_", "ORCHESTRATOR_", "MEMORY_", "WINDOW_", "DECODE_"))]
        saved = {k: os.environ.pop(k) for k in env_keys}
        try:
            with tempfile.TemporaryDirectory() as d:
                root = Path(d)
                (root / "config.yaml").write_text("memory:\n  worker_input_tokens: 19000\n")
                (root / "deploy.yaml").write_text(
                    "orchestrator: mycloud\nworker: mygpu\n")
                (root / "deploy" / "orchestrator").mkdir(parents=True)
                (root / "deploy" / "worker").mkdir(parents=True)
                (root / "deploy" / "orchestrator" / "mycloud.yaml").write_text(
                    "kind: cloud\nmodel: some-orch\nbase_url: https://api.example.com/v1\n"
                    "context: 200000\nmax_output: 8192\n")
                (root / "deploy" / "worker" / "mygpu.yaml").write_text(
                    "kind: llama-local\nmodel: w-model\nbase_url: http://127.0.0.1:9000/v1\n"
                    "parallel: 8\nctx_per_slot: 45056\nmax_output: 12288\n"
                    "serve:\n  kv: q8_0\n  split_mode: layer\n")
                cfg = cfg_mod.load(config_dir=root)
                # endpoint + concurrency came from the selected worker backend
                self.assertEqual(cfg.worker_parallel, 8)
                self.assertIn("127.0.0.1:9000", cfg.worker.base_url)
                self.assertEqual(cfg.orchestrator.model_id, "some-orch")
                # behavior came from config.yaml
                self.assertEqual(cfg.worker_input_tokens, 19000)
        finally:
            os.environ.update(saved)
            os.environ.update(saved)

    def test_invariant_caught(self):
        os.environ["WORKER_INPUT_TOKENS"] = "999999"
        try:
            with self.assertRaises(AssertionError):
                cfg_mod.load().validate()
        finally:
            del os.environ["WORKER_INPUT_TOKENS"]

    def test_shell_and_json_emitters(self):
        cfg = cfg_mod.load()
        shell = emit_mod._shell_exports(cfg)
        self.assertIn("export WORKER_URL=", shell)
        self.assertIn("export WORKER_KV=", shell)  # physics reaches the shell too
        d = emit_mod._as_dict(cfg)
        self.assertIsInstance(d["weights"]["candidates"], list)

    def test_env_emitter_carries_deploy(self):
        env = emit_mod._env_exports(cfg_mod.load())
        self.assertIn("export FOOL_HOST=", env)      # rig deploy var
        self.assertIn("export NAS_MODELS=", env)
        self.assertIn("export WORKER_URL=", env)     # and method config

    def test_render_opencode_injects_config(self):
        cfg = cfg_mod.load()
        base = {
            "$schema": "x", "default_agent": "build",
            "agent": {"build": {"mode": "primary"}, "plan": {"mode": "primary"}},
            "compaction": {"auto": False},
        }
        oc = emit_mod.render_opencode(cfg, base)
        # config-derived provider block present, with URL/model/limits from the loader
        self.assertIn("fool-ds4", oc["provider"])
        self.assertIn("magus", oc["provider"])
        self.assertEqual(oc["provider"]["fool-ds4"]["options"]["baseURL"], cfg.orchestrator.base_url)
        self.assertEqual(
            oc["provider"]["magus"]["models"][cfg.worker.model_id]["limit"]["context"],
            cfg.worker_ctx_per_slot,
        )
        self.assertEqual(oc["model"], f"fool-ds4/{cfg.orchestrator.model_id}")
        self.assertEqual(oc["small_model"], f"magus/{cfg.worker.model_id}")
        # base passed through untouched
        self.assertEqual(oc["agent"]["build"]["mode"], "primary")
        self.assertEqual(oc["compaction"]["auto"], False)


class TestCliBoundary(unittest.TestCase):
    """The subprocess boundary the JS adapter uses. Proves `python -m core.tools.cli` works."""

    def test_cli_write_and_search_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            env = dict(os.environ)
            env["MEMORY_DB"] = os.path.join(d, "cli.db")
            env["REDIS_URL"] = "redis://127.0.0.1:6399"  # force sqlite fallback
            env["PYTHONPATH"] = ROOT
            w = subprocess.run(
                [sys.executable, "-m", "core.tools.cli", "memory_write",
                 "--json", json.dumps({"content": "cli boundary fact"}),
                 "--session", "s1", "--agent", "build"],
                cwd=ROOT, env=env, capture_output=True, text=True,
            )
            self.assertEqual(w.returncode, 0, w.stderr)
            self.assertIn("saved", w.stdout)
            s = subprocess.run(
                [sys.executable, "-m", "core.tools.cli", "memory_search",
                 "--json", json.dumps({"query": "boundary"}),
                 "--session", "s1", "--agent", "build"],
                cwd=ROOT, env=env, capture_output=True, text=True,
            )
            self.assertEqual(s.returncode, 0, s.stderr)
            result = json.loads(s.stdout)
            self.assertGreaterEqual(result["metadata"]["hits"], 1)


if __name__ == "__main__":
    unittest.main()


_MINIMAL_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R"
    b"/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
    b"4 0 obj<</Length 55>>stream\n"
    b"BT /F1 24 Tf 72 720 Td (fools trick pdf test) Tj ET\n"
    b"endstream\nendobj\n"
    b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
    b"trailer<</Root 1 0 R>>\n"
)


class TestPdfAndLibraryFetch(unittest.TestCase):
    """pdf_read (ephemeral web->context) and library_fetch (permanent acquire) tool bodies."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_scratch = os.environ.get("SCRATCH_DIR")
        os.environ["SCRATCH_DIR"] = self._tmp.name
        self.log = MemoryLog(
            db_path=os.path.join(self._tmp.name, "m.db"),
            redis_url="redis://127.0.0.1:6399",
            resolve_thread=identity_resolver().resolve,
        )
        self.ctx = ToolContext(sessionID="s1", agent="build")

    def tearDown(self):
        self.log.close()
        if self._old_scratch is None:
            os.environ.pop("SCRATCH_DIR", None)
        else:
            os.environ["SCRATCH_DIR"] = self._old_scratch
        self._tmp.cleanup()

    def test_pdf_read_extracts_to_scratch(self):
        from core.tools import pdf
        src = os.path.join(self._tmp.name, "paper.pdf")
        with open(src, "wb") as fh:
            fh.write(_MINIMAL_PDF)
        out = pdf.pdf_read({"url": "file://" + src}, self.ctx, self.log)
        self.assertIn("fools trick pdf test", out["output"])
        txt = out["metadata"]["path"]
        self.assertTrue(txt.startswith(self._tmp.name))
        self.assertTrue(os.path.exists(txt))
        self.assertEqual(out["metadata"]["pages"], 1)

    def test_pdf_read_rejects_non_pdf(self):
        from core.tools import pdf
        src = os.path.join(self._tmp.name, "page.html")
        with open(src, "wb") as fh:
            fh.write(b"<html><body>not a pdf</body></html>")
        out = pdf.pdf_read({"url": "file://" + src}, self.ctx, self.log)
        self.assertIn("not a PDF", out["output"])

    def test_pdf_read_requires_url(self):
        from core.tools import pdf
        self.assertIn("no url", pdf.pdf_read({}, self.ctx, self.log)["output"])

    def test_library_fetch_reports_ok_and_miss(self):
        from core.tools import library as lib
        orig = lib._api
        try:
            lib._api = lambda path, params=None, timeout=30, method="GET": {
                "ok": True, "method": "arxiv", "path": "/inbox/2301.00001.pdf", "queued": 1}
            out = lib.library_fetch({"arxiv": "2301.00001"}, self.ctx, self.log)
            self.assertIn("/inbox/2301.00001.pdf", out["output"])
            self.assertTrue(out["metadata"]["ok"])

            lib._api = lambda path, params=None, timeout=30, method="GET": {
                "ok": False, "reason": "no open-access copy", "identifiers": {"doi": "10.x"}}
            out = lib.library_fetch({"doi": "10.x/y"}, self.ctx, self.log)
            self.assertIn("no open-access copy", out["output"])
            self.assertFalse(out["metadata"]["ok"])
        finally:
            lib._api = orig

    def test_library_fetch_requires_a_reference(self):
        from core.tools import library as lib
        self.assertIn("required", lib.library_fetch({}, self.ctx, self.log)["output"])
