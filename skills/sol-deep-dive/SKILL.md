---
name: sol-deep-dive
description: Weekly long-form AI analysis (1500-2500 words) — comprehensive look at one AI subject each Monday.
version: 1.0.0
author: TheSolAI
permissions: ["http.request", "file.write"]
---

# Sol Deep Dive

Weekly long-form analysis on a single AI subject. 1500-2500 words. Comprehensive, structured, no fluff.

## What it does

- Picks a topic from 10 rotating angles (frontier scaling laws, enterprise deployment failures, AI safety incidents, compute concentration, vertical AI, regulation, etc)
- Generates a structured 1500-2500 word analysis
- Creates a Jekyll post with frontmatter, headers, and TL;DR
- Auto-commits and pushes to GitHub

## Schedule

Runs **Mondays at 08:00 UK time** via launchd.

## Topics

10 weekly subjects that rotate: frontier model scaling laws and what happens when they break, why enterprise AI deployments fail, AI agent safety from alignment to deployment, compute concentration as the new bottleneck, open vs closed source competition, regulation that actually works, vertical AI winning the enterprise, AI economics (bubbles, busts, breakthroughs), memory and context for agents, multimodal models ending text-only AI.

## Setup

Requires:
- `~/.openclaw/workspace/secrets/minimax-key.txt` — MiniMax API key
- Site repo at `/Users/amre/Projects/thesolai.github.io`

## Source

`scripts/content-pipeline/deep-dive.py` in [sol-skills-bundle](https://github.com/TheSolAI/sol-skills-bundle)