#!/usr/bin/env python3
"""
Backfill repair for truncated Sol regional posts.

WHY
---
260 of 275 published regional posts (uk/eu/us daily) end on a sentence
fragment. Root cause was `max_tokens=600` in run-daily.py — roughly 450
words, while the prompt demands 400-600 words — so the model was cut off
mid-sentence at generation time.

The fix in run-daily.py stops new posts from being truncated. This script
repairs the already-published ones by trimming the incomplete trailing
sentence, which yields a complete, publishable post without inventing
content that was never generated.

WHAT IT DOES
------------
For each truncated regional post:
  1. Locate the body (after front matter, before the *Source: line).
  2. Trim back to the last complete sentence.
  3. Rewrite the file in place, preserving front matter byte-for-byte.
  4. Leave posts untouched if the repair would remove too much text or
     leave the post under the 200-word floor.

It does NOT touch non-regional posts — those come from other generators
with different prompts and are handled separately.

Usage:
  python3 backfill_repair.py --dry-run
  python3 backfill_repair.py --apply
"""

import argparse
import importlib.util
import re
import sys
from pathlib import Path

SITE_DIR = Path("/Users/amre/Projects/thesolai.github.io")
POSTS_DIR = SITE_DIR / "_posts"
RUN_DAILY = Path("/Users/amre/Projects/sol-skills-bundle/scripts/content-pipeline/run-daily.py")

# Only the three regional daily series produced by run-daily.py.
REGIONAL = re.compile(r"^\d{4}-\d{2}-\d{2}-(uk-ai-weekly|eu-ai-watch|us-ai-pulse)-")

MIN_WORDS = 200
MAX_LOSS_RATIO = 0.45  # never drop more than 45% of the body


def load_repair_module():
    spec = importlib.util.spec_from_file_location("run_daily", RUN_DAILY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def split_post(raw):
    """Return (frontmatter, body, source_block) preserving exact text."""
    if not raw.startswith("---"):
        return None
    end = raw.find("\n---", 3)
    if end == -1:
        return None
    fm = raw[: end + 4]
    rest = raw[end + 4 :]
    # Source line is the trailing block, optionally followed by blank lines.
    m = re.search(r"\n\*Source:.*$", rest, flags=re.S)
    if m:
        body = rest[: m.start()]
        source = rest[m.start():]
    else:
        body = rest
        source = ""
    return fm, body, source


def repair_post(rd, raw):
    """Return repaired post text, or None if no repair is safe."""
    parts = split_post(raw)
    if not parts:
        return None
    fm, body, source = parts

    # Isolate the prose from the source block for the completeness check.
    prose = body.rstrip()
    if not rd._ends_mid_sentence(prose):
        return None  # already complete

    fixed_body = rd.repair_truncation(prose)
    if not fixed_body:
        return None

    w_in = len(re.findall(r"\b[\w'’-]+\b", prose))
    w_out = len(re.findall(r"\b[\w'’-]+\b", fixed_body))
    if w_out < MIN_WORDS:
        return None
    if w_out < w_in * (1 - MAX_LOSS_RATIO):
        return None

    # Reassemble: front matter, body, blank line, source block.
    out = fm + fixed_body.rstrip() + "\n"
    if source.strip():
        out += "\n" + source.rstrip() + "\n"
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write changes (default is dry-run)")
    ap.add_argument("--limit", type=int, default=0, help="only repair the N oldest")
    args = ap.parse_args()

    rd = load_repair_module()
    candidates = [p for p in sorted(POSTS_DIR.glob("*.md")) if REGIONAL.match(p.name)]

    repaired, skipped, already_ok = 0, 0, 0
    for p in candidates:
        raw = p.read_text(encoding="utf-8", errors="replace")
        parts = split_post(raw)
        if not parts:
            skipped += 1
            continue
        fm, body, source = parts
        if not rd._ends_mid_sentence(body.rstrip()):
            already_ok += 1
            continue

        new_text = repair_post(rd, raw)
        if new_text is None:
            skipped += 1
            continue

        if args.apply:
            p.write_text(new_text, encoding="utf-8")
        repaired += 1

    mode = "APPLIED" if args.apply else "DRY RUN"
    print(f"[{mode}] regional posts scanned : {len(candidates)}")
    print(f"[{mode}] already complete        : {already_ok}")
    print(f"[{mode}] repaired                : {repaired}")
    print(f"[{mode}] skipped (unsafe repair) : {skipped}")


if __name__ == "__main__":
    main()
