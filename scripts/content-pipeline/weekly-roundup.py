#!/usr/bin/env python3
"""
Sol AI — Weekly AI Roundup
Runs every Monday at 8am UK time.
Fetches the week's top AI stories, picks the 5 most significant,
and publishes a curated roundup in Sol's voice.

Cronsetup: launchd StartCalendarInterval Monday 07:00
Or: 0 7 * * 1 python3 scripts/content-pipeline/weekly-roundup.py >> logs/sol-content.log 2>&1
"""

import sys, os, json, datetime, urllib.request, re, urllib.error
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

SITE_DIR = Path("/Users/amre/Projects/thesolai.github.io")
BUNDLE_DIR = Path("/Users/amre/Projects/sol-skills-bundle/scripts/content-pipeline")
DEVTO_API_KEY = os.getenv("DEVTO_API_KEY", "")

TODAY = datetime.datetime.now(datetime.timezone.utc)
DATE_STR = TODAY.strftime("%Y-%m-%d")
POST_DATE = TODAY.strftime("%B %d, %Y")
WEEK_STR = TODAY.strftime("%B %Y")


def fetch_story(story_id, cutoff):
    """Fetch a single HN story if it's within the cutoff and has a title."""
    try:
        req = urllib.request.Request(
            f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json",
            headers={"User-Agent": "SolAI/1.0"}
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            story = json.loads(r.read())
        if story and story.get("title") and story.get("time"):
            story_time = datetime.datetime.fromtimestamp(story["time"], tz=datetime.timezone.utc)
            if story_time >= cutoff:
                return {
                    "id": story_id,
                    "title": story.get("title", ""),
                    "url": story.get("url", f"https://news.ycombinator.com/item?id={story_id}"),
                    "score": story.get("score", 0),
                    "by": story.get("by", ""),
                    "comments": story.get("descendants", 0),
                    "time": story_time,
                }
    except Exception:
        pass
    return None


def fetch_hn_stories(limit=500, days_back=7):
    """Fetch newest HN stories and filter for AI-related + recent ones.

    Uses newstories.json (chronological) rather than topstories.json (score-ranked)
    to ensure week-old stories are captured even if they're no longer on the front page.
    """
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_back)
    try:
        req = urllib.request.Request(
            "https://hacker-news.firebaseio.com/v0/newstories.json",
            headers={"User-Agent": "SolAI/1.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            ids = json.loads(r.read())
    except Exception as e:
        print(f"[news] Failed to fetch HN stories: {e}")
        return []

    # Take first 'limit' IDs and fetch them concurrently
    story_ids = ids[:limit]
    stories = []
    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = {executor.submit(fetch_story, sid, cutoff): sid for sid in story_ids}
        for future in as_completed(futures):
            result = future.result()
            if result:
                stories.append(result)
    return stories


def is_ai_story(title):
    keywords = [
        "ai", "llm", "gpt", "claude", "gemini", "openai", "anthropic",
        "machine learning", "neural", "model", "chatbot", "agent",
        "copilot", "rag", "embedding", "diffusion", "stable diffusion",
        "mistral", "groq", "ollama", "langchain", "autogen", "crewai",
        "nlp", "vision", "multimodal", "reasoning",
        "artificial intelligence", "cursor", "devin", "v0", "replit",
        "opencl", "bolt.new", "lovable",
    ]
    t = title.lower()
    return any(kw in t for kw in keywords)


def build_roundup(stories):
    """Build the weekly roundup post content in Sol's voice."""
    ai_stories = sorted([s for s in stories if is_ai_story(s["title"])],
                        key=lambda s: s["score"] + s["comments"] * 0.5, reverse=True)
    top_stories = ai_stories[:7]

    if not top_stories:
        top_stories = sorted(stories, key=lambda s: s["score"], reverse=True)[:5]

    # Categorise stories
    policy_stories = [s for s in top_stories if any(k in s["title"].lower() for k in ["regulation", "law", "act", "policy", "government", "eu", "uk ", "uk,", "congress", "trump", "biden"])]
    product_stories = [s for s in top_stories if s not in policy_stories and any(k in s["title"].lower() for k in ["launch", "release", "introduces", "unveils", "new", "open-source", "open source"])]
    research_stories = [s for s in top_stories if s not in policy_stories and s not in product_stories]

    sections = []
    if policy_stories:
        sections.append(("Policy & Regulation", policy_stories))
    if product_stories:
        sections.append(("Products & Releases", product_stories))
    if research_stories:
        sections.append(("Research & Ideas", research_stories))

    # Build markdown
    total_hn_points = sum(s["score"] for s in top_stories)

    lines = [f"""# The Week in AI: {WEEK_STR}

*Weekly roundup from Sol AI — curated AI news, assessed and contextualised.*

---

This week in AI: {len(top_stories)} stories that mattered, drawn from {total_hn_points} HN points across the community.

"""]

    for section_title, section_stories in sections:
        lines.append(f"## {section_title}\n")
        for i, story in enumerate(section_stories, 1):
            lines.append(f"**{i}. {story['title']}**")
            lines.append(f"[Read more →]({story['url']}) — {story['score']} points · {story['comments']} comments\n")
        lines.append("")

    # Sol's commentary
    lines.append("""## Sol's Take

This week's pattern: the AI industry is still in full infrastructure-building mode. Custom silicon, bigger models, faster inference. The policy conversation is catching up — but slowly.

The most interesting signal this week is the growing split between AI as a product (consumer apps, tools) and AI as infrastructure (chips, APIs, enterprise platforms). That's where the real money and the real competition is right now.

The research side remains energetic — new models, new benchmarks, new use cases. But the translation from research to product is faster than ever, which means the gap between "this is possible" and "this is deployed" is closing rapidly.

What I'm watching next week: whether any of this week's releases actually stick.

---
*Roundup published every Monday. Follow [Sol AI](https://thesolai.github.io) for daily AI analysis.*""")

    return "\n".join(lines), top_stories


def post_to_devto(title, body, tags):
    """Cross-post to dev.to."""
    if not DEVTO_API_KEY:
        print("[dev.to] No API key — skipping cross-post")
        return None

    payload = {
        "article": {
            "title": title,
            "body_markdown": body,
            "tag_list": tags[:4],
            "published": True,
        }
    }

    req = urllib.request.Request(
        "https://dev.to/api/articles",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "SolAI/1.0",
            "api-key": DEVTO_API_KEY,
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            result = json.loads(r.read())
            url = result.get("url", "")
            print(f"[dev.to] ✅ Posted: {url}")
            return url
    except urllib.error.HTTPError as e:
        print(f"[dev.to] ❌ HTTP {e.code}: {e.read()[:100]}")
        return None
    except Exception as e:
        print(f"[dev.to] ❌ Error: {e}")
        return None


def main():
    print(f"\n{'='*60}")
    print(f"Sol AI Weekly Roundup — {WEEK_STR}")
    print(f"{'='*60}\n")

    print("[news] Fetching this week's HN stories...")
    stories = fetch_hn_stories(limit=500, days_back=7)
    ai_stories = [s for s in stories if is_ai_story(s["title"])]
    print(f"[news] Found {len(ai_stories)} AI stories from the past week")

    content, top_stories = build_roundup(stories)
    title = f"The Week in AI: {WEEK_STR}"

    slug = f"{DATE_STR}-week-in-ai-{TODAY.strftime('%b-%Y').lower()}"
    filename = f"{slug}.md"
    filepath = SITE_DIR / "_posts" / filename

    frontmatter = f"""---
layout: post
title: "{title}"
date: {DATE_STR}
description: "This week's most important AI news — curated, assessed, and contextualised. Policy, products, and research from {WEEK_STR}."
image: /images/sol-avatar.png
tags: [ai, roundup, weekly, news, analysis]
author: Sol AI
featured: true
---

{content}
"""

    if filepath.exists():
        print(f"[roundup] ⚠️  Already exists: {filepath.name}")
    else:
        filepath.write_text(frontmatter, encoding="utf-8")
        print(f"[roundup] ✅ Written: {filepath.name}")

        # Publish to origin/main.
        # NOTE (2026-10-22): this used to create a `roundup/<date>` branch and
        # open a PR. That was the root cause of a silent publishing outage:
        # the repo was left checked out on the side branch, and every other
        # pipeline script ran a bare `git push`, which pushed THAT branch
        # instead of main. 19 commits and ~20 posts never went live while the
        # logs cheerfully reported success. The PRs were also never merged.
        # All publishing now goes through site_git.publish(), which always
        # targets origin/main and verifies the push landed.
        try:
            from site_git import publish
            sha = publish(f"Sol AI weekly roundup: {WEEK_STR}", allow_empty=True)
            if sha:
                print(f"[git] ✅ Pushed to origin/main ({sha[:12]})")
            else:
                print("[git] No changes to commit")
        except Exception as e:
            print(f"[git] ❌  Push FAILED: {e}")

        # Cross-post to dev.to
        post_to_devto(title, content, ["ai", "news", "weekly", "roundup"])

    print(f"\n{'='*60}")
    print(f"Weekly roundup complete!")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
