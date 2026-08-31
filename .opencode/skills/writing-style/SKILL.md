---
name: writing-style
description: 'The house prose style — how sentences are built, how a claim lands, how a subsection is shaped, how an argument spirals, and the constructions that are banned. Use when drafting or revising
  any prose, when text "sounds like AI" or "doesn''t sound like me", when checking a draft against the style, or before writing a new section. Triggers on: writing style, prose, sounds like AI, AI slop,
  lazy language, tighten this, how should I phrase, rewrite this, style guide, does this read well, my voice.'
version: 1.0.0
metadata:
  fools:
    tags:
    - writing
    - voice
    - style
    - prose
    - drafting
    related_skills:
    - argumentative-prose
    - trim-pass
---


# Writing style

This describes how the prose here actually works when it is working, and what to avoid
when it is not. Read §1–4 to write; read §5 as a filter before shipping.

The style has three sources doing three jobs. **Voice** (§1) is how sentences are built.
**Levin's formula** (§2) is how a Results subsection is shaped. **The Jungian spiral**
(§3) is how the whole argument moves. A draft can have clean sentences and a correct
section template and still fail, because it never spirals.

---

## 1. Voice

### The topic sentence is the finding

Do not warm up. The first sentence of a paragraph states the result, and the rest of the
paragraph earns it.

> **It does not survive.** Across 2,730 chimeric top-down runs spanning all 91 pairs,
> persistence averages −0.0031 against +0.0238 for the bottom-up chimeras…

> **Peak is blind to the goal.**

> **The first hypothesis is right and the second is wrong**, with one qualification the
> figure makes visible.

### The cleft is the signature construction

*What X is, is Y.* It fronts the topic and delays the payload, and it is the most
characteristic move in this prose. Used at roughly 2 per 1,000 words, evenly spread —
frequent enough to be a voice, not so clustered that it becomes a tic.

> **What separates them is** when and how often they act, not how far.
> **What recommends it is** that the ordering is reliable.
> **What the effect survives** matters more than its size.
> **What a host admits** and does not build is also what it can shed without repair.

### Concede in the same breath as the claim

The most persuasive sentences disclose against interest, and they do it *where the claim
is made*, not quarantined in a limitations section.

> …which we would rather name than dress.
> One pair in four does not make a window, and we claim none, but that is what the data does.
> The separation holds at both units, and its *shape* is what carries the reading.
> We report the discrepancy instead of hiding it.

This is not modesty. A reader who sees you volunteer the weak spot extends credit on
everything else.

### Name the reader's objection as theirs

Address the sceptic directly and without defensiveness. The reader is a co-conspirator
working it out, not an opponent.

> A reader could take that for evidence that the coordinator ranks the agents
> differently. It is not.
> A reader who computed that the obvious way would get the opposite answer.
> One feature of the figure needs an explanation the caption cannot hold, because a
> reader will reach for it immediately.

### Rhythm: long, long, short

Two or three long analytical sentences, then a short one that lands. The variance is the
point; uniform sentence length reads as generated.

> Cycle writes about 87 times, once per element, and its kernel barely declines at all.
> **It does not stir.** It computes where each element belongs and puts it there.

### Concrete beats abstract, and the concrete image should be exact

> That the **smallest hand** builds the largest transient…
> a **tenancy for the duration of the pursuit**
> the array both **pays for order and gets paid for it**

Metaphor is allowed and welcome, on two conditions: it must be *cashed* (the literal
claim follows immediately), and it must be *rare enough to land*. One good image per
section, not one per paragraph.

### Coin terms deliberately, then use them

*Side-quest. Universal steganography. Algorithmic ingression. The thinnest nontrivial
rung.* A coinage earns its place by being used more than twice and by doing work no
existing phrase does. Coin it once, define it on the spot, then rely on it.

### Numbers live inside sentences

Not parenthetical afterthoughts. And a number carries its scope with it.

> persistence rises from 0.0053 at *N* = 20 to 0.0140 at *N* = 200, a 2.6× climb against
> the 1.7× seen when the two end states are pooled

"Substantially more" is worth less than "6.3×", and "6.3×" is worth less than
"+0.022, 95% CI [+0.007, +0.037]" when the denominator is small.

### The colon delivers evidence; the semicolon balances two halves

> The split has a mechanism behind it**:** a frozen cell is an obstacle…
> Peak is a lucky snapshot**;** persistence is the order a pair holds while it works.

---

## 2. Levin's section formula

Every Results subsection does four things:

1. **A title that is a claim with a verb**, memorable enough to repeat. Not "Scheduler
   independence" but *"The pattern survives a deterministic scheduler."* Not "The
   operator's estimator, pointed at human tissue" (no verb) but *"The same operator
   finds a disturbed field in human tumor tissue."*
2. **The question in the reader's terms.** *"What if some of the agents break?"*
3. **What was done and what came out**, statistics inline.
4. **What follows, and what does not.**

Figure titles obey the same rule and echo the subsection they serve. Aim for **8–10
figures** in a main text; beyond that no single figure carries a claim.

**Never let this become a stamp.** Four subsections opening "To test this, we…" reads
like a filled-in form. Vary the entry — open on the question, the result, the objection,
or the one case that breaks the pattern. The formula is what a subsection must
*contain*, not the order its sentences must appear in.

---

## 3. The spiral

The argument does not run from premises to QED. It circles the subject, returning from a
different angle each pass, so the reader assembles the conclusion rather than receiving
it.

In practice: the same phenomenon is met as a **measurement** problem, then as a
**mechanism**, then as a **formalism**, then as a **biological analogue**, then as an
**interpretation**. Each pass assumes the last and adds an angle.

This licenses two things a linear style would not:

- **Deliberate return.** Meeting cycle sort, or the peak-versus-integral problem, in
  four sections is structure, not redundancy — provided each return comes from a new
  direction.
- **Deferred payoff.** A hook set in the Background and cashed in the Discussion is good
  architecture.

And forbids two:

- **Restatement without a new angle.** Test: does this pass add a number, a comparison,
  a consequence, or an angle the earlier one lacked? If not, cut it and cite the earlier.
- **A hook that never sets.** A formalism named and never used again is a debt.

---

## 4. One claim, one home

Every claim is stated **once, at full strength, in the section whose job it is.** Every
other mention is a pointer.

- **Results** state findings; **Discussion** states what follows and cites upward. If
  most of the Discussion's numbers already appear in the Results, it is summarizing
  rather than synthesizing.
- **Methods** specify; **Robustness** argues. Methods defending a choice against an
  imagined referee is misplaced.
- **Captions** say what the figure shows and what to conclude. Methodology appearing
  *only* in a caption is in the wrong place.

When the same sentence appears twice, the later one loses it.

---

## 5. The filter

Run before shipping. These are bans, not preferences.

**Constructions**

- `It's not X. It's Y.` as two sentences. State what a thing *is*. A single-sentence
  contrast (*"not how far it reaches, but when it acts"*) is fine and often sharpest —
  the ban is the two-sentence dramatic form.
- Dramatic noun-phrase headings ("The Pricing Trap").
- Fake-punchy one-line paragraphs for effect.
- Lists built in threes for rhythm. Three real things is fine.
- "Let's dive in", "Here's the thing", "In a world where", "But here's the kicker".
- Contractions and ampersands in journal prose (those rules are blog voice; they do not
  transfer).

**The announce-then-say family** — the one that keeps coming back. Each announces that
something deserves attention instead of saying it:

> It is worth noting / stating / naming / pausing on
> X deserves a check rather than a defence
> X needs stating carefully · needs more care than we first gave it
> One feature of X is worth recording
> The reach of that point is what makes it worth making
> X is easy to get wrong · It is important to note

Fix by deleting the announcement: *"The ε knob is not an ad hoc manipulation, and it is
worth naming what it is. It is an entropy-regularized-control dial."* → *"The ε knob is
an entropy-regularized-control dial."* A "worth ___ing" survives only with a real reason
attached: *"having it twice matters, because rate is the axis the physics would have
nominated."*

**Emdashes** are a symptom, not a style — they mark a sentence that was never structured.
Ask what the dash is doing and use the punctuation that does that job: a colon before a
definition, commas around an appositive, a full stop between independent claims, or a
restructure. **Target zero in prose.** Table cells meaning "not applicable" are not prose.

**Single-construction dominance.** No contrastive form above ~**2 per 1,000 words**.
Count them. "Rather than" at 5 per 1,000 reads as a tic even when no single use is wrong.
The trap is swapping one crutch for another: converting 206 "rather than" into 151 "and
not" fixes nothing. Vary across `rather than` / `and not` / `X, not Y` / `instead of`,
and prefer the restructure that needs no contrast at all.

**Shape words get their shape checked.** *Climbs, rises, monotone, sharpens, twice,
steadily* — attached to a series that dips, peaks, or falls short of the ratio claimed
is the most common factual defect in this prose, and endpoints being correct is exactly
why it survives review. Reconstruct the series before you use the word.

### Mechanical checks

```
grep -oniE "worth [a-z]+ing|deserves? [a-z]+|needs? (more )?(stating|care|a check)" *.tex
grep -o -- '---' *.tex | wc -l
grep -PznoE '(is|are) not [^.;]{5,80}[.;]\s*(It|They|That) (is|are)' *.tex
# per-1000 counts for each contrastive form; repeated paragraph openers;
# 4- and 5-grams recurring 4+ times that are not technical terms
```

**Then read it.** Every automated pass that produced this file reported success while
leaving broken sentences behind — including ones the pass itself created. Greps find
patterns. Only reading finds a paragraph whose transition died with the filler sentence
that was carrying it.
