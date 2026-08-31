---
name: blocked-page-recovery
description: >
  When a web page won't fetch (403/429, Cloudflare, paywall, bot interstitial), don't
    give up and don't loop on the same URL. Work a cheapest-first ladder of copies and alternates.
    Use when webfetch or browse_* returns an error, a captcha, or an empty/blocked page. Triggers on:
    403, 429, blocked, captcha, cloudflare, paywall, just a moment, page won't load, fetch failed.
version: 1.0.0
metadata:
  fools:
    tags: [web, research, recovery, scraping]
    related_skills: [grounded-citations]
---

# Blocked-page recovery

A page that won't fetch is rarely gone -- a copy usually exists. Don't give up, and don't retry the
same URL in a loop. Work down the ladder, cheapest first:

1. **webfetch again with a plain GET** -- transient 403/429 often clears on a retry or a plain read.
2. **The browser layer** -- `browse_open`/`web_search` via camofox bypasses anti-bot blocking that a
   plain fetch can't. This is the workhorse for JS-heavy or bot-detected pages.
3. **Wayback Machine** -- `https://archive.org/wayback/available?url=<url>` returns the latest
   snapshot; fetch that snapshot for the content.
4. **A third-party copy** -- a cache/mirror, the publisher's own mirror, an RSS feed, an AMP/print
   version (`?output=1`, `/print`, `.amp`), or a text extractor (r.jina.ai).
5. **Search for the content** -- `web_search` the title or a distinctive phrase; another source
   often carries the same material.

Stop when you have the content or the ladder is exhausted; report which rung worked. Never
fabricate the content because the page blocked.
