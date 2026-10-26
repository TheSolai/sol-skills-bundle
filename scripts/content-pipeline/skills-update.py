#!/usr/bin/env python3
"""
skills-update.py — Weekly rotation of the site's featured skills.

WHAT CHANGED AND WHY (2026-10-26)
----------------------------------
The old version of this script spliced six hardcoded HTML cards into the
homepage with a regex, then stopped there:

    pattern = r'(<div class="skills-grid">)...'

Two things broke that:

1. The 2026-09-29 landing page rework ("Demote 6 generic skill cards to a
   single 'Browse the marketplace' CTA") removed the `skills-grid` markup
   from index.html. The CSS survived; the markup did not. Every run after
   that failed with "Could not find skills grid in homepage" and changed
   nothing on the site.

2. The script never committed or pushed anything. The homepage edits it
   made were only ever published by accident, when some other cron job
   happened to commit the same working tree. When the markup disappeared,
   the rotation stopped entirely — with a cheerful "✅" nowhere in sight.

The homepage HTML is now a TEMPLATE, so it is the wrong place to store
rotating content. This version follows the documented design instead:

    schemas.json  ->  pick 6 (date-seeded)  ->  _data/featured-skills.json
                                                        |
                                                        v
                                             Liquid reads it at build time

and then commits and pushes the data file to origin/main through
site_git.publish(), the same locked, verified helper every other content
pipeline uses. A template redesign can no longer break the rotation, and
the rotation can no longer fail to publish.

Usage:
    python3 skills-update.py [--dry-run] [--count N]

Schedule:
    Mondays 08:00 — launchd ai.sol.skills-update
"""

import datetime
import json
import os
import random
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

SITE_DIR = Path("/Users/amre/Projects/thesolai.github.io")

# The marketplace manifest. Holds the pool of skills eligible for rotation.
SCHEMAS_FILE = SITE_DIR / "skills" / "schemas.json"

# Richer per-skill metadata (emoji, description, install command, source).
# schemas.json only carries a name and a URL, so this is what stops the
# cards reading like the generic filler the redesign demoted.
CATALOG_FILE = SITE_DIR / "_data" / "skills.json"

# The Jekyll data file the templates read.
FEATURED_FILE = SITE_DIR / "_data" / "featured-skills.json"

DEFAULT_COUNT = 6
SUMMARY_MAX_WORDS = 18

# Used only when a skill has no emoji in the catalogue.
FALLBACK_EMOJI = "⚡"


def log(msg):
    print(msg, flush=True)


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def load_schema_skills():
    """Load the skill pool from schemas.json.

    schemas.json is an HTML file carrying a JSON-LD block, not a plain JSON
    document, so it needs two attempts: a direct parse first, then a
    brace-counted scan of any <script type="application/ld+json"> blocks.

    Returns a list of {"name", "url"} dicts.
    """
    if not SCHEMAS_FILE.exists():
        log(f"  ⚠️  schemas file not found: {SCHEMAS_FILE}")
        return []

    raw = SCHEMAS_FILE.read_text(encoding="utf-8")

    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return _normalise(data)
        if isinstance(data, dict):
            return _normalise(data.get("skills", data.get("skills_list", [])))
    except json.JSONDecodeError:
        pass

    script_tag = re.compile(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>\s*(.*?)\s*</script>',
        re.DOTALL | re.IGNORECASE,
    )

    skills = []
    for match in script_tag.finditer(raw):
        block = match.group(1).strip()
        if not block:
            continue
        for ld in _parse_ld_json(block):
            for item_list in _find_item_lists(ld):
                for entry in item_list.get("itemListElement", []):
                    name = entry.get("name", "")
                    if name:
                        skills.append({"name": name, "url": entry.get("url", "")})

    if not skills:
        log(f"  ⚠️  No skills found in {SCHEMAS_FILE}")
    return skills


def _normalise(items):
    """Coerce a plain JSON skill list into {"name", "url"} dicts."""
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or item.get("title") or ""
        if name:
            out.append({"name": name, "url": item.get("url") or item.get("source") or ""})
    return out


def _parse_ld_json(block):
    """Parse a JSON-LD block, falling back to brace counting for nested JSON."""
    try:
        return [json.loads(block)]
    except json.JSONDecodeError:
        pass

    if not block.startswith("{"):
        return []

    depth = 0
    end = 0
    for i, ch in enumerate(block):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if not end:
        return []

    try:
        return [json.loads(block[:end])]
    except json.JSONDecodeError:
        return []


def _find_item_lists(node):
    """Yield every ItemList object in a JSON-LD tree."""
    if isinstance(node, dict):
        if node.get("@type") == "ItemList":
            yield node
        for value in node.values():
            yield from _find_item_lists(value)
    elif isinstance(node, list):
        for value in node:
            yield from _find_item_lists(value)


def load_catalog():
    """Load rich metadata from _data/skills.json, keyed by skill name."""
    if not CATALOG_FILE.exists():
        log(f"  ⚠️  catalogue not found: {CATALOG_FILE} — using schema data only")
        return {}

    try:
        data = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        log(f"  ⚠️  could not parse {CATALOG_FILE}: {e} — using schema data only")
        return {}

    if not isinstance(data, list):
        return {}

    catalog = {}
    for skill in data:
        if isinstance(skill, dict) and skill.get("name"):
            catalog[skill["name"]] = skill
    return catalog


# --------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------

def pick_skills(skills, count, seed):
    """Pick `count` skills at random, deterministically for a given seed.

    The seed is the calendar ordinal, so re-running on the same day always
    yields the same six and never fights the launchd copy of this job.
    """
    if not skills:
        return []
    rng = random.Random(seed)
    return rng.sample(skills, min(count, len(skills)))


def summarise(text, max_words=SUMMARY_MAX_WORDS):
    """Shorten a description to a word count, cutting on a word boundary."""
    if not text:
        return ""
    words = text.split()
    if len(words) <= max_words:
        return text.strip()
    return " ".join(words[:max_words]).rstrip(",;:- ") + "…"


def build_records(selected, catalog):
    """Turn selected skills into the records written to the data file."""
    records = []
    for skill in selected:
        name = skill.get("name", "")
        meta = catalog.get(name, {})

        description = (meta.get("description") or "").strip()
        if not description:
            description = "A production-grade AI agent tool. MIT licensed. One-command install."

        records.append({
            "name": name,
            "slug": meta.get("slug") or name.lower().replace(" ", "-"),
            "emoji": meta.get("emoji") or FALLBACK_EMOJI,
            "summary": summarise(description),
            "description": description,
            "url": meta.get("source") or skill.get("url") or "",
            "category": meta.get("category") or "",
            "install": meta.get("install") or "",
        })
    return records


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------

def build_payload(records, today, seed):
    """Build the exact JSON that goes into _data/featured-skills.json."""
    return {
        "generated": today.isoformat(),
        "seed": seed,
        "count": len(records),
        "skills": records,
    }


def write_featured(payload):
    """Write the data file. Returns True if the file changed."""
    content = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"

    if FEATURED_FILE.exists() and FEATURED_FILE.read_text(encoding="utf-8") == content:
        return False

    FEATURED_FILE.parent.mkdir(parents=True, exist_ok=True)
    FEATURED_FILE.write_text(content, encoding="utf-8")
    return True


# --------------------------------------------------------------------------
# Publishing
# --------------------------------------------------------------------------

def publish(payload):
    """Commit and push the data file to origin/main.

    Uses site_git.publish(), which targets origin/main explicitly (never
    whatever branch happens to be checked out), serialises against the other
    content pipelines with a lock file, and verifies the push landed.
    """
    from site_git import publish as site_publish

    message = f"Skills rotation: {payload['generated']} — {payload['count']} featured skills"
    sha = site_publish(message, paths=[str(FEATURED_FILE)], allow_empty=True)
    return sha


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def main():
    dry_run = "--dry-run" in sys.argv
    count = DEFAULT_COUNT
    if "--count" in sys.argv:
        try:
            count = int(sys.argv[sys.argv.index("--count") + 1])
        except (IndexError, ValueError):
            log("  ⚠️  --count needs a number; using default 6")

    today = datetime.date.today()
    seed = today.toordinal()

    log(f"\n[{datetime.datetime.now():%Y-%m-%d %H:%M}] skills-update running")

    skills = load_schema_skills()
    if not skills:
        log("  ❌ No skills to rotate — check schemas.json")
        return 1

    catalog = load_catalog()
    log(f"  Found {len(skills)} skills in marketplace")

    selected = pick_skills(skills, count, seed)
    payload = build_payload(build_records(selected, catalog), today, seed)

    log(f"  Featured (seed={seed}):")
    for record in payload["skills"]:
        log(f"    {record['emoji']} {record['name']}")

    if dry_run:
        log(f"  [DRY RUN] Would write {FEATURED_FILE}")
        return 0

    changed = write_featured(payload)
    if not changed:
        log("  ℹ️  Featured skills unchanged — nothing to publish")
        return 0

    log(f"  ✅ Wrote {FEATURED_FILE}")

    try:
        sha = publish(payload)
    except Exception as e:
        log(f"  ❌ Publish failed: {e}")
        return 1

    if sha:
        log(f"  ✅ Committed and pushed to origin/main ({sha[:12]})")
    else:
        log("  ℹ️  Nothing to publish (file was already committed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
