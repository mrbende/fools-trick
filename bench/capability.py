#!/usr/bin/env python3
"""Capability + reasoning + code + instruction-following via lm-evaluation-harness.

This is a thin, correct wrapper over EleutherAI's lm-eval so our numbers are directly
comparable to every published result. It does NOT reimplement any task -- it selects the
right client per node and shells out to `lm-eval`, writing standard JSON output.

Node clients (VERIFIED against the live endpoints):
  worker (llama.cpp)   -> local-chat-completions + apply_chat_template. llama.cpp returns a
                          non-OpenAI shape for /completions with logprobs.
  orchestrator (sparkinfer) -> ALSO chat-only: it does NOT expose /v1/completions at all
                          ({"detail":"Not Found"}).

Loglikelihood MC (fully verified live):
  - orchestrator (fool, real vLLM) DOES support /v1/completions with echo+prompt_logprobs,
    so proper loglikelihood scoring of MMLU/ARC/HellaSwag WORKS there (mmlu_anatomy acc=1.0
    verified). Use local-completions -> /v1/completions.
  - worker (llama.cpp) cannot echo per-token PROMPT logprobs, so it runs generative tasks only.

Routing: multiple-choice loglikelihood tasks go to the ORCHESTRATOR (completions client);
generative tasks (gsm8k, ifeval, code) run on EITHER node via the right client. capability
picks the client from --node automatically.

Task tiers (standard lm-eval task names, all generative):
  quick : gsm8k, ifeval
  gen   : gsm8k, ifeval, humaneval_plus, mbpp_plus
  full  : + mmlu_pro (generative), gpqa_main_generative_n_shot, bbh_cot_fewshot

  bench/capability.py --node worker --url ... --tier gen  --size small --out DIR
  bench/capability.py --node fool   --url ... --tier full --size small --out DIR
"""
import argparse, json, os, random, subprocess, sys

# Code tasks are NOT here: lm-eval's humaneval/mbpp extractors mis-parse this reasoning worker's
# output (it emits signature+docstring, then "Here is the completed function:" + the real fenced
# block; the lm-eval instruct filter grabs the signature-only first block -> pass@1=0 despite
# correct code). Our own bench/eval.py `code` command extracts the fenced block robustly and
# EXECUTES the EvalPlus tests -- it is the right tool for the code axis on this model. lm-eval
# owns reasoning/instruction-following/MC (where it is clean and comparable); we own code + tools.
TIERS = {
    "quick": ["gsm8k", "ifeval"],
    "gen": ["gsm8k", "ifeval"],  # reasoning + instruction-following; code via bench/eval.py code
    "mc": ["mmlu", "arc_challenge", "hellaswag", "winogrande"],  # loglikelihood, ORCHESTRATOR only
    "full": ["gsm8k", "ifeval", "gpqa_main_generative_n_shot", "bbh_cot_fewshot",
             "mmlu", "arc_challenge", "hellaswag", "winogrande"],
}

# Size -> samples per task (0 = full dataset). Sampling is RANDOM (via --samples with a
# fixed seed for reproducibility), not first-N, so small runs are representative.
SIZE_N = {"smoke": 5, "small": 25, "large": 200, "max": 0}
TASK_SIZE = {"gsm8k": 1319, "ifeval": 541, "humaneval_instruct": 164, "mbpp_plus_instruct": 378,
             "mmlu_pro": 12032, "gpqa_main_generative_n_shot": 448, "bbh_cot_fewshot": 6511,
             "mmlu": 14042, "arc_challenge": 1172, "hellaswag": 10042, "winogrande": 1267}
# Tasks that require logprobs (multiple-choice/loglikelihood): orchestrator/vLLM only.
LOGLIK_TASKS = {"mmlu", "mmlu_pro", "arc_challenge", "hellaswag", "winogrande",
                "gpqa_main_zeroshot", "truthfulqa_mc1", "truthfulqa_mc2"}
# Tasks that execute model-written code.
CODE_TASKS = {"humaneval_instruct", "mbpp_plus_instruct", "humaneval_plus", "mbpp_plus", "humaneval", "mbpp"}


def random_samples(tasks, n, seed):
    """Build a --samples JSON mapping each task to n random doc indices, for representative
    small runs. Returns None for a full run (n=0)."""
    if not n:
        return None
    rng = random.Random(seed)
    out = {}
    for t in tasks:
        size = TASK_SIZE.get(t, 1000)
        k = min(n, size)
        out[t] = sorted(rng.sample(range(size), k))
    return out


def run(node, url, model, tokenizer, tasks, n, seed, out_dir, concurrency):
    loglik = [t for t in tasks if t in LOGLIK_TASKS]
    gen = [t for t in tasks if t not in LOGLIK_TASKS]
    # Loglikelihood MC only scores where prompt_logprobs are available -> orchestrator only.
    if loglik and node != "fool":
        sys.stderr.write(
            f"[capability] loglikelihood tasks {loglik} require prompt_logprobs (orchestrator "
            f"only); skipping on node={node}. Run them with --node fool.\n")
        loglik = []
    tasks = loglik + gen
    if not tasks:
        sys.stderr.write("[capability] no runnable tasks for this node; nothing to do.\n")
        return 0

    # Loglikelihood -> completions client (/v1/completions, prompt_logprobs). Generative ->
    # chat client (/v1/chat/completions + template). If a run mixes both on the orchestrator,
    # the completions client handles both (it also does generate_until), so prefer it there.
    if loglik:
        client = "local-completions"; base = f"{url}/completions"; chat = False
    else:
        client = "local-chat-completions"; base = f"{url}/chat/completions"; chat = True

    # max_length: lm-eval defaults to 2048 when it can't read the tokenizer's context, which
    # TRUNCATES a reasoning model that thinks-then-answers -> null content -> blank scores
    # (observed: DeepSeek gsm8k=0 because reasoning ate the 2048 budget before emitting content).
    # Give the orchestrator (reasoning) a large window; the worker is fine at the default.
    max_length = 40960 if node == "fool" else 8192
    # Concurrency is node-aware: the worker serves multiple slots, but fool is a SINGLE-STREAM
    # bandwidth-bound orchestrator. Hitting fool with num_concurrent=4 causes per-request
    # TimeoutError / null-content (measured: 2 of 5 gsm8k answers dropped -> strict-match 0.40 vs
    # flexible 0.80, a pure artifact). Force fool to 1 so its capability number is real.
    eff_concurrency = 1 if node == "fool" else concurrency
    margs = (f"model={model},base_url={base},tokenizer={tokenizer},"
             f"num_concurrent={eff_concurrency},tokenized_requests=False,max_length={max_length}")
    cmd = [sys.executable, "-m", "lm_eval", "--model", client, "--tasks", ",".join(tasks),
           "--model_args", margs, "--output_path", out_dir, "--log_samples"]
    if chat:
        cmd += ["--apply_chat_template"]
    # Generation budget: reasoning models need room to think THEN answer. Code tasks also need
    # room for the full fenced block. gen_kwargs max_gen_toks applies to generate_until tasks.
    gen_toks = 8192 if node == "fool" else (1024 if any(t in CODE_TASKS for t in tasks) else 2048)
    cmd += ["--gen_kwargs", f"max_gen_toks={gen_toks}"]
    samples = random_samples(tasks, n, seed)
    if samples:
        cmd += ["--samples", json.dumps(samples)]
    if any(t in CODE_TASKS for t in tasks):
        cmd += ["--confirm_run_unsafe_code"]
    print(f"[capability] {node}: {' '.join(tasks)}  (n={n or 'full'}/task)", flush=True)
    env = {**os.environ, "HF_ALLOW_CODE_EVAL": "1"}  # required by humaneval_plus/mbpp_plus
    return subprocess.run(cmd, cwd=os.path.dirname(os.path.abspath(__file__)), env=env).returncode


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--node", required=True, choices=["worker", "fool"])
    ap.add_argument("--url", required=True, help="endpoint base incl /v1")
    ap.add_argument("--model", required=True)
    ap.add_argument("--tokenizer", required=True, help="real HF id for tokenization")
    ap.add_argument("--tier", default="gen", choices=list(TIERS))
    ap.add_argument("--tasks", default="", help="comma list; overrides --tier")
    ap.add_argument("--size", default="small", choices=list(SIZE_N),
                    help="smoke/small/large/max -> random samples per task")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", required=True, help="lm-eval output dir")
    ap.add_argument("--concurrency", type=int, default=4)
    a = ap.parse_args()
    tasks = a.tasks.split(",") if a.tasks else TIERS[a.tier]
    os.makedirs(a.out, exist_ok=True)
    return run(a.node, a.url, a.model, a.tokenizer, tasks, SIZE_N[a.size], a.seed,
               a.out, a.concurrency)


if __name__ == "__main__":
    sys.exit(main())
