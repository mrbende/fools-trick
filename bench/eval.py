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

Scored by exact match. Requires the bench venv (datasets). HF token auto-read.

  bench/eval.py gsm8k --url URL --model ID [--n 40]
  bench/eval.py ruler --url URL --model ID [--lengths 4096 8192 16384] [--n 20]
  bench/eval.py deep  --url URL --model ID [--lengths 32768 131072 262144 370000] [--n 5]
"""
import argparse, json, random, re, sys, time, urllib.request
import ui

try:
    from datasets import load_dataset
except ImportError:
    sys.stderr.write("need the bench venv: .bench-venv/bin/python (pip install datasets)\n")
    sys.exit(2)


def chat(url, model, content, max_tokens, timeout=600):
    body = {"model": model, "messages": [{"role": "user", "content": content}],
            "max_tokens": max_tokens, "temperature": 0}
    req = urllib.request.Request(url + "/v1/chat/completions",
                                 data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    d = json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode())
    d["_wall"] = time.perf_counter() - t0
    return d


def answer_text(d):
    m = d["choices"][0]["message"]
    return (m.get("content") or m.get("reasoning_content") or "").strip()


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


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    defaults_n = {"gsm8k": 40, "ruler": 20, "deep": 5}
    for name in ("gsm8k", "ruler", "deep"):
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
    {"gsm8k": cmd_gsm8k, "ruler": cmd_ruler, "deep": cmd_deep}[a.cmd](a, out, md)
    if out: out.close()
    if md: md.close()


if __name__ == "__main__":
    sys.exit(main())
