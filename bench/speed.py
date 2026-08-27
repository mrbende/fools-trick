#!/usr/bin/env python3
"""Speed benchmark: TTFT, prefill/decode tok/s, concurrency, prefix-cache verification.

Engine-aware:
  - llama.cpp (worker): reads the server's own `timings` object (prompt_per_second,
    predicted_per_second, prompt_n) -- the honest rates, network/queue excluded.
  - vLLM (fool): reads `usage` (prompt/completion tokens, cached_tokens) and computes
    aggregate rates over wall-clock; scrapes /metrics for preemptions.
  - TTFT is always streamed wall-clock (the only honest way), counting reasoning deltas.

Filler is code-shaped and its length is only a target -- every row records the server's
real prompt token count. stdlib only.

  bench/speed.py --url URL --model ID --engine {llama|vllm} [--depths ...] [--out FILE]
"""
import argparse, json, sys, time, urllib.request, urllib.error, statistics, threading, random
import ui

FILLER = (
    "def process(items, cfg):\n"
    "    out = []\n"
    "    for it in items:\n"
    "        if it.get('active') and it['score'] > cfg.threshold:\n"
    "            out.append(transform(it, cfg.mode))\n"
    "    return out\n\n"
)  # ~64 tokens/block, measured roughly against Qwen/DeepSeek tokenizers


def post(url, path, body, timeout, stream=False):
    req = urllib.request.Request(
        url + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=timeout)


def filler_to(target_tokens):
    reps = max(1, target_tokens // 64)
    return FILLER * reps


def stream_once(url, model, prompt, max_tokens, timeout):
    """One streamed chat request. Returns dict with ttft, wall, and usage/timings."""
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens, "temperature": 0,
        "stream": True, "stream_options": {"include_usage": True},
    }
    t0 = time.perf_counter()
    ttft = None
    usage = {}
    timings = {}
    ntok = 0
    try:
        resp = post(url, "/v1/chat/completions", body, timeout, stream=True)
    except Exception as e:
        return {"error": repr(e)[:200]}
    for raw in resp:
        line = raw.decode("utf-8", "replace").strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            obj = json.loads(data)
        except json.JSONDecodeError:
            continue
        choices = obj.get("choices") or []
        if choices:
            delta = choices[0].get("delta") or {}
            # count reasoning tokens as real first tokens
            piece = delta.get("content") or delta.get("reasoning") or delta.get("reasoning_content")
            if piece:
                if ttft is None:
                    ttft = time.perf_counter() - t0
                ntok += 1
        if obj.get("usage"):
            usage = obj["usage"]
        if obj.get("timings"):
            timings = obj["timings"]
    wall = time.perf_counter() - t0
    if ttft is None:
        ttft = wall
    return {"ttft": ttft, "wall": wall, "ntok": ntok, "usage": usage, "timings": timings}


def rates(r, engine):
    """Return (prompt_tokens, prefill_tps, decode_tps) from the right source per engine."""
    u = r.get("usage") or {}
    t = r.get("timings") or {}
    pt = u.get("prompt_tokens") or t.get("prompt_n")
    ct = u.get("completion_tokens") or t.get("predicted_n") or r.get("ntok")
    if engine == "llama" and t:
        return pt, t.get("prompt_per_second"), t.get("predicted_per_second")
    # vllm / fallback: derive from wall-clock
    decode = (ct / (r["wall"] - r["ttft"])) if r.get("wall", 0) > r.get("ttft", 0) and ct else None
    prefill = (pt / r["ttft"]) if pt and r.get("ttft") else None
    return pt, prefill, decode


def cached_tokens(r):
    d = (r.get("usage") or {}).get("prompt_tokens_details") or {}
    return d.get("cached_tokens", 0)


def vllm_metrics(url):
    try:
        body = urllib.request.urlopen(url + "/metrics", timeout=5).read().decode()
    except Exception:
        return {}
    out = {}
    for line in body.splitlines():
        for key, pfx in (("preemptions", "vllm:num_preemptions_total"),
                         ("kv", "vllm:kv_cache_usage_perc")):
            if line.startswith(pfx) and " " in line:
                try:
                    out[key] = float(line.rsplit(" ", 1)[1])
                except ValueError:
                    pass
    return out


def emit(rec, out):
    """JSONL to the machine-readable file; the terminal view is drawn via ui."""
    if out:
        out.write(json.dumps(rec) + "\n"); out.flush()


def depth_sweep(url, model, engine, depths, reps, timeout, out, md):
    ui.phase(f"depth sweep  {model}  ({engine})")
    if md: md.write(f"\n## {model} @ {url}  depth sweep ({engine})\n\n")
    tbl = ui.Table("depth sweep", ["ctx", "prompt_tok", "TTFT s", "prefill t/s", "decode t/s"], md)
    for depth in depths:
        rs = []
        with ui.console.status(f"depth {depth}: prefilling..."):
            for rep in range(reps):
                tag = random.randint(10**9, 10**10)  # unique so the cold sweep bypasses cache
                prompt = f"// run {tag}\n" + filler_to(depth) + "\nReply with exactly: ok"
                r = stream_once(url, model, prompt, 16, timeout)
                if "error" in r:
                    ui.log.error("depth %s: %s", depth, r["error"])
                    emit({"test": "speed", "depth": depth, "error": r["error"]}, out)
                    tbl.add([depth, "err", r["error"][:24], "-", "-"], style="red")
                    break
                pt, pf, dec = rates(r, engine)
                emit({"test": "speed", "target_ctx": depth, "prompt_tokens": pt,
                      "ttft_s": round(r["ttft"], 3), "prefill_tps": round(pf, 1) if pf else None,
                      "decode_tps": round(dec, 1) if dec else None}, out)
                rs.append((pt, r["ttft"], pf, dec))
        if rs:
            med = lambda i: statistics.median([x[i] for x in rs if x[i] is not None]) if any(x[i] is not None for x in rs) else None
            pt, ttft, pf, dec = med(0), med(1), med(2), med(3)
            tbl.add([int(pt) if pt else "?", int(pt) if pt else "?",
                     f"{ttft:.2f}", round(pf, 1) if pf else "?", round(dec, 1) if dec else "?"])
    tbl.render()


def concurrency(url, model, engine, levels, timeout, out, md):
    ui.phase("concurrency (aggregate decode)")
    if md: md.write(f"\n## {model} @ {url}  concurrency\n\n")
    tbl = ui.Table("concurrency", ["streams", "wall s", "agg tok/s", "preempt"], md)
    prompt = "Write 80 lines counting up from one, in words, one number per line."
    for n in levels:
        with ui.console.status(f"{n} concurrent stream(s) decoding..."):
            m0 = vllm_metrics(url) if engine == "vllm" else {}
            results = [None] * n
            def worker(i):
                results[i] = stream_once(url, model, prompt, 128, timeout)
            t0 = time.perf_counter()
            threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
            for t in threads: t.start()
            for t in threads: t.join()
            wall = time.perf_counter() - t0
            m1 = vllm_metrics(url) if engine == "vllm" else {}
        tot = sum((r.get("usage", {}).get("completion_tokens") or r.get("ntok", 0))
                  for r in results if r and "error" not in r)
        preempt = int(m1.get("preemptions", 0) - m0.get("preemptions", 0)) if m0 else "-"
        agg = tot / wall if wall else 0
        if isinstance(preempt, int) and preempt > 0:
            ui.log.warning("%d concurrent: %d preemptions (KV thrash)", n, preempt)
        emit({"test": "concurrency", "streams": n, "wall_s": round(wall, 2),
              "agg_tps": round(agg, 1), "preemptions": preempt}, out)
        tbl.add([n, f"{wall:.1f}", f"{agg:.0f}", preempt],
                style=("red" if isinstance(preempt, int) and preempt > 0 else None))
    tbl.render()


def cache_check(url, model, engine, timeout, out, md):
    ui.phase("prefix-cache verification")
    if md: md.write(f"\n## {model} @ {url}  prefix-cache\n\n")
    tbl = ui.Table("prefix cache", ["run", "prompt_tok", "cached", "hit %"], md)
    prefix = "You are a code reviewer. Reference material follows.\n" + filler_to(8000)
    with ui.console.status("cold then warm pass..."):
        r1 = stream_once(url, model, prefix + "\nQuestion: reply ok.", 8, timeout)
        r2 = stream_once(url, model, prefix + "\nQuestion: reply ok twice.", 8, timeout)
    verdict = None
    for label, r in (("1 cold", r1), ("2 warm", r2)):
        if "error" in r:
            tbl.add([label, "error", "-", "-"], style="red"); continue
        pt = (r["usage"] or {}).get("prompt_tokens") or (r["timings"] or {}).get("prompt_n") or 0
        cached = cached_tokens(r)
        if engine == "llama" and label.startswith("2"):
            reused = (r1["timings"] or {}).get("prompt_n", 0) - pt if r1.get("timings") else 0
            cached = max(cached, reused)
        hit = (100 * cached / pt) if pt else 0
        emit({"test": "cache", "run": label, "prompt_tokens": pt, "cached": cached, "hit_pct": round(hit)}, out)
        tbl.add([label, pt, cached, f"{hit:.0f}"])
        if label.startswith("2"):
            verdict = hit
    tbl.render()
    if verdict is not None:
        if verdict > 50:
            ui.summary("prefix caching", 1, 1, f"warm reused ~{verdict:.0f}% of prompt")
        else:
            ui.summary("prefix caching", 0, 1, f"only {verdict:.0f}% cached -- off or prefix too small")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--engine", choices=["llama", "vllm"], required=True)
    ap.add_argument("--depths", type=int, nargs="+", default=[512, 8192, 45056])
    ap.add_argument("--concurrency", type=int, nargs="+", default=[1, 2, 3])
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--timeout", type=int, default=1200)
    ap.add_argument("--out")
    ap.add_argument("--md")
    ap.add_argument("--logfile")
    ap.add_argument("--skip", nargs="*", default=[], help="any of: depth concurrency cache")
    a = ap.parse_args()
    ui.setup_logging(a.logfile)
    out = open(a.out, "a") if a.out else None
    md = open(a.md, "a") if a.md else None
    ui.log.info("speed  %s @ %s", a.model, a.url)
    if md: md.write(f"# speed  {a.model} @ {a.url}  {time.strftime('%Y-%m-%dT%H:%M:%S')}\n")
    if "depth" not in a.skip:
        depth_sweep(a.url, a.model, a.engine, a.depths, a.reps, a.timeout, out, md)
    if "concurrency" not in a.skip:
        concurrency(a.url, a.model, a.engine, a.concurrency, a.timeout, out, md)
    if "cache" not in a.skip:
        cache_check(a.url, a.model, a.engine, a.timeout, out, md)
    if out: out.close()
    if md: md.close()


if __name__ == "__main__":
    sys.exit(main())
