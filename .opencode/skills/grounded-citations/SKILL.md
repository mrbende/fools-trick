---
name: grounded-citations
description: >
  Every claim from an outside source gets an inline numbered citation and a Sources
    list. A ledger owns the url->number mapping so citations come from retrieval, never from memory.
    Use for research, any answer that cites the web or external docs, and any high-stakes factual
    claim. Triggers on: research, cite, sources, references, fact-check, with sources, prove it.
version: 1.0.0
metadata:
  fools:
    tags: [research, citations, grounding, anti-confabulation]
    related_skills: [blocked-page-recovery]
---

# Grounded citations

Every claim taken from an outside source gets an inline numbered citation `[n]` and a `Sources:`
list. The mapping from URL to number is owned by retrieval, not by the model's memory.

## The discipline

- Fetch the source with `webfetch` (plain read) or `browse_open`/`web_search` (JS-heavy/blocked;
  blocked-page-recovery if it 403s).
- Assign each source a number as you fetch it, and record `url -> [n]` in your notes (a `note`
  call per source).
- In the answer, a factual claim from a source is followed by its `[n]`. A claim from your own
  prior knowledge is marked `[unverified]`, never given a number.
- Never invent a URL, a number, a date, or a quote to look complete. If a fact wasn't in a fetched
  page, it doesn't get a citation.
- A verbatim quote must literally appear in the fetched page text; if it doesn't, paraphrase or
  drop it.

## The point

The model emits only small integers it was handed; it never fabricates a source. For high-stakes
work, the Sources list is the audit trail: each `[n]` maps to a URL you actually retrieved.
