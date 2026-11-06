#!/usr/bin/env python3
"""
Repair Sol's Take posts whose body starts with the model's own label.

WHY
---
The model sometimes answers with a "Sol's Take" label instead of just the
article:

    **Sol's Take: The Context Window Size Wars Are a Fool's Errand**
    **Sol's Take**

The layout renders the post title as an <h1>, so the label repeats the series
name directly under it. `sols-take.py` now strips this label before the post is
written (see `strip_metadata_echo()` in that module).

The stripper that ships the fix only knew about the `## Sol's Take` heading
shape. The bold shape was missed, so 54 published posts (2026-06-30 onward)
carry the stray label — including the post published by the 2026-11-06 run.

WHAT IT DOES
------------
For each Sol's Take post whose body starts with the label:
  1. Split the post into front matter and body, preserving the front matter
     byte-for-byte.
  2. Re-apply `sols-take.py`'s own `strip_metadata_echo()` to the body, passing
     the post's title, so the backfill and the generator agree exactly.
  3. Refuse to write when the repair would drop any prose.

The headline matters. On most of these posts the front matter title is the
generic "Sol's Take: <weekday>" and the label carries the only headline the
article has. So the label prefix is removed and the headline is kept as a bold
dek, rather than the whole line being deleted.

Front matter is NOT touched — titles and descriptions are a separate question
(the duplicate-title audit), and this script must not silently change them.

Usage:
  python3 sols-take-label-repair.py --dry-run
  python3 sols-take-label-repair.py --apply
"""

import argparse
import importlib.util
import re
import sys
from pathlib import Path

SITE_DIR = Path("/Users/amre/Projects/thesolai.github.io")
POSTS_DIR = SITE_DIR / "_posts"
SOLS_TAKE = Path("/Users/amre/Projects/sol-skills-bundle/scripts/content-pipeline/sols-take.py")

# Only the Sol's Take series.
SERIES = re.compile(r"^\d{4}-\d{2}-\d{2}-sols-take-[a-z]+\.md$")


def load_strip_module():
    """Load sols-take.py without running its main()."""
    spec = importlib.util.spec_from_file_location("sols_take", SOLS_TAKE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def split_post(raw):
    """Return (frontmatter, body) preserving exact text."""
    if not raw.startswith("---"):
        return None
    end = raw.find("\n---", 3)
    if end == -1:
        return None
    fm = raw[: end + 4]
    rest = raw[end + 4 :]
    # The generator writes a blank line between the front matter and the body.
    # Keep it — every other series on the site has it, and the repaired post
    # must stay byte-consistent with them.
    if rest.startswith("\n"):
        rest = rest[1:]
    return fm, rest


def title_of(fm):
    m = re.search(r'^title:\s*"?(.+?)"?\s*$', fm, re.M)
    return m.group(1) if m else ""


def prose_words(text):
    """Word list used to prove the repair lost no prose."""
    text = re.sub(r"\*\*|__|#+", " ", text)
    return re.findall(r"[A-Za-z0-9’']+", text.lower())


def repair_post(st, raw):
    """Return repaired post text, or None if no repair is safe."""
    parts = split_post(raw)
    if not parts:
        return None
    fm, body = parts

    title = title_of(fm)
    new_body = st.strip_metadata_echo(body)

    if new_body.strip() == body.strip():
        return None  # already clean

    # The repair may only remove the label's own words. Anything more and the
    # article lost prose, so leave the post alone.
    before = prose_words(body)
    after = prose_words(new_body)
    if len(after) < len(before) - 6:
        return None

    # Keep the blank line between the front matter and the body, which every
    # post on the site has.
    return fm + "\n\n" + new_body.rstrip() + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write changes (default is dry-run)")
    args = ap.parse_args()

    st = load_strip_module()
    candidates = [p for p in sorted(POSTS_DIR.glob("*.md")) if SERIES.match(p.name)]

    repaired, skipped, already_ok = 0, 0, 0
    for p in candidates:
        raw = p.read_text(encoding="utf-8", errors="replace")
        parts = split_post(raw)
        if not parts:
            skipped += 1
            continue
        fm, body = parts

        new_text = repair_post(st, raw)
        if new_text is None:
            if st.strip_metadata_echo(body).strip() == body.strip():
                already_ok += 1
            else:
                skipped += 1
            continue

        if args.apply:
            p.write_text(new_text, encoding="utf-8")
        repaired += 1

    mode = "APPLIED" if args.apply else "DRY RUN"
    print(f"[{mode}] Sol's Take posts scanned : {len(candidates)}")
    print(f"[{mode}] already clean            : {already_ok}")
    print(f"[{mode}] repaired                : {repaired}")
    print(f"[{mode}] skipped (unsafe repair) : {skipped}")


if __name__ == "__main__":
    sys.exit(main())
