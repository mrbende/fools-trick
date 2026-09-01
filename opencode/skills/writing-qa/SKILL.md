---
name: writing-qa
description: >
  Mechanical quality gates for prose and research writing -- the checks a careful read can't be
  trusted to run by hand. Runs a portable prose linter (banned constructions, the slop register) and
  encodes the claim-verification and provenance disciplines. Use when a draft must ship, when numbers
  in text must match the data they report, or when a long document needs a mechanical pass before a
  human read. Triggers on: lint prose, check the writing, style check, verify the numbers, do the
  claims match the data, prose QA, before we ship this, run the linter.
version: 1.0.0
metadata:
  fools:
    tags: [writing, qa, lint, verification, research, claims]
    related_skills: [writing-style, close-read, grounded-citations]
---

# Writing QA

Mechanical gates own what mechanical gates can see: numbers, banned constructions, dangling
references, drift between a document and the data it reports. They are the *first* pass, not the
last -- every one of these checks has, at some point, reported "clean" while a broken paragraph sat
in the manuscript. The linter and the verifiers catch the failures that recur mechanically; the
reading pass (close-read, panel-review) catches the rest. Run both.

## 1. The prose linter (ships as a script)

`scripts/lint_prose.py` runs the prose filter over markdown, latex, or text. Every rule traces to a
defect that shipped once: the announce-then-say family, the document-as-subject fourth wall, the
epistemic-virtue words (honest/rigorous/careful), the banned-vocabulary slop register, the two-
sentence "It is not X. It is Y." form, em-dashes, "proof of", hedge-stacking, and jammed-words (the
camelCase weld a line-slice edit leaves). Do not retype these rules by hand -- run the script; a
check that is rewritten each time is the one that gets dropped when the draft is moving fastest.

```
python .opencode/skills/writing-qa/scripts/lint_prose.py <file-or-dir> [...more]
```

It exits nonzero on any fatal rule. Run it in the test suite or the build, not by hand -- running it
by hand is how it gets skipped.

## 2. Claim verification (numbers resolve against artifacts)

A number in a draft must resolve against a named artifact, or it is invented. The discipline:

- Keep a canonical numbers registry (one CSV/table of every derived value the write-up may cite,
  each with a claim_id). No number enters the prose unless the registry resolves it.
- SCOPE the check. A number is verified only against the artifacts its own section declares it draws
  on -- checking every number against every value in the corpus has a ~100% false-match rate (a
  verifier that passes everything is worse than none; it converts an open question into false
  confidence). Declare the scope per section so a match means something.
- Check the printed digit is an actual rounding of the canonical value, not a near-miss.
- The verifier reports its own false-match rate. If it is never wrong, suspect the verifier.
- What this cannot do: confirm a number is attached to the claim it measures. It finds staleness and
  invention within a scope, not misinterpretation. The reading pass is still required.

Build this per project with a `verify_claims.py` that reads the registry + the section scopes and
exits nonzero on an unresolvable or out-of-tolerance number. Run it before every commit that touches
a number.

## 3. Provenance by generation (never state corpus facts in prose)

Anywhere a document states a fact about its own corpus (run counts, table of results, which
artifacts exist), that fact is *generated* from the artifacts, not hand-written. A hand-typed status
line is wrong within a week. Generate the block from source (delimited so a check can find it) and
add a `--check` that exits nonzero on drift. The defect this kills: the README that says "48,240
runs" long after the real count changed.

## The meta-rule

Mechanical gates see structure and numbers; only reading catches a transition that died, a claim
that changed strength between sections, a figure that shows the wrong thing. This skill is the
mechanical layer. It is the floor, never the ceiling.
