#!/usr/bin/env python3
"""Unit tests for the benchmark parsers -- the load-bearing logic that turns raw
server/opencode output into the numbers we trust. stdlib unittest, no deps, no network.

Run: python3 -m unittest discover -s tests   (or: make test)
"""
import os, sys, unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bench"))
import e2e, eval as ev, speed  # noqa: E402
import ui  # noqa: E402


class TestStats(unittest.TestCase):
    """Wilson CI must widen for small n and never exceed [0,100], so a 5/5 is never read as a
    precise 100%."""

    def test_full_small_n_has_wide_ci(self):
        lo, hi = ui.wilson(5, 5)
        self.assertLess(lo, 100)          # 100% on n=5 is uncertain
        self.assertLessEqual(hi, 100.0001)

    def test_larger_n_tighter(self):
        self.assertGreater(ui.wilson(200, 200)[0], ui.wilson(5, 5)[0])

    def test_zero_total_safe(self):
        self.assertEqual(ui.wilson(0, 0), (0.0, 0.0))
        self.assertIn("n=0", ui.stat_str(0, 0))

    def test_stat_str_format(self):
        s = ui.stat_str(4, 5)
        self.assertIn("80.0%", s)
        self.assertIn("n=5", s)


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


class TestCodeEval(unittest.TestCase):
    """code eval must extract runnable code from a reply and score by real execution."""

    def test_extract_fenced(self):
        reply = "Here you go:\n```python\ndef f(x):\n    return x + 1\n```\nDone."
        code = ev.extract_code(reply, "f")
        self.assertIn("def f(x):", code)
        self.assertNotIn("Here you go", code)

    def test_extract_unfenced_falls_back(self):
        reply = "def f(x):\n    return x + 1\n"
        self.assertIn("def f(x):", ev.extract_code(reply, "f"))

    def test_run_test_passes_correct(self):
        prog = "def add(a,b):\n    return a+b\n\ndef check(f):\n    assert f(2,3)==5\n\ncheck(add)\n"
        ok, _ = ev.run_test(prog)
        self.assertTrue(ok)

    def test_run_test_fails_wrong(self):
        prog = "def add(a,b):\n    return a-b\n\ndef check(f):\n    assert f(2,3)==5\n\ncheck(add)\n"
        ok, _ = ev.run_test(prog)
        self.assertFalse(ok)

    def test_run_test_times_out(self):
        prog = "while True:\n    pass\n"
        ok, detail = ev.run_test(prog, timeout=2)
        self.assertFalse(ok)
        self.assertEqual(detail, "timeout")


class TestToolScoring(unittest.TestCase):
    """tools eval scorer: order-independent AST match, tolerant arg values, irrelevance."""

    def test_simple_match(self):
        case = {"expect": [("get_weather", {"city": "Tokyo"})]}
        ok, _ = ev.score_tool_case(case, [("get_weather", {"city": "Tokyo"})])
        self.assertTrue(ok)

    def test_numeric_coercion(self):
        case = {"expect": [("add", {"a": 5, "b": 8})]}
        ok, _ = ev.score_tool_case(case, [("add", {"a": "5", "b": 8})])
        self.assertTrue(ok)

    def test_wrong_function(self):
        case = {"expect": [("send_email", {"to": "x@y.com"})]}
        ok, _ = ev.score_tool_case(case, [("get_weather", {"city": "x"})])
        self.assertFalse(ok)

    def test_parallel_order_independent(self):
        case = {"expect": [("get_weather", {"city": "Paris"}), ("get_weather", {"city": "London"})]}
        ok, _ = ev.score_tool_case(case, [("get_weather", {"city": "London"}),
                                          ("get_weather", {"city": "Paris"})])
        self.assertTrue(ok)

    def test_irrelevance_pass_when_no_call(self):
        ok, _ = ev.score_tool_case({"expect": []}, [])
        self.assertTrue(ok)

    def test_irrelevance_fail_on_spurious_call(self):
        ok, _ = ev.score_tool_case({"expect": []}, [("get_weather", {"city": "Rome"})])
        self.assertFalse(ok)

    def test_missing_required_arg(self):
        case = {"expect": [("search_flights", {"origin": "SFO", "dest": "JFK", "date": "2025-06-01"})]}
        ok, _ = ev.score_tool_case(case, [("search_flights", {"origin": "SFO", "dest": "JFK"})])
        self.assertFalse(ok)


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
