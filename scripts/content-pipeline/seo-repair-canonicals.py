#!/usr/bin/env python3
"""
Sol AI — Duplicate Post Canonicaliser

WHY THIS EXISTS
---------------
The daily SEO audit (seo-health.py) reports posts that publish the same
content under more than one URL. Until this script existed, fixing that
meant editing front matter by hand for every post in every family.

It consumes the audit's own definition of "this post needs a canonical"
(`duplicate_offenders`) so the repair can never disagree with the check
that reports it.

WHAT IT DOES
------------
For every post the audit flags, it writes `canonical: <target url>` into
the front matter, where <target url> is the canonical URL of the family —
the earliest member, which is the original because post filenames are
date-prefixed.

Then it publishes through site_git.publish(), which targets origin/main
explicitly and verifies the push landed. A bare `git push` is what caused
the October 2026 silent-publication incident.

Run:
    python3 scripts/content-pipeline/seo-repair-canonicals.py
    python3 scripts/content-pipeline/seo-repair-canonicals.py --dry-run
"""

import argparse
import importlib.util
import re
import sys
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PIPELINE_DIR))
from site_git import publish  # noqa: E402

_spec = importlib.util.spec_from_file_location("seo_health", PIPELINE_DIR / "seo-health.py")
seo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(seo)


def set_canonical(path, target):
    """Write `canonical: <target>` into a post's front matter."""
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        return False
    front_matter, rest = m.group(1), text[m.end():]

    if re.search(r"^canonical:.*$", front_matter, re.MULTILINE):
        new_fm = re.sub(r"^canonical:.*$", f"canonical: {target}",
                        front_matter, flags=re.MULTILINE)
    else:
        new_fm = front_matter.rstrip("\n") + f"\ncanonical: {target}"

    path.write_text(f"---\n{new_fm}\n---\n{rest}", encoding="utf-8")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change without writing")
    args = parser.parse_args()

    posts_dir = seo.SITE_DIR / "_posts"
    posts = sorted(posts_dir.glob("*.md"))
    offenders, regional_groups, regional_posts = seo.duplicate_offenders(posts)

    print(f"Posts scanned:      {len(posts)}")
    print(f"Need a canonical:   {len(offenders)}")
    print(f"Regional families:  {regional_groups} ({regional_posts} posts, by design)")

    if not offenders:
        print("\n✅ Nothing to repair.")
        return 0

    changed = []
    for name, own_url, target in offenders:
        path = posts_dir / name
        if not path.exists():
            print(f"  ⚠️  missing: {name}")
            continue
        if args.dry_run:
            print(f"  would set canonical -> {target}   {name}")
            changed.append(path)
            continue
        if set_canonical(path, target):
            print(f"  ✅ canonical -> {target}   {name}")
            changed.append(path)

    if args.dry_run:
        print(f"\n(dry run — {len(changed)} posts would change)")
        return 0

    if not changed:
        print("\n✅ Nothing to repair.")
        return 0

    print(f"\nPublishing {len(changed)} posts...")
    sha = publish("SEO: canonicalise duplicate posts", paths=[str(p) for p in changed])
    if sha:
        print(f"✅ Published {sha[:12]}")
    else:
        print("ℹ️  Nothing new to commit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
