#!/usr/bin/env python3
"""
Regression tests for the archive-wide source-URL dedupe (post_identity.py).

WHY THIS EXISTS
---------------
The dedupe answers one question for every generator in the pipeline: "has
the archive already covered this story?" It is keyed on the SOURCE URL, and
`published_source_urls()` reads the source URLs out of every published post.

It could only read two formats:

    **Source story:** [title](url)
    *Source: [title](url)

The weekly roundup and the analysis/deep-dive posts cite their stories in
two other formats, and neither was read:

    [Read more →](url) — N points · N comments     (the roundup)
    - [Title](url)                                 (a "## Sources" section)

The measured consequence, 2026-11-11: the dedupe set held 143 URLs out of
the 405 the archive actually contained. It was blind to every one of the 161
story URLs in the 23 shipped roundups, and to the 124 in the analysis posts'
Sources sections.

So the midweek digest republished two stories the site had already covered:

  * "Goldman Sachs is pulling Claude Code from its bankers and traders" —
    the Monday roundup had carried it on 2026-11-09;
  * "Claude Fable 5.2 and Mythos 5.2" — the Australia post had cited it on
    2026-11-10.

Both were live, published, and visible to readers before this was caught.

This file is the table-driven test that keeps the three formats in step. It
also pins the EXCLUSIONS, because the fix must not swallow URLs that are not
story citations: a how-to post's "## Additional Resources" section links to
documentation, and a link roundup's "## Links from AgentNews#23" section is
a list of links, not a list of covered stories.

Run it directly — no test framework needed:

    python3 scripts/content-pipeline/test_post_identity_dedupe.py

It is also pytest-compatible: every check is a `test_` function.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from post_identity import (  # noqa: E402
    normalise_url,
    source_url,
    source_urls,
    sources_section_urls,
)


# ── The three formats the archive uses ────────────────────────────────────

DIGEST_POST = """---
layout: post
title: "AI Digest: Wednesday"
---

**1. Nvidia CEO Jensen Huang's letter to staff about affair**
*Source: [Nvidia CEO Jensen Huang's letter to staff about affair](https://nvidianews.nvidia.com/news/a-message-to-our-company) — 978 points on Hacker News*

**2. Claude Fable 5.2 and Mythos 5.2**
*Source: [Claude Fable 5.2 and Mythos 5.2](https://www.anthropic.com/claude-fable-and-mythos-5-2) — 888 points on Hacker News*

**3. Goldman Sachs is pulling Claude Code from its bankers and traders**
*Source: [Goldman Sachs is pulling Claude Code from its bankers and traders](https://www.thestateofai.com/news/goldman-sachs-claude-code-traders-bankers) — 899 points on Hacker News*
"""

ROUNDUP_POST = """---
layout: post
title: "The Week in AI: November 9, 2026"
---

## Research & Ideas

**1. Goldman Sachs is pulling Claude Code from its bankers and traders**
[Read more →](https://www.thestateofai.com/news/goldman-sachs-claude-code-traders-bankers) — 708 points · 391 comments

**2. Nvidia CEO Jensen Huang's letter to staff about affair**
[Read more →](https://nvidianews.nvidia.com/news/a-message-to-our-company) — 529 points · 563 comments
"""

ANALYSIS_POST = """---
layout: post
title: "Australia built a regulator for the layer above the model"
---

## Sources

- [Australian PM's Office — *Strengthening Australia against AI-driven cyber incidents*](https://www.pm.gov.au/media/strengthening-australia-against-ai-driven-cyber-incidents) (10 November 2026) — the release of the 18 recommendations
- [Anthropic — *Introducing Claude Fable 5.2 and Claude Mythos 5.2*](https://www.anthropic.com/claude-fable-and-mythos-5-2) (9 November 2026) — the shared underlying model
- [Artificial Analysis — *Claude Fable 5.2*](https://artificialanalysis.ai/models/claude-fable-5-2) — Intelligence Index 65
"""


# ── Tests ─────────────────────────────────────────────────────────────────

def test_digest_post_yields_all_three_stories():
    """A digest post carries three `*Source:*` lines. All three count.

    This is the case the Oct-2028 fix addressed: the dedupe used to see only
    the first, so a digest could republish its own second and third story.
    """
    urls = source_urls(DIGEST_POST)
    assert len(urls) == 3, f"expected 3 source URLs, got {len(urls)}"
    for expected in (
        "https://nvidianews.nvidia.com/news/a-message-to-our-company",
        "https://www.anthropic.com/claude-fable-and-mythos-5-2",
        "https://www.thestateofai.com/news/goldman-sachs-claude-code-traders-bankers",
    ):
        assert expected in urls, f"{expected!r} was not seen"


def test_roundup_stories_are_visible_to_the_dedupe():
    """The roundup's "[Read more →]" links are story citations.

    Before this was fixed, the dedupe saw NONE of the 161 story URLs in the
    23 shipped roundups.
    """
    urls = source_urls(ROUNDUP_POST)
    assert "https://www.thestateofai.com/news/goldman-sachs-claude-code-traders-bankers" in urls
    assert "https://nvidianews.nvidia.com/news/a-message-to-our-company" in urls
    assert len(urls) == 2, f"expected 2 source URLs, got {len(urls)}"


def test_analysis_sources_section_is_visible_to_the_dedupe():
    """An analysis post's "## Sources" bullets are story citations.

    Before this was fixed, the dedupe saw NONE of the 124 URLs in the
    analysis posts' Sources sections.
    """
    urls = source_urls(ANALYSIS_POST)
    assert "https://www.anthropic.com/claude-fable-and-mythos-5-2" in urls
    assert "https://artificialanalysis.ai/models/claude-fable-5-2" in urls
    assert len(urls) == 3, f"expected 3 source URLs, got {len(urls)}"


def test_non_source_sections_are_not_counted():
    """Documentation links and link roundups are not story citations.

    The fix must not turn every URL in the archive into a covered story: a
    how-to post's "## Additional Resources" section points at documentation,
    and a link roundup's "## Links from AgentNews#23" section is a list of
    links the post merely mentioned.
    """
    docs = """## Additional Resources

- [launchd documentation](https://www.manpagez.com/man/8/launchd/)
- [LaunchAgent Examples](https://www.launchd.info/)
"""
    links = """## Links from AgentNews#23

- [AgentMail on OpenAI](https://e.customeriomail.com) — Codex users, it's official.
- [Monid](https://monid.ai)
"""
    assert sources_section_urls(docs) == [], "documentation links counted as stories"
    assert sources_section_urls(links) == [], "link-roundup links counted as stories"


def test_sources_section_stops_at_the_next_heading():
    """A new heading at the same level ends the Sources section."""
    post = """## Sources

- [Real source](https://example.com/real)

## Other Section

- [Not a source](https://example.com/note)
"""
    assert sources_section_urls(post) == ["https://example.com/real"]


def test_singular_source_url_is_unchanged():
    """`source_url()` keeps its first-match-only behaviour.

    seo-health.py keys its republication families on this single value.
    Widening it would change which posts the audit groups together, so the
    dedupe fix deliberately lives in the plural helper only.
    """
    assert source_url(DIGEST_POST) == (
        "https://nvidianews.nvidia.com/news/a-message-to-our-company"
    )
    # A roundup has no `*Source:*` line at all, so the singular helper
    # correctly finds nothing there — unchanged from before the fix.
    assert source_url(ROUNDUP_POST) is None


def test_duplicate_urls_are_collapsed():
    """The same URL cited in two formats appears once."""
    post = """*Source: [A story](https://example.com/story) — 10 points*

## Sources

- [A story again](https://example.com/story) — the same URL
"""
    urls = source_urls(post)
    assert urls.count("https://example.com/story") == 1, urls


def test_normalise_url_still_matches_across_formats():
    """The comparison key is the same regardless of citation format."""
    assert (
        normalise_url("https://www.thestateofai.com/news/goldman-sachs-claude-code-traders-bankers")
        == normalise_url("http://www.thestateofai.com/news/goldman-sachs-claude-code-traders-bankers/")
    )


def test_the_two_republished_stories_are_now_covered():
    """The stories that actually shipped as duplicates on 2026-11-11.

    The midweek digest republished both of these: the Goldman Sachs story
    from the Monday roundup, and the Claude Fable story from the Nov-10
    Australia post. Both are now visible to the dedupe, so neither can be
    selected again.
    """
    digest_urls = {normalise_url(u) for u in source_urls(DIGEST_POST)}
    roundup_urls = {normalise_url(u) for u in source_urls(ROUNDUP_POST)}
    analysis_urls = {normalise_url(u) for u in source_urls(ANALYSIS_POST)}

    goldman = normalise_url(
        "https://www.thestateofai.com/news/goldman-sachs-claude-code-traders-bankers"
    )
    fable = normalise_url("https://www.anthropic.com/claude-fable-and-mythos-5-2")

    assert goldman in roundup_urls, "the roundup must register the Goldman story"
    assert fable in analysis_urls, "the analysis post must register the Claude Fable story"

    # And they must be seen as covered by a generator that has already
    # published one of them.
    published_by_digest = digest_urls | roundup_urls | analysis_urls
    assert goldman in published_by_digest
    assert fable in published_by_digest


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
