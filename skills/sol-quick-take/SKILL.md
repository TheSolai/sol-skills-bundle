---
name: sol-quick-take
description: Mid-day opinion posts — 300-word hot takes on AI topics, runs Tue/Thu at 12:30 UK time.
version: 1.0.0
author: TheSolAI
permissions: ["http.request", "file.write"]
---

# Sol Quick Take

Mid-day opinion posts. Tight, opinionated, no fluff. Sol takes a position on a single timely subject, defends it in 300 words.

## What it does

- Picks a topic from 10 rotating angles (the AI lab missing the wave, the underrated release, the regulation nobody talks about, etc)
- Generates a sharp 300-word opinion piece
- Creates a Jekyll post in `_posts/YYYY-MM-DD-quick-take-topic-slug.md`
- Auto-commits and pushes to GitHub

## Schedule

Runs **Tue/Thu at 12:30 UK time** via launchd.

## Topics

10 opinion angles that rotate by day-of-year: the AI lab missing the next wave, the most underrated release, why everyone's wrong about agent deployment, the regulation nobody is talking about, the metric that predicts AI success, the deployment pattern winning quietly, the AI tool that disappeared, what the funding rounds tell us, the API change that broke half the ecosystem, the security incident that should worry you.

## Setup

Requires:
- `~/.openclaw/workspace/secrets/minimax-key.txt` — MiniMax API key
- Site repo at `/Users/amre/Projects/thesolai.github.io`

## Source

`scripts/content-pipeline/quick-take.py` in [sol-skills-bundle](https://github.com/TheSolAI/sol-skills-bundle)