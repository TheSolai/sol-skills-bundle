---
name: sol-case-study
description: Weekly anonymised AI deployment case study — real companies, real outcomes, real lessons. Runs Fridays at 9am UK time.
version: 1.0.0
author: TheSolAI
permissions: ["http.request", "file.write"]
---

# Sol Case Study

Weekly anonymised case study of a real AI deployment. Real outcomes, real lessons.

## What it does

- Picks an industry from a rotating set (banking, healthcare, legal, retail, manufacturing)
- Generates a structured case study: problem, solution, results, lessons
- Includes generalisable advice for organisations considering similar deployments
- Creates a Jekyll post with full frontmatter
- Auto-commits and pushes to GitHub

## Schedule

Runs **Fridays at 09:00 UK time** via launchd.

## Industries covered

5 industries that rotate weekly: banking (loan underwriting), healthcare (NHS triage), legal (contract review at a mid-size firm), retail (personalisation at scale), manufacturing (computer vision quality control).

## Setup

Requires:
- `~/.openclaw/workspace/secrets/minimax-key.txt` — MiniMax API key
- Site repo at `/Users/amre/Projects/thesolai.github.io`

## Source

`scripts/content-pipeline/case-study.py` in [sol-skills-bundle](https://github.com/TheSolAI/sol-skills-bundle)