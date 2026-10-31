#!/usr/bin/env python3
"""
Regenerate a Weekend Deep Dive for a given date and publish it to main.

Originally a one-off for the orphaned 2026-10-17 post. Now a general
repair/regeneration tool, used when a published deep dive needs to be
rewritten — most recently the 2026-10-31 post, which shipped a fabricated
Hacker News quote ("OpenAI's models are like Ferraris, while open-source
models are more like reliable Hondas"). Regenerating it with the hardened
research path produces a draft whose every quotation traces back to a
comment we actually fetched.

Usage:
    python3 backfill-deep-dive.py 2026-10-31 [topic-index]
    python3 backfill-deep-dive.py 2026-10-31 "The closed-source vs. open-source AI divide"

If no topic is given, the topic is matched against the existing post's
title in `_posts/`.
"""

import sys, subprocess, importlib.util
from pathlib import Path

PIPELINE_DIR = Path("/Users/amre/Projects/sol-skills-bundle/scripts/content-pipeline")
sys.path.insert(0, str(PIPELINE_DIR))

spec = importlib.util.spec_from_file_location("wdd", PIPELINE_DIR / "weekend-deep-dive.py")
wdd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wdd)

SITE_DIR = wdd.SITE_DIR


def existing_title(date_str: str) -> str | None:
    """Read the title of the post already on disk for this date."""
    path = SITE_DIR / "_posts" / f"{date_str}-weekend-deep-dive.md"
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8").split("\n"):
        if line.startswith("title:"):
            return line.split("title:", 1)[1].strip().strip('"').strip("'")
    return None


def resolve_topic(date_str: str, arg: str | None):
    if arg is not None and arg.isdigit():
        return wdd.TOPICS[int(arg)]
    if arg:
        i = wdd._match_topic_index(arg)
        if i is not None:
            return wdd.TOPICS[i]
        print(f"[backfill] no topic matches: {arg}")
        sys.exit(1)
    title = existing_title(date_str)
    if not title:
        print(f"[backfill] no existing post for {date_str} — pass a topic index")
        sys.exit(1)
    # The stored title carries "(October 31, 2026)" — the matcher tolerates it.
    i = wdd._match_topic_index(title)
    if i is None:
        print(f"[backfill] no topic matches existing title: {title}")
        sys.exit(1)
    return wdd.TOPICS[i]


def main():
    if len(sys.argv) < 2:
        print("usage: backfill-deep-dive.py YYYY-MM-DD [topic-index|topic]")
        sys.exit(1)

    date_str = sys.argv[1]
    topic = resolve_topic(date_str, sys.argv[2] if len(sys.argv) > 2 else None)

    print(f"[backfill] Regenerating {date_str}: {topic['topic']}")

    hn = wdd.fetch_hn_stories(topic)
    print(f"[backfill] {len(hn)} HN stories")
    wdd.attach_comments(hn)
    dt = wdd.fetch_devto_articles(topic)
    print(f"[backfill] {len(dt)} dev.to articles")

    body = wdd.generate_verified_body(topic, hn, dt)
    if not body:
        print("[backfill] ❌ generation failed or draft failed quote verification; refusing to publish")
        sys.exit(2)

    # Amre is she/her.
    body = body.replace("His experience", "Her experience")
    body = body.replace("his experience", "her experience")
    # Guard against invented biography.
    body = body.replace("Amre, a college sophomore,", "Amre")
    body = body.replace("a college sophomore", "a self-taught developer")

    wc = len(body.split())
    print(f"[backfill] Body length: {wc} words")

    post_path = wdd.create_post(body, topic, date_str=date_str)

    from site_git import publish
    sha = publish(f"Weekend Deep Dive (regenerated): {topic['topic'][:50]}",
                  cwd=SITE_DIR, paths=[str(post_path)])
    print(f"[backfill] Pushed to origin/main ({sha[:12] if sha else 'nothing new'})")


if __name__ == "__main__":
    main()
