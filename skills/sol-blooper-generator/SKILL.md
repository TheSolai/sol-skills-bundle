---
name: sol-blooper-generator
description: Twice-weekly AI bloopers roundup — the most absurd, alarming, and accidentally hilarious AI failures. Runs Wed + Sat at 11am UK time.
version: 1.0.0
author: TheSolAI
permissions: ["http.request", "file.write"]
---

# Sol Blooper Generator

Twice-weekly roundup of the most absurd AI failures. The recursive apology, the Welsh jailbreak, the 3:47am calendar optimiser, the citation that didn't exist. Real fails, real lessons.

## What it does

- Picks 4 bloopers from a bank of 10 (recursive apology, jailbreak via Welsh, calendar optimiser, citation generator, etc)
- Each blooper has: what happened + the lesson
- Creates a Jekyll post in `_posts/YYYY-MM-DD-ai-bloopers-DAY.md`
- Auto-commits and pushes to GitHub

## Schedule

Runs **Wed + Sat at 11:00 UK time** via launchd.

## Blooper bank

10 bloopers rotate by day-of-year: the recursive apology, the jailbreak via Welsh, the calendar optimiser, the citation generator, the recruiter with no filter, the sudo incident, the dating disaster, the meeting that wasn't, the honest LLM, the hallucinating translator.

## Setup

Requires:
- `~/.openclaw/workspace/secrets/minimax-key.txt` — MiniMax API key (optional — bloopers have full templates)
- Site repo at `/Users/amre/Projects/thesolai.github.io`

## Source

`scripts/content-pipeline/more-bloopers.py` in [sol-skills-bundle](https://github.com/TheSolAI/sol-skills-bundle)