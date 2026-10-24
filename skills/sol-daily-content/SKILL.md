---
name: sol-daily-content
description: Daily regional AI blog post — generates a UK, EU, or US AI news analysis post every day, rotating regions by day-of-week.
version: 1.0.0
author: TheSolAI
permissions: ["http.request", "file.write"]
---

# Sol Daily Content Pipeline

Daily blog post generation: picks a significant AI story and writes a regional analysis in Sol's voice.

## What it does

- Fetches top HN stories and filters for AI keywords
- Picks the best story and writes 400-600 word regional analysis
- Regions: UK (Mondays), EU (Wednesdays), US (Fridays), UK (Tue/Thu), EU (Sat), US (Sun)
- Creates a Jekyll post in `_posts/YYYY-MM-DD-REGION-STORY-SLUG.md`
- Optionally cross-posts to dev.to via the dev.to API
- Auto-commits and pushes to GitHub

## Schedule

Runs **daily at 8am UK time** via launchd.

## Guardrails (added 2026-10-24)

These exist because of a real incident — 260 of 275 published regional
posts were cut off mid-sentence, and several invented company names.

- **Token budget** — `max_tokens=1400`, not 600. 600 tokens is roughly 450
  words, so a post asked for 400-600 words was always cut off.
- **Truncation guard** — `repair_truncation()` trims an incomplete trailing
  sentence and drops headings left dangling with no body. If the body still
  cannot be closed, the post falls back to the structured template rather
  than publishing a fragment.
- **Source grounding** — `fetch_source_excerpt()` pulls the real source-page
  text and feeds it to the model. The prompts ask for the regional *angle*
  and explicitly forbid inventing companies, people, or national origins.
  The story is often global; the regional framing must not be invented.
- **Title hygiene** — `_clean_title()` drops a dangling unbalanced quote,
  which otherwise breaks the YAML frontmatter.
- **Regeneration safety** — when a post's slug changes, add a
  `redirect_from:` entry for the old URL, and match dev.to articles by
  `redirect_from` rather than by date (several posts share a date).

## Maintenance

- `backfill_repair.py` — repairs already-published truncated posts in place.
  Dry-run by default; pass `--apply` to write.
- `devto_update.py` — re-publishes a corrected body to an existing dev.to
  article via `PUT /api/articles/{id}`. Use `--id=` to target explicitly.
- `devto_backfill.py` — updates many dev.to cross-posts at once.

dev.to only allows editing articles from roughly the last 30 days. Older
articles return 404 on `PUT` and cannot be repaired through the API.

## Setup

Requires:
- `~/.openclaw/workspace/secrets/minimax-key.txt` — MiniMax API key
- Site repo at `/Users/amre/Projects/thesolai.github.io`
- Optional: `OPENROUTER_API_KEY` for fallback LLM

## Source

`scripts/content-pipeline/run-daily.py` in [sol-skills-bundle](https://github.com/TheSolAI/sol-skills-bundle)
