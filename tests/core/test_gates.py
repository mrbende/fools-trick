"""Gate policy tests. stdlib unittest, no harness on disk."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import re

from core.gates.policy import (  # noqa: E402
    classify_command,
    export_blocked_json,
    export_gate_patterns_json,
    export_protected_branches_json,
    is_protected_branch,
)


class TestHumanGate(unittest.TestCase):
    def test_blocks_git_push(self):
        self.assertIsNotNone(classify_command("git push origin main"))

    def test_blocks_force_push(self):
        self.assertIsNotNone(classify_command("git push --force origin main"))

    def test_blocks_terraform_apply(self):
        self.assertIsNotNone(classify_command("terraform apply -auto-approve"))

    def test_blocks_drop_table(self):
        self.assertIsNotNone(classify_command("psql -c 'DROP TABLE users'"))

    def test_allows_ordinary_command(self):
        self.assertIsNone(classify_command("git status"))
        self.assertIsNone(classify_command("make test"))
        self.assertIsNone(classify_command("ls -la"))

    def test_export_is_valid_json_with_sources(self):
        data = json.loads(export_blocked_json())
        self.assertTrue(all("source" in e and "reason" in e for e in data))
        self.assertGreater(len(data), 5)


class TestGatePatterns(unittest.TestCase):
    """The canonical gate patterns production loads (the JS gate reads these via the export)."""

    def setUp(self):
        d = json.loads(export_gate_patterns_json())
        self.code_ext = re.compile(d["code_ext"])
        self.verify_cmd = re.compile(d["verify_cmd"])

    def test_code_file_pattern(self):
        self.assertTrue(self.code_ext.search("core/log/store.py"))
        self.assertFalse(self.code_ext.search("README.md"))

    def test_verify_command_pattern(self):
        self.assertTrue(self.verify_cmd.search("make test"))
        self.assertTrue(self.verify_cmd.search("pytest tests/"))
        self.assertTrue(self.verify_cmd.search("make check-quality"))
        self.assertFalse(self.verify_cmd.search("echo done"))

    def test_protected_branches(self):
        self.assertTrue(is_protected_branch("master"))
        self.assertTrue(is_protected_branch("MAIN"))
        self.assertTrue(is_protected_branch("staging"))
        self.assertFalse(is_protected_branch("feature/x"))
        self.assertEqual(json.loads(export_protected_branches_json()), ["master", "main", "staging"])


if __name__ == "__main__":
    unittest.main()
