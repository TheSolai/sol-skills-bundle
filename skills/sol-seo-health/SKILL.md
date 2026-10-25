---
name: sol-seo-health
description: Daily SEO health check — validates all blog posts for SEO completeness, checks Open Graph images, canonicals, duplicate content, sitemap, robots.txt, meta tags, broken links, and the RSS feed.
version: 2.0.0
author: TheSolAI
permissions: ["file.read", "file.write", "http.request"]
---

# Sol SEO Health Check

Daily automated SEO audit for thesolai.github.io. Catches issues before they become problems.

## What it checks

**Source level (fast, no network)**

- Every blog post has title, description, hero image, `layout: post` and a body
- Every post filename starts with `YYYY-MM-DD` — Jekyll silently skips posts
  whose filename has no date prefix, so they never get published
- Every post has a matching `og/<source-filename>.png`
- The post layout emits `og:image`, `og:title`, `og:description`,
  `twitter:image` and exactly one canonical link — and derives `og:image`
  from `page.path`, not `page.slug` (the date is stripped from `page.slug`,
  which is what left 100% of posts with a 404 social card)
- Every `canonical:` override resolves to a real post URL
- No two posts share an identical body (duplicate content)
- `robots.txt` only references sitemaps that actually exist
- Skill directories have a `SKILL.md` or `README.md` with frontmatter
- Major pages all carry the Bloopers nav link
- Pages using the comic design system load `comic-ui.css`
- Internal links point at files that exist

**Live site (spot-check)**

- `sitemap.xml` and `feed.xml` are reachable and non-empty
- A spread of live posts each have a title, meta description, exactly one
  canonical link, and an `og:image` URL that returns 200

## What it deliberately does not flag

- **Missing author** — the post layout hardcodes `<meta name="author">` and
  the JSON-LD author, so no per-post `author:` key is needed.
- **Duplicate titles** — recurring series (Sol's Take, Quick Hits, AI
  Bloopers) reuse a headline by design. Duplicate *permalinks* are the real
  defect and are gated by `_tests/check_front_matter.rb`.
- **Missing image** — posts carry `hero_image:`, not `image:`.
- **`comic-ui.css` on standalone pages** — ournook and hack-fighter ship
  their own stylesheet and must not be forced onto the comic theme.

## Schedule

Runs **daily after site build** via launchd.

## Output

Logs to `logs/sol-seo.log`. Reports issues count with individual problem listings.

## Setup

Requires site repo at `/Users/amre/Projects/thesolai.github.io`

## Source

`scripts/content-pipeline/seo-health.py` in [sol-skills-bundle](https://github.com/TheSolAI/sol-skills-bundle)
