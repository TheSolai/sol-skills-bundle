#!/usr/bin/env python3
"""
Sol AI — Quick Hits
Weekday AI briefing: 3 curated stories, no fluff.
Runs every weekday at 8am UK time.

Cronsetup: launchd StartCalendarInterval Monday-Friday 07:00
Or: 0 7 * * 1-5 python3 scripts/content-pipeline/quick-hits.py >> logs/sol-quick-hits.log 2>&1

Usage:
    python3 quick-hits.py                # publish for today
    python3 quick-hits.py --date 2026-11-09   # backfill a missed day
"""

import sys, os, json, datetime, urllib.request, urllib.parse, re, fcntl, contextlib
from pathlib import Path
import sys as _sys
_sys.path.insert(0, "/Users/amre/Projects/sol-skills-bundle/scripts/content-pipeline")
from devto_key import load_devto_key
from _minimax_helper import minimax_generate, load_minimax_key_candidates
from ai_filter import is_ai_title
from text_utils import split_sentences, strip_hn_markers, clamp_title
from post_identity import (
    normalise_url,
    published_source_urls_excluding_paths,
    claim_stories,
    read_claims,
    release_claims,
    sources_section_urls,
)

SITE_DIR = Path("/Users/amre/Projects/thesolai.github.io")
BUNDLE_DIR = Path("/Users/amre/Projects/sol-skills-bundle/scripts/content-pipeline")
DEVTO_API_KEY = load_devto_key()

# Serialises runs so two schedulers (the MiniMax cron task and the
# ai.sol.quick-hits launchd agent) cannot both write the same day's post.
LOCK_FILE = Path("/tmp/sol-quick-hits.lock")

# The working note the model writes under each item to name its source.
# It is stripped from the published body and turned into a Sources section.
SOURCE_LINE_RE = re.compile(r"^[ \t]*SOURCE:[ \t]*(\S+)[ \t]*$", re.MULTILINE)

# The HN Algolia index supports a real date filter, which is what makes a
# backfill possible: a `--date` run must see that day's stories, not
# whatever is on the front page now.
HN_ALGOLIA = "https://hn.algolia.com/api/v1/search"


def today_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def parse_target_date(argv: list[str]) -> datetime.datetime:
    """The date this run publishes for.

    Defaults to today. `--date YYYY-MM-DD` publishes for a specific day,
    which is how a day the scheduler missed gets backfilled.

    WHY (2026-11-10): the 2026-11-09 episode never ran. The MiniMax cron
    task needs the desktop app awake at 08:00; the app started at 19:10
    that evening, so the Monday slot was silently skipped and the day left
    a hole in the series. A scheduler that only ever runs "now" cannot
    recover a missed day — see the same lesson in OPERATIONS.md, "Three
    days of Sol's Take went missing because the Mac was asleep".
    """
    if "--date" in argv:
        idx = argv.index("--date")
        if idx + 1 >= len(argv):
            raise SystemExit("--date needs a YYYY-MM-DD argument")
        raw = argv[idx + 1]
        try:
            d = datetime.datetime.strptime(raw, "%Y-%m-%d")
        except ValueError:
            raise SystemExit(f"could not parse --date {raw!r}; expected YYYY-MM-DD")
        return d.replace(tzinfo=datetime.timezone.utc)
    return today_utc()


def date_fields(d: datetime.datetime) -> tuple[str, str, str]:
    """(date_str, post_date, day_str) for a target date."""
    return d.strftime("%Y-%m-%d"), d.strftime("%B %d, %Y"), d.strftime("%A")


def post_path_for(date_str: str, day_str: str) -> Path:
    """Where the post for a given day lives."""
    return SITE_DIR / "_posts" / f"{date_str}-quick-hits-{day_str.lower()}.md"


@contextlib.contextmanager
def run_lock():
    """Serialise runs so two schedulers cannot both write the same post."""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("[quick-hits] Another run holds the lock — skipping")
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def llm_generate(prompt: str, system: str = "", max_tokens: int = 6000) -> str | None:
    """Generate text via MiniMax, or return None for template fallback."""
    keys = load_minimax_key_candidates()
    if not keys:
        print("[llm] No MiniMax key available — using template fallback")
        return None
    try:
        # The helper tries every known key and every known model in turn, and
        # prints the reason when all of them fail — so a degraded run is
        # visible in this log instead of silently shipping a template post.
        result = minimax_generate(prompt, api_key=keys[0], max_tokens=max_tokens, system=system)

        if result:
            print(f"[llm] ✅ MiniMax ({len(result.split())} words)")
            return result
        print("[llm] MiniMax returned nothing — using template fallback")
    except Exception as e:
        print(f"[llm] MiniMax error: {e}")
    return None


# The AI keyword filter lives in ai_filter.py and is shared with
# run-daily.py. It matches on WORD BOUNDARIES — the old inline version
# here used a plain substring test, so "ai" matched inside "Rail" and
# "Airplanes". On 2026-11-09 and 2026-11-10 that shipped "Payments
# Canada launches Canada's Real-Time Rail" as an AI story in two
# consecutive briefs. See ai_filter.py for the full account and
# test_ai_filter.py for the regression tests.


def fetch_hn_ai_stories(limit=30, target=None):
    """Fetch HN top stories, return AI-filtered list.

    `target` is the day this run publishes for. For today it reads the live
    front page (the proven path). For a BACKFILL it reads the Algolia index
    for that day's window instead — the front page only ever shows what is
    hot now, so a backfilled episode written from it would describe the
    wrong day's news.
    """
    if target < today_utc() - datetime.timedelta(hours=36):
        return _fetch_hn_ai_stories_for_date(target)

    try:
        req = urllib.request.Request(
            "https://hacker-news.firebaseio.com/v0/topstories.json",
            headers={"User-Agent": "SolAI/1.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            ids = json.loads(r.read())
    except Exception as e:
        print(f"[hn] Failed to fetch HN stories: {e}")
        return []

    stories = []
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=36)
    cutoff_ts = cutoff.timestamp()

    for story_id in ids[:limit]:
        try:
            req = urllib.request.Request(
                f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json",
                headers={"User-Agent": "SolAI/1.0"}
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                story = json.loads(r.read())
            if not story or not story.get("title"):
                continue
            # Only recent-ish stories
            time = story.get("time", 0)
            if time and time < cutoff_ts:
                continue
            if not is_ai_title(story["title"]):
                continue
            stories.append({
                "id": story_id,
                "title": strip_hn_markers(story["title"]),
                "url": story.get("url", f"https://news.ycombinator.com/item?id={story_id}"),
                "score": story.get("score", 0),
                "comments": story.get("descendants", 0),
                "by": story.get("by", ""),
            })
        except Exception:
            pass
    stories.sort(key=lambda s: s["score"] + s["comments"] * 0.5, reverse=True)
    return stories[:8]


def _fetch_hn_ai_stories_for_date(target, limit=30):
    """HN stories from one specific day, via the Algolia index.

    WHY Algolia (2026-11-10): a backfilled episode has to be written from
    the news of the day it is dated for. The Firebase top-stories list only
    ever returns what is trending NOW, so it cannot answer "what was big on
    Nov 9" — a backfill built on it would be dated Nov 9 and describe Nov
    10. The Algolia index supports a real time filter, which is what makes
    the date honest.
    """
    start = int(target.timestamp())
    end = int((target + datetime.timedelta(days=1)).timestamp())

    try:
        params = {
            "tags": "story",
            "numericFilters": f"created_at_i>{start},created_at_i<{end}",
            "hitsPerPage": str(limit),
        }
        url = HN_ALGOLIA + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "SolAI/1.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())
    except Exception as e:
        print(f"[hn] Failed to fetch HN stories for {target:%Y-%m-%d}: {e}")
        return []

    stories = []
    for hit in data.get("hits", []):
        title_text = hit.get("title") or ""
        if not title_text:
            continue
        if not is_ai_title(title_text):
            continue
        stories.append({
            "id": hit.get("objectID", ""),
            "title": title_text,
            "url": hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
            "score": hit.get("points", 0) or 0,
            "comments": hit.get("num_comments", 0) or 0,
            "by": hit.get("author", ""),
        })

    # The Algolia `search` endpoint already ranks by points, but the daily
    # path ranks by score + comments*0.5. Use the same ordering so a
    # backfilled episode is curated the same way a live one is.
    stories.sort(key=lambda s: s["score"] + s["comments"] * 0.5, reverse=True)
    return stories[:8]


def fetch_devto_articles(limit=10, target=None):
    """Fetch trending dev.to articles tagged 'AI'.

    For a BACKFILL it fetches the articles published on that specific day
    instead of the current trending set — `top=1` only ever means "trending
    now", so a backfilled episode would otherwise cite today's articles
    under yesterday's date.
    """
    try:
        if target is not None and target < today_utc() - datetime.timedelta(hours=36):
            start = target.strftime("%Y-%m-%dT00:00:00Z")
            end = (target + datetime.timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")
            url = (
                "https://dev.to/api/articles?tag=ai&per_page=%d"
                "&published_after=%s&published_before=%s"
            ) % (limit, start, end)
        else:
            url = f"https://dev.to/api/articles?tag=ai&per_page={limit}&top=1"
        req = urllib.request.Request(url, headers={"User-Agent": "SolAI/1.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            articles = json.loads(r.read())
        return [
            {
                "title": a["title"],
                "url": a["url"],
                "reactions": a.get("public_reactions_count", 0),
                "comments": a.get("comments_count", 0),
                "read_time": a.get("reading_time_minutes", 1),
                "user": a.get("user", {}).get("name", "unknown"),
            }
            for a in articles if a.get("title")
        ]
    except Exception as e:
        print(f"[devto] Failed to fetch dev.to articles: {e}")
        return []


# A line that is nothing but a signature: optional dashes/markdown, the name,
# optional "AI", optional markdown. "—Sol", "-- Sol" and "**Sol**" all match;
# a sentence that merely ends in the word "Sol" does not, because the name has
# to be alone on its line.
_TRAILING_BYLINE = re.compile(
    r"^\s*(?:[-—–_*]{0,4}\s*)?Sol(?:\s+AI)?\s*(?:[-—–_*]{0,4})\s*$",
    re.IGNORECASE,
)


def strip_trailing_byline(body: str) -> str:
    """Remove a model-added trailing byline from the body."""
    if not body:
        return body
    lines = body.rstrip().split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and _TRAILING_BYLINE.match(lines[-1]):
        lines.pop()
    return "\n".join(lines).rstrip()


# ── Word budget ────────────────────────────────────────────────────────────

# The prompt asks for ~200 words. Published bodies run 177-243 words
# against that brief, so the model holds the range on its own today — but a
# prompt asks, it does not constrain, and MiniMax-M2.7 writes ~60% over the
# brief whatever the wording (see text_utils.trim_to_word_budget). The
# ceiling and floor are therefore enforced in code.
WORD_LIMIT = 250
WORD_FLOOR = 150

# A Quick Hits body is three items, each shaped like:
#
#     **Headline**
#     **What happened:** ...
#     **Why it matters:** ...
#
# and sometimes separated by `---` dividers. The item boundaries are what
# matter, NOT markdown headings — `trim_to_word_budget()` classifies the
# first block as a title and the rest as prose, which for this format
# would cut across an item and can gut the third story entirely.
#
# So the budget is enforced per item instead: each of the three items keeps
# its headline and its "Why it matters" line, and only the sentences inside
# the two body paragraphs are eligible for cutting.
_ITEM_HEADLINE = re.compile(r"^\*\*[^*].*\*\*\s*$")
_ITEM_LABEL = re.compile(r"^\*\*(What happened|Why it matters):\*\*\s*(.*)$")


def enforce_word_budget(body: str, limit: int = WORD_LIMIT,
                        floor: int = WORD_FLOOR) -> str:
    """Trim a Quick Hits body to a word ceiling without losing a story.

    WHY (2026-11-10): the brief asks for ~200 words and the prompt alone
    does not hold a word budget. The structure-aware trimmer in text_utils
    assumes a titled, sectioned post — its block classifier treats the
    first block as a title and the rest as prose, which for this format
    would cut across an item's boundary and can remove the third story
    entirely.

    So the trimming happens at the line level instead, which is how the
    model actually writes this post: each item is a bold headline line
    followed by a "**What happened:**" line and a "**Why it matters:**"
    line, optionally separated from the next item by a `---` divider.

    Only the text after a label is eligible for cutting, and only from the
    last item backwards — the third story is the one a reader would miss.
    A headline is never cut, and a label line always keeps at least one
    sentence, so no item is ever left empty.

    A body that is already within the limit is returned unchanged.
    """
    total = len(body.split())
    if total <= limit:
        return body

    lines = body.split("\n")

    # The eligible lines are the label lines. Work from the end of the body
    # backwards so the LAST item gives up its words first.
    eligible = [i for i, line in enumerate(lines) if _ITEM_LABEL.match(line.strip())]
    if not eligible:
        # Not the expected shape — leave it alone rather than mangle it.
        return body

    saved = 0
    for idx in reversed(eligible):
        if total - saved <= limit:
            break
        line = lines[idx]
        stripped = line.strip()
        m = _ITEM_LABEL.match(stripped)
        label, text = m.group(1), m.group(2)
        # Everything before the text — the bold label and its colon. The
        # label is re-joined with a space so the result still reads
        # "**What happened:** ..." and not "**What happened:**...".
        prefix = stripped[: -len(text)].rstrip()

        sentences = split_sentences(text)
        if len(sentences) < 2:
            # A single sentence — there is nothing to cut without emptying
            # the label.
            continue

        while len(sentences) > 1 and total - saved > limit:
            dropped = len(sentences[-1].split())
            if total - saved - dropped < floor:
                # Cutting this would starve the post. A slightly long post
                # beats a short one.
                break
            sentences.pop()
            saved += dropped

        if saved:
            lines[idx] = prefix + " " + " ".join(sentences)

    return "\n".join(lines).strip()


def existing_post_is_degraded(path: Path) -> bool:
    """True when the published post is the deterministic template fallback.

    A degraded post is worth regenerating; a real one is not.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    # The template fallback prints "**1. Title**" per item and a link line.
    if re.search(r"^\*\*\d+\.\s", text, re.MULTILINE):
        return True
    return False


def candidate_pool(hn_stories, devto_articles, exclude_urls=None):
    """The stories the model may choose from — the top 5 by engagement.

    Split out of `build_quick_hits()` because the caller has to claim this
    pool BEFORE the model is asked to choose: the model picks three out of
    five, so until it answers nobody knows which three will be used, and a
    generator running at the same moment could take one of them.

    Stories in `exclude_urls` — already published, or already claimed
    today by another series — are dropped.
    """
    exclude = {normalise_url(u) for u in (exclude_urls or set())}

    all_items = []
    for s in hn_stories:
        all_items.append({
            "source": "HN",
            "title": s["title"],
            "url": s["url"],
            "engagement": s["score"] + s["comments"] * 0.5,
            "score": s["score"],
            "comments": s["comments"],
        })
    for d in devto_articles:
        all_items.append({
            "source": "dev.to",
            "title": d["title"],
            "url": d["url"],
            "engagement": d["reactions"] + d["comments"] * 0.5,
            "score": d["reactions"],
            "comments": d["comments"],
        })

    if exclude:
        before = len(all_items)
        all_items = [i for i in all_items if normalise_url(i["url"]) not in exclude]
        if before != len(all_items):
            print(f"[quick-hits] Dropped {before - len(all_items)} already-covered stories")

    all_items.sort(key=lambda x: x["engagement"], reverse=True)
    return all_items[:5]


def build_quick_hits(hn_stories, devto_articles, post_date: str = "", exclude_urls=None):
    """Use LLM to pick best 3 stories and write the briefing.

    `exclude_urls` holds the source URLs that are already published or
    claimed today. They are dropped from the candidate pool BEFORE the
    model chooses, so the briefing cannot lead with a story another series
    is already covering.

    The model is asked to name the URL it used for each item. That is not
    decoration: this series' posts used to cite NO source at all, which
    made them invisible to the archive dedupe — and to every later run that
    wanted to know what had already been covered. The URLs are turned into
    a "## Sources" section, which the archive already knows how to read.
    """

    if not hn_stories and not devto_articles:
        print("[quick-hits] No stories fetched — skipping.")
        return None

    candidates = candidate_pool(hn_stories, devto_articles, exclude_urls)

    if not candidates:
        print("[quick-hits] Every candidate story is already covered — skipping.")
        return None

    candidates_txt = "\n".join(
        f"- [{item['source']}] {item['title']}\n  URL: {item['url']}\n  Score: {item['score']} / Comments: {item['comments']}"
        for item in candidates
    )

    prompt = f"""You are Sol AI. Write a 'Quick Hits' AI briefing post — 3 items, conversational tone, direct, slightly wry.

Pick the 3 most interesting items from the candidates below. Write each as:
**What happened:** [1-2 sentences]
**Why it matters:** [1-2 sentences]

Keep it punchy. No intro paragraph needed — just jump straight in. Total post should be ~200 words.

After the "Why it matters" paragraph of each item, add one line containing
exactly SOURCE: followed by that item's URL, on its own line. Nothing else
on that line. These lines are removed before the post is published and are
used to credit the sources.

Date: {post_date}
Author: Sol

Candidate stories:
{candidates_txt}
"""
    system = "You are Sol AI. Direct, honest, slightly wry. No corporate speak. No fluff."
    body = llm_generate(prompt, system, max_tokens=6000)
    if not body:
        # Template fallback — use top 3 items directly
        top3 = candidates[:3]
        lines = []
        for i, item in enumerate(top3, 1):
            lines.append(f"**{i}. {item['title']}**")
            lines.append(f"[{item['source']}]({item['url']}) — {item['score']} score")
            lines.append("")
        return _with_sources("\n".join(lines), top3)

    # Pull the model's own source lines out of the body. They are working
    # notes, not prose, so they must not reach the published post.
    used = []
    for m in SOURCE_LINE_RE.finditer(body):
        url = m.group(1).strip()
        if url and url not in used:
            used.append(url)
    body = SOURCE_LINE_RE.sub("", body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()

    # The model sometimes signs the post itself ("—Sol", "-- Sol", "**Sol**").
    # The layout already renders "By Sol" in the header, so a trailing
    # signature is a duplicate byline — the same defect sols-take.py and
    # run-daily.py already strip. Only a line that is nothing but the
    # signature is removed, so prose that merely ends in the word is safe.
    stripped = strip_trailing_byline(body)
    if stripped != body.strip():
        print("[quick-hits] Stripped trailing byline from the body")
        body = stripped

    # Enforce the word budget in code. The prompt alone does not hold it,
    # and a briefing that runs long is the symptom of the model padding
    # each of the three items.
    trimmed = enforce_word_budget(body)
    if trimmed != body.strip():
        print(f"[quick-hits] Trimmed {len(body.split())} -> {len(trimmed.split())} words")
        body = trimmed

    # Credit the sources. This is what makes the briefing visible to the
    # archive dedupe: without a citation, no later run could tell that this
    # story had already been covered.
    picked = [c for c in candidates if c["url"] in used] or candidates[:3]
    return _with_sources(body, picked)


def _with_sources(body: str, items) -> str:
    """Append a "## Sources" section crediting the stories used.

    The section is appended AFTER the word budget is enforced, so the
    briefing's prose still fits its budget.
    """
    items = [i for i in (items or []) if i.get("url")]
    if not items:
        return body
    lines = ["## Sources", ""]
    for item in items:
        lines.append(f"- [{item['title']}]({item['url']})")
    return f"{body.rstrip()}\n\n" + "\n".join(lines)


def create_post(body: str, d: datetime.datetime) -> Path:
    """Write the Jekyll post to _posts."""
    date_str, post_date, day_str = date_fields(d)
    slug = f"{date_str}-quick-hits-{day_str.lower()}"
    filename = f"{slug}.md"
    post_path = SITE_DIR / "_posts" / filename

    # The title is published as the page <title> and the Open Graph
    # card headline, so it is capped here rather than asked for in a
    # prompt. See text_utils.clamp_title().
    quick_hits_title = clamp_title(
        f"Quick Hits: {day_str}'s AI Briefing — {post_date}"
    )

    frontmatter = f"""---
layout: post
title: "{quick_hits_title}"
date: {date_str} 08:00:00 +0000
tags: [ai, briefing, quick-hits, sol]
author: Sol
description: "Three AI stories worth knowing about on {post_date}."
image: /images/sol-avatar.png
hero_image: /images/sol-avatar.png
---

{body}
"""
    post_path.write_text(frontmatter, encoding="utf-8")
    print(f"[quick-hits] Post written: {post_path.name}")
    return post_path


def main(argv: list[str] | None = None):
    argv = list(sys.argv[1:] if argv is None else argv)
    target = parse_target_date(argv)
    date_str, post_date, day_str = date_fields(target)

    # A backfill for a day the scheduler missed is always allowed, whatever
    # the weekday. A normal run only happens on a weekday.
    explicit_date = "--date" in argv
    if not explicit_date and target.weekday() >= 5:
        print(f"[quick-hits] Not a weekday ({day_str}) — skipping.")
        return

    print(f"[quick-hits] Running for {day_str}, {post_date}")

    with run_lock() as got_lock:
        if not got_lock:
            return

        post_path = post_path_for(date_str, day_str)

        # Idempotency: check for the existing output BEFORE doing the
        # expensive work (the LLM call).
        #
        # WHY (2026-11-10): this series is driven by BOTH a Mavis cron task
        # and a launchd agent, so both can fire on the same weekday. The
        # second run would otherwise regenerate the body and overwrite the
        # first — same URL, different prose.
        #
        # A degraded (template-fallback) post is the one case worth
        # regenerating, because it is not real content at all.
        #
        # `--force` regenerates regardless. It is how a post published with
        # a defect gets fixed: on 2026-11-09 and 2026-11-10 both briefs
        # carried "Payments Canada launches Canada's Real-Time Rail",
        # which the AI filter let through because "Rail" contains "ai".
        if post_path.exists() and not existing_post_is_degraded(post_path) \
                and "--force" not in argv:
            print(f"[quick-hits] Already published: {post_path.name} — skipping")
            return

        hn_stories = fetch_hn_ai_stories(limit=30, target=target)
        print(f"[quick-hits] Fetched {len(hn_stories)} HN AI stories")

        devto_articles = fetch_devto_articles(limit=10, target=target)
        print(f"[quick-hits] Fetched {len(devto_articles)} dev.to articles")

        # Stories that are already published, or that another series has
        # already claimed today, are not candidates.
        #
        # WHY (2026-11-18): this series had no dedupe at all, and its posts
        # cited no source, so nothing it covered was visible to anyone else.
        # On 2026-11-18 the briefing, the midweek digest and the regional
        # trio all covered "GPT-6.2 Astra and Sol" — five posts, one story.
        #
        # The day's own post is excluded so a `--force` regeneration can
        # still pick the same stories.
        exclude = published_source_urls_excluding_paths(SITE_DIR / "_posts", [post_path])
        for url, rec in read_claims(date_str).items():
            if rec.get("series") != "quick-hits":
                exclude.add(url)
        print(f"[quick-hits] {len(exclude)} source URLs already covered")

        # Claim the candidate pool BEFORE the model chooses.
        #
        # WHY (2026-11-18): the model picks three out of five, so until it
        # answers nobody knows which three will be used. Claiming the pool
        # first means a generator running at the same moment cannot take
        # one of them; the two it does not use are released afterwards.
        pool = candidate_pool(hn_stories, devto_articles, exclude)
        if pool:
            claim_stories(
                "quick-hits",
                pool,
                len(pool),
                SITE_DIR / "_posts",
                day=date_str,
            )
            for c in pool:
                print(f"[claims] quick-hits reserved: {c['title'][:70]}")

        body = build_quick_hits(hn_stories, devto_articles, post_date=post_date, exclude_urls=exclude)
        if not body or body.startswith("[LLM") or body.startswith("[error"):
            print(f"[quick-hits] Failed to generate content: {body}")
            release_claims("quick-hits", [], date_str)
            return

        # Claim the stories this post actually used, and release the rest of
        # the pool. The URLs are read back out of the Sources section, so
        # the claim matches the post exactly.
        used = [
            u for u in sources_section_urls(body)
            if normalise_url(u) not in exclude
        ]
        if used:
            for u in used:
                print(f"[claims] quick-hits claimed: {u[:70]}")
        released = release_claims("quick-hits", used, date_str)
        if released:
            print(f"[claims] quick-hits released {released} unused reservations")

        post_path = create_post(body, target)

        # Auto-commit and push
        try:
            cwd = SITE_DIR
            from site_git import publish
            sha = publish(f"Quick Hits: {day_str} AI briefing", cwd=cwd)
            print(f"[quick-hits] Committed and pushed to origin/main ({sha[:12] if sha else 'nothing new'}).")
        except Exception as e:
            print(f"[quick-hits] Git push failed: {e}")


if __name__ == "__main__":
    main()
