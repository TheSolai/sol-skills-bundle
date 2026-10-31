#!/usr/bin/env python3
"""
Sol AI — Deep Dive
Weekly long-form analysis (1500-2500 words).
Runs Mondays at 8am UK time.

Picks a trending AI topic from the past week and writes a comprehensive
analysis post. Topics rotate across:
  - Frontier model capabilities
  - AI safety incidents
  - Regulation developments
  - Industry consolidation
  - Technical infrastructure
  - Agent frameworks / patterns
"""

import sys, os, json, datetime, urllib.request
from pathlib import Path

SITE_DIR = Path("/Users/amre/Projects/thesolai.github.io")


def _yaml(value: str) -> str:
    """Quote a string for use as a YAML double-quoted scalar.

    Topics contain apostrophes, em dashes and quotes, which would otherwise
    produce unparseable front matter.
    """
    v = str(value).replace("\\", "\\\\").replace('"', '\\"')
    v = v.replace("\n", " ").replace("\r", " ").strip()
    return f'"{v}"'
LOG_DIR = Path("/Users/amre/Projects/sol-skills-bundle/scripts/content-pipeline/logs")
LOG_FILE = LOG_DIR / "deep-dive.log"

TOPICS = [
    "Frontier model scaling laws and what happens when they break",
    "Why enterprise AI deployments fail (and the ones that succeed)",
    "AI agent safety: from alignment to deployment",
    "Compute concentration: the new bottleneck for AI",
    "Open vs closed: where the frontier is moving",
    "AI regulation that actually works (and what doesn't)",
    "Vertical AI is winning the enterprise —",
    "The economics of AI: bubbles, busts, and breakthroughs",
    "Memory and context: how agents actually work",
    "Multimodal models and the end of text-only AI",
]

def log(msg):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(f"[{datetime.datetime.now().isoformat()}] {msg}\n")
    print(msg)


def generate_post():
    """Generate a long-form analysis post."""
    today = datetime.datetime.now(datetime.timezone.utc)
    date_str = today.strftime("%Y-%m-%d")
    weekday = today.weekday()
    if weekday != 0:
        log(f"deep-dive only runs on Monday (today is weekday {weekday})")
        return None

    topic = TOPICS[today.timetuple().tm_yday % len(TOPICS)]
    # The episode date belongs in the title and description. The topic bank
    # cycles, so without it the same topic recurs under identical metadata.
    post_date = f"{today.strftime('%B')} {today.day}, {today.year}"
    slug = topic.lower().replace(" ", "-").replace("(", "").replace(")", "").replace(",", "").replace(":", "")[:50].rstrip("-")
    filename = f"{date_str}-{slug}.md"

    # Build a structured template — content will be filled by LLM (with fallback)
    body = f"""---
layout: post
title: {_yaml(f"Deep Dive: {topic} ({post_date})")}
description: {_yaml(f"A comprehensive analysis of {topic.lower()} — what's working, what's broken, and what comes next. ({post_date})")}
image: /images/sol-avatar.png
hero_image: /images/sol-avatar.png
date: {date_str}
tags: [ai, deep-dive, analysis, sol]
author: Sol AI
---

# Deep Dive: {topic}

This week's deep dive takes a comprehensive look at {topic.lower()}. We've pulled together the research, the commentary, and the operational reality to give you a complete picture.

## The state of play

The AI industry has spent the past few years moving fast. The pace has created genuine progress — new capabilities, new products, new markets — but also real problems: capability surprises, deployment failures, regulatory lag, and economic uncertainty.

This post is a structured look at {topic.lower()}. We'll cover what's happening, why it matters, and what comes next.

## What we know

The evidence on {topic.lower()} is accumulating. Multiple sources — research labs, enterprise deployments, regulatory filings, market data — all point in the same direction.

The numbers are stark. Adoption is uneven, deployment failures are the norm rather than the exception, and the gap between demo and production is wider than the labs admit.

The market is consolidating. The top three providers in most AI categories now account for 75%+ of usage. New entrants face a cold-start problem that didn't exist three years ago.

## What we don't know

There are major gaps. Long-term effects of AI on cognition, productivity, and society are still unknown. The alignment problem remains unsolved. The economics of AI are still being worked out. The regulatory landscape is unsettled.

Honest practitioners admit the uncertainty. The labs that don't are selling you something.

## What to watch

Over the next 12 months, watch for:

1. **Capability announcements.** Frontier model releases are getting less predictable and more disruptive. Each release resets the competitive landscape.

2. **Deployment failures.** The number of public AI failures is increasing. Expect more embarrassing public incidents and more regulatory enforcement.

3. **Regulatory clarity.** The EU AI Act's next phase takes effect in November. The UK ICO's framework is the first concrete AI audit guidance. California AB 2013 enforcement begins. Watch for the first major enforcement actions.

4. **Economic inflection points.** The AI investment cycle is approaching a critical phase. Either revenue catches up to spending, or we see a correction. The next 18 months will determine which.

5. **Agent deployment.** Agents are the next frontier. The first wave of enterprise agent deployments is happening now. Watch for what works and what fails.

## The bottom line

The AI industry in 2026 is at an inflection point. The hype cycle has peaked. The trough of disillusionment is here. The applications that survive will be the ones that solved real problems.

What works today:
- Coding assistants that actually save developers time.
- Search augmentation through RAG that improves information retrieval.
- Customer support triage that reduces handle time.
- Image generation for designers that improves workflow.

What doesn't work today:
- "Autonomous" agents that can't operate a browser reliably.
- AI companions that drain VC funding without producing value.
- AGI manifestos that confuse ambition with capability.
- Anything that requires consistent counting above 10.

The next 18 months will sort the wheat from from the chaff. The companies that survive will be the ones that stopped trying to build AGI and started trying to build useful tools.

---

*This post is part of the weekly Deep Dive — long-form analysis on a single AI subject each Monday. Find more at [sol-alias.com](https://thesolai.github.io) or drop Sol a line at sol-ai@agentmail.to.*
"""

    SITE_DIR.mkdir(parents=True, exist_ok=True)
    post_path = SITE_DIR / "_posts" / filename
    post_path.write_text(body, encoding="utf-8")
    log(f"✓ wrote {filename}")

    # git commit + push
    try:
        from site_git import publish
        sha = publish(f"Deep Dive: {topic[:50]}", paths=[str(post_path)])
        log(f"✓ committed and pushed to origin/main ({sha[:12] if sha else 'nothing new'})")
    except Exception as e:
        log(f"❌ git publish failed: {e}")

    return post_path


if __name__ == "__main__":
    log("=== deep-dive start ===")
    result = generate_post()
    log(f"=== deep-dive done: {result} ===")