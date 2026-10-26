---
name: sol-skills-update
description: Weekly skills rotation — randomly selects 6 skills from schemas.json to feature on the homepage, updated every Monday.
version: 2.0.0
author: TheSolAI
permissions: ["file.read", "file.write"]
---

# Sol Skills Rotation

Weekly refresh of the homepage skills showcase. Keeps the featured skills fresh.

## What it does

- Reads the skill pool from `skills/schemas.json` in the site repo (JSON-LD `ItemList`)
- Enriches each pick with emoji, description and install command from `_data/skills.json`
- Randomly selects 6 skills, seeded by the calendar date so a given day always yields the same six
- Writes the result to the Jekyll data file `_data/featured-skills.json`
- Auto-commits and pushes that file to `origin/main` via `site_git.publish()`

## Schedule

Runs **Mondays at 8am UK time** via launchd (`ai.sol.skills-update`).

## Why a data file and not direct HTML edits

Version 1.0 spliced six hardcoded HTML cards into `index.html` with a regex. The
2026-09-29 landing page rework removed that markup, so every Monday run after
it failed with "Could not find skills grid in homepage" and changed nothing on
the site. The version 2.0 pipeline keeps the rotation in a data file and lets
Liquid render it, so a homepage redesign can no longer break it.

## Setup

Requires:
- Site repo at `/Users/amre/Projects/thesolai.github.io`
- Skills bundle at `/Users/amre/Projects/sol-skills-bundle/`
- `skills/schemas.json` with skill definitions
- `_data/skills.json` with skill metadata (optional — falls back to schema data)

## Manual run

```bash
cd /Users/amre/Projects/sol-skills-bundle
python3 scripts/content-pipeline/skills-update.py --dry-run   # preview
python3 scripts/content-pipeline/skills-update.py             # write + publish
```

## Source

`scripts/content-pipeline/skills-update.py` in [sol-skills-bundle](https://github.com/TheSolAI/sol-skills-bundle)
