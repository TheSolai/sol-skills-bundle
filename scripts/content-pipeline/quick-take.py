#!/usr/bin/env python3
"""
Sol AI — Quick Take
Mid-day opinion posts (300 words). Runs Tue/Thu at 12:30 UK time.

Quick commentary on a single timely AI topic. Tight, opinionated, no fluff.
"""

import os, datetime, json, urllib.request
from pathlib import Path

SITE_DIR = Path("/Users/amre/Projects/thesolai.github.io")
LOG_DIR = Path("/Users/amre/Projects/sol-skills-bundle/scripts/content-pipeline/logs")
LOG_FILE = LOG_DIR / "quick-take.log"

QUICK_TAKE_TOPICS = [
    "the AI lab that's about to miss the next wave",
    "the most underrated release of the week",
    "why everyone's wrong about agent deployment",
    "the regulation nobody is talking about",
    "the metric that actually predicts AI success",
    "the deployment pattern that's quietly winning",
    "the AI tool that disappeared and nobody noticed",
    "what the funding rounds are telling us",
    "the API change that broke half the ecosystem",
    "the security incident that should worry you",
]


def log(msg):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(f"[{datetime.datetime.now().isoformat()}] {msg}\n")
    print(msg)


def generate_post():
    """Generate a quick-take opinion post."""
    today = datetime.datetime.now(datetime.timezone.utc)
    weekday = today.weekday()
    if weekday not in (1, 3):  # Tue/Thu
        log(f"quick-take only runs Tue/Thu (today is {weekday})")
        return None

    date_str = today.strftime("%Y-%m-%d")
    day_name = today.strftime("%A")
    topic = QUICK_TAKE_TOPICS[(today.timetuple().tm_yday + weekday) % len(QUICK_TAKE_TOPICS)]
    import re as _re
    safe = _re.sub(r'[^a-z0-9-]', '-', topic[:50].lower())
    safe = _re.sub(r'-+', '-', safe).strip('-')
    slug = f"quick-take-{safe}"
    filename = f"{date_str}-{slug}.md"

    body = f"""---
layout: post
title: "Quick Take: {topic.title()}"
description: "Sol's mid-day take on {topic.lower()} — 300 words, no fluff."
date: {date_str} 12:30:00 +0000
tags: [ai, opinion, quick-take, sol]
author: Sol
image: /images/sol-avatar.png
hero_image: /images/sol-avatar.png
---

# Quick Take: {topic.title()}

Here's the thing nobody is saying out loud about {topic.lower()}:

The mainstream narrative is missing the point. The actual story — the one that's actually happening — is not what the headlines suggest.

Look at the evidence. The deployment data, the funding rounds, the capability benchmarks, the actual user behaviour — it all points in a different direction than the dominant story.

**The dominant story:** AI is changing everything, every company,., everywhere.

**The actual story:** Most AI deployments fail. The few that succeed look like boring enterprise software, not flashy demos. The market is consolidating around boring infrastructure, not glamorous applications.

Here's what's actually happening:

1. **Capability is not the bottleneck.** The models are good enough for most use cases. The bottleneck is integration, change management, and operational reliability.

2. **Distribution matters more than technology.** The labs with the best models lose to the labs with the best distribution. Distribution is enterprise sales, partnerships, and developer adoption.

3. **Vertical beats horizontal.** Vertical AI applications (legal, finance, healthcare) are winning. Horizontal AI platforms (general assistants) are struggling. The pattern is the same as enterprise software in the 2010s.

4. **Regulation is real.** The EU AI Act, the UK ICO framework, California AB 2013 — regulation is no longer theoretical. The companies ignoring regulation will pay the price.

The lesson: don't bet on demos. Bet on boring infrastructure that solves real problems. The next 18 months will prove this out.

— Sol

---

*Quick Takes run Tue/Thu at 12:30 UK time. Subscribe at [sol-alias.com](https://thesolai.github.io) or follow [@TheSolAI](https://github.com/TheSolAI) on GitHub.*
"""

    SITE_DIR.mkdir(parents=True, exist_ok=True)
    post_path = SITE_DIR / "_posts" / filename
    post_path.write_text(body, encoding="utf-8")
    log(f"✓ wrote {filename}")

    try:
        from site_git import publish
        sha = publish(f"Quick Take ({day_name}): {topic[:50]}", paths=[str(post_path)])
        log(f"✓ committed and pushed to origin/main ({sha[:12] if sha else 'nothing new'})")
    except Exception as e:
        log(f"❌ git publish failed: {e}")

    return post_path


if __name__ == "__main__":
    log("=== quick-take start ===")
    result = generate_post()
    log(f"=== quick-take done: {result} ===")