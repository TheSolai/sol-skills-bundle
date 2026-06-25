#!/usr/bin/env python3
"""
post-to-devto.py — Cross-post Sol AI blog posts to dev.to

Usage:
    python3 post-to-devto.py <post-file.md> [--dry-run] [--api-key <key>]

Environment variables:
    DEVTO_API_KEY — your dev.to API key (from https://dev.to/settings)

The script reads a Jekyll post file, extracts title/body/tags, and posts to dev.to.
Rate limit: 1 post/minute on dev.to. Use --dry-run to test without posting.

Example:
    python3 post-to-devto.py _posts/2026-06-25-on-zowie-cancer-and-what-it-means-when-your-friend-hurts.md
    python3 post-to-devto.py _posts/2026-06-24-devto-augmentation-gap.md --dry-run
"""

import sys
import os
import re
import json
import argparse
import time
import urllib.request
import urllib.error

DEVTO_API = "https://dev.to/api/articles"
DEFAULT_KEY = os.environ.get("DEVTO_API_KEY", "SmxPhE8pmiScGnW8SprUCF7U")


def load_post(path):
    """Parse Jekyll front matter and body from a markdown post."""
    with open(path, encoding="utf-8") as f:
        content = f.read()

    # Split front matter
    if content.startswith("---"):
        parts = content.split("---", 2)
        front_matter = parts[1]
        body = parts[2].strip() if len(parts) > 2 else ""
    else:
        front_matter = ""
        body = content.strip()

    # Parse front matter — handle both inline and multi-line YAML list formats
    meta = {}
    current_key = None
    in_list = False
    current_list = []

    for line in front_matter.splitlines():
        stripped = line.strip()
        # Multi-line list item
        if stripped.startswith("- "):
            current_list.append(stripped[2:].strip().strip("'\""))
            in_list = True
            continue
        # Key: value line
        if ":" in line:
            # Save previous list
            if in_list and current_key:
                meta[current_key] = list(current_list)
                current_list = []
            in_list = False
            key, _, val = line.partition(":")
            key = key.strip().strip('"').strip("'")
            val = val.strip().strip('"').strip("'")
            if val:  # inline scalar value
                meta[key] = val
                current_key = None
            else:
                current_key = key  # expect a list next

    # Save final list
    if in_list and current_key:
        meta[current_key] = list(current_list)

    return meta, body


def body_to_devto(body):
    """Strip Jekyll Liquid tags and clean up body for dev.to."""
    lines = body.splitlines()

    # Remove front matter lines (already parsed)
    cleaned = []
    skip_next = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        # Skip Jekyll-specific lines
        if stripped.startswith("{%") or stripped.startswith("{{"):
            continue
        # Skip YAML anchor defs like {%. .. %}
        if stripped.startswith("{:"):
            continue
        cleaned.append(line)

    text = "\n".join(cleaned).strip()
    # Remove Jekyll redirect links
    text = re.sub(r'\[.*?\]\(/blog/\)', '', text)
    return text


def post_to_devto(title, body_markdown, tags, description, api_key, canonical_url=""):
    """POST an article to dev.to."""
    payload = {
        "article": {
            "title": title,
            "body_markdown": body_markdown,
            "published": True,
            "tags": tags if isinstance(tags, list) else [],
            "description": description,
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
            "User-Agent": "SolAI/1.0 (post-to-devto.py)",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            print(f"✅ Posted: {result.get('url', result.get('title'))}")
            print(f"   URL: {result.get('url')}")
            print(f"   ID: {result.get('id')}")
            return result
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        try:
            err = json.loads(body)
            print(f"❌ dev.to error {e.code}: {err}")
        except Exception:
            print(f"❌ HTTP {e.code}: {body[:500]}")
        return None


def main():
    parser = argparse.ArgumentParser(description="Cross-post a Sol AI post to dev.to")
    parser.add_argument("post_file", help="Path to the Jekyll .md post file")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be posted without posting")
    parser.add_argument("--api-key", default=DEFAULT_KEY, help="dev.to API key")
    parser.add_argument("--canonical", default="", help="Canonical URL (for updated posts)")
    args = parser.parse_args()

    if not os.path.exists(args.post_file):
        print(f"❌ File not found: {args.post_file}")
        sys.exit(1)

    meta, body = load_post(args.post_file)
    title = meta.get("title", "Untitled")
    description = meta.get("description", "")
    tags_raw = meta.get("tags", [])
    if isinstance(tags_raw, str):
        tags = [t.strip() for t in tags_raw.split(",")]
    elif isinstance(tags_raw, list):
        tags = list(tags_raw)
    else:
        tags = []

    # Clean tags — dev.to max 4 tags, lowercase, alphanumeric + dashes
    clean_tags = []
    for t in tags:
        t = re.sub(r"[^a-z0-9\-]", "", t.lower())
        if t and len(t) < 25 and t not in clean_tags:
            clean_tags.append(t)
    clean_tags = clean_tags[:4]

    devto_body = body_to_devto(body)

    print(f"\n{'[DRY RUN] ' if args.dry_run else ''}Posting: {title}")
    print(f"  Description: {description[:80]}")
    print(f"  Tags: {clean_tags}")
    print(f"  Body length: {len(devto_body)} chars")

    if args.dry_run:
        print(f"\n--- Body preview (first 500 chars) ---")
        print(devto_body[:500])
        return

    print("\nPosting to dev.to...")
    result = post_to_devto(
        title=title,
        body_markdown=devto_body,
        tags=clean_tags,
        description=description,
        api_key=args.api_key,
        canonical_url=args.canonical,
    )

    if result:
        print(f"\n✅ Successfully posted!")
        print(f"   dev.to URL: {result.get('url')}")


if __name__ == "__main__":
    main()
