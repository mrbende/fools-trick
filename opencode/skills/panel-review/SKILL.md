---
name: panel-review
description: 'Run a multi-persona adversarial peer-review panel on a manuscript, proposal, or long design document. Spawns 10-14 independent reviewer subagents with distinct professional expertise and critique
  levels, then synthesizes their reports into one prioritized review. Use when the user asks for a "panel review", "reviewer panel", "peer review simulation", "review as if submitting", or wants a document
  stress-tested from many povs at once. Triggers on: panel review, multi-reviewer, referee reports, pre-submission review, red-team a paper.'
version: 1.0.0
metadata:
  fools:
    tags:
    - writing
    - review
    - adversarial
    - panel
    - qa
    related_skills:
    - close-read
    - argumentative-prose
---


# Panel review

Simulate journal peer review by fanning out independent reviewer personas over one
document, then synthesizing. First run: `paper/reviews/` (2026-07-27), 13 reviewers,
~105k words of reports, on the ingression paper.

The value is **not** the individual reports — it is (a) which criticisms *multiple
independent reviewers reach separately*, and (b) which of those survive verification
against ground truth. Everything below is in service of those two things.

---

## One reviewer always reads for structure

Whatever the persona mix, reserve one slot for a reviewer who reads **only the section and figure
titles, the abstract, and the last paragraph** — and never the body. Their brief:

- Is the argument reconstructible from the titles alone?
- Does each section make a larger claim than the one before it?
- Does the paper end on its biggest idea, or on whatever the authors ran last?
- Is the ending a claim about the world or a claim about the paper?
- Which title asserts more than a skim of its own subsection supports?

This reviewer catches what every body-reading reviewer misses, because a flat or anticlimactic arc is
invisible when you are checking claims one at a time. Their report is short and goes first in the
synthesis, since reordering is cheap and rewriting is not.

See `argumentative-prose` for the standard they are reviewing against.

## 1. Read the document yourself first

Do not skip this. You cannot orchestrate or synthesize what you haven't read, you
can't detect when a reviewer has misread something, and you can't verify their
technical claims later.

Read every section in full before spawning anything. For a paper: `main.tex`, all
`sections/*.tex`, skim the `.bib`, list the figures. Budget ~5 tool calls.

---

## 2. Design the roster

Ten to fourteen personas. Each needs three things: **a professional identity**, **a
stance** (friendly / critical / adversarial), and **a persona-specific attack surface**
— the 6–12 concrete things *this* reviewer is best placed to catch.

The attack surfaces are what make the reports non-redundant. Writing them requires
having read the document (see §1). Generic "review this paper" prompts produce
thirteen copies of the same review.

### The roster that worked

| # | Persona | Stance | What only they caught |
|---|---|---|---|
| 1 | Domain wet-lab scientist (the paper's biology field) | Friendly, rigorous | The core analogy is *inverted* vs. the literature it invokes |
| 2 | Statistician / methodologist | Harsh | Unit-of-analysis inconsistency across the whole paper |
| 3 | Theorist from the relevant physics/math | Critical, constructive | The headline null model is the wrong null |
| 4 | Theorist from the relevant CS/eng field | Skeptical, territorial | Reimplementability audit; "is any of this surprising?" |
| 5 | Philosopher of science | Critical, precise | Conceptual audit: which terms shift meaning between sections |
| 6 | Practitioner/clinician in the application domain | Harsh on translation | Missing 70-year-old prior literature; what would make it matter |
| 7 | Methods specialist for the paper's weakest arm | Detail-obsessed | A load-bearing mathematical identity is stated incompletely |
| 8 | Bright undergraduate in the field | Honest reading log | The best craft feedback in the panel, by a distance |
| 9 | Journal handling editor | Pragmatic | Scope, venue fit, press liability, self-citation ratio |
| 10 | Insider critic of the paper's pet formalism | Critical, generous | Verified an algebraic identity the authors missed |
| 11 | **Reviewer 2** (adversarial generalist, default reject) | Brutal | The single sharpest logical hit in the panel |
| 12 | Smart lay professional adjacent to the topic | Allergic to jargon | Arithmetic that doesn't close; jargon hiding simple ideas |
| 13 | Science editor / narrative structuralist | Craft | Word counts, act structure, the ten best and worst sentences |

### Roster principles

- **At least two non-experts** (#8, #12). They produce the most actionable writing
  feedback and they catch arithmetic. Frame them as *reader reports*, not reviews —
  ask for a log, not a verdict.
- **Exactly one true adversary** (#11). Instruct them to find the *load-bearing*
  weakness and to verify each hit against the text before stating it. Two adversaries
  is redundant; zero means nobody probes the weakest joint.
- **One editor** (#9). Only they will surface scope, venue, housekeeping, press risk,
  and citation-pattern problems. Highest ratio of actionable-to-effort in the panel.
- **One insider critic per formalism the paper borrows.** Someone who *uses* the
  framework and resents its misuse is far more useful than an outsider.
- **A specialist for the weakest arm**, even if that arm is 5% of the paper. It will
  be the part that determines how the whole document is judged.
- Vary stance deliberately. Uniformly harsh panels produce noise; uniformly friendly
  ones produce nothing.

Swap personas to match the document. For a grant: add a program officer, a budget
reviewer, a competing PI. For a design doc: an SRE, a security engineer, the
on-call who inherits it, a new hire.

---

## 3. Write the prompts

### Shared scaffold — every reviewer gets this

```
You are acting as an anonymous peer reviewer for an unspecified high-tier
[FIELD] journal. You have been sent a manuscript. You have NO other context:
no access to the authors' code, no repo history, no prior conversations.

THE MANUSCRIPT is at <absolute path>/
  - <enumerate every file>
  - figures/*.png (you MAY view figures with Read if a claim depends on one)

RULES:
- Read EVERY section file in full, in order, before forming judgments.
- Do NOT read anything outside <paper dir>. No scripts/, no output/, no git.
- If you find planning documents inside the paper dir, DO NOT read them —
  they are not part of the submission.
- Judge the manuscript as submitted.

YOUR PERSONA:
<identity, training, what they know cold, what they don't know,
 stance, and what they're impatient with>

Pay particular attention to:
<6-12 concrete attack surfaces, with section refs and specific numbers>

OUTPUT FORMAT (use these exact headings):
## Recommendation
## What I think this paper claims
## Strengths
## Major concerns          (numbered, section-specific, with the fix)
## Minor concerns
## Reading experience      (where you followed, where you got lost — be specific)
## Significance
## Top three changes

Your report is the deliverable — write it as your final message, not to a file.
```

### The isolation rule is load-bearing

Reviewers must not see the code, the git history, or planning docs. In the first run
this is exactly what made three reviewers independently conclude that a headline
variable was circular — because the paper's Methods sentence said it was. The code
said otherwise. **That gap between what the paper says and what the work does is the
single most valuable thing the panel finds, and you only find it by keeping reviewers
paper-only and then checking yourself.**

### Non-expert prompts are different

Do not use the review scaffold. Ask for a *log*:

```
## Did I understand it? (honest self-assessment)
## My N-sentence version for a friend      (+ which parts am I unsure of)
## Section-by-section reading log          (takeaway / stumble / hard 1-5 / interesting 1-5)
## Words I did not know
   (explained well | used before explaining | never explained)
## Sentences I had to read three times     (quote them)
## Moments that clicked                    (quote them)
## Where I got bored                       (be specific and unashamed)
## Did the figures help?
## Did I feel patronized?
## What I'd tell the authors
```

The "quote them" instruction is what turns vague impressions into edits.

### Persona-specific prompts should name numbers

Vague: "check the statistics." Useful: "Interrogate the n=48 vs 48 permutation at
p=0.0001 — that is the floor for 9,999 iterations; check the clustering structure of
those 48." Give each reviewer the specific hooks their expertise applies to. This is
where your §1 read pays off.

---

## 4. Launch

- Use `Agent` with `subagent_type: general-purpose`.
- **Batch 6–7 per message** so they run concurrently. Two messages for a 13-panel.
- Each report costs ~90–120k subagent tokens and takes 7–12 minutes. A 13-panel is
  roughly 1.4M subagent tokens and ~20 minutes wall clock, arriving staggered.
- They report back as task notifications. Keep responses between notifications to one
  line — do not summarize or speculate about pending reviewers.
- Never re-delegate a reviewer's work. If a report is thin, `SendMessage` that agent.

---

## 5. Verify before you relay — the step that matters most

When ≥2 reviewers independently make the same technical claim, **check it yourself
against the code or data.** This is the orchestrator's unique contribution and it
takes ~5 tool calls.

Three outcomes, all valuable:

1. **Confirmed defect.** First run: three reviewers said the top-down control was a
   definitional zero. `grep` found `("topdown", algo, algo, ...)` — homogeneous — and
   the metric returns hard `0.0` when the baseline is zero. Real, and the most
   consequential finding of the exercise.
2. **Confirmed misreading, caused by the document.** Three reviewers said a headline
   driver was circular. The code computed something else entirely. The reviewers were
   wrong; the paper's Methods sentence was wrong; the *fix* is in the paper. Report
   both halves.
3. **Reviewer error.** State that you checked and it doesn't hold. Do not relay a
   wrong objection just because it was confidently written.

Convergence means "something is wrong at this spot," not "the stated conclusion is
correct." Never relay a convergent claim unverified.

---

## 6. Synthesize

Write `00-SYNTHESIS.md` first, before dumping the individual reports. Structure:

1. **Verdict table** — every reviewer, one row. The spread is information.
2. **Verified defects** — with file:line evidence. Lead with these.
3. **Convergent criticisms**, each tagged with how many reviewers reached it
   independently (`9/13`, `5/13`). Count is the ranking function.
4. **Readability findings** — synthesized from the non-experts plus whatever the
   specialists said about flow. Give this real weight; it's usually the most
   actionable section and the easiest to act on.
5. **What every reviewer praised** — so the author doesn't sand off what works while
   fixing what doesn't. Panels reliably converge here too.
6. **Your own reading** — 2–4 observations no single reviewer made, from seeing all
   thirteen at once. This is your actual job. First run: (a) the rigor lapses all
   lapse *toward* the thesis, which reads as motivated even when it isn't; (b) the
   biology arc runs strong→weak so the loudest moment precedes the quietest claim;
   (c) the split is an upgrade, not a concession.
7. **Priority-ordered action list**, tiered: blockers / high-value experiments /
   framing / craft.
8. **Index table** of the individual reports.

Then write each report verbatim to `NN-persona-name.md`. Verbatim — do not compress.
The author will want to read the full argument on whichever criticism lands.

---

## 7. What to tell the user

Relay: the verdict spread, the verified defects with evidence, the top convergent
criticisms with counts, your own synthesis, and where the reviewers *disagreed* —
disagreements usually mark a genuine authorial choice rather than a defect.

Flag explicitly that the reports contain errors of their own, and that figure-panel
claims read off PNGs should be spot-checked. A panel is a generator of hypotheses
about a document, not an oracle.

---

## Calibration notes from the first run

- **13 reviewers, zero accepts, zero minor revisions.** A panel this adversarial will
  never return "accept." Read the *spread* (6 major / 2 reject / 1 desk-reject), not
  the mode.
- **The single most-reported item** (9/13) was a misattributed statistic in the
  abstract — a one-sentence fix. Panels are unreasonably good at finding the cheap
  high-value edits alongside the hard ones.
- **Seven reviewers independently observed that aphorism density correlated with
  argument weakness.** Convergence on *craft* is as real a signal as convergence on
  method, and nobody else will ever tell an author this.
- **The undergraduate found a truncated sentence** that twelve experts read past.
- **Eleven of thirteen independently said "this is two papers," and all eleven put the
  seam in the same place.** When a structural verdict converges that hard, it is the
  finding.
- Cost: ~1.4M subagent tokens, ~20 min wall clock, ~105k words out. Worth it before a
  real submission; overkill for a draft still in motion.
