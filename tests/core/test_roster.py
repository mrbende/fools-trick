"""Agent roster agreement. The roster is defined in config.yaml (agents.workers); the worker
agent defs in opencode/agents/, the task permissions in opencode.base.json, and the test.sh
parity roster must all match it, or delegation silently targets an agent that doesn't exist.
stdlib unittest, no harness on disk.
"""

import json
import os
import re
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)

from core import config as cfg_mod  # noqa: E402


class TestRosterAgreement(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = cfg_mod.load()
        cls.workers = set(cls.cfg.workers)
        cls.primaries = set(cls.cfg.primaries)

    def test_agent_defs_match_config_workers(self):
        agents_dir = os.path.join(ROOT, "opencode", "agents")
        on_disk = {os.path.splitext(f)[0] for f in os.listdir(agents_dir) if f.endswith(".md")}
        self.assertEqual(on_disk, self.workers,
                         f"opencode/agents/ {sorted(on_disk)} != config workers {sorted(self.workers)}")

    def test_base_json_task_perms_cover_workers(self):
        with open(os.path.join(ROOT, "opencode.base.json")) as fh:
            base = json.load(fh)
        task = base["agent"]["build"]["permission"]["task"]
        allowed = {k for k, v in task.items() if v == "allow"}
        # every worker must be dispatchable by the orchestrator
        self.assertTrue(self.workers.issubset(allowed),
                        f"build's task perms {sorted(allowed)} miss workers {sorted(self.workers - allowed)}")

    def test_base_json_primaries_match_config(self):
        with open(os.path.join(ROOT, "opencode.base.json")) as fh:
            base = json.load(fh)
        primaries = {n for n, a in base["agent"].items() if a.get("mode") == "primary"}
        self.assertEqual(primaries, self.primaries,
                         f"base.json primaries {sorted(primaries)} != config {sorted(self.primaries)}")

    def test_test_sh_roster_matches(self):
        # the shell parity checker hardcodes the roster; it must not drift from config.yaml
        with open(os.path.join(ROOT, "deploy", "scripts", "test.sh")) as fh:
            src = fh.read()
        m = re.search(r'sub\s*=\s*\{([^}]*)\}', src)
        self.assertIsNotNone(m, "could not find the worker roster in test.sh")
        names = set(re.findall(r'"(\w+)"', m.group(1)))
        self.assertEqual(names, self.workers,
                         f"test.sh roster {sorted(names)} != config workers {sorted(self.workers)}")

    def test_orchestrator_prompt_names_workers(self):
        with open(os.path.join(ROOT, "prompts", "orchestrator.md")) as fh:
            src = fh.read()
        for w in self.workers:
            self.assertIn(f"@{w}", src, f"orchestrator prompt never names worker @{w}")


if __name__ == "__main__":
    unittest.main()
