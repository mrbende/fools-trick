"""The prose filter, as a portable script rather than a habit.

Every rule here was added because it was violated on a real manuscript, and at least one was
violated *after* being added, because the check lived in an ad-hoc verification block that the next
edit did not copy. A check that is retyped each time is a check that gets dropped exactly when the
draft is changing fastest. Run this; do not reconstruct it.

    python lint_prose.py PATH [PATH ...]    # markdown or latex files/dirs
    python lint_prose.py PATH --quiet       # exit code only

Distilled from the emergent-sort / steganographic-sorting paper lint (scripts/lint_prose.py) into a
path-driven, file-format-agnostic form. Rules with fatal=True fail the run.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# (name, pattern, fatal). Each rule traces to a shipped defect; do not loosen without a reason.
RULES: list[tuple[str, str, bool]] = [
    # announce-then-say: "It is worth noting...", "X deserves a check..." -- say the thing instead.
    ("announce-then-say",
     r"\b(?:needs?|deserves?|merits?|warrants?|bears?|requires?)\s+(?:\w+\s+){0,3}?"
     r"(?:stating|saying|noting|naming|recording|a\s+mention|a\s+check|attention|care|comment|emphasis)\b"
     r"|\bis\s+(?:the\s+reason\s+it\s+is\s+)?worth\s+\w+ing\b"
     r"|\bworth\s+(?:stating|saying|noting|naming|recording|remembering|pausing)\b"
     r"|\bit\s+(?:is|should\s+be)\s+(?:worth|noted|emphasi[sz]ed|stressed|remembered)\b"
     r"|\bmust\s+be\s+(?:said|noted|stated|emphasi[sz]ed)\b"
     r"|\bthe\s+(?:point|reason|question|distinction)\s+(?:is\s+worth|needs?|deserves?)\b", True),
    # document-as-subject: "this paper shows", "the figure reports" -- the fourth wall.
    ("document-as-subject",
     r"\bthis\s+(?:paper|study|section|manuscript|draft)\b"
     r"|\b(?:this|the)\s+(?:table|figure|panel)\s+(?:shows|says|reports|makes|is)\b"
     r"|\bthe\s+rest\s+of\s+(?:the\s+paper|this\s+work)\b"
     r"|\bwhat\s+follows\s+is\b", True),
    # epistemic-virtue words: honest/rigorous/careful are self-congratulation, not evidence.
    ("epistemic-virtue", r"\b(?:honest|honestly|careful|carefully|rigorous|rigorously)\b", True),
    # banned vocabulary: the AI-slop register.
    ("banned-vocabulary",
     r"\b(?:delve|leverage|robust|comprehensive|crucial|moreover|furthermore|actually|clearly|simply|very)\b", True),
    # the two-sentence dramatic form: "It is not X. It is Y."
    ("not-X-It-Y", r"(?:is|are)\s+not\s+[^.;:]{5,80}\.\s+(?:It|They|That)\s+(?:is|are)\b", True),
    # em-dash: a symptom, not a style -- marks a sentence that was never structured.
    ("em-dash", r"(?<![\\{])---|—", True),
    # "proof of" / "proves that": empirical work shows, it does not prove.
    ("proof-of", r"\bproof\s+of\b|\bproves\s+that\b", True),
    # hedge-stacking: "may possibly", "might potentially".
    ("hedge-stack", r"\b(?:may|might|could)\s+(?:possibly|perhaps|potentially)\b", True),
]

ALLOWED_CAMEL = {"arXiv", "bioRxiv", "medRxiv"}


def _strip(text: str) -> str:
    """Remove comments and non-prose so a rule sees only the sentences a reader reads."""
    text = re.sub(r"\\begin\{table\}.*?\\end\{table\}", " ", text, flags=re.S)   # latex floats
    text = re.sub(r"\\todo\{[^}]*\}", " ", text)                                   # latex scaffold
    text = re.sub(r"%.*", " ", text)                                               # latex comments
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)                            # markdown/html comments
    text = re.sub(r"`[^`]*`", " ", text)                                           # inline code
    text = re.sub(r"```.*?```", " ", text, flags=re.S)                             # code fences
    return text


def _jammed_words(text: str) -> list[str]:
    """A lowercase letter immediately followed by a capital inside a word -- the weld a line-slice
    edit leaves when it deletes the tail of a sentence and joins the next one on. LaTeX compiles it
    and the claim verifier sees no number, so it needs a mechanical guard of its own."""
    return [m.group(0) for m in re.finditer(r"\b[a-z]{2,}[A-Z][a-z]{2,}\b", text)
            if m.group(0) not in ALLOWED_CAMEL]


def _files(paths: list[str]) -> list[Path]:
    out = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            out.extend(sorted(p.rglob("*.md")) + sorted(p.rglob("*.tex")) + sorted(p.rglob("*.txt")))
        elif p.is_file():
            out.append(p)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    failures = 0
    for path in _files(args.paths):
        try:
            body = _strip(path.read_text())
        except OSError:
            continue
        for name, pattern, fatal in RULES:
            for m in re.finditer(pattern, body, re.I):
                failures += 1 if fatal else 0
                if not args.quiet:
                    a = max(0, m.start() - 60)
                    ctx = " ".join(body[a:m.end() + 40].split())
                    print(f"{path}: {name}\n    ...{ctx}...")
        for w in _jammed_words(body):
            failures += 1
            if not args.quiet:
                print(f"{path}: jammed-words\n    ...{w}...")
    if not args.quiet:
        print(f"\n{failures} fatal" if failures else "\nclean")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
