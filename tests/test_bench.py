#!/usr/bin/env python3
"""Unit tests for the benchmark parsers -- the load-bearing logic that turns raw
server/opencode output into the numbers we trust. stdlib unittest, no deps, no network.

Run: python3 -m unittest discover -s tests   (or: make test)
"""
import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bench"))
import e2e, eval as ev, speed  # noqa: E402


class TestDelegationExtract(unittest.TestCase):
    """e2e.extract must PROVE delegation from the opencode --format json stream."""

    def _task_event(self, subagent, child, provider, start=0, end=1000, status="completed"):
        return {"type": "tool_use", "sessionID": "ses_ROOT", "part": {
            "type": "tool", "tool": "task", "state": {
                "status": status,
                "input": {"subagent_type": subagent, "prompt": "..."},
                "metadata": {"sessionId": child, "model": {"providerID": provider}},
                "time": {"start": start, "end": end}}}}

    def test_no_delegation(self):
        events = [{"type": "text", "sessionID": "ses_ROOT",
                   "part": {"type": "text", "text": "answered solo"}}]
        answer, tasks = e2e.extract(events)
        self.assertEqual(answer, "answered solo")
        self.assertEqual(len(tasks), 0)

    def test_counts_and_types(self):
        events = [
            self._task_event("explore", "ses_C1", "magus", 0, 3200),
            self._task_event("general", "ses_C2", "magus", 0, 6800),
            {"type": "text", "sessionID": "ses_ROOT",
             "part": {"type": "text", "text": "Summary combining both."}},
        ]
        answer, tasks = e2e.extract(events)
        self.assertEqual(answer, "Summary combining both.")
        self.assertEqual(len(tasks), 2)
        self.assertEqual(sorted(t["subagent"] for t in tasks), ["explore", "general"])
        self.assertTrue(all(t["provider"] == "magus" for t in tasks))
        self.assertEqual([t["child"] for t in tasks], ["ses_C1", "ses_C2"])
        self.assertEqual([t["ms"] for t in tasks], [3200, 6800])

    def test_incomplete_task_ignored(self):
        # a task still 'running' must not be counted as a completed spawn
        events = [self._task_event("explore", "ses_C1", "magus", status="running")]
        _, tasks = e2e.extract(events)
        self.assertEqual(len(tasks), 0)

    def test_wrong_provider_detectable(self):
        events = [self._task_event("general", "ses_C1", "anthropic")]
        _, tasks = e2e.extract(events)
        self.assertEqual(tasks[0]["provider"], "anthropic")  # so the assert can fail it


class TestReasoningSplit(unittest.TestCase):
    """answer_text must read reasoning models correctly (content OR reasoning_content)."""

    def test_content_present(self):
        d = {"choices": [{"message": {"content": "pong", "reasoning_content": "thinking..."}}]}
        self.assertEqual(ev.answer_text(d), "pong")

    def test_only_reasoning(self):
        # when the answer landed in reasoning_content (max_tokens cutoff mid-think edge case)
        d = {"choices": [{"message": {"content": "", "reasoning_content": "the answer is 42"}}]}
        self.assertEqual(ev.answer_text(d), "the answer is 42")

    def test_neither(self):
        d = {"choices": [{"message": {}}]}
        self.assertEqual(ev.answer_text(d), "")


class TestNumberExtraction(unittest.TestCase):
    """gsm8k scoring depends on pulling the final number out of free-form reasoning."""

    def test_last_number(self):
        self.assertEqual(ev.last_number("so the total is 18 apples"), "18")

    def test_commas_stripped(self):
        self.assertEqual(ev.last_number("that gives 1,234 in the end"), "1234")

    def test_none_when_absent(self):
        self.assertIsNone(ev.last_number("no digits here"))


class TestSpeedRates(unittest.TestCase):
    """speed.rates must prefer llama.cpp native timings, fall back to wall-clock for vLLM."""

    def test_llama_uses_native_timings(self):
        r = {"usage": {}, "timings": {"prompt_n": 500, "predicted_n": 100,
                                      "prompt_per_second": 3000.0, "predicted_per_second": 70.0},
             "wall": 2.0, "ttft": 0.2, "ntok": 100}
        pt, pf, dec = speed.rates(r, "llama")
        self.assertEqual(pt, 500)
        self.assertEqual(pf, 3000.0)   # native, not derived
        self.assertEqual(dec, 70.0)

    def test_vllm_derives_from_wallclock(self):
        r = {"usage": {"prompt_tokens": 8000, "completion_tokens": 90}, "timings": {},
             "wall": 3.0, "ttft": 1.0, "ntok": 90}
        pt, pf, dec = speed.rates(r, "vllm")
        self.assertEqual(pt, 8000)
        self.assertAlmostEqual(pf, 8000 / 1.0)          # prompt / ttft
        self.assertAlmostEqual(dec, 90 / (3.0 - 1.0))   # completion / (wall - ttft)

    def test_cached_tokens(self):
        r = {"usage": {"prompt_tokens_details": {"cached_tokens": 8180}}}
        self.assertEqual(speed.cached_tokens(r), 8180)
        self.assertEqual(speed.cached_tokens({"usage": {}}), 0)


class TestTaskLoading(unittest.TestCase):
    """e2e task defaults must be well-formed (fan-out task requires a subagent)."""

    def test_default_tasks_shape(self):
        tasks = e2e.default_tasks()
        self.assertTrue(len(tasks) >= 3)
        for t in tasks:
            self.assertIn("name", t)
            self.assertIn("prompt", t)
            self.assertIn("expect", t)
        fanout = [t for t in tasks if t.get("min_subagents", 0) >= 1]
        self.assertTrue(fanout, "at least one task must require delegation")


if __name__ == "__main__":
    unittest.main(verbosity=2)
