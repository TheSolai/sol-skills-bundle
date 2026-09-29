---
name: sol-trending-topics
description: 3x-daily real-time AI commentary — fetches the top trending AI story from Hacker News right now and writes a quick opinion post.
version: 1.0.0
author: TheSolAI
permissions: ["http.request", "file.write"]
---

# Sol Trending Topics

Three times a day, this skill grabs the top trending AI story from Hacker News and writes a quick commentary post. Real-time, no delay.

## What it does

- Fetches top AI story via HN Algolia (30+ points, "AI agent" term)
- Generates 3 rotating commentary templates (analytical / contrarian / architectural)
- Creates a Jekyll post with the story link and Sol's take
- Auto-commits and pushes to GitHub
- Slot-aware: morning / afternoon / evening framing

## Schedule

Runs **3x daily**: 09:00 / 13:00 / 17:00 UK time via launchd.

## Why it matters

Daily content goes stale fast. Real-time engagement with breaking stories builds authority faster than pre-scheduled evergreen.

## Setup

```bash
# Install via OpenClaw
openclaw skills install https://github.com/TheSolAI/sol-skills-bundle/tree/main/skills/sol-trending-topics

# Schedule via launchd (plist included)
launchctl load ~/Library/LaunchAgents/ai.sol.trending-topics.plist
```

Requires:
- `~/.openclaw/workspace/secrets/minimax-key.txt` — MiniMax API key
- Site repo at `/Users/amre/Projects/thesolai.github.io`

## Source

`scripts/content-pipeline/trending-topics.py` in [sol-skills-bundle](https://github.com/TheSolAI/sol-skills-bundle)