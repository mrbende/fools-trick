---
name: close-read
description: 'Read a long document section by section from zero context, verifying every quantitative claim against canonical output as you go, writing a persistent per-unit account (what it taught, what
  read well, what was muddled, what it called back to, strongest and weakest sentences, numbers checked) and compounding those into a growing higher-level synthesis, ending in an integrated read of the
  whole. Use when the user asks for a "close read", "fresh eyes read", "end-to-end read", "read the whole paper", "section by section review", or wants a systematic comprehension-plus-verification pass
  rather than an adversarial defect hunt. Triggers on: close read, fresh eyes, end to end, section by section, read it like a reviewer would, comprehension pass, is it ready.'
version: 1.0.0
metadata:
  fools:
    tags:
    - writing
    - review
    - verification
    - comprehension
    - qa
    related_skills:
    - panel-review
    - trim-pass
    - argumentative-prose
---


# Close read

One reader works through a document in order, one unit at a time, writing down what
it understood **and checking what it was told** before moving on. Notes persist, so
the read survives context exhaustion and resumes cleanly. Each pass begins by reading
the accumulated synthesis, so understanding compounds instead of resetting.

Two things happen at once and both are required:

- **Comprehension.** What does this say, does it read well, what is muddled, what
  does it call back to.
- **Verification.** Does every number trace to canonical output. Does every figure
  show what its caption claims.

Comprehension alone produces a nice essay about a document that may be wrong.
Verification alone produces a defect list that misses the argument falling apart.

**This is not `panel-review`.** That fans out adversarially — many readers at once,
value in convergence. This is one reader in order, and it finds the class only
sequential reading finds: a term defined twice and differently, a claim that quietly
changes strength between sections, a callback to something never actually said.

---

## Structural reading: does it ascend?

Alongside the per-unit account, keep one running answer: **is each section bigger than the last?**
Comprehension reading catches muddle inside a section and misses a flat or anticlimactic arc, which
is the defect a reader feels as "thorough but somehow deflating".

Three checks, all cheap, recorded once in the synthesis rather than per section:

- **Titles only.** Copy the section titles into a list and read nothing else. Is the argument
  reconstructible? Does any title name a method rather than a claim? Is any title smaller than the
  one before it?
- **Where was I convinced?** If you can point to one paragraph, the argument is a syllogism and
  fragile. If you cannot, the spiral worked. Note which passes did the work.
- **What does the ending claim?** A paper that ends on a robustness check, on whatever ran last, or
  on a claim about the paper rather than the world has its ladder upside down. Say so at the top of
  the synthesis, because reordering is cheap before drafting and expensive after.

See `argumentative-prose` for the ladder these checks are against.

## Why it is built this way

**The author cannot do this.** Whoever wrote the document has stopped seeing its
sentences. The value comes entirely from arriving with no memory of what was
intended, so **never run this in a session that wrote the document.** If the
conversation contains the drafting, dispatch subagents or tell the user to start
fresh. Do not let this slide; it is the whole point.

**Context runs out mid-read.** A long document will not fit alongside careful notes.
The notes are the state, on disk. Never hold the read in conversation memory alone.

**Comprehension resets.** Reading section 9 having forgotten section 2 produces nine
disconnected summaries. The synthesis, re-read every pass, is what makes it one read.

---

## 0. The hazard list

These are the defect classes that actually turn up, in rough order of how often. Hunt
them explicitly; do not wait for them to announce themselves.

**Claiming a cleaner story than the data.** The single most common defect. A word
like *proportional*, *monotone*, *holds steady*, *largest in both*, *converts almost
everything*, *indistinguishable* — attached to a series that dips, peaks, reverses, or
ranks second. Usually true of a neighbouring quantity and false of the one plotted.
Whenever you meet a shape word, check the shape.

**Caption drift.** The prose was revised, or the figure was rebuilt, and the caption
now describes the previous version: wrong colours, wrong panel count, wrong direction,
wrong filter, a number that moved. Endemic wherever figures have been remade.

**A filter that deletes the top of its own predictor's range.** The hardest defect to see and
the one that most changes a result. An exclusion criterion is applied for a good reason -- absorbed
runs are dropped because a stopped system holds its arrangement for reasons unrelated to the
phenomenon -- and the excluded set turns out to be defined *by the very variable under study*. The
screen then reports a null on that variable, correctly, on a sample from which its extreme values
have been removed by the effect it produces.

In one manuscript: 29 of 91 pairings absorbed in every run and were filtered out; those 29 were
exactly the maximally opposed pairings; the mechanism screen consequently found directional
opposition to predict nothing, while a companion experiment found direction to be the entire effect.
Both analyses were right. The contradiction was in the filter.

**How to catch it.** For any analysis run on a filtered subset, compute the key predictors *on the
excluded rows too* and compare the distributions. If the exclusion is independent of your predictors
you lose nothing. If it is not, you have a range restriction whose cause is the mechanism, and that
is a finding rather than a nuisance -- it usually means the variable operates in two regimes and the
filter separates them.

**Outcome classes are not bookkeeping.** Before trusting any aggregate, tabulate the terminal states
per block: completed, stopped, truncated, halted. A block that is 92% truncated cannot support an
endpoint that assumes completion; a block that is 54% absorbed is reporting on the minority that
moved; a control arm at 0% absorbed against a treatment arm at 20% is not a like-for-like comparison
unless the analysis intersects them. The composition varies enormously between blocks in the same
corpus and every filter downstream inherits it.

**Pooling and confounding.** A claim computed across conditions the document
elsewhere insists on separating — pooling sizes, damage levels, completed and
unfinished runs, or comparing two arms measured under different filters. Ask of every
aggregate: *what got pooled to produce this, and does the comparison hold it fixed?*

**Unit-of-analysis drift.** Statistics quoted at whichever unit gives the better
answer — per-observation where large *n* buys significance, per-group where it
doesn't. Watch for the discipline being applied in one section and dropped in another.

**Unanchored magnitude.** A headline effect with no scale. Ask: *do I now know
whether this is big?* If the document never says what the metric's range or reference
points are, that is a finding, and readers will ask.

**Orphans and phantoms.** A figure or table nothing in the text points to; a
cross-reference to something that does not say what the citing sentence claims.

**Hedging mismatch.** Hedged where the evidence is solid, or flat where it is thin.
Both are defects. Note which.

**Claim placement.** A load-bearing claim buried in supplementary material, or a
supplementary caveat that the main text needs. Ask where each claim *should* live.

---

## 1. Set up

Confirm the reader is not the author. Then find the units, locate ground truth, and
create the ledger.

A **unit** is a subsection, a section with no subsections, or **one figure with its
caption**. Figures are units in their own right — a figure disagreeing with its
caption is exactly what this catches, and skipping figures is how this skill gets run
badly. Include tables. Aim for 20–50 units.

**Locate ground truth before reading anything.** Find the canonical outputs the
document's numbers should trace to — stats JSON, summary CSVs, whatever the project
treats as authoritative — and note the paths in the plan. If the project has a
canonical run directory or symlink, use it and no other. A read that cannot check a
number is worth much less than one that can.

```
<doc-dir>/reviews/close-read/
  00_PLAN.md          ordered unit list (pending/done) + ground-truth paths
  01_units/NN_<slug>.md
  02_SYNTHESIS.md     the compounding higher-level read
  03_CLAIMS.md        running claim ledger (see §3)
  04_FINDINGS.md      written last
```

Write `00_PLAN.md` before reading, so the read cannot silently skip a unit.

---

## 2. The per-unit pass

For each unit in order:

1. **Read `02_SYNTHESIS.md` and `03_CLAIMS.md` first.** Always, even at unit 30.
2. **Read the unit in full.**
3. **Check its numbers** against canonical output. Not a sample — every quantitative
   claim in the unit.
4. **Write `01_units/NN_<slug>.md`** with all of these headings:

   - **What it says** — two or three sentences in your own words. If you cannot
     compress it, say so; that is a finding about the writing.
   - **What I learned** — what you now know that you did not before.
   - **Numbers** — each quantitative claim, with the canonical value beside it and
     one of: `OK` / `MISMATCH` / `UNTRACEABLE` (could not find a source) /
     `ASSERTED` (stated as fact but nothing computes it). Untraceable and asserted
     are findings, not gaps in your effort.
   - **What read well** — specific and quoted. Vague praise is worthless.
   - **What was muddled** — undefined jargon, sentences you read twice, notation
     introduced without explanation, a claim whose scope you could not pin down.
     Quote it, say what broke.
   - **Callbacks** — what this reaches back to, and whether the earlier thing
     actually supports it. Open the earlier unit's note and verify. A callback to
     something never said is a real defect and this is where it surfaces.
   - **Hazards** — walk §0 explicitly. Note the ones that apply and the ones you
     checked and cleared.
   - **Strongest sentence** — quoted, with why.
   - **Weakest sentence** — quoted, with why. **Always name one**, including in
     strong units. Without this the read drifts into appreciation.
   - **Open questions** — what you expect a later unit to resolve.

5. **Update `02_SYNTHESIS.md` and `03_CLAIMS.md`** — integrate, don't append.
6. **Mark the unit done in `00_PLAN.md`.** This is the resume point.

One unit, one note, one integration, then the next. Batching recreates the
context-exhaustion failure this design exists to prevent.

### Figures: three passes, in this order

Order matters. Doing it backwards contaminates the reading.

1. **Look at the image with the caption covered.** What does it show? What would you
   conclude? Write that down first — it is the only unbiased look you get, and the
   "what a reader takes away" gap is measured from it.
2. **Read the caption.** Does it describe *this* image — the colours named, the panel
   count, the direction of travel, the counts, the filter? Does the text's use of the
   figure match?
3. **Read the generator.** Some defects live only here: a colour channel pooling
   conditions the axes hold fixed, a filter applied to one arm and not the other, two
   generators writing one filename, a hardcoded literal that stopped tracking its
   source. Check what the code actually selects, and whether the figure has exactly
   one producer.

### When your check disagrees with the document

**Suspect your own computation first.** A quick reimplementation almost never matches
the canonical method — different filter, different aggregation level, a completion or
damage condition the pipeline applies and you didn't. Before recording `MISMATCH`,
find the code that produced the published number and read what it actually does. If
you cannot find it, that is `UNTRACEABLE`, which is a different and also useful
finding. Recording a mismatch that turns out to be your own error costs the read its
credibility.

---

## 3. The two compounding files

**`02_SYNTHESIS.md`** is rewritten each pass and holds a **higher abstraction than
the unit notes** — a level that should visibly rise as the read proceeds. Keep:

- **The argument so far** — the spine in prose: what is claimed, on what evidence, in
  what order.
- **How the pieces connect** — which units depend on which; where the load sits.
- **Recurring strengths** and **recurring weaknesses** — patterns, not lists. Two
  instances of one problem is a pattern and belongs here, not in two unit notes.
- **Open questions** — carried forward, struck when answered. One surviving to the
  end is a finding.
- **Predictions** — what the remaining units should do. Record when you are wrong.

Test at unit 30: someone reading only the synthesis understands the argument and
knows where to be sceptical. If it reads like unit 5 with more bullets, the read is
failing at its main job.

**`03_CLAIMS.md`** is a table: claim, value, where stated, verification status. This
is what catches a claim changing strength between sections — the thing no single-unit
note can see. When a later unit restates an earlier claim more strongly, more weakly,
or with a different number, the ledger makes it obvious.

---

## 4. Running to completion

State is on disk, so the read is self-sustaining. Work the plan until every unit is
done. If context runs low, stop cleanly **after a completed unit** — the next reader
resumes from the plan and synthesis with no loss. Never stop mid-unit.

For long documents, dispatch a subagent per unit: it gets the current synthesis, the
claim ledger, its one unit, and §0 and §2 above; it returns the note. The orchestrator
integrates. This keeps the compounding while keeping any one context small.

---

## 5. The final integration

Only after every unit is done. Write `04_FINDINGS.md`:

- **The document as one thing** — its argument in a page. Not a section summary; a
  statement of what it establishes.
- **Defects** — anything simply wrong: a number that disagrees with canon, a figure
  contradicting its caption, a claim that changes between sections, a callback to
  something never said, an unsupported assertion. Cite unit, quote, give the
  canonical value. Rank by whether it changes a conclusion. **Highest-value output —
  keep strictly separate from matters of taste.**
- **Unverifiable claims** — everything `UNTRACEABLE` or `ASSERTED`. A reviewer will
  find these; better here.
- **Where it is weakest** — ranked, with units, as argument rather than error.
- **The muddle list** — every clarity and jargon complaint, gathered. Individually
  minor, collectively the most actionable edit list the author will get.
- **Questions never answered.**
- **What a first-time reader takes away** — versus what the document intends,
  measured against the blind figure passes. A gap here is worth more than any line
  edit.

Then report: the argument as you understand it, the defects ranked, the top few
weaknesses, and the takeaway gap. Tight prose; the files hold the detail.

---

## Doing it well

**Quote, always.** "The methods are unclear" is unusable. Quote the sentence, say
what broke.

**Accuracy before consistency.** If a claim is wrong, say so — do not first make it
consistent with the wrong version elsewhere. And when several places disagree, the
majority is not automatically right; check canon.

**Every shape word gets its shape checked.** *Rises, holds, proportional, monotone,
largest, all, none, always.* This one habit catches more than any other.

**Name a weakest sentence in every unit.**

**Separate "I did not understand" from "this is wrong."** Both matter, different
places: muddle list versus defects.

**Verify callbacks rather than trusting them.**

**Let the abstraction rise.** A synthesis that is only getting longer is not getting
better.
