#!/usr/bin/env python3
"""
devto-poster.py — Auto-post new Sol AI blog posts to dev.to

Run after the daily content pipeline (run-daily.py).
Posts any Jekyll blog posts that haven't been cross-posted yet.
Tracks posted URLs in _data/devto-posted.json to avoid duplicates.

Usage:
    python3 devto-poster.py [--dry-run]

Crontab entry (after daily-content runs, ~7:30am UK):
    30 7 * * * cd /Users/amre/Projects/sol-skills-bundle && python3 scripts/content-pipeline/devto-poster.py >> logs/devto-poster.log 2>&1
"""

import sys
import os
import json
import re
import time
import datetime
import urllib.request
import urllib.error
from pathlib import Path

SITE_DIR = Path("/Users/amre/Projects/thesolai.github.io")
BUNDLE_DIR = Path("/Users/amre/Projects/sol-skills-bundle")
LOG_FILE = BUNDLE_DIR / "scripts/content-pipeline/logs/devto-post.log"
TRACKER_FILE = BUNDLE_DIR / "scripts/content-pipeline/logs/devto-posted.json"
DEVTO_API = "https://dev.to/api/articles"
DEVTO_API_KEY = os.environ.get("DEVTO_API_KEY", "SmxPhE8pmiScGnW8SprUCF7U")


def load_tracker():
    if TRACKER_FILE.exists():
        with open(TRACKER_FILE) as f:
            return json.load(f)
    return {}


def save_tracker(tracker):
    TRACKER_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(TRACKER_FILE, "w") as f:
        json.dump(tracker, f, indent=2)


def load_post(path):
    with open(path, encoding="utf-8") as f:
        content = f.read()

    if content.startswith("---"):
        parts = content.split("---", 2)
        front_matter = parts[1]
        body = parts[2].strip() if len(parts) > 2 else ""
    else:
        front_matter = ""
        body = content.strip()

    # Parse YAML-like front matter
    meta = {}
    current_key = None
    in_list = False
    current_list = []

    for line in front_matter.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            current_list.append(stripped[2:].strip().strip("'\""))
            in_list = True
            continue
        if ": " in line or ":" in line:
            if in_list and current_key:
                meta[current_key] = list(current_list)
                current_list = []
            in_list = False
            if ": " in line:
                key, _, val = line.partition(": ")
            else:
                key, _, val = line.partition(":")
            key = key.strip().strip('"').strip("'")
            val = val.strip().strip('"').strip("'")
            if val:
                meta[key] = val
                current_key = None
            else:
                current_key = key

    if in_list and current_key:
        meta[current_key] = list(current_list)

    return meta, body


def body_to_devto(body):
    """Clean Jekyll body for dev.to posting."""
    lines = body.splitlines()
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("{%") or stripped.startswith("{{"):
            continue
        if stripped.startswith("{:"):
            continue
        cleaned.append(line)
    text = "\n".join(cleaned).strip()
    text = re.sub(r'\[.*?\]\(/blog/\)', '', text)
    return text


def jekyll_url(post_path):
    """Derive the Jekyll permalink from a post filename."""
    name = post_path.stem  # e.g. "2026-06-25-my-post-title"
    # Format: /blog/YYYY/MM/DD/title/
    date_part = name[:10]  # "2026-06-25"
    title_part = name[11:]  # "my-post-title"
    return f"https://thesolai.github.io/blog/{date_part.replace('-', '/')}/{title_part}/"


def devto_tags(tags_raw):
    """Convert Jekyll tags to dev.to format."""
    if isinstance(tags_raw, str):
        tags = [t.strip() for t in tags_raw.split(",")]
    elif isinstance(tags_raw, list):
        tags = list(tags_raw)
    else:
        tags = []
    clean = []
    for t in tags:
        t = re.sub(r"[^a-z0-9\-]", "", t.lower())
        if t and len(t) < 25 and t not in clean:
            clean.append(t)
    return clean[:4]


def post_to_devto(title, body, tags, description, api_key, canonical_url=""):
    payload = {
        "article": {
            "title": title,
            "body_markdown": body,
            "published": True,
            "tags": tags,
            "description": description[:500] if description else title[:200],
        }
    }
    if canonical_url:
        payload["article"]["canonical_url"] = canonical_url

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        DEVTO_API,
        data=data,
        headers={
            "Content-Type": "application/json",
            "api-key": api_key,
            "User-Agent": "SolAI/1.0 (devto-poster.py)",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result
    except urllib.error.HTTPError as e:
        body_err = e.read().decode("utf-8")
        try:
            err = json.loads(body_err)
            print(f"  ❌ dev.to error {e.code}: {err}")
        except Exception:
            print(f"  ❌ HTTP {e.code}: {body_err[:300]}")
        return None


def get_recent_posts(hours=20):
    """Get Jekyll posts modified in the last N hours."""
    cutoff = datetime.datetime.now() - datetime.timedelta(hours=hours)
    posts_dir = SITE_DIR / "_posts"
    if not posts_dir.exists():
        return []

    recent = []
    for f in posts_dir.iterdir():
        if not f.suffix == ".md":
            continue
        mtime = datetime.datetime.fromtimestamp(f.stat().st_mtime)
        if mtime > cutoff:
            recent.append(f)
    return sorted(recent, key=lambda p: p.stat().st_mtime, reverse=True)


def main():
    dry_run = "--dry-run" in sys.argv
    tracker = load_tracker()

    print(f"\n[{datetime.datetime.now():%Y-%m-%d %H:%M}] dev.to auto-poster running")
    print(f"  Tracker has {len(tracker)} posts already posted")

    posts = get_recent_posts(hours=22)
    if not posts:
        print("  No recent posts found. Done.")
        return

    print(f"  Found {len(posts)} recently-modified posts")

    posted_count = 0
    for post_path in posts:
        slug = post_path.stem

        # Skip drafts
        if slug.startswith("draft-") or slug.startswith("IDEA-"):
            continue

        # Check if already posted
        if slug in tracker:
            print(f"  ⏭ Already posted: {slug}")
            continue

        meta, body = load_post(post_path)
        title = meta.get("title", "")
        description = meta.get("description", "")
        tags_raw = meta.get("tags", [])
        tags = devto_tags(tags_raw)
        canonical = jekyll_url(post_path)
        devto_body = body_to_devto(body)

        if not title or len(devto_body) < 200:
            print(f"  ⏭ Skipping (too short or no title): {slug}")
            continue

        print(f"\n  📝 Posting: {title}")
        print(f"     Tags: {tags}")
        print(f"     URL:  {canonical}")

        if dry_run:
            print("     [DRY RUN — not posting]")
            continue

        # Respect rate limit: 1 post/minute
        print("  Posting (rate limit: 1/min)...")
        time.sleep(62)

        result = post_to_devto(
            title=title,
            body=devto_body,
            tags=tags,
            description=description,
            api_key=DEVTO_API_KEY,
            canonical_url=canonical,
        )

        if result and result.get("url"):
            tracker[slug] = {
                "url": result["url"],
                "id": result.get("id"),
                "posted_at": datetime.datetime.now().isoformat(),
                "canonical": canonical,
            }
            save_tracker(tracker)
            print(f"  ✅ Posted: {result['url']}")
            posted_count += 1
        else:
            # If it failed due to rate limit, stop and wait
            print("  ⚠️  Posting failed — stopping to respect rate limit")
            break

    print(f"\n[{datetime.datetime.now():%Y-%m-%d %H:%M}] Done. Posted {posted_count} new posts.")


if __name__ == "__main__":
    main()
