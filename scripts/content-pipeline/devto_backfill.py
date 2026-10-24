#!/usr/bin/env python3
"""
Backfill repaired bodies to dev.to for regional cross-posts.

The 260 regional posts that were repaired on the Jekyll site were also
cross-posted to dev.to, and those dev.to articles still carry the original
truncated body. This script re-publishes the repaired body to each article.

The tracker (`logs/devto-posted.json`) maps the local slug to the dev.to
article id, so each local post is matched to its own dev.to article.

Usage:
  python3 devto_backfill.py --limit=10 --delay=3
  python3 devto_backfill.py --limit=0 --delay=3   # 0 = no limit
"""

import argparse
import importlib.util
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from devto_key import load_devto_key  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "devto_update", Path(__file__).parent / "devto_update.py")
_du = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_du)

SITE_DIR = Path("/Users/amre/Projects/thesolai.github.io")
REGIONAL = re.compile(r"^\d{4}-\d{2}-\d{2}-(uk-ai-weekly|eu-ai-watch|us-ai-pulse)-")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--delay", type=float, default=3.0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    api_key = load_devto_key()
    if not api_key:
        print("❌ No dev.to API key found")
        sys.exit(1)

    tracker = _du.load_tracker()
    load_post = _du.load_post
    body_to_devto = _du.body_to_devto
    devto_footer = _du.devto_footer
    jekyll_url = _du.jekyll_url
    devto_tags = _du.devto_tags

    # Build the work list: local regional posts that have a dev.to article.
    work = []
    for slug, entry in tracker.items():
        if not REGIONAL.match(slug) or not entry.get("id"):
            continue
        local = SITE_DIR / "_posts" / f"{slug}.md"
        if not local.exists():
            continue
        work.append((slug, local, entry))
    work.sort()

    if args.limit:
        work = work[: args.limit]

    print(f"dev.to regional cross-posts to update: {len(work)}")
    if args.dry_run:
        for slug, _, entry in work[:10]:
            print(f"  would update id={entry['id']} {slug}")
        return

    ok = failed = skipped = 0
    for i, (slug, local, entry) in enumerate(work, 1):
        meta, body = load_post(local)
        title = meta.get("title", "")
        description = meta.get("description", "")
        tags = devto_tags(meta.get("tags", []))
        canonical = jekyll_url(local)
        devto_body = body_to_devto(body, canonical_base="https://thesolai.github.io")
        devto_body += devto_footer(canonical)

        status, info = _du.devto_update(
            entry["id"], title, devto_body, tags, description,
            api_key, canonical_url=canonical)

        if status == "updated":
            ok += 1
            print(f"  [{i}/{len(work)}] ✅ {slug[:64]}")
        else:
            failed += 1
            print(f"  [{i}/{len(work)}] ❌ {slug[:58]} -> {str(info)[:90]}")

        if i % 20 == 0:
            print(f"      ...{i}/{len(work)} processed, {ok} ok, {failed} failed")
        time.sleep(args.delay)

    print(f"\nDone: {ok} updated, {failed} failed")


if __name__ == "__main__":
    main()
