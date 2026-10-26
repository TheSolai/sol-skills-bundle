#!/usr/bin/env python3
"""
Sol AI — Trending Topics
3x daily scan for trending AI stories + quick commentary post.
Runs 09:00 / 13:00 / 17:00 UK time.

Scans HN Algolia for trending AI stories (>=30 points), picks the top one,
and writes a short commentary post. Different from Quick Hits — these
are real-time responses to breaking stories.
"""

import os, datetime, urllib.request, urllib.parse, json
from pathlib import Path

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


def fetch_top_story():
    """Fetch the top trending AI story right now."""
    params = {
        "query": "AI agent",
        "tags": "story",
        "numericFilters": "points>30",
        "hitsPerPage": 10,
    }
    url = HN_ALGOLIA + "?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SolAI-Trending/1.0"})
        data = json.loads(urllib.request.urlopen(req, timeout=15).read())
        return data.get("hits", [{}])[0] if data.get("hits") else None
    except Exception as e:
        log(f"fetch failed: {e}")
        return None


def generate_post():
    """Generate a trending-topic commentary post."""
    today = datetime.datetime.now(datetime.timezone.utc)
    date_str = today.strftime("%Y-%m-%d")
    hour = today.hour
    slot = "morning" if hour < 12 else ("afternoon" if hour < 16 else "evening")

    story = fetch_top_story()
    if not story:
        log("no trending story found")
        return None

    title = story.get("title", "AI News Today")
    url = story.get("url") or f"https://news.ycombinator.com/item?id={story.get('objectID')}"
    points = story.get("points", 0)
    comments = story.get("num_comments", 0)

    # Short headline for the post
    if len(title) > 70:
        title_short = title[:67] + "..."
    else:
        title_short = title

    import re as _re
    safe = _re.sub(r'[^a-z0-9-]', '-', title_short[:50].lower())
    safe = _re.sub(r'-+', '-', safe).strip('-')
    slug = f"trending-{safe}"
    filename = f"{date_str}-{slug}.md"

    template = COMMENTARY_TEMPLATES[hour % len(COMMENTARY_TEMPLATES)]
    body = template.format(headline_topic=title.lower())

    full_body = f"""---
layout: post
title: "Trending: {title_short}"
description: "Sol's take on the {slot} AI story: {title_short[:100]}"
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

*Trending Topics posts run 3x daily (09:00 / 13:00 / 17:00 UK). Subscribe at [sol-alias.com](https://thesolai.github.io) or follow [@TheSolAI](https://github.com/TheSolAI) on GitHub.*
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