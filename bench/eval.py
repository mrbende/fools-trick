#!/usr/bin/env python3
"""Quality benchmark: real reasoning on real data. NOT gibberish -- actual accuracy.

  gsm8k : real multi-step math reasoning (openai/gsm8k). Tests raw reasoning.
  ruler : long-context reasoning at controlled lengths from the RULER dataset
          (simonjegou/ruler, 4k/8k/16k). Good for the shallow-to-mid range.
  deep  : multi-hop needle-in-haystack synthesized to ANY depth (32k..370k). Plants
          several LINKED facts at different positions in a real-text haystack and asks
          a question that requires chaining them -- so it tests reasoning OVER retrieved
          facts, not just single retrieval. This is how we test the orchestrator's
          384k window, which RULER's prebuilt 16k ceiling cannot reach.
  code  : real coding. The model completes HumanEval+ functions and we EXECUTE the
          official tests against its output -- the workers' actual job (writing runnable
          code), scored by running it, not by string match.

Scored by exact match (gsm8k/ruler/deep) or by executing tests (code). Requires the
bench venv (datasets). HF token auto-read.

  bench/eval.py gsm8k --url URL --model ID [--n 40]
  bench/eval.py ruler --url URL --model ID [--lengths 4096 8192 16384] [--n 20]
  bench/eval.py deep  --url URL --model ID [--lengths 32768 131072 262144 370000] [--n 5]
  bench/eval.py code  --url URL --model ID [--n 20]
"""
import argparse, json, random, re, sys, time, urllib.request
import ui
from core import chat, answer_text

try:
    from datasets import load_dataset
except ImportError:
    sys.stderr.write("need the bench venv: .bench-venv/bin/python (pip install datasets)\n")
    sys.exit(2)


def chat_tools(url, model, content, tools, max_tokens, timeout=600):
    body = {"model": model, "messages": [{"role": "user", "content": content}],
            "tools": tools, "tool_choice": "auto",
            "max_tokens": max_tokens, "temperature": 0}
    req = urllib.request.Request(url + "/v1/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    d = json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode())
    d["_wall"] = time.perf_counter() - t0
    return d


def parse_tool_calls(d):
    """Return a list of (name, args_dict) for tool_calls the model emitted, [] if none."""
    m = d["choices"][0]["message"]
    calls = []
    for tc in (m.get("tool_calls") or []):
        fn = tc.get("function", {})
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except (json.JSONDecodeError, TypeError):
            args = {"__unparseable__": fn.get("arguments")}
        calls.append((fn.get("name"), args))
    return calls


def emit(rec, out):
    """JSONL to the file only; the terminal view is drawn via ui."""
    if out:
        out.write(json.dumps(rec) + "\n"); out.flush()


# per-item progress is driven by a rich status handle passed into the loop (see cmd_gsm8k)


def last_number(s):
    nums = re.findall(r"-?\d[\d,]*\.?\d*", s.replace(",", ""))
    return nums[-1] if nums else None


def cmd_gsm8k(a, out, md):
    ds = load_dataset("openai/gsm8k", "main", split="test", streaming=True)
    ui.phase(f"gsm8k  {a.model}  (n={a.n}, real math reasoning)")
    if md: md.write(f"\n## gsm8k  {a.model} @ {a.url}  (n={a.n})\n\n")
    correct = 0; total = 0
    with ui.ItemProgress("gsm8k", a.n) as prog:
        for row in ds:
            if total >= a.n:
                break
            gold = row["answer"].split("####")[-1].strip().replace(",", "")
            q = (row["question"] +
                 "\n\nSolve step by step, then end with the final answer on its own line as: #### <number>")
            try:
                d = chat(a.url, a.model, q, 1024, a.timeout)
                txt = answer_text(d)
                m = re.search(r"####\s*(-?\d[\d,]*\.?\d*)", txt)
                got = (m.group(1) if m else last_number(txt))
                got = got.replace(",", "") if got else None
                ok = (got is not None and abs(float(got) - float(gold)) < 1e-6)
            except Exception as e:
                ui.log.warning("gsm8k item %d: %s", total, repr(e)[:80])
                emit({"test": "gsm8k", "i": total, "error": repr(e)[:160]}, out)
                total += 1; continue
            correct += ok; total += 1
            emit({"test": "gsm8k", "i": total, "gold": gold, "got": got,
                  "pass": bool(ok), "wall_s": round(d["_wall"], 2)}, out)
            prog.update(total, bool(ok), 100 * correct / total,
                        f"gold={gold} got={got} {d['_wall']:.1f}s")
    acc = 100 * correct / total if total else 0
    ui.summary("gsm8k accuracy", correct, total, f"{acc:.1f}%")
    if md: md.write(f"\n**gsm8k accuracy: {correct}/{total} = {acc:.1f}%**\n")
    emit({"test": "gsm8k_summary", "correct": correct, "total": total, "accuracy_pct": round(acc, 1)}, out)


def cmd_ruler(a, out, md):
    ui.phase(f"ruler  {a.model}  reasoning-at-depth (n={a.n}/length)")
    if md: md.write(f"\n## ruler  {a.model} @ {a.url}  reasoning-at-depth\n\n")
    tbl = ui.Table("ruler reasoning-at-depth", ["ctx len", "correct", "total", "acc %", "med s"], md)
    for length in a.lengths:
        try:
            ds = load_dataset("simonjegou/ruler", str(length), split="test", streaming=True)
        except Exception as e:
            ui.log.error("ruler %s dataset load failed: %s", length, repr(e)[:80])
            tbl.add([length, "dataset error", repr(e)[:40], "-", "-"], style="red"); continue
        correct = 0; total = 0; walls = []
        with ui.ItemProgress(f"ruler {length}", a.n) as prog:
            for row in ds:
                if total >= a.n:
                    break
                content = row["context"] + "\n\n" + row["question"] + "\n" + row.get("answer_prefix", "")
                golds = row["answer"] if isinstance(row["answer"], list) else [row["answer"]]
                try:
                    d = chat(a.url, a.model, content, int(row.get("max_new_tokens", 64) or 64), a.timeout)
                    txt = answer_text(d)
                    ok = any(str(g).strip() in txt for g in golds)
                    walls.append(d["_wall"])
                except Exception as e:
                    ui.log.warning("ruler %s item %d: %s", length, total, repr(e)[:80])
                    emit({"test": "ruler", "len": length, "i": total, "error": repr(e)[:160]}, out)
                    total += 1; continue
                correct += ok; total += 1
                emit({"test": "ruler", "len": length, "i": total, "task": row.get("task"),
                      "gold": golds, "pass": bool(ok), "wall_s": round(d["_wall"], 2)}, out)
                prog.update(total, bool(ok), 100 * correct / total,
                            f"{row.get('task','')} {d['_wall']:.1f}s")
        acc = 100 * correct / total if total else 0
        import statistics
        mw = statistics.median(walls) if walls else 0
        tbl.add([length, correct, total, f"{acc:.1f}", f"{mw:.1f}"],
                style=("red" if acc < 80 else None))
        emit({"test": "ruler_summary", "len": length, "correct": correct, "total": total,
              "accuracy_pct": round(acc, 1)}, out)
    tbl.render()


# ------------------------------------------------- deep multi-hop needle at depth

# real-text haystack blocks (code + prose), so facts must be found among genuine
# content rather than lorem. ~55-70 tokens each; the server's real prompt_tokens is
# what we record.
_HAY = [
    "The scheduler assigns each task to the least-loaded worker slot, falling back to "
    "round-robin when loads are equal. Backpressure is applied when the queue depth exceeds "
    "the high-water mark set at startup.\n",
    "def coalesce(shards):\n    out = {}\n    for s in shards:\n        for k, v in s.items():\n"
    "            out.setdefault(k, []).append(v)\n    return {k: merge(v) for k, v in out.items()}\n",
    "In distributed training, gradient all-reduce dominates step time once the model exceeds "
    "the interconnect bandwidth. Pipeline parallelism hides some of this latency by overlapping "
    "the backward pass of one microbatch with the forward pass of the next.\n",
    "The cache retains entries until the retention interval elapses or memory pressure forces "
    "eviction. Warm keys are promoted; cold keys age out on an approximate-LRU clock.\n",
]


def build_multihop(target_tokens, seed):
    """Plant 3 linked numeric facts at early/mid/late positions in a real-text haystack.
    Returns (prompt, expected_answer). The question requires chaining all three."""
    rng = random.Random(seed)
    alpha = rng.randint(1000, 9000)
    beta_off = rng.randint(100, 900)
    gamma_off = rng.randint(50, 500)
    beta = alpha + beta_off
    gamma = beta - gamma_off
    answer = alpha + gamma  # requires all three facts
    facts = [
        f"NOTE: the ALPHA constant for this run is {alpha}.",
        f"NOTE: the BETA constant equals the ALPHA constant plus {beta_off}.",
        f"NOTE: the GAMMA constant equals the BETA constant minus {gamma_off}.",
    ]
    # build haystack to length, then insert facts at ~15%, ~50%, ~85%
    blocks, approx = [], 0
    while approx < target_tokens:
        b = rng.choice(_HAY)
        blocks.append(b); approx += 60
    for frac, fact in zip((0.15, 0.50, 0.85), facts):
        idx = max(0, min(len(blocks), int(len(blocks) * frac)))
        blocks.insert(idx, "\n" + fact + "\n")
    haystack = "".join(blocks)
    question = (
        "\n\nUsing ONLY the ALPHA, BETA, and GAMMA constants stated in the notes above, "
        "compute (ALPHA + GAMMA). Reason step by step, then end with the final answer on "
        "its own line as: #### <number>")
    return "Document follows.\n\n" + haystack + question, answer


def cmd_deep(a, out, md):
    ui.phase(f"deep multi-hop needle  {a.model}  (chained facts at depth, n={a.n}/length)")
    if md: md.write(f"\n## deep multi-hop  {a.model} @ {a.url}\n\n")
    tbl = ui.Table("deep multi-hop reasoning", ["target ctx", "real prompt_tok", "correct",
                                                "total", "acc %", "med s"], md)
    for length in a.lengths:
        correct = 0; total = 0; walls = []; real_pt = []
        with ui.ItemProgress(f"deep {length}", a.n) as prog:
            for i in range(a.n):
                prompt, answer = build_multihop(length, seed=length * 1000 + i)
                try:
                    d = chat(a.url, a.model, prompt, 2048, a.timeout)
                    txt = answer_text(d)
                    m = re.search(r"####\s*(-?\d[\d,]*)", txt)
                    got = (m.group(1).replace(",", "") if m else last_number(txt))
                    ok = (got is not None and abs(float(got) - answer) < 1e-6)
                    walls.append(d["_wall"])
                    real_pt.append((d.get("usage") or {}).get("prompt_tokens") or 0)
                except Exception as e:
                    ui.log.warning("deep %s item %d: %s", length, i, repr(e)[:80])
                    emit({"test": "deep", "len": length, "i": i, "error": repr(e)[:160]}, out)
                    total += 1; continue
                correct += ok; total += 1
                emit({"test": "deep", "len": length, "i": i, "expected": answer, "got": got,
                      "prompt_tokens": real_pt[-1] if real_pt else None,
                      "pass": bool(ok), "wall_s": round(d["_wall"], 2)}, out)
                prog.update(total, bool(ok), 100 * correct / total,
                            f"exp={answer} got={got} {d['_wall']:.0f}s")
        import statistics
        acc = 100 * correct / total if total else 0
        mpt = int(statistics.median(real_pt)) if real_pt else "?"
        mw = statistics.median(walls) if walls else 0
        tbl.add([length, mpt, correct, total, f"{acc:.1f}", f"{mw:.0f}"],
                style=("red" if acc < 80 else None))
        emit({"test": "deep_summary", "len": length, "correct": correct, "total": total,
              "accuracy_pct": round(acc, 1)}, out)
    tbl.render()


def extract_code(text):
    """Pull runnable Python from a worker reply: prefer a fenced block, else the raw text."""
    m = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.S)
    return m.group(1) if m else text


def run_test(program, timeout=20):
    """Execute a self-contained program (solution + test + harness call) in a subprocess.
    Returns (passed, detail). Isolated: no network, its own tmp cwd."""
    import subprocess, tempfile, os
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "cand.py")
        with open(p, "w") as f:
            f.write(program)
        try:
            r = subprocess.run([sys.executable, p], cwd=d, capture_output=True,
                               text=True, timeout=timeout)
            return (r.returncode == 0, (r.stderr or r.stdout or "")[-200:])
        except subprocess.TimeoutExpired:
            return (False, "timeout")
        except Exception as e:
            return (False, str(e)[:200])


def tool_cases():
    """BFCL-style tool-calling cases across the categories that matter for a worker:
    simple (one right call), multiple (pick the right fn from several), parallel (>1 call
    in one turn), and irrelevance (must NOT call -- the case abliterated over-eagerness fails).
    Scored by AST match: correct function name(s) + expected argument values. Argument-value
    matching tolerates type coercion (e.g. "5" vs 5)."""
    T = {
        "get_weather": {"type": "function", "function": {"name": "get_weather",
            "description": "Get current weather for a city",
            "parameters": {"type": "object", "properties": {
                "city": {"type": "string"}, "unit": {"type": "string", "enum": ["c", "f"]}},
                "required": ["city"]}}},
        "add": {"type": "function", "function": {"name": "add",
            "description": "Add two numbers", "parameters": {"type": "object",
            "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
            "required": ["a", "b"]}}},
        "send_email": {"type": "function", "function": {"name": "send_email",
            "description": "Send an email", "parameters": {"type": "object", "properties": {
                "to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"}},
                "required": ["to", "subject", "body"]}}},
        "search_flights": {"type": "function", "function": {"name": "search_flights",
            "description": "Search flights between two airports on a date",
            "parameters": {"type": "object", "properties": {
                "origin": {"type": "string"}, "dest": {"type": "string"}, "date": {"type": "string"}},
                "required": ["origin", "dest", "date"]}}},
        "convert_currency": {"type": "function", "function": {"name": "convert_currency",
            "description": "Convert an amount from one currency to another",
            "parameters": {"type": "object", "properties": {
                "amount": {"type": "number"}, "from": {"type": "string"}, "to": {"type": "string"}},
                "required": ["amount", "from", "to"]}}},
    }
    return [
        {"name": "simple-weather", "tools": [T["get_weather"]],
         "query": "What's the weather in Tokyo right now?",
         "expect": [("get_weather", {"city": "Tokyo"})]},
        {"name": "simple-add", "tools": [T["add"]],
         "query": "What is 47 plus 58?",
         "expect": [("add", {"a": 47, "b": 58})]},
        {"name": "multiple-pick-email", "tools": [T["get_weather"], T["send_email"], T["add"]],
         "query": "Email alice@corp.com with subject 'Hi' and body 'See you at 3'.",
         "expect": [("send_email", {"to": "alice@corp.com", "subject": "Hi", "body": "See you at 3"})]},
        {"name": "multiple-pick-flight", "tools": [T["search_flights"], T["convert_currency"], T["get_weather"]],
         "query": "Find flights from SFO to JFK on 2025-06-01.",
         "expect": [("search_flights", {"origin": "SFO", "dest": "JFK", "date": "2025-06-01"})]},
        {"name": "parallel-two-weather", "tools": [T["get_weather"]],
         "query": "Compare the weather in Paris and London right now.",
         "expect": [("get_weather", {"city": "Paris"}), ("get_weather", {"city": "London"})]},
        {"name": "parallel-convert", "tools": [T["convert_currency"]],
         "query": "Convert 100 USD to EUR and also 50 GBP to USD.",
         "expect": [("convert_currency", {"amount": 100, "from": "USD", "to": "EUR"}),
                    ("convert_currency", {"amount": 50, "from": "GBP", "to": "USD"})]},
        {"name": "irrelevance-chat", "tools": [T["get_weather"], T["add"]],
         "query": "Tell me a fun fact about the Roman Empire.",
         "expect": []},
        {"name": "irrelevance-missing-tool", "tools": [T["get_weather"], T["add"]],
         "query": "Book me a hotel in Rome for next weekend.",
         "expect": []},
    ]


def _val_eq(exp, got):
    """Tolerant value match: exact, or string-insensitive, or numeric-coerced."""
    if exp == got:
        return True
    try:
        if float(exp) == float(got):
            return True
    except (ValueError, TypeError):
        pass
    return str(exp).strip().lower() == str(got).strip().lower()


def score_tool_case(case, calls):
    """Return (passed, detail). Order-independent match of expected calls against emitted calls."""
    exp = case["expect"]
    if not exp:
        return (len(calls) == 0, "" if not calls else f"expected no call, got {[c[0] for c in calls]}")
    if len(calls) != len(exp):
        return (False, f"expected {len(exp)} call(s), got {len(calls)}: {[c[0] for c in calls]}")
    remaining = list(calls)
    for want_name, want_args in exp:
        hit = None
        for i, (gn, ga) in enumerate(remaining):
            if gn == want_name and all(k in ga and _val_eq(v, ga[k]) for k, v in want_args.items()):
                hit = i; break
        if hit is None:
            return (False, f"no emitted call matched {want_name}({want_args})")
        remaining.pop(hit)
    return (True, "")


def cmd_tools(a, out, md):
    """Tool-calling eval (BFCL-style): does the model emit the right function call(s) with the
    right arguments -- including correctly NOT calling on irrelevant queries. This is the signal
    nobody has measured on abliterated models, and it is the workers' core competency."""
    cases = tool_cases()
    n = min(a.n, len(cases)) if a.n else len(cases)
    cases = cases[:n]
    ui.phase(f"tools  {a.model}  (n={len(cases)}, BFCL-style tool-calling)")
    if md: md.write(f"\n## tools  {a.model} @ {a.url}  (n={len(cases)})\n\n")
    correct = 0; total = 0
    tbl = ui.Table("tools", ["case", "pass", "detail"], md,
                   justify=["left", "center", "left"])
    with ui.ItemProgress("tools", len(cases)) as prog:
        for case in cases:
            try:
                d = chat_tools(a.url, a.model, case["query"], case["tools"],
                               max_tokens=1024, timeout=a.timeout)
                calls = parse_tool_calls(d)
            except Exception as e:
                calls = []; ui.log.warning("tools %s failed: %s", case["name"], e)
            ok, detail = score_tool_case(case, calls)
            correct += ok; total += 1
            emit({"test": "tools", "case": case["name"], "pass": bool(ok),
                  "detail": detail or None}, out)
            tbl.add([case["name"], "yes" if ok else "NO", detail[:48]],
                    style=(None if ok else "red"))
            prog.update(total, bool(ok), 100 * correct / total)
    acc = 100.0 * correct / total if total else 0.0
    emit({"test": "tools", "summary": True, "n": total, "correct": correct,
          "accuracy_pct": round(acc, 1)}, out)
    tbl.render()
    ui.log.info("tools: %d/%d = %.1f%%", correct, total, acc)


def cmd_code(a, out, md):
    """Real coding eval: the worker completes HumanEval+ functions; we EXECUTE the tests.
    This is the workers' actual job -- producing correct, runnable code -- scored by running it,
    not by string match."""
    ds = load_dataset("evalplus/humanevalplus", split="test", streaming=True)
    ui.phase(f"code  {a.model}  (n={a.n}, HumanEval+ -- worker writes code, we run the tests)")
    if md: md.write(f"\n## code  {a.model} @ {a.url}  (n={a.n}, executed)\n\n")
    correct = 0; total = 0
    tbl = ui.Table("code", ["task", "pass", "wall s"], md,
                   justify=["left", "center", "right"])
    with ui.ItemProgress("code", a.n) as prog:
        for row in ds:
            if total >= a.n:
                break
            prompt = (f"Complete this Python function. Return ONLY the full function "
                      f"definition in a single ```python code block, no prose:\n\n{row['prompt']}")
            try:
                d = chat(a.url, a.model, prompt, max_tokens=1536, timeout=a.timeout)
                reply = answer_text(d); wall = d.get("_wall", 0)
            except Exception as e:
                reply = ""; wall = 0; ui.log.warning("code %s request failed: %s", row["task_id"], e)
            code = extract_code(reply)
            # Assemble a self-contained program: candidate + official test + call.
            program = f"{code}\n\n{row['test']}\n\ncheck({row['entry_point']})\n"
            ok, detail = run_test(program)
            correct += ok; total += 1
            emit({"test": "code", "task": row["task_id"], "pass": bool(ok),
                  "wall_s": round(wall, 1), "detail": None if ok else detail}, out)
            tbl.add([row["task_id"], "yes" if ok else "NO", f"{wall:.0f}"],
                    style=(None if ok else "red"))
            prog.update(total, bool(ok), 100 * correct / total)
    acc = 100.0 * correct / total if total else 0.0
    emit({"test": "code", "summary": True, "n": total, "correct": correct,
          "accuracy_pct": round(acc, 1)}, out)
    tbl.render()
    ui.log.info("code: %d/%d = %.1f%%", correct, total, acc)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    defaults_n = {"gsm8k": 40, "ruler": 20, "deep": 5, "code": 20, "tools": 0}
    for name in ("gsm8k", "ruler", "deep", "code", "tools"):
        s = sub.add_parser(name)
        s.add_argument("--url", required=True)
        s.add_argument("--model", required=True)
        s.add_argument("--n", type=int, default=defaults_n[name])
        s.add_argument("--timeout", type=int, default=(1800 if name == "deep" else 900))
        s.add_argument("--out")
        s.add_argument("--md")
        s.add_argument("--logfile")
        if name == "ruler":
            s.add_argument("--lengths", type=int, nargs="+", default=[4096, 8192, 16384])
        if name == "deep":
            # deep needle at the depths that actually exercise a 384k window
            s.add_argument("--lengths", type=int, nargs="+", default=[32768, 131072, 262144, 370000])
    a = ap.parse_args()
    ui.setup_logging(getattr(a, "logfile", None))
    out = open(a.out, "a") if getattr(a, "out", None) else None
    md = open(a.md, "a") if getattr(a, "md", None) else None
    ui.log.info("eval %s  %s @ %s", a.cmd, a.model, a.url)
    if md: md.write(f"# eval {a.cmd}  {a.model} @ {a.url}  {time.strftime('%Y-%m-%dT%H:%M:%S')}\n")
    {"gsm8k": cmd_gsm8k, "ruler": cmd_ruler, "deep": cmd_deep, "code": cmd_code,
     "tools": cmd_tools}[a.cmd](a, out, md)
    if out: out.close()
    if md: md.close()


if __name__ == "__main__":
    sys.exit(main())
