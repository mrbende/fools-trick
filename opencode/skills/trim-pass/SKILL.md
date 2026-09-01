---
name: trim-pass
description: 'Shorten a long paper by elegance rather than deletion — measure where the words actually are, find redundancy, overexplanation, misplaced claims, hooks that never set, and passages that do
  not earn their length, then rewrite so the narrative gets tighter instead of thinner. Use when the user says the paper is too long, needs trimming, is bloated, needs a cut pass, has repetition, or asks
  about proportionality and what to cut. Triggers on: too long, trim, cut it down, tighten, shorten, bloated, proportionality, what would you cut, word count, page limit.'
version: 1.0.0
metadata:
  fools:
    tags:
    - writing
    - editing
    - shortening
    - structure
    related_skills:
    - writing-style
    - argumentative-prose
---


# Trim pass

A long paper is rarely long because of its sentences. It is long because sections are
doing each other's jobs, because claims are made three times in different words, and
because passages that once needed defending are still being defended. Cutting sentences
treats the symptom. This pass finds the structural cause, fixes that, and lets the
length follow.

**Load the `writing-style` skill first.** Everything below assumes it — the banned
constructions, the one-claim-one-home rule, the spiral. This skill is where you apply it
at scale.

**Start from a clean git tree** and say so. You will edit freely and often; the tree is
the undo. Verify before you begin:

```
git status --short          # must be empty
```

Then work in one pass per phase, rebuilding and verifying between phases.

---

## Trim for altitude, not only for length

A trim pass that only removes words can leave the arc worse, because the cheapest cuts are usually in
the late sections where the biggest ideas live. Before cutting, rank the sections by altitude (how
much of the world each speaks to) and protect the top.

- **Cut from the plateau, not the summit.** Adjacent sections making the same-sized claim are the
  first candidates: merge them and keep the better title.
- **A section that only illustrates is a supplement.** If its claim is already carried elsewhere, it
  is a pointer.
- **Demote figures that do not carry a claim.** If a caption can be replaced by a sentence, replace
  it and reclaim the slot. Aim for 8 to 10 in a main text.
- **Never trim a limit to save words.** Limits raise altitude, and cutting them makes the paper
  weaker as well as shorter.
- **Protect the ending.** If the trim leaves the paper finishing on a smaller claim than it did
  before, the trim failed regardless of the word count.

See `argumentative-prose` for the ladder.

## 0. The rule that governs everything

**Cut what does not earn its place; keep what carries the argument even when it repeats.**

The spiral means deliberate return is a feature. A phenomenon met four times from four
angles is structure. The same claim restated four times in different words is bloat. The
test that separates them:

> Does this pass contain a number, a comparison, a consequence, or an angle the earlier
> pass did not?

If yes, keep it and make the new angle explicit. If no, cut it and cite the earlier
statement. Apply this to every recurrence you find in Phase 2.

---

## 1. Measure before cutting

Never open a section and start trimming. Find out where the words are.

**Per-file word counts and caption share.** Strip comments and macros, count words per
section file, and separately count how much of each file is inside `\caption{}`. A
caption share above ~20% in any file means methodology has migrated into captions.

**Per-subsection counts** for every file above ~3,000 words. This is what localizes
bloat: one subsection at 2,400 words next to one at 240 tells you more than the file
total does.

**Proportionality, not just size.** Compare each section's word count against the
evidential weight it carries. A complete null that closes a real alternative deserves
its 240 words; a refinement that got 2,000 because it was hard to defend is
disproportionate. **Space should track importance, not the effort it took to convince
yourself.**

Report the table before you touch anything. It focuses everything that follows.

---

## 2. Find the structural causes

These are the four that actually produce length. Hunt them in this order — each one
found earlier makes the later ones smaller.

### 2.1 Sections doing each other's jobs

The single largest source. Diagnose with numbers, not impressions:

- **What fraction of the Discussion's numeric values already appear in the Results?**
  Extract both sets and intersect. Approaching 100% means the Discussion is summarizing
  rather than synthesizing, and 30–40% of it can go without losing an argument.
- **Is Methods larger than Introduction plus Background?** If so it is probably
  defending choices, not specifying procedures. Justification belongs where the
  objection is answered.
- **Does any claim appear verbatim in two sections?** Search for repeated 8+ word
  spans across files. The later occurrence loses.

Fix by **relocation**, not deletion: move the finding to the section whose job it is,
and leave a pointer. Length falls as a side effect and the paper gets more decisive.

### 2.2 The inverted allocation

Look for the opposite of what you expect:

- **Findings parked in the supplement** while the main text summarizes them. Anything
  cited in the abstract must have a main-text home.
- **Findings that exist only in the Discussion** — a result with numbers that appears
  nowhere in the Results. Search the Discussion for values absent from Results.
- **Load-bearing methodology in captions.**

These usually cancel out on word count — you promote as much as you demote — but they
are what makes a paper feel like a lineage of its authors' evolving understanding
rather than a decisive account.

### 2.3 The pre-emptive objection paragraph

A recognizable shape: state a result, then spend a paragraph defending it against a
reviewer who has not spoken. Each is correct and each could be two sentences. Find them
by looking for paragraphs that open by conceding ("The question needs a level to be
asked at", "Two things are easy to conflate here") and contain no new number.

### 2.4 Hooks that never set

Every promise must be paid. Enumerate:

- Formalisms named in the Background — is each one used in the Results or Discussion?
- Forward references ("we return to this in §X") — does §X deliver?
- Questions posed in the Introduction — is each answered, in order?
- Terms coined — is each used more than twice?

An unpaid hook is cut or cashed. A formalism named once and never used again is pure
cost.

---

## 3. Then the sentence level

Only after the structure is fixed. Apply `writing-style`'s banned list at scale:

- The announce-then-say family, in all its wordings.
- Emdashes: restructure, do not just substitute punctuation.
- Construction dominance: count per 1,000 words, keep every form under ~2.
- Repeated paragraph openers and repeated distinctive phrases (n-gram scan, 4- and
  5-grams appearing 4+ times that are not technical terms).
- Stock phrases: the same vivid formulation reused in three sections is a tic even
  though it was good once.

**Sentences that say nothing may go without ceremony.** A sentence that adds no number,
no qualification, no transition and no angle is deletable on sight.

---

## 4. Rewriting, not thinning

When a passage must shrink, prefer in this order:

1. **Relocate** — it belongs elsewhere and the other place needs it.
2. **Restructure** — two welded thoughts become two sentences, or one better one.
3. **Compress** — same content, fewer words.
4. **Delete** — only after the first three fail.

A trimmed paper should read as *more* decisive, not thinner. If a section reads as
having lost its argument, you compressed where you should have relocated.

---

## 5. Verify, every phase

After each phase, not at the end:

```
cd paper && make            # builds clean, no undefined refs, no overfull boxes
python paper/lint_paper.py
python scripts/verify_paper_numbers.py
```

And the loss audit, which is what makes free editing safe:

```
# every number and citation present before the pass must still be present,
# or its removal must be deliberate and explained
git show HEAD:paper/sections/X.tex | grep -o '[0-9]\+\.[0-9]\+' | sort -u
```

**Any value that disappears is either a deliberate replacement you can name, or a
regression.** There is no third category.

---

## 6. What this pass must not do

- **Do not cut disclosure.** The concessions, the caveats, the "we would rather name it
  than dress it" sentences are the most valuable prose in the paper. They are never
  bloat. If the paper must lose 3,000 words, it does not lose these.
- **Do not flatten the spiral into a line.** Deliberate return from a new angle is
  structure. Only restatement without an angle is fat.
- **Do not touch numbers, scope labels, or units** to save words. "at *N* = 100" is
  four words that prevent a defect.
- **Do not remove a qualification because the sentence is long.** Long and qualified
  beats short and overstated.

---

## 7. Report

Give the user, in this order:

1. **The measurement table** — words per section, caption share, before and after.
2. **The structural finding** — what was doing whose job, with the numbers that show it.
3. **What moved** and **what was cut**, separately. Relocation and deletion are
   different acts and the user should see which they got.
4. **What you deliberately did not cut**, and why. Usually the disclosures.
5. **Verification state** — build, lint, checks, and the loss audit.

Be honest about the total. A reorganization that improves allocation may barely change
the word count, and saying so is better than implying a cut that did not happen.
