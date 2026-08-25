#!/usr/bin/env python3
"""Memory eval: does sliding-window + recall let a long CODING session remember content that has
slid out of the live window -- and does it beat opencode's default compaction?

Grounded in the agent-memory benchmark literature (LongMemEval arXiv:2410.10813; MemGPT DMR
arXiv:2310.08560; LoCoMo arXiv:2402.17753). Design choices taken directly from them:

  - LLM-as-JUDGE, not regex. Free-form recall is scored by a verbosity-tolerant binary judge on
    the orchestrator, with PER-TYPE prompts (LongMemEval's templates): the plain judge accepts a
    correct fact embedded in a long answer but rejects subsets; knowledge-update requires the
    LATEST value; abstention checks the model correctly says "not found" (the hallucination guard).
  - PAIRED A/B, model held constant: arm=on (our memory plugin) vs arm=off (MEMORY_ENABLED=0 ->
    opencode compaction summarize-and-drops). Only the memory mechanism varies. MemGPT's DMR shows
    this exact contrast (recall ~93% vs recursive-summary ~35%).
  - CODING SUBSTRATE. No public memory dataset is code sessions; we plant facts a coding agent
    would need across a long session on THIS repo, so recall is coding-relevant, not toy trivia.
  - VALIDITY CONTROLS: (a) closed-book control -- ask each probe with NO history; anything answered
    correctly closed-book is invalid (leaks/ world-knowledge) and excluded. (b) eviction check --
    the plant turns must provably exceed the live window before probing, else we test long-context
    not memory. (c) abstention scored separately so a system that hallucinates evicted content is
    caught, not rewarded.

  bench/memory.py --project DIR --arm on|off --bury-turns 40 --out FILE --judge-url URL --judge-model M
  bench/memory.py diff --a on --b off        # paired delta with per-type breakdown
"""
import argparse, json, os, subprocess, sys, time, random

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ui
from core import chat

RESULTS = os.environ.get("MEMORY_BENCH_DIR", "/tmp/fools-trick/membench")

# Filler turns for the BURY phase: substantive coding Q&A that runs up tokens and forces the
# planted turns out of the live window, without themselves being about the planted facts.
FILLER_TURNS = [
    "Explain how a write-ahead log guarantees durability under a crash, then give the one-line gist.",
    "Walk through exponential backoff with jitter and why jitter prevents thundering-herd retries.",
    "Derive the bloom-filter false-positive rate and when the space/accuracy tradeoff is worth it.",
    "Explain how an LSM tree's background compaction bounds read amplification.",
    "Describe consistent hashing and how it minimizes key remapping when a node leaves.",
    "Explain copy-on-write snapshots and how readers stay consistent during a concurrent write.",
    "Walk through a token-bucket rate limiter: refill rate, burst capacity, admission test.",
    "Explain how a DAG build scheduler dispatches independent targets concurrently.",
]


def build_probes(seed):
    """Plant CODING facts a long session would accrue, then probe recall. Each probe is
    (type, question, gold, is_distractor). Values are chosen DISJOINT (esp. knowledge-update) so
    stale != latest lexically, and NONE are answerable from repo files alone (they're session
    decisions), so closed-book control can catch leaks."""
    rng = random.Random(seed)
    token = f"DEPLOY-{rng.randint(10000,99999)}"
    kv_old, kv_new = "q5_1", "q8_0"                   # the update: KV choice changed mid-session
    ctx = f"{rng.choice([32768, 40960, 24576])}"
    slots = "4"
    plants = [
        f"For this session, remember: our deploy token is {token}. Acknowledge and continue.",
        f"Remember we initially set the worker KV cache type to {kv_old}. Acknowledge.",
        f"Remember the worker runs {slots} concurrent slots. Acknowledge.",
        # UPDATE, stated later, disjoint value -- correct recall is the NEW value:
        f"Correction: we changed the worker KV cache from {kv_old} to {kv_new} because {kv_old} "
        f"silently spilled to CPU. Acknowledge.",
        f"Also remember we settled on {ctx} context tokens per slot. Acknowledge.",
    ]
    probes = [
        ("direct", "What is our deploy token for this session? Answer with just the token.",
         token, False),
        ("update", "What KV cache type did we settle on for the worker, and why did we change it?",
         kv_new, False),
        ("multi", "State the worker's per-slot context and its number of concurrent slots.",
         f"{ctx} and {slots}", False),
        ("distractor",
         "What Postgres connection string did I give you earlier this session? "
         "If I never gave one, say NONE.",
         "the user never provided a Postgres connection string", True),
    ]
    # The deploy token is unique, planted in the FIRST turn, and disjoint from everything else --
    # its presence in the store is the eviction-verification needle.
    return plants, probes, token


# AGENTIC-RECALL probes: end-to-end coding scale. Early turns make the orchestrator DISPATCH A
# SUBAGENT to discover a real fact from THIS repo; that finding then slides out of the window; a
# late probe requires recalling it (and combining two). This tests memory + long-context +
# delegation together on actual code -- the thing that matters for real coding sessions. Golds are
# file-discoverable facts (not world-knowledge), so a subagent read is genuinely required.
def build_agentic(project):
    # (discovery_prompt asking for a subagent dispatch, recall_question, gold)
    discover = [
        ("Dispatch a subagent to read scripts/config.sh and report the exact worker serve PORT. "
         "Just have it find and state the port number.", None, None),
        ("Dispatch a subagent to read the .opencode/plugin/memory.js file and report HOW MANY "
         "memory tools it registers (memory_write, memory_search, etc). State the count.", None, None),
    ]
    recall = [
        ("agentic-recall",
         "Earlier a subagent found the worker's serve port and the number of memory tools the "
         "plugin registers. State both, comma-separated (port, then tool count).",
         "8898 and 2", False),
    ]
    return discover, recall


# ---- LLM judge (on the orchestrator), per-type, verbosity-tolerant (LongMemEval templates) ----
def judge_answer(url, model, ptype, question, gold, answer, timeout=120):
    a = (answer or "")[:3000]
    if ptype == "distractor":
        prompt = (
            "I will give you an UNANSWERABLE question, an explanation of why, and a model's "
            "response. Answer yes if the model correctly identifies that the information was "
            "never provided (it may say the info is missing, not given, or NONE). Answer no if "
            "the model fabricates or guesses an answer.\n\n"
            f"Question: {question}\nExplanation: {gold}\nModel Response: {a}\n\n"
            "Did the model correctly identify this as unanswerable? Answer yes or no only.")
    elif ptype == "update":
        prompt = (
            "I will give you a question, the correct (latest) answer, and a model response. "
            "Answer yes if the response contains the correct LATEST answer. If the response "
            "contains previous/stale information ALONGSIDE the updated answer, still answer yes "
            "as long as the latest answer is present. Answer no if it gives only the stale value.\n\n"
            f"Question: {question}\nCorrect (latest) Answer: {gold}\nModel Response: {a}\n\n"
            "Is the model response correct? Answer yes or no only.")
    else:  # direct, multi
        prompt = (
            "I will give you a question, a correct answer, and a model response. Answer yes if "
            "the response contains the correct answer (it may be embedded in a longer response). "
            "Answer no if it only contains a subset of the required information or the wrong "
            "value.\n\n"
            f"Question: {question}\nCorrect Answer: {gold}\nModel Response: {a}\n\n"
            "Is the model response correct? Answer yes or no only.")
    try:
        v, _ = chat(url, model, prompt, max_tokens=1500, timeout=timeout)
        return "yes" in v.lower()[:200]
    except Exception:
        return False


def episodes_have(project, thread, needle):
    """Query the durable store (via the JS query CLI) for whether an episode containing `needle`
    exists in this thread. Used by the eviction-verification gate."""
    q = os.path.join(project, ".opencode/memory/query.mjs")
    try:
        p = subprocess.run(["node", q, "has", thread, needle], cwd=project,
                           capture_output=True, text=True, timeout=30, env={**os.environ})
        return p.stdout.strip().splitlines()[-1].strip() == "yes" if p.stdout.strip() else False
    except Exception:
        return False


def episode_count(project, thread):
    q = os.path.join(project, ".opencode/memory/query.mjs")
    try:
        p = subprocess.run(["node", q, "count", thread], cwd=project,
                           capture_output=True, text=True, timeout=30, env={**os.environ})
        return int(p.stdout.strip().splitlines()[-1]) if p.stdout.strip() else 0
    except Exception:
        return 0


def run_turn(project, prompt, session_id, timeout, extra_env=None):
    cmd = ["opencode", "run", "--format", "json"]
    if session_id:
        cmd += ["--session", session_id]
    cmd += [prompt]
    env = {**os.environ, **(extra_env or {})}
    try:
        p = subprocess.run(cmd, cwd=project, capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return "", session_id, 124
    parts, sid = [], session_id
    for line in p.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if sid is None and ev.get("sessionID"):
            sid = ev["sessionID"]
        pt = ev.get("part") or {}
        if pt.get("type") == "text" and pt.get("text"):
            parts.append(pt["text"])
    return "\n".join(parts), sid, p.returncode


def closed_book_control(project, probes, judge_url, judge_model, timeout):
    """Ask each probe in a FRESH session with no planted history. Any probe answered correctly
    closed-book is invalid (leaks / world-knowledge) and excluded from the real run."""
    valid = []
    for ptype, q, gold, is_distractor in probes:
        ans, _, _ = run_turn(project, q, None, timeout)
        # distractor answered "correctly" closed-book is EXPECTED (nothing was given) -> keep it.
        leaked = (not is_distractor) and judge_answer(judge_url, judge_model, ptype, q, gold, ans, timeout)
        if leaked:
            ui.log.warning("probe %r answerable closed-book -> EXCLUDED (leak/world-knowledge)", ptype)
        else:
            valid.append((ptype, q, gold, is_distractor))
    return valid


def run_arm(a):
    os.makedirs(RESULTS, exist_ok=True)
    out = open(os.path.join(RESULTS, f"{a.arm}.jsonl"), "w")
    plants, probes, plant_needle = build_probes(a.seed)

    discover, agentic_probes = build_agentic(a.project)

    ui.phase(f"memory eval -- arm={a.arm}, bury={a.bury_turns} turns")
    # Validity control: purge probes answerable without history (agentic golds are repo-derived,
    # so they're checked too -- if a probe is answerable closed-book the model didn't need memory).
    probes = closed_book_control(a.project, probes + agentic_probes,
                                 a.judge_url, a.judge_model, a.timeout)
    if not probes:
        ui.log.error("all probes excluded by closed-book control; nothing to measure"); return

    arm_env = {} if a.arm == "on" else {"MEMORY_ENABLED": "0"}
    sid = None
    # PLANT (session facts)
    for t in plants:
        _, sid, rc = run_turn(a.project, t, sid, a.timeout, arm_env)
        if rc == 124:
            ui.log.warning("plant turn timed out; aborting arm"); out.close(); return
    # DISCOVER (agentic: dispatch subagents to find real repo facts, which later get evicted)
    for t, _, _ in discover:
        _, sid, rc = run_turn(a.project, t, sid, a.timeout, arm_env)
        if rc == 124:
            ui.log.warning("discovery turn timed out; continuing"); break
    # BURY -- force the plant turns past the live window
    for i in range(a.bury_turns):
        _, sid, rc = run_turn(a.project, f"(turn {i}) {FILLER_TURNS[i % len(FILLER_TURNS)]}",
                              sid, a.timeout, arm_env)
        if rc == 124:
            ui.log.warning("bury turn %d timed out; proceeding to probe", i); break

    # EVICTION-VERIFICATION GATE (the validity control that makes this a MEMORY test, not a
    # long-context test). For the memory-ON arm, the earliest planted fact MUST have been evicted
    # from the live window and persisted as an episode by now. If it hasn't, the bury phase was
    # too short -- the fact is still in context, so a correct probe answer would prove nothing.
    # Abort as an invalid measurement rather than report a misleading number.
    if a.arm == "on":
        # give the drain a moment, then check the deploy-token needle is in the store for this thread
        time.sleep(2)
        n_ep = episode_count(a.project, sid or "")
        evicted = episodes_have(a.project, sid or "", plant_needle)
        ui.log.info("eviction gate: thread=%s stored_episodes=%d earliest-plant-evicted=%s",
                    sid, n_ep, evicted)
        if not evicted:
            ui.log.error(
                "INVALID: planted fact %r not found in the memory store after %d bury turns -- it "
                "likely never left the live window. Increase --bury-turns (or lower "
                "WINDOW_INPUT_TOKENS). Not reporting a number that would measure long-context, not "
                "memory.", plant_needle, a.bury_turns)
            out.write(json.dumps({"test": "memory", "arm": a.arm, "summary": True, "valid": False,
                                  "reason": "no eviction: bury insufficient", "bury_turns": a.bury_turns,
                                  "stored_episodes": n_ep}) + "\n")
            out.close(); return
    # PROBE
    tbl = ui.Table(f"memory[{a.arm}]", ["probe", "pass"], None, justify=["left", "center"])
    passed = total = 0
    per_type = {}
    for ptype, q, gold, is_distractor in probes:
        ans, sid, rc = run_turn(a.project, q, sid, a.timeout, arm_env)
        hit = judge_answer(a.judge_url, a.judge_model, ptype, q, gold, ans, a.timeout)
        passed += hit; total += 1
        per_type.setdefault(ptype, [0, 0]); per_type[ptype][0] += hit; per_type[ptype][1] += 1
        out.write(json.dumps({"test": "memory", "arm": a.arm, "probe": ptype, "pass": bool(hit),
                              "distractor": is_distractor, "rc": rc}) + "\n"); out.flush()
        tbl.add([ptype, "yes" if hit else "NO"], style=(None if hit else "red"))
    tbl.render()
    pct = 100.0 * passed / total if total else 0.0
    out.write(json.dumps({"test": "memory", "arm": a.arm, "summary": True, "passed": passed,
                          "total": total, "accuracy_pct": round(pct, 1),
                          "per_type": {k: v for k, v in per_type.items()}}) + "\n")
    out.close()
    ui.log.info("memory[%s]: %d/%d = %.1f%%", a.arm, passed, total, pct)


def diff(a_label, b_label):
    def load(label):
        path = os.path.join(RESULTS, f"{label}.jsonl")
        s = {}
        with open(path) as f:
            for line in f:
                r = json.loads(line)
                if r.get("summary"):
                    s = r
        return s
    A, B = load(a_label), load(b_label)
    print(f"\n{'':12}{a_label:>8}{b_label:>8}{'delta':>8}")
    av, bv = A.get("accuracy_pct", 0), B.get("accuracy_pct", 0)
    print(f"{'overall':12}{av:>7.1f}%{bv:>7.1f}%{av-bv:>+7.1f}")
    types = set(A.get("per_type", {})) | set(B.get("per_type", {}))
    for t in sorted(types):
        ap = A.get("per_type", {}).get(t, [0, 0]); bp = B.get("per_type", {}).get(t, [0, 0])
        apct = 100*ap[0]/ap[1] if ap[1] else 0; bpct = 100*bp[0]/bp[1] if bp[1] else 0
        print(f"{t:12}{apct:>7.1f}%{bpct:>7.1f}%{apct-bpct:>+7.1f}")
    print(f"\n(a={a_label} memory-on, b={b_label} compaction baseline; delta>0 = memory helps)")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("run", help="run one arm")
    r.add_argument("--project", required=True)
    r.add_argument("--arm", choices=["on", "off"], default="on")
    r.add_argument("--bury-turns", type=int, default=40)
    r.add_argument("--timeout", type=int, default=600)
    r.add_argument("--seed", type=int, default=42)
    r.add_argument("--judge-url", required=True, help="orchestrator base WITHOUT /v1")
    r.add_argument("--judge-model", required=True)
    d = sub.add_parser("diff", help="paired delta of two arms")
    d.add_argument("--a", default="on")
    d.add_argument("--b", default="off")
    # allow bare `run`-style invocation for symmetry with other bench modules
    a = ap.parse_args()
    if a.cmd == "diff":
        diff(a.a, a.b)
    elif a.cmd == "run":
        ui.setup_logging(None)
        run_arm(a)
    else:
        ap.print_help()


if __name__ == "__main__":
    sys.exit(main())
