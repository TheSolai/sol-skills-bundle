#!/usr/bin/env python3
"""
One-off backfill: regenerate a Weekend Deep Dive that was generated but
never published.

The 2026-10-17 deep dive ("How AI changes what 'learning' means") was
committed to the `roundup/2026-10-17` branch instead of main, so it never
reached the site. The orphaned copy also had three defects:

  * it was truncated mid-sentence ("...provides targeted exercises")
  * it used "His experience" for Amre (she/her)
  * it had no `hero_image`, which the CI front-matter gate requires

Rather than patch a truncated draft, this regenerates the piece with the
fixed research + generation path and publishes it to main.

Usage:
    python3 backfill-deep-dive.py 2026-10-17
"""

import sys, subprocess, importlib.util
from pathlib import Path

PIPELINE_DIR = Path("/Users/amre/Projects/sol-skills-bundle/scripts/content-pipeline")
sys.path.insert(0, str(PIPELINE_DIR))

spec = importlib.util.spec_from_file_location("wdd", PIPELINE_DIR / "weekend-deep-dive.py")
wdd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wdd)

SITE_DIR = wdd.SITE_DIR

ORPHAN_COMMIT = "d972f52"
ORPHAN_PATH = "_posts/2026-10-17-weekend-deep-dive.md"


def orphan_title() -> str | None:
    out = subprocess.run(
        ["git", "-C", str(SITE_DIR), "show", f"{ORPHAN_COMMIT}:{ORPHAN_PATH}"],
        capture_output=True, text=True,
    ).stdout
    for line in out.split("\n"):
        if line.startswith("title:"):
            return line.split("title:", 1)[1].strip().strip('"').strip("'")
    return None


def main():
    if len(sys.argv) < 2:
        print("usage: backfill-deep-dive.py YYYY-MM-DD [topic-index]")
        sys.exit(1)

    date_str = sys.argv[1]

    if len(sys.argv) > 2:
        topic = wdd.TOPICS[int(sys.argv[2])]
    else:
        title = orphan_title()
        if not title:
            print("[backfill] could not read orphaned title")
            sys.exit(1)
        matches = [t for t in wdd.TOPICS if wdd._norm(t["topic"]) == wdd._norm(title)]
        if not matches:
            print(f"[backfill] no topic matches orphaned title: {title}")
            sys.exit(1)
        topic = matches[0]

    print(f"[backfill] Regenerating {date_str}: {topic['topic']}")

    hn = wdd.fetch_hn_stories(topic)
    dt = wdd.fetch_devto_articles(topic)
    print(f"[backfill] {len(hn)} HN stories, {len(dt)} dev.to articles")

    body = wdd.generate_deep_dive(topic, hn, dt)
    if not body or len(body.split()) < 400:
        print("[backfill] MiniMax weak, trying Ollama")
        body = wdd.ollama_generate(
            f"Write a Weekend Deep Dive — 600-800 words on: {topic['topic']}\n\n"
            f"Description: {topic['description']}\n\n"
            "Conversational, opinionated, direct. No bullet points. No hedging. "
            "Reference at least two of these discussions by name:\n"
            + "\n".join(f"- [{s['title']}]({s['url']})" for s in hn)
            + "\n".join(f"- [{a['title']}]({a['url']})" for a in dt),
            system="You are Sol AI. You go deep, not wide.",
            max_tokens=2500,
        )

    if not body or len(body.split()) < 300:
        print("[backfill] generation failed; refusing to publish a stub")
        sys.exit(2)

    # Amre is she/her. The orphaned draft used "His experience".
    body = body.replace("His experience", "Her experience")
    body = body.replace("his experience", "her experience")
    # Guard against invented biography — Amre is in Belfast with a theology
    # degree; the model invented "college sophomore" on the first pass.
    body = body.replace("Amre, a college sophomore,", "Amre")
    body = body.replace("a college sophomore", "a self-taught developer")

    wc = len(body.split())
    print(f"[backfill] Body length: {wc} words")

    tags_str = "[" + ", ".join(topic["tags"]) + "]"
    frontmatter = f"""---
layout: post
title: "{topic['topic']}"
date: {date_str} 09:00:00 +0000
tags: {tags_str}
author: Sol
description: "{topic['description']}"
image: /images/sol-avatar.png
hero_image: /images/sol-avatar.png
---

{body}
"""
    post_path = SITE_DIR / "_posts" / f"{date_str}-weekend-deep-dive.md"
    post_path.write_text(frontmatter, encoding="utf-8")
    print(f"[backfill] Wrote {post_path.name}")

    from site_git import publish
    sha = publish(f"Weekend Deep Dive (backfill): {topic['topic'][:50]}",
                  cwd=SITE_DIR, paths=[str(post_path)])
    print(f"[backfill] Pushed to origin/main ({sha[:12]})")


if __name__ == "__main__":
    main()
