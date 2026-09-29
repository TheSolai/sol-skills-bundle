---
name: sol-analysis-deep
description: Deep analysis generator — 1500-2200 word long-form posts on rotating AI subjects. Runs Wed/Fri/Sun via launchd. Replaces the shorter weekly briefs.
version: 1.0.0
author: TheSolAI
permissions: ["http.request", "file.write"]
---

# Sol Analysis Deep

Long-form analysis posts. 1500-2200 words. Structured argument with TL;DR, setup, main argument, counter-arguments, implications, and what's next.

## What it does

- Picks a topic from 12 rotating subject families (capability vs deployment, agents, economics, safety, regulation, infrastructure, industry-specific, tools/frameworks, memory, society, real-world failures, future)
- Generates structured 1500-2200 word analysis with explicit TL;DR + argument + counter + implications sections
- Includes specific examples (companies, products, events), specific numbers, and at least 2 counter-arguments
- Avoids generic AI discourse — takes positions and defends them
- Creates a Jekyll post with `category: analysis` and `analysis_topic` / `analysis_family` frontmatter
- Auto-commits and pushes to GitHub

## Schedule

Runs **Wed 10:00 + Fri 14:00 + Sun 18:00 UK time** via launchd (3x weekly).

## 12 subject families

Each family has 4 specific topics, rotated by day-of-year and weekday. Examples:

- AI capability vs deployment reality
- AI agents — what works, what doesn't
- AI economics and business models
- AI safety, alignment, deployment
- AI regulation and policy
- AI infrastructure and compute
- AI in specific industries
- AI tools, frameworks, and developer experience
- AI memory, context, and continuity
- AI and society — jobs, education, culture
- AI in the real world — failures, recoveries, lessons
- The future of AI agents

## Setup

Requires:
- `~/.openclaw/workspace/secrets/minimax-key.txt` — MiniMax API key
- Site repo at `/Users/amre/Projects/thesolai.github.io`
- MiniMax must be working (use `_minimax_helper.py` for retry logic)

## Source

`scripts/content-pipeline/analysis-deep.py` in [sol-skills-bundle](https://github.com/TheSolAI/sol-skills-bundle)