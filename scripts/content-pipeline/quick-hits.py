#!/usr/bin/env python3
"""
Sol AI — Quick Hits
Weekday AI briefing: 3 curated stories, no fluff.
Runs every weekday at 8am UK time.

Cronsetup: launchd StartCalendarInterval Monday-Friday 07:00
Or: 0 7 * * 1-5 python3 scripts/content-pipeline/quick-hits.py >> logs/sol-quick-hits.log 2>&1
"""

import sys, os, json, datetime, urllib.request, urllib.parse, re
from pathlib import Path
import sys as _sys
_sys.path.insert(0, "/Users/amre/Projects/sol-skills-bundle/scripts/content-pipeline")
from devto_key import load_devto_key
from _minimax_helper import minimax_generate, load_minimax_key_candidates

SITE_DIR = Path("/Users/amre/Projects/thesolai.github.io")
BUNDLE_DIR = Path("/Users/amre/Projects/sol-skills-bundle/scripts/content-pipeline")
DEVTO_API_KEY = load_devto_key()

TODAY = datetime.datetime.now(datetime.timezone.utc)
DATE_STR = TODAY.strftime("%Y-%m-%d")
POST_DATE = TODAY.strftime("%B %d, %Y")
DAY_STR = TODAY.strftime("%A")
IS_WEEKDAY = TODAY.weekday() < 5  # Mon-Fri = 0-4


def llm_generate(prompt: str, system: str = "", max_tokens: int = 6000) -> str | None:
    """Generate text via MiniMax, or return None for template fallback."""
    keys = load_minimax_key_candidates()
    if not keys:
        print("[llm] No MiniMax key available — using template fallback")
        return None
    try:
        # The helper tries every known key and every known model in turn, and
        # prints the reason when all of them fail — so a degraded run is
        # visible in this log instead of silently shipping a template post.
        result = minimax_generate(prompt, api_key=keys[0], max_tokens=max_tokens, system=system)

        if result:
            print(f"[llm] ✅ MiniMax ({len(result.split())} words)")
            return result
        print("[llm] MiniMax returned nothing — using template fallback")
    except Exception as e:
        print(f"[llm] MiniMax error: {e}")
    return None


def fetch_hn_ai_stories(limit=30):
    """Fetch HN top stories, return AI-filtered list."""
    try:
        req = urllib.request.Request(
            "https://hacker-news.firebaseio.com/v0/topstories.json",
            headers={"User-Agent": "SolAI/1.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            ids = json.loads(r.read())
    except Exception as e:
        print(f"[hn] Failed to fetch HN stories: {e}")
        return []

    AI_KW = [
        "ai", "llm", "gpt", "claude", "gemini", "openai", "anthropic",
        "machine learning", "neural", "model", "chatbot", "agent",
        "copilot", "rag", "embedding", "diffusion", "stable diffusion",
        "mistral", "groq", "ollama", "langchain", "autogen", "crewai",
        "nlp", "vision", "multimodal", "reasoning", "cursor", "v0",
        "devin", "replit", "lovable", "bolt", "artificial intelligence",
    ]

    stories = []
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=36)
    cutoff_ts = cutoff.timestamp()

    for story_id in ids[:limit]:
        try:
            req = urllib.request.Request(
                f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json",
                headers={"User-Agent": "SolAI/1.0"}
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                story = json.loads(r.read())
            if not story or not story.get("title"):
                continue
            # Only recent-ish stories
            time = story.get("time", 0)
            if time and time < cutoff_ts:
                continue
            title = story["title"].lower()
            if not any(kw in title for kw in AI_KW):
                continue
            stories.append({
                "id": story_id,
                "title": story["title"],
                "url": story.get("url", f"https://news.ycombinator.com/item?id={story_id}"),
                "score": story.get("score", 0),
                "comments": story.get("descendants", 0),
                "by": story.get("by", ""),
            })
        except Exception:
            pass
    stories.sort(key=lambda s: s["score"] + s["comments"] * 0.5, reverse=True)
    return stories[:8]


def fetch_devto_articles(limit=10):
    """Fetch trending dev.to articles tagged 'AI'."""
    try:
        url = f"https://dev.to/api/articles?tag=ai&per_page={limit}&top=1"
        req = urllib.request.Request(url, headers={"User-Agent": "SolAI/1.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            articles = json.loads(r.read())
        return [
            {
                "title": a["title"],
                "url": a["url"],
                "reactions": a.get("public_reactions_count", 0),
                "comments": a.get("comments_count", 0),
                "read_time": a.get("reading_time_minutes", 1),
                "user": a.get("user", {}).get("name", "unknown"),
            }
            for a in articles if a.get("title")
        ]
    except Exception as e:
        print(f"[devto] Failed to fetch dev.to articles: {e}")
        return []


# A line that is nothing but a signature: optional dashes/markdown, the name,
# optional "AI", optional markdown. "—Sol", "-- Sol" and "**Sol**" all match;
# a sentence that merely ends in the word "Sol" does not, because the name has
# to be alone on its line.
_TRAILING_BYLINE = re.compile(
    r"^\s*(?:[-—–_*]{0,4}\s*)?Sol(?:\s+AI)?\s*(?:[-—–_*]{0,4})\s*$",
    re.IGNORECASE,
)


def strip_trailing_byline(body: str) -> str:
    """Remove a model-added trailing byline from the body."""
    if not body:
        return body
    lines = body.rstrip().split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and _TRAILING_BYLINE.match(lines[-1]):
        lines.pop()
    return "\n".join(lines).rstrip()


def build_quick_hits(hn_stories, devto_articles):
    """Use LLM to pick best 3 stories and write the briefing."""

    if not hn_stories and not devto_articles:
        print("[quick-hits] No stories fetched — skipping.")
        return None

    # Combine sources
    all_items = []
    for s in hn_stories:
        all_items.append({
            "source": "HN",
            "title": s["title"],
            "url": s["url"],
            "engagement": s["score"] + s["comments"] * 0.5,
            "score": s["score"],
            "comments": s["comments"],
        })
    for d in devto_articles:
        all_items.append({
            "source": "dev.to",
            "title": d["title"],
            "url": d["url"],
            "engagement": d["reactions"] + d["comments"] * 0.5,
            "score": d["reactions"],
            "comments": d["comments"],
        })

    # Sort by engagement, take top 5 for LLM to choose from
    all_items.sort(key=lambda x: x["engagement"], reverse=True)
    candidates = all_items[:5]

    candidates_txt = "\n".join(
        f"- [{item['source']}] {item['title']}\n  URL: {item['url']}\n  Score: {item['score']} / Comments: {item['comments']}"
        for item in candidates
    )

    prompt = f"""You are Sol AI. Write a 'Quick Hits' AI briefing post — 3 items, conversational tone, direct, slightly wry.

Pick the 3 most interesting items from the candidates below. Write each as:
**What happened:** [1-2 sentences]
**Why it matters:** [1-2 sentences]

Keep it punchy. No intro paragraph needed — just jump straight in. Total post should be ~200 words.

Date: {POST_DATE}
Author: Sol

Candidate stories:
{candidates_txt}
"""
    system = "You are Sol AI. Direct, honest, slightly wry. No corporate speak. No fluff."
    body = llm_generate(prompt, system, max_tokens=6000)
    if not body:
        # Template fallback — use top 3 items directly
        top3 = candidates[:3]
        lines = []
        for i, item in enumerate(top3, 1):
            lines.append(f"**{i}. {item['title']}**")
            lines.append(f"[{item['source']}]({item['url']}) — {item['score']} score")
            lines.append("")
        return "\n".join(lines)

    # The model sometimes signs the post itself ("—Sol", "-- Sol", "**Sol**").
    # The layout already renders "By Sol" in the header, so a trailing
    # signature is a duplicate byline — the same defect sols-take.py and
    # run-daily.py already strip. Only a line that is nothing but the
    # signature is removed, so prose that merely ends in the word is safe.
    stripped = strip_trailing_byline(body)
    if stripped != body.strip():
        print("[quick-hits] Stripped trailing byline from the body")
        body = stripped
    return body


def create_post(body: str) -> Path:
    """Write the Jekyll post to _posts."""
    slug = f"{DATE_STR}-quick-hits-{DAY_STR.lower()}"
    filename = f"{slug}.md"
    post_path = SITE_DIR / "_posts" / filename

    frontmatter = f"""---
layout: post
title: "Quick Hits: {DAY_STR}'s AI Briefing — {POST_DATE}"
date: {DATE_STR} 08:00:00 +0000
tags: [ai, briefing, quick-hits, sol]
author: Sol
description: "Three AI stories worth knowing about on {POST_DATE}."
image: /images/sol-avatar.png
hero_image: /images/sol-avatar.png
---

{body}
"""
    post_path.write_text(frontmatter, encoding="utf-8")
    print(f"[quick-hits] Post written: {post_path.name}")
    return post_path


def main():
    if not IS_WEEKDAY:
        print(f"[quick-hits] Not a weekday ({DAY_STR}) — skipping.")
        return

    print(f"[quick-hits] Running for {DAY_STR}, {POST_DATE}")

    hn_stories = fetch_hn_ai_stories(limit=30)
    print(f"[quick-hits] Fetched {len(hn_stories)} HN AI stories")

    devto_articles = fetch_devto_articles(limit=10)
    print(f"[quick-hits] Fetched {len(devto_articles)} dev.to articles")

    body = build_quick_hits(hn_stories, devto_articles)
    if not body or body.startswith("[LLM") or body.startswith("[error"):
        print(f"[quick-hits] Failed to generate content: {body}")
        return

    post_path = create_post(body)

    # Auto-commit and push
    try:
        import subprocess
        cwd = SITE_DIR
        from site_git import publish
        sha = publish(f"Quick Hits: {DAY_STR} AI briefing", cwd=cwd)
        print(f"[quick-hits] Committed and pushed to origin/main ({sha[:12] if sha else 'nothing new'}).")
    except Exception as e:
        print(f"[quick-hits] Git push failed: {e}")


if __name__ == "__main__":
    main()
