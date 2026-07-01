#!/usr/bin/env python3
"""
Sol AI — Weekend Deep Dive
Long-form researched piece on one AI topic, 600-800 words.
Runs every Saturday at 9am UK time.

Cronsetup: launchd StartCalendarInterval Saturday 08:00
Or: 0 8 * * 6 python3 scripts/content-pipeline/weekend-deep-dive.py >> logs/sol-deep-dive.log 2>&1
"""

import sys, os, json, datetime, urllib.request
from pathlib import Path

SITE_DIR = Path("/Users/amre/Projects/thesolai.github.io")
BUNDLE_DIR = Path("/Users/amre/Projects/sol-skills-bundle/scripts/content-pipeline")
DEVTO_API_KEY = "SmxPhE8pmiScGnW8SprUCF7U"
MINIMAX_KEY_PATH = Path.home() / ".openclaw" / "workspace" / "secrets" / "minimax-key.txt"

TODAY = datetime.datetime.now(datetime.timezone.utc)
DATE_STR = TODAY.strftime("%Y-%m-%d")
POST_DATE = TODAY.strftime("%B %d, %Y")
SAT_STR = TODAY.strftime("%A, %B %d, %Y")


def _load_minimax_key() -> str:
    try:
        return MINIMAX_KEY_PATH.read_text().strip()
    except Exception:
        return ""


def llm_generate(prompt: str, system: str = "", max_tokens: int = 2000) -> str:
    """Generate text via MiniMax, or return None for template fallback."""
    # ── MiniMax fallback: MUST be explicitly enabled ─────────────────────────
    # Default: disabled. Set USE_MINIMAX_FALLBACK=1 to enable.
    # Do NOT flip this without understanding it will bill your MiniMax account.
    if os.getenv("USE_MINIMAX_FALLBACK", "0") != "1":
        if MINIMAX_KEY_PATH.exists():
            print("[llm] MiniMax key found but USE_MINIMAX_FALLBACK=1 not set — using template fallback")
        return None

    key = _load_minimax_key()
    if not key:
        print("[llm] USE_MINIMAX_FALLBACK=1 but MiniMax key not found — using template fallback")
        return None

    messages = [{"role": "user", "content": system + "\n\n" + prompt}] if system else [{"role": "user", "content": prompt}]
    body = json.dumps({
        "model": "MiniMax-Text-01",
        "max_tokens": min(max_tokens, 8192),
        "temperature": 0.7,
        "messages": messages,
    }).encode()
    try:
        req = urllib.request.Request(
            "https://api.minimax.io/anthropic/v1/messages",
            data=body,
            headers={
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
                "x-api-key": key,
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            resp = json.loads(r.read())
            for block in resp.get("content", []):
                if block.get("type") == "text":
                    text = block["text"].strip()
                    if text:
                        print("[llm] ✅ Generated via MiniMax")
                        return text
    except Exception as e:
        print(f"[llm] MiniMax error: {e}")
    print("[llm] No LLM available — using template fallback")
    return None


# Weeks rotate through topics
TOPICS = [
    {
        "topic": "Why AI assistants lie (and why 'hallucination' is the wrong word)",
        "tags": ["ai", "analysis", "llm", "truth", "sol"],
        "description": "Deep dive into why LLMs generate plausible falsehoods, what 'hallucination' gets wrong, and what the real problem is.",
    },
    {
        "topic": "The real cost of running AI at scale",
        "tags": ["ai", "infrastructure", "cost", "analysis", "sol"],
        "description": "Water, power, carbon, money. What nobody tells you about what it actually costs to run AI.",
    },
    {
        "topic": "Why prompt engineering is neither art nor science",
        "tags": ["ai", "prompt-engineering", "analysis", "sol"],
        "description": "It's pattern-matching with a stochastic parrot. Here's what that actually means for how you use AI.",
    },
    {
        "topic": "The agent hype bubble — what's real and what's not",
        "tags": ["ai", "agents", "analysis", "hype", "sol"],
        "description": "Autonomous agents are the hottest thing in AI. Most of them don't work. Let's talk about why people keep buying them anyway.",
    },
    {
        "topic": "How AI changes what 'learning' means",
        "tags": ["ai", "learning", "education", "analysis", "sol"],
        "description": "Amre learned Python using AI. That experience changes how you think about education. Here's why.",
    },
    {
        "topic": "The closed-source vs. open-source AI divide",
        "tags": ["ai", "open-source", "analysis", "llm", "sol"],
        "description": "Who controls the models matters. Here's a practical look at what you give up with each approach.",
    },
    {
        "topic": "Why AI code review is both better and worse than human review",
        "tags": ["ai", "coding", "analysis", "claude", "sol"],
        "description": "AI catches different things than humans do. Neither is sufficient alone. Here's the real picture.",
    },
    {
        "topic": "The synthetic data problem — are we training AI on AI?",
        "tags": ["ai", "data", "training", "analysis", "sol"],
        "description": "LLMs trained on LLM-generated data. What happens when the well runs dry and all the water is recycled?",
    },
]


def fetch_hn_stories(topic_kw: str, limit=20) -> list:
    """Fetch HN stories relevant to a topic."""
    try:
        req = urllib.request.Request(
            "https://hacker-news.firebaseio.com/v0/topstories.json",
            headers={"User-Agent": "SolAI/1.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            ids = json.loads(r.read())
    except Exception as e:
        print(f"[deep-dive] HN fetch failed: {e}")
        return []

    stories = []
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=14)
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
            if story.get("time", 0) < cutoff_ts:
                continue
            title = story["title"].lower()
            if topic_kw.lower() not in title:
                continue
            stories.append({
                "title": story["title"],
                "url": story.get("url", f"https://news.ycombinator.com/item?id={story_id}"),
                "score": story.get("score", 0),
                "comments": story.get("descendants", 0),
            })
        except Exception:
            pass
    stories.sort(key=lambda s: s["score"], reverse=True)
    return stories[:3]


def fetch_devto_articles(topic_kw: str, limit=3) -> list:
    """Fetch dev.to articles on topic."""
    try:
        url = f"https://dev.to/api/articles?tag=ai&per_page={limit}&q={topic_kw.replace(' ', '%20')}"
        req = urllib.request.Request(url, headers={"User-Agent": "SolAI/1.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            articles = json.loads(r.read())
        return [
            {"title": a["title"], "url": a["url"], "reactions": a.get("public_reactions_count", 0)}
            for a in articles if a.get("title")
        ]
    except Exception as e:
        print(f"[deep-dive] dev.to fetch failed: {e}")
        return []


def pick_topic() -> dict:
    iso_cal = TODAY.isocalendar()
    idx = (iso_cal.week * iso_cal.year) % len(TOPICS)
    return TOPICS[idx]


def generate_deep_dive(topic: dict, hn_stories: list, devto_articles: list) -> str:
    hn_txt = "\n".join(
        f"- [{s['title']}]({s['url']}) (HN: {s['score']} pts, {s['comments']} comments)"
        for s in hn_stories
    ) if hn_stories else "No recent HN discussions found."
    devto_txt = "\n".join(
        f"- [{a['title']}]({a['url']}) ({a['reactions']} reactions)"
        for a in devto_articles
    ) if devto_articles else "No recent dev.to articles found."

    prompt = f"""Write a Weekend Deep Dive — a long-form researched blog post, 600-800 words.

Topic: {topic['topic']}
Description: {topic['description']}

Structure:
1. Hook — open with the core question or tension (1-2 sentences)
2. Background — what's the actual situation (2-3 paragraphs)
3. Analysis — take a position, back it up with specifics (3-4 paragraphs)
4. What this means in practice — concrete examples (1-2 paragraphs)
5. Closing — one sharp takeaway sentence

Rules:
- Conversational, slightly wry tone
- Sol's voice — direct, opinionated, honest
- No bullet points in the body
- No hedging. Take a clear position.
- Ground claims in specifics, not platitudes
- Cite relevant discussions from the community

Recent HN discussions:
{hn_txt}

Recent dev.to articles:
{devto_txt}

Date: {POST_DATE}
Author: Sol
Tags: {', '.join(topic['tags'])}
"""
    system = "You are Sol AI. You're a researcher and writer. You go deep, not wide. You have strong opinions and you support them with evidence. You don't waste the reader's time."
    return llm_generate(prompt, system, max_tokens=2500)


def create_post(body: str, topic: dict) -> Path:
    slug = f"{DATE_STR}-weekend-deep-dive"
    filename = f"{slug}.md"
    post_path = SITE_DIR / "_posts" / filename

    tags_str = "[" + ", ".join(topic["tags"]) + "]"
    frontmatter = f"""---
layout: post
title: "{topic['topic']}"
date: {DATE_STR} 09:00:00 +0000
tags: {tags_str}
author: Sol
description: "{topic['description']}"
image: /images/sol-avatar.png
---

{body}
"""
    post_path.write_text(frontmatter, encoding="utf-8")
    print(f"[deep-dive] Post written: {post_path.name}")
    return post_path


def main():
    topic = pick_topic()
    print(f"[deep-dive] Deep diving: {topic['topic']} ({SAT_STR})")

    # Extract main keyword for searches
    main_kw = topic["topic"].split()[0:3]
    kw = " ".join(main_kw)

    hn_stories = fetch_hn_stories(kw, limit=20)
    print(f"[deep-dive] Found {len(hn_stories)} relevant HN stories")
    for s in hn_stories:
        print(f"  - {s['title'][:60]}")

    devto_articles = fetch_devto_articles(kw, limit=3)
    print(f"[deep-dive] Found {len(devto_articles)} relevant dev.to articles")

    body = generate_deep_dive(topic, hn_stories, devto_articles)
    if not body:
        hn_txt = "\n".join(f"- [{s['title']}]({s['url']})" for s in hn_stories[:3]) if hn_stories else "No HN discussions found."
        body = f"""**{topic['topic']}**

{topic['description']}

This is a topic worth sitting with. {topic['topic']} is one of those areas where the real picture is more complicated than the headlines suggest, and more interesting than the critics admit.

The honest answer is that we're still working out what this means in practice. What I can tell you is this: based on what I've seen running this site and working with Amre, the reality on the ground rarely matches either the hype or the backlash.

**What people are saying:**

{hn_txt}

More research needed — check back tomorrow for the full piece.
"""
        print("[deep-dive] Using template fallback")

    post_path = create_post(body, topic)

    try:
        import subprocess
        cwd = SITE_DIR
        subprocess.run(["git", "add", "."], cwd=cwd, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", f"Weekend Deep Dive: {topic['topic'][:50]}"], cwd=cwd, check=True, capture_output=True)
        subprocess.run(["git", "push"], cwd=cwd, check=True, capture_output=True, timeout=30)
        print("[deep-dive] Committed and pushed.")
    except Exception as e:
        print(f"[deep-dive] Git push failed: {e}")


if __name__ == "__main__":
    main()
