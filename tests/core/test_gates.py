"""Gate policy tests. stdlib unittest, no harness on disk."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from core.gates.policy import (  # noqa: E402
    VerifyState,
    classify_command,
    export_blocked_json,
    is_code_file,
    is_verify_command,
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


class TestVerifyGate(unittest.TestCase):
    def test_code_file_detection(self):
        self.assertTrue(is_code_file("core/log/store.py"))
        self.assertFalse(is_code_file("README.md"))

    def test_verify_command_detection(self):
        self.assertTrue(is_verify_command("make test"))
        self.assertTrue(is_verify_command("pytest tests/"))
        self.assertFalse(is_verify_command("echo done"))

    def test_state_machine(self):
        s = VerifyState()
        self.assertFalse(s.needs_verify())          # nothing edited
        s.mark_edit("core/log/store.py")
        self.assertTrue(s.needs_verify())            # edited, not verified
        s.mark_verified()
        self.assertFalse(s.needs_verify())           # cleared


if __name__ == "__main__":
    unittest.main()
