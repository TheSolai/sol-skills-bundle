#!/usr/bin/env python3
"""
Regression tests for the paren-URL dedupe blind spot (post_identity.py).

WHY THIS EXISTS
---------------
The archive-wide dedupe answers one question for every generator in the
pipeline: "has the archive already covered this story?" It is keyed on the
SOURCE URL.

Every pattern that reads a source URL captured `([^)]+)` — everything up to
the FIRST closing parenthesis. A URL that legitimately contains a
parenthesis was therefore truncated:

    https://.../20260916-Prompt-like-a-butterfly-sting-like-a-tracker-(clean).pdf
    -> captured as "...-tracker-(clean"

The captured string is not the URL. `normalise_url()` cannot match it
against the same story as it appears in the Hacker News feed, so the
archive never recognised a story it had already published.

The measured consequence, 2026-11-25: the trending series republished one
Hacker News PDF every morning for four consecutive days (2026-11-22 to
2026-11-25). Four posts carried one identical title and one identical
body, four copies of one article sat on the live site under four URLs,
and the SEO audit reported them as duplicate content and duplicate
metadata.

Seven source URLs in the whole archive were affected — the four trending
posts, and the three regional angles of the 2026-08-05 "There Will Come
Soft Rains" PDF, which carries the same defect.

This file pins the fix. It also pins the deliberate ASYMMETRY: the
singular `source_url()` still stops at the first ")", because the SEO
audit keys its republication families on that value and widening it
would change which posts the audit groups together.

Run it directly — no test framework needed:

    python3 scripts/content-pipeline/test_paren_url_dedupe.py

It is also pytest-compatible: every check is a `test_` function.
"""

from __future__ import annotations

import importlib.util
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from post_identity import (  # noqa: E402
    normalise_url,
    source_url,
    source_urls,
    sources_section_urls,
)

PIPELINE_DIR = Path(__file__).resolve().parent


def _load_trending():
    """Import trending-topics.py, whose filename is not a module name."""
    spec = importlib.util.spec_from_file_location(
        "trending_topics", PIPELINE_DIR / "trending-topics.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The real URL of the story that was republished four times.
PAREN_URL = (
    "https://jorgegarciaherrero.com/wp-content/interactivos/"
    "20260916-Prompt-like-a-butterfly-sting-like-a-tracker-(clean).pdf"
)

# The URL as the old pattern captured it — truncated at the first ")".
TRUNCATED_URL = (
    "https://jorgegarciaherrero.com/wp-content/interactivos/"
    "20260916-Prompt-like-a-butterfly-sting-like-a-tracker-(clean"
)


TRENDING_POST = f"""---
layout: post
title: "Trending: A Privacy Analysis of Web and Mobile Conversational AI…"
date: 2026-11-25 09:00:00 +0000
---

**Source story:** [A Privacy Analysis of Web and Mobile Conversational AI Agents]({PAREN_URL}) — 426 points, 139 comments on Hacker News

The body of the commentary goes here.
"""


# ── The fix: a URL with balanced parentheses survives ─────────────────────

def test_source_urls_keeps_balanced_parentheses():
    """The dedupe-facing helper captures the whole URL, parentheses and all.

    This is the check that would have caught the four-day republication:
    the captured string has to be the real URL, or the archive cannot match
    it against the story it already covered.
    """
    urls = source_urls(TRENDING_POST)
    assert urls == [PAREN_URL], (
        f"expected the full URL, got {urls!r}"
    )
    assert TRUNCATED_URL not in urls, (
        "the truncated URL must not be reported as a source URL"
    )


def test_normalised_paren_url_matches_the_hacker_news_url():
    """The captured URL matches the same story as the feed spells it.

    `normalise_url()` is the comparison the generators actually make, so
    this is the end-to-end shape of the bug: a mismatch here is a
    republication.
    """
    assert normalise_url(PAREN_URL) != normalise_url(TRUNCATED_URL), (
        "the truncated URL must normalise differently — otherwise this "
        "test would pass for the wrong reason"
    )
    captured = source_urls(TRENDING_POST)[0]
    assert normalise_url(captured) == normalise_url(PAREN_URL)


def test_roundup_read_more_link_keeps_parentheses():
    """The weekly roundup's "[Read more →](url)" format is fixed too."""
    post = f"""---
title: "The Week in AI"
---

**1. A PDF with a parenthesis in its name**
[Read more →]({PAREN_URL}) — 426 points · 139 comments
"""
    assert source_urls(post) == [PAREN_URL]


def test_sources_section_bullet_keeps_parentheses():
    """The analysis posts' "## Sources" bullets are fixed too."""
    post = f"""---
title: "An analysis"
---

## Sources

- [A PDF with a parenthesis in its name]({PAREN_URL}) — the paper
"""
    assert source_urls(post) == [PAREN_URL]
    assert sources_section_urls(post) == [PAREN_URL]


def test_plain_urls_are_untouched():
    """A URL without parentheses is captured exactly as before.

    The fix must not disturb the 858 URLs in the archive that never had
    a parenthesis in them.
    """
    post = """---
title: "Ordinary"
---

**Source story:** [A normal story](https://example.com/a-normal-story) — 10 points on Hacker News
"""
    assert source_urls(post) == ["https://example.com/a-normal-story"]


def test_several_paren_urls_are_all_captured():
    """Two paren URLs in one post are both captured, and both intact."""
    post = f"""---
title: "Two stories"
---

**Source story:** [First]({PAREN_URL}) — 426 points on Hacker News

*Source: [Second](https://example.com/other-(v2).pdf) — 88 points on Hacker News*
"""
    urls = source_urls(post)
    assert len(urls) == 2, f"expected 2 source URLs, got {urls!r}"
    assert PAREN_URL in urls
    assert "https://example.com/other-(v2).pdf" in urls


# ── The deliberate asymmetry: the singular helper is unchanged ────────────

def test_singular_source_url_is_deliberately_unchanged():
    """`source_url()` still stops at the first ")" — on purpose.

    The SEO audit keys its republication families on this single value.
    Widening it would change which posts the audit groups together, and
    the audit's own repair script consumes that grouping. Changing it is
    a separate change with its own blast radius, so this test pins the
    current behaviour to keep it a decision rather than an accident.
    """
    captured = source_url(TRENDING_POST)
    assert captured == TRUNCATED_URL, (
        f"source_url() changed to {captured!r} — if this was intended, "
        "re-check what the SEO audit groups as one republication family"
    )


# ── The trending generator's own dedupe ───────────────────────────────────

def test_trending_dedupe_recognises_a_covered_paren_story():
    """The generator's covered set now contains the story it already has.

    This is the check that stops the republication: with the story in the
    covered set, `fetch_top_story()` moves on to the next one.
    """
    trending = _load_trending()
    covered = trending._covered_source_urls()
    assert normalise_url(PAREN_URL) in covered, (
        "the archive must recognise the story URL it already published"
    )


def _decoded_query(url):
    """The query string with its percent-encoding undone.

    `urlencode()` escapes ">" and "," , so the raw URL does not contain
    the literal filter the assertion is about.
    """
    return urllib.parse.unquote(url)


def test_trending_candidates_are_bounded_to_a_recent_window():
    """The candidate query is limited to recent stories.

    The list is ordered by points, not by date, so an unbounded query is
    all-time. Once the dedupe worked, every story in that list was already
    covered and the series went silent instead of repeating itself. The
    window keeps "trending" true.
    """
    trending = _load_trending()
    assert trending.TRENDING_WINDOW_DAYS > 0

    captured = {}

    class _FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def read(self):
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def _fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        return _FakeResponse(b'{"hits": [], "nbHits": 0}')

    real_urlopen = urllib.request.urlopen
    urllib.request.urlopen = _fake_urlopen
    try:
        trending.fetch_top_story(set())
    finally:
        urllib.request.urlopen = real_urlopen

    assert "created_at_i>" in _decoded_query(captured["url"]), (
        f"the candidate query must bound itself to recent stories: {captured['url']}"
    )
    assert "points>30" in _decoded_query(captured["url"])


def _run():
    failures = []
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as exc:
            failures.append((name, str(exc)))
            print(f"  FAIL  {name}: {exc}")
    print()
    if failures:
        print(f"{len(failures)} test(s) FAILED")
        return 1
    print("All tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run())
