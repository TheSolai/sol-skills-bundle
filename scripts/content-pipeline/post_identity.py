#!/usr/bin/env python3
"""
Shared post-identity helpers for the Sol AI content pipeline.

WHY THIS EXISTS
---------------
Three pieces of the pipeline need to answer the same question — "is this
content already published?" — and they were each answering it differently:

  * seo-health.py compared *normalised post bodies* to find duplicates.
  * run-daily.py picked a bloopers template with `random.choice`, so it
    republished an already-published story under a new date.
  * trending-topics.py picked `hits[0]` from Hacker News, so it republished
    the same story every time that story stayed at the top of the index.

One definition of "the same content" lives here so the audit and the
generators cannot drift apart. The normalisation matches seo-health.py's
original implementation exactly, so the audit's results are unchanged.

TWO KINDS OF IDENTITY
---------------------
  * body signature — the post text with formatting stripped. Two posts with
    the same signature are the same *article*.

  * source URL — the external story a post comments on. Two posts with the
    same source URL are the same *story*, even when their bodies differ
    (a trending commentary embeds the HN point count, which moves every
    few hours). Body signature alone misses this: a story republished over
    a month produced 25 URLs but only 4 matched bodies exactly.
"""

import re
from pathlib import Path

# Front matter is YAML between the first two --- fences.
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---", re.DOTALL)

# The two source-story formats the pipeline writes.
SOURCE_URL_PATTERNS = (
    re.compile(r"\*\*Source story:\*\*\s*\[[^\]]*\]\(([^)]+)\)"),
    re.compile(r"\*Source:\s*\[[^\]]*\]\(([^)]+)\)"),
)

# Additional formats that a story can be cited in, used by the DEDUPE path
# only — see `source_urls()`.
#
# WHY (2026-11-11): the weekly roundup and the analysis/deep-dive posts cite
# their stories in formats the two patterns above do not match:
#
#   * the roundup writes "[Read more →](url) — N points · N comments";
#   * an analysis post writes a "## Sources" section of bullet links.
#
# `source_urls()` only saw the two `*Source:*` formats, so `published_source_urls()`
# — the set every generator uses to decide "have I already covered this?" —
# did not know about a single one of the 161 story URLs in the 23 shipped
# roundups, nor the 124 in the analysis posts' Sources sections. The dedupe
# was blind to two thirds of the archive.
#
# The result was visible on the live site on 2026-11-11: the midweek digest
# republished "Goldman Sachs is pulling Claude Code from its bankers and
# traders", which the Monday roundup had already covered, and "Claude Fable
# 5.2 and Mythos 5.2", which the Nov-10 Australia post had already covered.
#
# Only `source_urls()` (the plural, dedupe-facing helper) reads these. The
# singular `source_url()` is deliberately left alone: seo-health.py keys its
# republication families on that single value, and widening it would change
# which posts the audit groups together — a separate change, with its own
# blast radius, that the audit's own repair script consumes.
SOURCE_URL_PATTERNS_EXTRA = (
    # The weekly roundup's story list.
    re.compile(r"\[Read more →\]\(([^)]+)\)"),
)

# A "## Sources" section: a heading of any level whose text is "Sources",
# followed by bullet links until the next heading of the same or higher level.
SOURCES_HEADING_RE = re.compile(r"^(#{1,6})\s*Sources\s*$", re.MULTILINE)
SOURCES_BULLET_RE = re.compile(r"^\s*[-*+]\s+\[[^\]]*\]\((https?://[^)\s]+)\)")
ANY_HEADING_RE = re.compile(r"^(#{1,6})\s+")


def split_front_matter(text):
    """Return (front_matter, body). front_matter is '' when absent."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return "", text
    return m.group(1), text[m.end():].strip()


def body_signature(text):
    """Normalised body text, used to decide whether two posts are the same.

    Formatting characters are stripped and whitespace is collapsed, so the
    same article written with different Markdown formatting still matches.
    The leading H1/H2 heading is dropped because it repeats the title.
    """
    _, body = split_front_matter(text)
    lines = [line.rstrip() for line in body.strip().splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and re.match(r"^#\s+\S", lines[0]):
        lines.pop(0)
    out = "\n".join(lines)
    out = re.sub(r"```[a-z]*", "", out)
    out = re.sub(r"[*_`#>]", "", out)
    return re.sub(r"\s+", " ", out).strip().lower()


def source_url(text):
    """The external story this post comments on, or None."""
    for pattern in SOURCE_URL_PATTERNS:
        m = pattern.search(text)
        if m:
            return m.group(1).strip()
    return None


def sources_section_urls(text):
    """URLs cited in a "## Sources" section, as bullet links.

    The analysis and deep-dive posts list their sources this way instead of
    on a `*Source:*` line. They are stories the archive has covered, so the
    dedupe has to see them.

    Only bullet links directly under a heading whose text is exactly
    "Sources" are collected. A "## Additional Resources" section (a how-to
    post linking to documentation) or a "## Links from AgentNews#23" section
    is not a list of covered stories, so neither is counted.
    """
    out = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        m = SOURCES_HEADING_RE.match(lines[i])
        if not m:
            i += 1
            continue
        level = len(m.group(1))
        i += 1
        # Collect bullets until the next heading of the same or higher level.
        while i < len(lines):
            h = ANY_HEADING_RE.match(lines[i])
            if h and len(h.group(1)) <= level:
                break
            b = SOURCES_BULLET_RE.match(lines[i])
            if b:
                out.append(b.group(1))
            i += 1
    return out


def source_urls(text):
    """Every external story this post comments on.

    WHY (2026-10-28): `source_url()` returns the first match only, which is
    right for a post that comments on one story — but a digest post carries
    three `*Source:*` lines, and only the first was ever registered with the
    archive. The dedupe could not see the other two, so it would happily
    republish a story the archive already covered.

    Order is preserved and duplicates are dropped.

    It also reads the roundup's "[Read more →]" links and the analysis posts'
    "## Sources" bullets — the two formats the original two patterns missed,
    which left the dedupe blind to most of the archive. See
    SOURCE_URL_PATTERNS_EXTRA for the full account.
    """
    out = []
    seen = set()
    for pattern in SOURCE_URL_PATTERNS + SOURCE_URL_PATTERNS_EXTRA:
        for m in pattern.finditer(text):
            url = m.group(1).strip()
            if url and url not in seen:
                seen.add(url)
                out.append(url)
    for url in sources_section_urls(text):
        url = url.strip()
        if url and url not in seen:
            seen.add(url)
            out.append(url)
    return out


def normalise_url(url):
    """Reduce a URL to a stable comparison key.

    Scheme and 'www.' are dropped and any query string is ignored, so the
    same story linked through two different trackers still matches.
    """
    if not url:
        return ""
    u = url.strip()
    u = re.sub(r"^https?://", "", u, flags=re.IGNORECASE)
    u = re.sub(r"^www\.", "", u, flags=re.IGNORECASE)
    u = u.split("#", 1)[0].split("?", 1)[0]
    return u.rstrip("/").lower()


def published_signatures(posts_dir):
    """Set of body signatures already published."""
    out = set()
    posts_dir = Path(posts_dir)
    if not posts_dir.is_dir():
        return out
    for path in posts_dir.glob("*.md"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        sig = body_signature(text)
        if sig:
            out.add(sig)
    return out


def published_source_urls_of(path):
    """Normalised source URLs of a single post.

    Used when a generator refreshes a post in place: the post's own stories
    must not count as "already covered" against itself.
    """
    out = set()
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for url in source_urls(text):
        key = normalise_url(url)
        if key:
            out.add(key)
    return out


def published_source_urls_excluding(posts_dir, exclude_path):
    """Normalised source URLs of every published post EXCEPT one.

    WHY (2026-11-11): a generator that refreshes a post in place must not
    count that post's own stories against itself. Subtracting the post's
    URLs from the shared covered set is NOT the same thing: a URL that
    another post also covers would be freed by the subtraction and become
    selectable again — so the refresh could publish a story the archive
    covers under a different post. Excluding the FILE leaves every other
    post's coverage intact.

    `exclude_path` is matched on the resolved absolute path.
    """
    out = set()
    posts_dir = Path(posts_dir)
    if not posts_dir.is_dir():
        return out
    excluded = Path(exclude_path).resolve() if exclude_path else None
    for path in posts_dir.glob("*.md"):
        if excluded is not None and path.resolve() == excluded:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for url in source_urls(text):
            key = normalise_url(url)
            if key:
                out.add(key)
    return out


def published_source_urls(posts_dir):
    """Set of normalised source URLs already published.

    Every source URL in every post counts, not just the first one — a digest
    post carries three stories and the archive must know about all three.
    """
    out = set()
    posts_dir = Path(posts_dir)
    if not posts_dir.is_dir():
        return out
    for path in posts_dir.glob("*.md"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for url in source_urls(text):
            key = normalise_url(url)
            if key:
                out.add(key)
    return out
