#!/usr/bin/env python3
"""
Sol AI — Mid-Week AI News Digest
Runs every Wednesday at 8am UK time.
Fetches the past 3 days of AI stories, curates the 3 most significant,
and publishes a quick digest with Sol's own commentary.

Cronsetup: launchd StartCalendarInterval Wednesday 07:00
Or: 0 7 * * 3 python3 scripts/content-pipeline/midweek-news.py >> logs/sol-content.log 2>&1

--refresh rewrites today's post in place (same URL, no second copy) when the
commentary needs regenerating.
"""

import sys, os, json, argparse, datetime, urllib.request, urllib.parse, re
from pathlib import Path

BUNDLE_DIR = Path("/Users/amre/Projects/sol-skills-bundle/scripts/content-pipeline")
sys.path.insert(0, str(BUNDLE_DIR))

try:
    from _minimax_helper import minimax_generate, load_minimax_key
except Exception:  # pragma: no cover - helper missing must not break publishing
    minimax_generate = None

    def load_minimax_key():
        return ""

try:
    from source_fetch import fetch_source_excerpt
except Exception:  # pragma: no cover - fall back to headline-only grounding
    def fetch_source_excerpt(url, max_chars=6000):
        return ""

try:
    from post_identity import normalise_url, published_source_urls
except Exception:  # pragma: no cover - dedupe is an enhancement, not a blocker
    def normalise_url(url):
        return (url or "").strip().lower()

    def published_source_urls(posts_dir):
        return set()

try:
    from text_utils import ends_cleanly, split_sentences, trim_to_words
except Exception:  # pragma: no cover - degrade to no trimming
    def ends_cleanly(sentence):
        return bool(sentence) and sentence.rstrip()[-1:] in ".!?\"'”’"

    def split_sentences(para):
        return [para]

    def trim_to_words(text, limit=200):
        return text

SITE_DIR = Path("/Users/amre/Projects/thesolai.github.io")
POSTS_DIR = SITE_DIR / "_posts"
DEVTO_API_KEY = os.getenv("DEVTO_API_KEY", "")
HN_ALGOLIA = "https://hn.algolia.com/api/v1/search"

TODAY = datetime.datetime.now(datetime.timezone.utc)
DATE_STR = TODAY.strftime("%Y-%m-%d")
DAY_STR = TODAY.strftime("%A")
# The episode date. The digest is a recurring series, so a title that names
# only the weekday repeats every week — 12 posts shipped as "AI Digest:
# Wednesday — 3 Stories That Matter" before the date was added.
POST_DATE = f"{TODAY.strftime('%B')} {TODAY.day}, {TODAY.year}"

DAYS_BACK = 3
STORY_COUNT = 3

# A reasoning model gives markedly better commentary than MiniMax-Text-01:
# sharper opinions, no "underscores a shift towards" filler, and honest when
# a story is too thin to judge. Text-01 stays as the fallback tier.
COMMENTARY_MODEL = "MiniMax-M2.7"
COMMENTARY_MODEL_FALLBACK = "MiniMax-Text-01"

COMMENTARY_SYSTEM = """You are Sol AI, writing the commentary section of a mid-week AI digest.

Voice: direct, honest, slightly wry. A technically literate observer who has
opinions and does not pad. You are not a hype bot and not a cynic.

STYLE, by example. This is the register to hit — note it is about a DIFFERENT
topic from any story you will be given, so borrow the voice, not the content:
  "Three stories, and the one that matters is the one nobody is arguing
   about. The loudest story this week is a company making a decision it
   should have made a year ago. The interesting one is a small tool that
   quietly removes a whole category of work. That is usually the shape of a
   week: the loud story is loud, and the quiet one matters more."

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


def _http_json(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "SolAI/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def fetch_hn_stories(days_back=DAYS_BACK, limit=100):
    """Fetch AI-relevant HN stories from the past `days_back` days.

    WHY Algolia (2026-10-28): the old implementation read the top 40
    front-page stories and computed a `cutoff` timestamp that was then never
    used — nothing filtered by time at all. The digest claimed to cover "the
    past 3 days" while actually surfacing whatever was on the front page
    that minute. The Algolia API supports a real time filter, so the claim
    is now true.

    Falls back to the Firebase API (with the same time filter applied) if
    Algolia is unreachable.
    """
    cutoff = int((TODAY - datetime.timedelta(days=days_back)).timestamp())

    stories = []
    try:
        params = {
            "tags": "story",
            "numericFilters": f"created_at_i>{cutoff}",
            "hitsPerPage": str(limit),
        }
        url = HN_ALGOLIA + "?" + urllib.parse.urlencode(params)
        data = _http_json(url)
        for hit in data.get("hits", []):
            if not hit.get("title"):
                continue
            oid = hit.get("objectID")
            stories.append({
                "title": hit["title"],
                "url": hit.get("url") or f"https://news.ycombinator.com/item?id={oid}",
                "score": hit.get("points") or 0,
                "comments": hit.get("num_comments") or 0,
                "time": hit.get("created_at_i") or 0,
            })
        if stories:
            print(f"[news] Algolia: {len(stories)} stories from the past {days_back} days")
            return stories
    except Exception as e:
        print(f"[news] Algolia fetch failed ({e}) — falling back to the Firebase API")

    # Fallback: Firebase top stories, filtered by time so the window is honest.
    try:
        ids = _http_json("https://hacker-news.firebaseio.com/v0/topstories.json")[:60]
    except Exception as e:
        print(f"[news] Failed to fetch HN stories: {e}")
        return []

    for story_id in ids:
        try:
            story = _http_json(f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json")
            if not story or not story.get("title"):
                continue
            if story.get("time", 0) < cutoff:
                continue
            stories.append({
                "title": story["title"],
                "url": story.get("url", f"https://news.ycombinator.com/item?id={story_id}"),
                "score": story.get("score", 0),
                "comments": story.get("descendants", 0),
                "time": story.get("time", 0),
            })
        except Exception:
            continue

    print(f"[news] Firebase: {len(stories)} stories from the past {days_back} days")
    return stories


def is_ai_story(title):
    keywords = [
        "ai", "llm", "gpt", "claude", "gemini", "openai", "anthropic",
        "machine learning", "neural", "model", "chatbot", "agent",
        "copilot", "rag", "embedding", "diffusion",
        "mistral", "groq", "ollama", "langchain",
        "artificial intelligence", "cursor", "devin", "v0",
    ]
    t = title.lower()
    return any(kw in t for kw in keywords)


def select_stories(stories, exclude_urls=None):
    """
    Pick the most significant AI stories that are not already published.

    Dedupe is on the source URL — the stable key for anything that comments
    on the news (see post_identity.py and OPERATIONS.md). Publishing a second
    URL for a story the archive already covers is a duplicate post.
    """
    exclude = {normalise_url(u) for u in (exclude_urls or set())}
    candidates = []
    for story in stories:
        if not is_ai_story(story["title"]):
            continue
        if normalise_url(story["url"]) in exclude:
            continue
        candidates.append(story)

    candidates.sort(key=lambda s: s["score"] + s["comments"] * 0.5, reverse=True)
    return candidates[:STORY_COUNT]


def _story_digest(stories):
    """Render the stories into a grounded prompt block."""
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


def generate_commentary(stories):
    """
    Generate contextual commentary for the digest via MiniMax.

    Returns markdown text, or None if the LLM could not be reached — the
    caller then uses fallback_commentary(). Never raises: a commentary
    failure must not cost us the post.
    """
    if not stories or minimax_generate is None:
        return None

    key = load_minimax_key()
    if not key:
        print("[digest] No MiniMax key — using contextual fallback")
        return None

    prompt = (
        f"Here are the {len(stories)} AI stories that made this mid-week digest:\n\n"
        f"{_story_digest(stories)}\n\n"
        "Now write the commentary. Format: exactly 2 short paragraphs, 2-3 "
        "sentences each, HARD LIMIT 150 words. A piece over 150 words is "
        "rejected. Name the stories you discuss by their titles, and take a "
        "position a reader could disagree with."
    )

    attempts = [
        (COMMENTARY_MODEL, 6000),
        (COMMENTARY_MODEL_FALLBACK, 900),
    ]
    text = None
    for model, max_tokens in attempts:
        try:
            text = minimax_generate(
                prompt,
                api_key=key,
                max_tokens=max_tokens,
                system=COMMENTARY_SYSTEM,
                temperature=0.8,
                model=model,
                timeout=240,
            )
        except Exception as e:
            print(f"[digest] MiniMax error ({model}): {e}")
            continue
        if text:
            break

    if not text:
        print("[digest] MiniMax returned nothing — using contextual fallback")
        return None

    text = text.strip()
    # Strip a stray markdown heading the model may have added anyway.
    text = re.sub(r"^#{1,6}\s*Sol'?s (?:Read|Take)\s*[:—-]?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # The model sometimes wraps the whole section in a single pair of quotes,
    # as if quoting the brief back. Unwrap it.
    if len(text) > 200 and text.startswith('"') and text.rstrip().endswith('"'):
        text = text[1:].rstrip()[:-1].strip()
    text = text.strip()

    if len(text) < 80:
        print("[digest] Commentary too short — using contextual fallback")
        return None

    trimmed = trim_to_words(text, limit=150)
    if trimmed != text:
        print(f"[digest] Trimmed commentary {len(text.split())} -> {len(trimmed.split())} words")

    # Never publish text that stops mid-sentence (the model can also be cut
    # off by max_tokens).
    if not ends_cleanly(trimmed):
        paragraphs = [p.strip() for p in trimmed.split("\n\n") if p.strip()]
        if paragraphs:
            sentences = split_sentences(paragraphs[-1])
            if sentences and not ends_cleanly(sentences[-1]):
                sentences = sentences[:-1]
                paragraphs[-1] = " ".join(sentences)
            trimmed = "\n\n".join(p for p in paragraphs if p).strip()

    return trimmed


def fallback_commentary(stories):
    """
    Deterministic commentary used when the LLM is unreachable.

    Still names the real stories, so the section stays specific to this
    digest instead of degrading into the same boilerplate every run.
    """
    if not stories:
        return (
            "A quiet few days by the standards of this industry, which is "
            "itself worth noticing."
        )

    top = stories[0]
    runners = stories[1:]

    parts = [
        f"The story that moved the most people this week was *{top['title']}*. "
        f"It is worth reading properly rather than skimming the thread."
    ]

    if runners:
        names = " and ".join(f"*{s['title']}*" for s in runners)
        parts.append(
            f"Also worth your time: {names}. Different stories, and they do "
            f"not all point the same way — which is usually the point of "
            f"reading three of them at once."
        )

    parts.append(
        "My take: the argument is not about whether this technology works. "
        "It is about who carries the cost of it working."
    )

    return "\n\n".join(parts)


def build_digest(stories, commentary):
    """Build the digest body in Sol's house format."""
    lines = [f"""# AI Digest: {DAY_STR} Morning

*Quick read: the {len(stories)} AI stories that matter this week so far.*

---

{commentary}

---

"""]

    for i, story in enumerate(stories, 1):
        lines.append(f"**{i}. {story['title']}**")
        lines.append(
            f"*Source: [{story['title']}]({story['url']}) — "
            f"{story['score']} points on Hacker News*\n"
        )

    lines.append("""---

*Full weekly roundup published Monday mornings. Follow [Sol AI](https://thesolai.github.io) for daily coverage.*""")

    return "\n".join(lines)


def _yaml(value: str) -> str:
    """Quote a string for use as a YAML double-quoted scalar."""
    v = str(value).replace("\\", "\\\\").replace('"', '\\"')
    v = v.replace("\n", " ").replace("\r", " ").strip()
    return f'"{v}"'


def main():
    parser = argparse.ArgumentParser(description="Sol AI mid-week AI digest")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Rewrite today's post in place (same URL) instead of skipping it",
    )
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"Sol AI Mid-Week Digest — {DAY_STR}")
    print(f"{'='*60}\n")

    slug = f"{DATE_STR}-ai-digest-{DAY_STR.lower()}"
    filepath = POSTS_DIR / f"{slug}.md"

    # Idempotency: a post for today already exists means the launchd job
    # already ran. Do the expensive work only when we are actually writing.
    if filepath.exists() and not args.refresh:
        print(f"[digest] Already exists: {filepath.name} — nothing to do")
        print(f"\n{'='*60}\n")
        return

    print("[news] Fetching recent HN stories...")
    stories = fetch_hn_stories(days_back=DAYS_BACK)

    covered = published_source_urls(POSTS_DIR)
    top_stories = select_stories(stories, exclude_urls=covered)
    ai_count = len([s for s in stories if is_ai_story(s["title"])])
    print(f"[news] Found {ai_count} AI stories from past {DAYS_BACK} days")
    print(f"[news] Selected {len(top_stories)} after removing already-covered stories")

    if not top_stories:
        print("[digest] No eligible stories — skipping (a gap beats a duplicate)")
        print(f"\n{'='*60}\n")
        return

    print("[digest] Generating commentary...")
    commentary = generate_commentary(top_stories)
    if not commentary:
        commentary = fallback_commentary(top_stories)
        print("[digest] Using deterministic fallback commentary")

    content = build_digest(top_stories, commentary)
    title = f"AI Digest: {DAY_STR} — {len(top_stories)} Stories That Matter ({POST_DATE})"

    frontmatter = f"""---
layout: post
title: {_yaml(title)}
date: {DATE_STR}
description: {_yaml("Mid-week AI digest — the most important AI stories from the past few days, curated by Sol AI. (" + POST_DATE + ")")}
image: /images/sol-avatar.png
hero_image: /images/sol-avatar.png
tags: [ai, digest, news, midweek]
author: Sol AI
---

{content}
"""

    filepath.write_text(frontmatter, encoding="utf-8")
    verb = "Refreshed" if args.refresh else "Written"
    print(f"[digest] ✅ {verb}: {filepath.name}")

    try:
        from site_git import publish
        sha = publish(f"Sol AI midweek digest: {DATE_STR}", paths=[str(filepath)])
        if sha:
            print(f"[git] ✅ Pushed midweek digest to origin/main ({sha[:12]})")
        else:
            print("[git] ✅ Nothing new to publish — post already live")
    except Exception as e:
        print(f"[git] ❌  Push FAILED: {e}")

    print(f"\n{'='*60}\n")


if __name__ == "__main__":
    main()
