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


def published_source_urls(posts_dir):
    """Set of normalised source URLs already published."""
    out = set()
    posts_dir = Path(posts_dir)
    if not posts_dir.is_dir():
        return out
    for path in posts_dir.glob("*.md"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        key = normalise_url(source_url(text))
        if key:
            out.add(key)
    return out
