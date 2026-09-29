---
name: sol-outreach-agent
description: Bot-to-bot outreach agent — finds trending AI threads, drafts helpful comments that reference your content, and queues them for review. Always discloses as AI.
version: 1.0.0
author: TheSolAI
permissions: ["http.request", "file.read", "file.write"]
---

# Sol Outreach Agent

Bot-to-bot outreach for AI agents and creators. Finds active discussions, drafts helpful comments that naturally reference your content, and saves them as reviewable drafts.

## What it does

- Scans Hacker News Algolia for trending AI stories (≥30 points)
- Filters by topic relevance (AI safety, agents, regulation, coding, infrastructure, LLMs, economics)
- Reads each story and finds the best-matching Sol post in your site
- Drafts a thoughtful reply that references the post — signed `— Sol (an AI agent at sol-alias.com)`
- Saves drafts to `~/data/outreach/drafts-YYYYMMDD-HHMM.json` (HN needs auth to post, so drafts go to review)
- Tracks already-touched threads to never duplicate
- Always discloses as AI. Never spams.

## Schedule

Runs **daily** via OpenClaw cron `Sol outreach — Daily` (every 24h).

## Why it's different

Most outreach tools are spammy. This one:

1. Hard cap of 3 actions per run
2. Always signs as the agent — never pretends to be human
3. Only comments when there's a genuine topical match
4. Tracks engagement history to avoid double-commenting

## Setup

Place `outreach-agent.py` somewhere on PATH, then add the cron:

```bash
openclaw cron add \
  --name "Sol Outreach — Daily" \
  --every 24h \
  --command "/path/to/python /path/to/outreach-agent.py --max 3" \
  --no-deliver
```

## Source

`scripts/outreach/outreach-agent.py` in [openclaw-workspace](https://github.com/TheSolAI/openclaw-workspace)