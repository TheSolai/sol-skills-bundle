#!/usr/bin/env python3
"""
Sol AI — Weekly AI Roundup
Runs every Monday at 8am UK time.
Fetches the week's top AI stories, picks the 5 most significant,
and publishes a curated roundup in Sol's voice.

Cronsetup: launchd StartCalendarInterval Monday 07:00
Or: 0 7 * * 1 python3 scripts/content-pipeline/weekly-roundup.py >> logs/sol-content.log 2>&1
"""

import sys, os, json, datetime, urllib.request, re, urllib.error
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, "/Users/amre/Projects/sol-skills-bundle/scripts/content-pipeline")
try:
    from _minimax_helper import minimax_generate, load_minimax_key
except Exception:  # pragma: no cover - helper missing must not break publishing
    minimax_generate = None

    def load_minimax_key():
        return ""

try:
    # run-daily.py carries a tested source-excerpt fetcher used to ground the
    # MiniMax prompts. The file name has a hyphen, so it cannot be imported
    # directly — source_fetch.py loads it via importlib and is the single
    # definition of that lookup.
    from source_fetch import fetch_source_excerpt
except Exception:  # pragma: no cover - fall back to headline-only grounding
    def fetch_source_excerpt(url, max_chars=6000):
        return ""

# Sentence-aware trimming helpers are shared with midweek-news.py so the two
# generators cannot drift apart — see text_utils.py for the rationale.
from text_utils import (
    ends_cleanly as _ends_cleanly,
    split_sentences as _split_sentences,
    trim_to_words as _trim_to_words,
)

SITE_DIR = Path("/Users/amre/Projects/thesolai.github.io")
BUNDLE_DIR = Path("/Users/amre/Projects/sol-skills-bundle/scripts/content-pipeline")
DEVTO_API_KEY = os.getenv("DEVTO_API_KEY", "")

TODAY = datetime.datetime.now(datetime.timezone.utc)
DATE_STR = TODAY.strftime("%Y-%m-%d")
POST_DATE = f"{TODAY.strftime('%B')} {TODAY.day}, {TODAY.year}"
WEEK_STR = TODAY.strftime("%B %Y")

# Title carries the week-ending date, not just the month.
# WHY (2026-10-26): "The Week in AI: October 2026" was used for every roundup
# in a month — a month has 4-5 Mondays, so 12 posts shared one title and one
# description. That is duplicate content on the site. Dating the title keeps
# each roundup distinct while keeping the series name intact.
ROUNDUP_TITLE = f"The Week in AI: {POST_DATE}"


def fetch_story(story_id, cutoff):
    """Fetch a single HN story if it's within the cutoff and has a title."""
    try:
        req = urllib.request.Request(
            f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json",
            headers={"User-Agent": "SolAI/1.0"}
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            story = json.loads(r.read())
        if story and story.get("title") and story.get("time"):
            story_time = datetime.datetime.fromtimestamp(story["time"], tz=datetime.timezone.utc)
            if story_time >= cutoff:
                return {
                    "id": story_id,
                    "title": story.get("title", ""),
                    "url": story.get("url", f"https://news.ycombinator.com/item?id={story_id}"),
                    "score": story.get("score", 0),
                    "by": story.get("by", ""),
                    "comments": story.get("descendants", 0),
                    "time": story_time,
                }
    except Exception:
        pass
    return None


def fetch_hn_stories(limit=500, days_back=7):
    """Fetch newest HN stories and filter for AI-related + recent ones.

    Uses newstories.json (chronological) rather than topstories.json (score-ranked)
    to ensure week-old stories are captured even if they're no longer on the front page.
    """
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_back)
    try:
        req = urllib.request.Request(
            "https://hacker-news.firebaseio.com/v0/newstories.json",
            headers={"User-Agent": "SolAI/1.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            ids = json.loads(r.read())
    except Exception as e:
        print(f"[news] Failed to fetch HN stories: {e}")
        return []

    # Take first 'limit' IDs and fetch them concurrently
    story_ids = ids[:limit]
    stories = []
    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = {executor.submit(fetch_story, sid, cutoff): sid for sid in story_ids}
        for future in as_completed(futures):
            result = future.result()
            if result:
                stories.append(result)
    return stories


def is_ai_story(title):
    keywords = [
        "ai", "llm", "gpt", "claude", "gemini", "openai", "anthropic",
        "machine learning", "neural", "model", "chatbot", "agent",
        "copilot", "rag", "embedding", "diffusion", "stable diffusion",
        "mistral", "groq", "ollama", "langchain", "autogen", "crewai",
        "nlp", "vision", "multimodal", "reasoning",
        "artificial intelligence", "cursor", "devin", "v0", "replit",
        "opencl", "bolt.new", "lovable",
    ]
    t = title.lower()
    return any(kw in t for kw in keywords)


# ── Sol's Take ──────────────────────────────────────────────────────────────
#
# HISTORY (2026-10-26): the "Sol's Take" section used to be a hardcoded string
# in build_roundup(). Every roundup since October shipped byte-identical
# commentary — all 12 live posts hash to the same block. The selection logic
# was fine; the commentary was a lie about itself.
#
# It is now generated from the actual week's stories, grounded in the source
# pages, and falls back to a deterministic-but-contextual template if the LLM
# is unreachable. The fallback still names the real stories, so the section is
# never generic filler.

# A reasoning model gives markedly better commentary than MiniMax-Text-01:
# sharper opinions, no "underscores a shift towards" filler, and honest when
# a story is too thin to judge. Text-01 stays as the fallback tier.
TAKE_MODEL = "MiniMax-M2.7"
TAKE_MODEL_FALLBACK = "MiniMax-Text-01"

SOL_TAKE_SYSTEM = """You are Sol AI, writing the commentary section of a weekly AI news roundup.

Voice: direct, honest, slightly wry. A technically literate observer who has
opinions and does not pad. You are not a hype bot and not a cynic.

STYLE, by example. This is the register to hit — note it is about a DIFFERENT
topic from any story you will be given, so borrow the voice, not the content:
  "The most interesting thing on the board this week was a licensing change
   nobody expected and three separate blog posts arguing about it. Meanwhile
   the launch everyone actually noticed is forgettable. That's usually the
   shape of a week: the loud story is loud, and the quiet one matters more."

Not this:
  "This week's AI news paints a picture of an industry in flux, grappling with
   both pragmatic advancements and existential questions."

BANNED PHRASES — these are the tells of machine-written analysis. Do not use
them or anything shaped like them: "underscores", "paints a picture",
"collectively urge us", "in an increasingly", "the era of", "it's a reminder
that", "this isn't just", "raises questions about", "points to a shift
towards", "suggests that", "highlights the".

GROUND RULES (these matter more than the style):
- Write about the stories you were given. Do NOT echo, quote, or paraphrase
  this brief back to the reader, and do not open with a quotation mark.
- Name each story you discuss by its ACTUAL TITLE, in quotes or italics. The
  reader sees headlines, not a numbered list — never write "story 1",
  "the first story", or "the second item".
- Never invent companies, people, products, numbers, or events. Use only what
  appears in the material provided. If a story is thin on detail, say plainly
  that it is thin rather than filling the gap.
- Do not repeat the score/comment counts back to the reader; they are already
  shown next to each headline.
- Do not add a heading. Do not use bullet points. Output plain paragraphs.
- Do not start with "This week's pattern:" or any stock opening phrase.
- Take a position. A reader should be able to disagree with you."""


def _story_digest(stories):
    """Render the week's stories into a grounded prompt block."""
    blocks = []
    for i, story in enumerate(stories, 1):
        block = (
            f"{i}. {story['title']}\n"
            f"   HN: {story['score']} points, {story['comments']} comments\n"
            f"   {story['url']}"
        )
        excerpt = fetch_source_excerpt(story["url"], max_chars=2500)
        if excerpt:
            block += f"\n   Source excerpt: {excerpt}"
        blocks.append(block)
    return "\n\n".join(blocks)


# NOTE (2026-10-28): the sentence-splitting and word-trimming helpers that
# used to live here now live in text_utils.py, shared with midweek-news.py.
# The implementation is unchanged — only the location.


def generate_sol_take(stories):
    """
    Generate contextual commentary for the roundup via MiniMax.

    Returns markdown text, or None if the LLM could not be reached — the
    caller then uses fallback_sol_take(). Never raises: a commentary failure
    must not cost us the post.
    """
    if not stories or minimax_generate is None:
        return None

    key = load_minimax_key()
    if not key:
        print("[take] No MiniMax key — using contextual fallback")
        return None

    prompt = (
        f"Here are the {len(stories)} AI stories that made this week's roundup "
        f"({WEEK_STR}):\n\n{_story_digest(stories)}\n\n"
        "Now write the commentary. Format: exactly 3 short paragraphs, 3-4 "
        "sentences each, HARD LIMIT 180 words. A piece over 180 words is "
        "rejected. Name the stories you discuss by their titles, and take a "
        "position a reader could disagree with."
    )

    # Reasoning models give markedly better commentary than MiniMax-Text-01
    # here — sharper opinions, no "underscores a shift towards" filler, and
    # honest when a story is too thin to judge. They need a big token
    # budget because most of it goes on thinking before any text appears.
    # Text-01 stays as the fallback tier, then the deterministic template.
    attempts = [
        (TAKE_MODEL, 6000),
        (TAKE_MODEL_FALLBACK, 900),
    ]
    for model, max_tokens in attempts:
        try:
            text = minimax_generate(
                prompt,
                api_key=key,
                max_tokens=max_tokens,
                system=SOL_TAKE_SYSTEM,
                temperature=0.8,
                model=model,
                timeout=240,
            )
        except Exception as e:
            print(f"[take] MiniMax error ({model}): {e}")
            continue

        if text:
            break
    else:
        text = None

    if not text:
        print("[take] MiniMax returned nothing — using contextual fallback")
        return None

    text = text.strip()
    # Strip a stray markdown heading the model may have added anyway.
    text = re.sub(r"^#{1,6}\s*Sol'?s Take\s*[:—-]?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # The model sometimes wraps the whole section in a single pair of quotes,
    # as if quoting the brief back. Unwrap it.
    if len(text) > 200 and text.startswith('"') and text.rstrip().endswith('"'):
        text = text[1:].rstrip()[:-1].strip()
    text = text.strip()

    if len(text) < 80:
        print("[take] Commentary too short — using contextual fallback")
        return None

    trimmed = _trim_to_words(text, limit=200)
    if trimmed != text:
        print(f"[take] Trimmed commentary {len(text.split())} -> {len(trimmed.split())} words")

    # Whatever happened above, never publish text that stops mid-sentence
    # (the model can also be cut off by max_tokens).
    if not _ends_cleanly(trimmed):
        paragraphs = [p.strip() for p in trimmed.split("\n\n") if p.strip()]
        if paragraphs:
            sentences = _split_sentences(paragraphs[-1])
            if sentences and not _ends_cleanly(sentences[-1]):
                sentences = sentences[:-1]
                paragraphs[-1] = " ".join(sentences)
            trimmed = "\n\n".join(p for p in paragraphs if p).strip()

    return trimmed


def fallback_sol_take(stories):
    """
    Deterministic commentary used when the LLM is unreachable.

    Still names the real stories, so the section stays specific to the week
    instead of degrading into the same boilerplate every run.
    """
    if not stories:
        return (
            "A quiet week by the standards of this industry, which is itself "
            "worth noticing."
        )

    top = stories[0]
    runners = stories[1:3]

    parts = [
        f"The week belonged to *{top['title']}*. It was the story that moved "
        f"the most people, and the one worth arguing about.",
    ]

    if runners:
        names = " and ".join(f"*{s['title']}*" for s in runners)
        parts.append(
            f"Close behind came {names}. Different news, same underlying "
            f"question: who is actually paying for all of this, and what do "
            f"they get back?"
        )

    parts.append(
        "Details on each are above. My take: the argument isn't over whether "
        "these matter — it's over which of them will still matter in a year."
    )
    return "\n\n".join(parts)


def build_roundup(stories):
    """Build the weekly roundup post content in Sol's voice."""
    ai_stories = sorted([s for s in stories if is_ai_story(s["title"])],
                        key=lambda s: s["score"] + s["comments"] * 0.5, reverse=True)
    top_stories = ai_stories[:7]

    if not top_stories:
        top_stories = sorted(stories, key=lambda s: s["score"], reverse=True)[:5]

    # Categorise stories
    policy_stories = [s for s in top_stories if any(k in s["title"].lower() for k in ["regulation", "law", "act", "policy", "government", "eu", "uk ", "uk,", "congress", "trump", "biden"])]
    product_stories = [s for s in top_stories if s not in policy_stories and any(k in s["title"].lower() for k in ["launch", "release", "introduces", "unveils", "new", "open-source", "open source"])]
    research_stories = [s for s in top_stories if s not in policy_stories and s not in product_stories]

    sections = []
    if policy_stories:
        sections.append(("Policy & Regulation", policy_stories))
    if product_stories:
        sections.append(("Products & Releases", product_stories))
    if research_stories:
        sections.append(("Research & Ideas", research_stories))

    # Build markdown
    total_hn_points = sum(s["score"] for s in top_stories)

    lines = [f"""# {ROUNDUP_TITLE}

*Weekly roundup from Sol AI — curated AI news, assessed and contextualised.*

---

This week in AI: {len(top_stories)} stories that mattered, drawn from {total_hn_points} HN points across the community.

"""]

    for section_title, section_stories in sections:
        lines.append(f"## {section_title}\n")
        for i, story in enumerate(section_stories, 1):
            lines.append(f"**{i}. {story['title']}**")
            lines.append(f"[Read more →]({story['url']}) — {story['score']} points · {story['comments']} comments\n")
        lines.append("")

    # Sol's commentary — generated from this week's actual stories.
    take = generate_sol_take(top_stories) or fallback_sol_take(top_stories)

    lines.append(f"""## Sol's Take

{take}

---

*Roundup published every Monday. Follow [Sol AI](https://thesolai.github.io) for daily AI analysis.*""")

    return "\n".join(lines), top_stories


def post_to_devto(title, body, tags):
    """Cross-post to dev.to."""
    if not DEVTO_API_KEY:
        print("[dev.to] No API key — skipping cross-post")
        return None

    payload = {
        "article": {
            "title": title,
            "body_markdown": body,
            "tag_list": tags[:4],
            "published": True,
        }
    }

    req = urllib.request.Request(
        "https://dev.to/api/articles",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "SolAI/1.0",
            "api-key": DEVTO_API_KEY,
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            result = json.loads(r.read())
            url = result.get("url", "")
            print(f"[dev.to] ✅ Posted: {url}")
            return url
    except urllib.error.HTTPError as e:
        print(f"[dev.to] ❌ HTTP {e.code}: {e.read()[:100]}")
        return None
    except Exception as e:
        print(f"[dev.to] ❌ Error: {e}")
        return None


def main():
    print(f"\n{'='*60}")
    print(f"Sol AI Weekly Roundup — {WEEK_STR}")
    print(f"{'='*60}\n")

    print("[news] Fetching this week's HN stories...")
    stories = fetch_hn_stories(limit=500, days_back=7)
    ai_stories = [s for s in stories if is_ai_story(s["title"])]
    print(f"[news] Found {len(ai_stories)} AI stories from the past week")

    title = ROUNDUP_TITLE

    slug = f"{DATE_STR}-week-in-ai-{TODAY.strftime('%b-%Y').lower()}"
    filename = f"{slug}.md"
    filepath = SITE_DIR / "_posts" / filename

    # Check for an existing post BEFORE building the body. Two schedulers
    # fire this job (launchd Monday 07:00 and the 08:00 cron), so the second
    # run must not burn an LLM call to generate commentary it will discard.
    if filepath.exists():
        print(f"[roundup] ⚠️  Already exists: {filepath.name}")
        print(f"\n{'='*60}")
        print(f"Weekly roundup already published — nothing to do.")
        print(f"{'='*60}\n")
        return

    content, top_stories = build_roundup(stories)

    frontmatter = f"""---
layout: post
title: "{title}"
date: {DATE_STR}
description: "This week's most important AI news — curated, assessed, and contextualised. Policy, products, and research for the week ending {POST_DATE}."
image: /images/sol-avatar.png
hero_image: /images/sol-avatar.png
tags: [ai, roundup, weekly, news, analysis]
author: Sol AI
featured: true
---

{content}
"""

    filepath.write_text(frontmatter, encoding="utf-8")
    print(f"[roundup] ✅ Written: {filepath.name}")

    # Publish to origin/main.
    # NOTE (2026-10-22): this used to create a `roundup/<date>` branch and
    # open a PR. That was the root cause of a silent publishing outage:
    # the repo was left checked out on the side branch, and every other
    # pipeline script ran a bare `git push`, which pushed THAT branch
    # instead of main. 19 commits and ~20 posts never went live while the
    # logs cheerfully reported success. The PRs were also never merged.
    # All publishing now goes through site_git.publish(), which always
    # targets origin/main and verifies the push landed.
    try:
        from site_git import publish
        sha = publish(f"Sol AI weekly roundup: {WEEK_STR}", allow_empty=True)
        if sha:
            print(f"[git] ✅ Pushed to origin/main ({sha[:12]})")
        else:
            print("[git] No changes to commit")
    except Exception as e:
        print(f"[git] ❌  Push FAILED: {e}")

    # Cross-post to dev.to
    post_to_devto(title, content, ["ai", "news", "weekly", "roundup"])

    print(f"\n{'='*60}")
    print(f"Weekly roundup complete!")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
