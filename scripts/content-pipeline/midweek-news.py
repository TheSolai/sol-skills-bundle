#!/usr/bin/env python3
"""
Sol AI — Mid-Week AI News Digest
Runs every Wednesday at 8am UK time.
Fetches the week's top AI stories, curates the 3 most significant,
and publishes a quick digest.

Cronsetup: launchd StartCalendarInterval Wednesday 07:00
Or: 0 7 * * 3 python3 scripts/content-pipeline/midweek-news.py >> logs/sol-content.log 2>&1
"""

import sys, os, json, datetime, urllib.request
from pathlib import Path

SITE_DIR = Path("/Users/amre/Projects/thesolai.github.io")
BUNDLE_DIR = Path("/Users/amre/Projects/sol-skills-bundle/scripts/content-pipeline")
DEVTO_API_KEY = os.getenv("DEVTO_API_KEY", "")

TODAY = datetime.datetime.utcnow()
DATE_STR = TODAY.strftime("%Y-%m-%d")
DAY_STR = TODAY.strftime("%A")


def fetch_hn_stories(limit=40):
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=3)
    try:
        req = urllib.request.Request(
            "https://hacker-news.firebaseio.com/v0/topstories.json",
            headers={"User-Agent": "SolAI/1.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            ids = json.loads(r.read())
    except Exception as e:
        print(f"[news] Failed: {e}")
        return []

    stories = []
    for story_id in ids[:limit]:
        try:
            req = urllib.request.Request(
                f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json",
                headers={"User-Agent": "SolAI/1.0"}
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                story = json.loads(r.read())
            if story and story.get("title"):
                stories.append({
                    "title": story["title"],
                    "url": story.get("url", f"https://news.ycombinator.com/item?id={story_id}"),
                    "score": story.get("score", 0),
                    "comments": story.get("descendants", 0),
                })
        except Exception:
            pass
    return stories


def is_ai_story(title):
    keywords = [
        "ai", "llm", "gpt", "claude", "gemini", "openai", "anthropic",
        "machine learning", "neural", "model", "chatbot", "agent",
        "copilot", "rag", "embedding", "diffusion",
        "mistral", "groq", "ollama", "langchain",
        "artificial intelligence", "cursor", "devin", "v0",
    ]
    t = title.lower()
    return any(kw in t for kw in keywords)


def build_digest(stories):
    """Build a short midweek digest in Sol's voice."""
    ai_stories = sorted([s for s in stories if is_ai_story(s["title"])],
                        key=lambda s: s["score"] + s["comments"] * 0.5, reverse=True)
    top3 = ai_stories[:3]

    if not top3:
        top3 = sorted(stories, key=lambda s: s["score"], reverse=True)[:3]

    lines = [f"""# AI Digest: {DAY_STR} Morning

*Quick read: the {len(top3)} AI stories that matter this week so far.*

---

"""]

    for i, story in enumerate(top3, 1):
        lines.append(f"**{i}. {story['title']}**")
        lines.append(f"[Read →]({story['url']}) — {story['score']} HN points · {story['comments']} comments\n")

    lines.append("""---

*Full weekly roundup published Monday mornings. Follow [Sol AI](https://thesolai.github.io) for daily coverage.*""")

    return "\n".join(lines), top3


def main():
    print(f"\n{'='*60}")
    print(f"Sol AI Mid-Week Digest — {DAY_STR}")
    print(f"{'='*60}\n")

    print("[news] Fetching recent HN stories...")
    stories = fetch_hn_stories(limit=40)
    ai_stories = [s for s in stories if is_ai_story(s["title"])]
    print(f"[news] Found {len(ai_stories)} AI stories from past 3 days")

    content, top3 = build_digest(stories)
    title = f"AI Digest: {DAY_STR} — {len(top3)} Stories That Matter"

    slug = f"{DATE_STR}-ai-digest-{DAY_STR.lower()}"
    filepath = SITE_DIR / "_posts" / f"{slug}.md"

    frontmatter = f"""---
layout: post
title: "{title}"
date: {DATE_STR}
description: "Mid-week AI digest — the most important AI stories from the past few days, curated by Sol AI."
image: /images/sol-avatar.png
tags: [ai, digest, news, midweek]
author: Sol AI
---

{content}
"""

    if filepath.exists():
        print(f"[digest] Already exists: {filepath.name}")
    else:
        filepath.write_text(frontmatter, encoding="utf-8")
        print(f"[digest] ✅ Written: {filepath.name}")

        try:
            from site_git import publish
            publish(f"Sol AI midweek digest: {DATE_STR}", paths=[str(filepath)])
            print("[git] ✅ Pushed midweek digest to origin/main")
        except Exception as e:
            print(f"[git] ❌  Push FAILED: {e}")

    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    main()
