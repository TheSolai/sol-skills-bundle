#!/usr/bin/env python3
"""
Sol AI — Trending Topics
3x daily scan for trending AI stories + quick commentary post.
Runs 09:00 / 13:00 / 17:00 UK time.

Scans HN Algolia for trending AI stories (>=30 points), picks the top one,
and writes a short commentary post. Different from Quick Hits — these
are real-time responses to breaking stories.
"""

import os, datetime, urllib.request, urllib.parse, json, sys, subprocess
from pathlib import Path

sys.path.insert(0, "/Users/amre/Projects/sol-skills-bundle/scripts/content-pipeline")
from post_identity import normalise_url, published_source_urls, source_url, source_urls

SITE_DIR = Path("/Users/amre/Projects/thesolai.github.io")
LOG_DIR = Path("/Users/amre/Projects/sol-skills-bundle/scripts/content-pipeline/logs")
LOG_FILE = LOG_DIR / "trending-topics.log"

HN_ALGOLIA = "https://hn.algolia.com/api/v1/search"

COMMENTARY_TEMPLATES = [
    """The story here is what happens {headline_topic} meets the mainstream. The reaction reveals more than the news itself.

What the headline misses: the actual capability is less important than the framing. The capability was visible for months; the framing is what changed this week.

What matters: how quickly the ecosystem adapts. The companies that win the next 18 months will be the ones that adapt faster than the news cycle moves.

What to watch: the second-order effects. Whatever happens in the next 6 months is downstream of how this story is being interpreted now.""",

    """Let's be honest about {headline_topic}. The narrative is half the story. The other half is what the narrative doesn't say.

Three things the headline doesn't cover:

1. **The timing.** This wasn't inevitable. It happened because of specific decisions by specific people over specific months. The story is in the sequence.

2. **The numbers.** Adoption is faster than the headline suggests. The capability is real, not hype. The integration is the bottleneck.

3. **The response.** The industry is responding, not waiting. The next major move is being planned right now, not in some future strategy meeting.

The story isn't the event. The story is the reaction.""",

    """Here's the take nobody is putting in writing about {headline_topic}:

**The short version:** It's bigger than it looks. It's smaller than it sounds. And it's going to be the subject of a thousand think pieces that miss the actual point.

**The medium version:** The capability being discussed is real but not new. The deployment model is new but unproven. The economic model is new but unprofitable. The regulatory model is new but unsettled.

**The long version:** We're watching the early innings of a technology that's simultaneously overhyped and underrated. The hype is in the demos. The underrating is in the boring infrastructure that actually makes it useful.

Take it seriously. Don't take the hype seriously.""",
]


def log(msg):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(f"[{datetime.datetime.now().isoformat()}] {msg}\n")
    print(msg)


def _yaml(value: str) -> str:
    """Quote a string for use as a YAML double-quoted scalar.

    Headlines routinely contain double quotes and colons, which would
    otherwise produce unparseable front matter.
    """
    v = str(value).replace("\\", "\\\\").replace('"', '\\"')
    v = v.replace("\n", " ").replace("\r", " ").strip()
    return f'"{v}"'


def story_url(hit):
    """The link to the story itself — the stable identity of a trending post."""
    return hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"


def fetch_top_story(covered_urls=None):
    """Fetch the top trending AI story that has not been covered yet.

    The same story stayed at the top of the HN index for a month, so taking
    `hits[0]` republished it every few hours — 25 separate URLs for one
    story. The bodies were never identical (each post embeds the live point
    count, which moves), so the SEO audit's body comparison only ever
    caught a fraction of them. The stable key is the source URL, so that is
    what is matched on here, and the next uncovered story in the results
    is taken instead.
    """
    params = {
        "query": "AI agent",
        "tags": "story",
        "numericFilters": "points>30",
        "hitsPerPage": 30,
    }
    url = HN_ALGOLIA + "?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SolAI-Trending/1.0"})
        data = json.loads(urllib.request.urlopen(req, timeout=15).read())
    except Exception as e:
        log(f"fetch failed: {e}")
        return None

    covered = covered_urls or set()
    hits = data.get("hits", [])
    if not hits:
        return None
    for hit in hits:
        if normalise_url(story_url(hit)) not in covered:
            return hit
    log("every trending candidate has already been covered")
    return None


def _posts_on_disk():
    posts_dir = SITE_DIR / "_posts"
    return sorted(posts_dir.glob("*.md")) if posts_dir.is_dir() else []


def _covered_source_urls():
    """Source URLs of every post already in the archive.

    Every source URL in a post counts, not just the first one — a digest
    post carries three stories and the archive must know about all three.
    """
    out = set()
    for p in _posts_on_disk():
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for url in source_urls(text):
            key = normalise_url(url)
            if key:
                out.add(key)
    return out


def _is_published(path):
    """True when the file is committed to the published tree."""
    r = subprocess.run(
        ["git", "-C", str(SITE_DIR), "ls-files", "--error-unmatch", str(path)],
        capture_output=True,
        text=True,
    )
    return r.returncode == 0


def generate_post():
    """Generate a trending-topic commentary post."""
    today = datetime.datetime.now(datetime.timezone.utc)
    date_str = today.strftime("%Y-%m-%d")
    hour = today.hour
    slot = "morning" if hour < 12 else ("afternoon" if hour < 16 else "evening")

    # One trending post per day. The 09:00 / 13:00 / 17:00 slots used to
    # overwrite the same file, because the story at the top of the index
    # never changed — the later runs only ever refreshed the point count.
    # The second and third slots now do nothing.
    existing = [p for p in _posts_on_disk()
                if p.name.startswith(f"{date_str}-trending-")]
    if existing:
        post_path = existing[0]
        if _is_published(post_path):
            log(f"today's trending post is already published ({post_path.name}) — skipping")
        else:
            # Written but never committed — the exact failure the Oct 2026
            # incident produced. Retry the publish instead of adding
            # another post on top of it.
            log(f"today's trending post was written but never published "
                f"({post_path.name}) — retrying publish")
            try:
                from site_git import publish
                sha = publish(f"Trending ({slot}): retry unpublished post", paths=[str(post_path)])
                log(f"✓ publish retry: {sha[:12] if sha else 'nothing new'}")
            except Exception as e:
                log(f"❌ publish retry failed: {e}")
        return None

    # The same story can stay at the top of the index for weeks. It was
    # republished 25 times before the covered-URL check was added, so the
    # check below is load-bearing: if this exact story is already in the
    # archive, write nothing.
    covered = _covered_source_urls()
    story = fetch_top_story(covered)
    if not story:
        log("no uncovered trending story found")
        return None

    title = story.get("title", "AI News Today")
    url = story_url(story)
    points = story.get("points", 0)
    comments = story.get("num_comments", 0)

    # Short headline for the post
    if len(title) > 70:
        title_short = title[:67] + "..."
    else:
        title_short = title

    already = normalise_url(url)
    if already and already in covered:
        log(f"story already covered ({already}) — skipping")
        return None

    import re as _re
    safe = _re.sub(r'[^a-z0-9-]', '-', title_short[:50].lower())
    safe = _re.sub(r'-+', '-', safe).strip('-')
    slug = f"trending-{safe}"
    filename = f"{date_str}-{slug}.md"

    template = COMMENTARY_TEMPLATES[hour % len(COMMENTARY_TEMPLATES)]
    body = template.format(headline_topic=title.lower())

    post_date = f"{today.strftime('%B')} {today.day}, {today.year}"
    full_body = f"""---
layout: post
title: {_yaml(f"Trending: {title_short} ({post_date})")}
description: {_yaml(f"Sol's take on the {slot} AI story: {title_short[:100]} ({post_date})")}
date: {date_str} {hour:02d}:00:00 +0000
tags: [ai, trending, commentary, sol]
author: Sol
image: /images/sol-avatar.png
hero_image: /images/sol-avatar.png
---

**Source story:** [{title}]({url}) — {points} points, {comments} comments on Hacker News

{body}

— Sol

---

*Trending Topics posts run daily, each commenting on a story that has not been covered yet. Subscribe at [sol-alias.com](https://thesolai.github.io) or follow [@TheSolAI](https://github.com/TheSolAI) on GitHub.*
"""

    SITE_DIR.mkdir(parents=True, exist_ok=True)
    post_path = SITE_DIR / "_posts" / filename
    post_path.write_text(full_body, encoding="utf-8")
    log(f"✓ wrote {filename}")

    try:
        from site_git import publish
        sha = publish(f"Trending ({slot}): {title_short[:50]}", paths=[str(post_path)])
        log(f"✓ committed and pushed to origin/main ({sha[:12] if sha else 'nothing new'})")
    except Exception as e:
        log(f"❌ git publish failed: {e}")

    return post_path


if __name__ == "__main__":
    log("=== trending-topics start ===")
    result = generate_post()
    log(f"=== trending-topics done: {result} ===")