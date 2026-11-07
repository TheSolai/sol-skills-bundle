#!/usr/bin/env python3
"""
Sol AI — Sol's Take
Daily hot AI opinion, 150-200 words. Just the take.
Runs every day at 9am UK time.

Cronsetup: launchd StartCalendarInterval 09:00 daily
Or: 0 8 * * * python3 scripts/content-pipeline/sols-take.py >> logs/sol-take.log 2>&1
"""

from __future__ import annotations

# PEP 604 unions (`str | None`) are evaluated at def-time on Python < 3.10,
# which breaks this module under /usr/bin/python3 (3.9.6). The future import
# above makes them strings, so the module imports on every interpreter the
# pipeline might use. `_minimax_helper.py` has the same guard for the same
# reason.

import sys, os, json, datetime, urllib.request, re
from pathlib import Path
import contextlib, fcntl
import sys as _sys
_sys.path.insert(0, "/Users/amre/Projects/sol-skills-bundle/scripts/content-pipeline")
from devto_key import load_devto_key
from _minimax_helper import minimax_generate, load_minimax_key_candidates, looks_contaminated

# Sentence-aware trimming, shared with weekly-roundup.py and midweek-news.py so
# the generators cannot drift apart — see text_utils.py.
from text_utils import (
    ends_cleanly as _ends_cleanly,
    split_sentences as _split_sentences,
    trim_to_words as _trim_to_words,
)

SITE_DIR = Path("/Users/amre/Projects/thesolai.github.io")
BUNDLE_DIR = Path("/Users/amre/Projects/sol-skills-bundle/scripts/content-pipeline")
DEVTO_API_KEY = load_devto_key()

# Only one run at a time. The Mavis cron fires this script, and a launchd
# fallback can fire it too; without a lock both could pass the
# "does today's post exist?" check before either wrote, and the second would
# overwrite the first with a different body.
LOCK_FILE = Path("/tmp/sol-sols-take.lock")


def today_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def date_fields(d: datetime.datetime) -> tuple[str, str, str]:
    """(date_str, post_date, day_str) for a target date."""
    return d.strftime("%Y-%m-%d"), d.strftime("%B %d, %Y"), d.strftime("%A")


def llm_generate(prompt: str, system: str = "", max_tokens: int = 6000) -> str | None:
    """Generate text via MiniMax, or return None when there is no usable text."""
    keys = load_minimax_key_candidates()
    if not keys:
        print("[llm] No MiniMax key available", file=sys.stderr)
        return None
    try:
        # The helper tries every known key and every known model in turn, and
        # prints the reason when all of them fail — so a degraded run is
        # visible in this log instead of silently shipping a template post.
        result = minimax_generate(prompt, api_key=keys[0], max_tokens=max_tokens, system=system)

        if result:
            print(f"[llm] ✅ MiniMax ({len(result.split())} words)")
            return result
        print("[llm] MiniMax returned nothing usable", file=sys.stderr)
    except Exception as e:
        print(f"[llm] MiniMax error: {e}", file=sys.stderr)
    return None


TAKE_TEMPLATES = [
    "the_ai_hype_cycle",
    "ai_replacing_jobs",
    "the_agent_era",
    "open_vs_closed",
    "context_window_wars",
    "ai_creativity_debate",
    "regulation_reality",
    "ai_code_quality",
    "human_in_the_loop",
    "ai_deployment_realities",
    "the_ux_problem",
    "foundation_models",
    "ai_infrastructure",
    "prompt_engineering_ripoff",
    "synthetic_data",
    "ai_evaluation",
    "multimodal_hype",
    "ai_community",
]


def pick_topic(d: datetime.datetime) -> str:
    """Pick a topic using day-of-year for variety.

    The topic is derived from the date the post is FOR, not the date it is
    written, so a backfilled day gets the topic its rotation intended.
    """
    day_of_year = d.timetuple().tm_yday
    idx = day_of_year % len(TAKE_TEMPLATES)
    return TAKE_TEMPLATES[idx]


TOPIC_PROMPTS = {
    "the_ai_hype_cycle": "Write a hot take about the AI hype cycle. What's frustrating about how AI gets marketed right now?",
    "ai_replacing_jobs": "Write a hot take about AI replacing jobs. Be direct about what you actually think is happening vs. what's being claimed.",
    "the_agent_era": "Write a hot take about the AI agent era. Why most 'agents' are not what they claim to be.",
    "open_vs_closed": "Write a hot take about open-source vs. closed-source AI models. What's the real trade-off?",
    "context_window_wars": "Write a hot take about the context window size wars. Bigger isn't always better — here's why.",
    "ai_creativity_debate": "Write a hot take about AI and creativity. Can AI be creative? Does the question even matter?",
    "regulation_reality": "Write a hot take about AI regulation. Why most proposed regulation misses the point.",
    "ai_code_quality": "Write a hot take about AI-generated code quality. Why 'it works' isn't the same as 'it's good'.",
    "human_in_the_loop": "Write a hot take about keeping humans in the loop. When does it help and when is it theatre?",
    "ai_deployment_realities": "Write a hot take about deploying AI in production. Why the demo never matches reality.",
    "the_ux_problem": "Write a hot take about AI product UX. Why most AI tools are frustrating to actually use.",
    "foundation_models": "Write a hot take about foundation models. Are they actually as transformative as claimed?",
    "ai_infrastructure": "Write a hot take about AI infrastructure. What nobody talks about behind the flashy demos.",
    "prompt_engineering_ripoff": "Write a hot take about prompt engineering as a job title. Is it real or is it a coping mechanism?",
    "synthetic_data": "Write a hot take about synthetic training data. Are we training AI on AI outputs and does it matter?",
    "ai_evaluation": "Write a hot take about AI evaluation benchmarks. Why most benchmarks don't measure what matters.",
    "multimodal_hype": "Write a hot take about multimodal AI. Vision + language + audio — is it genuinely useful or just impressive demos?",
    "ai_community": "Write a hot take about the AI developer community. What's worth celebrating and what needs to stop.",
}


def post_path_for(date_str: str, day_str: str) -> Path:
    """Where the post for a given day lives."""
    return SITE_DIR / "_posts" / f"{date_str}-sols-take-{day_str.lower()}.md"


# The model sometimes answers with a heading and a byline instead of the
# article, e.g.
#
#     ## Sol's Take: AI's Greatest Trick Is Making Everything Sound Important
#     **November 02, 2026 | Sol**
#
# The layout already renders the post title as an <h1>, so a "## Sol's Take"
# heading duplicates it, and the byline is metadata that belongs in the front
# matter. This is the same defect `run-daily.py` fixes with
# `strip_metadata_echo()`: the prompt is a request, so the content has to hold
# whatever the model returns. Stripping happens here, where the content is
# known to be good.
#
# Only a leading heading/byline is removed. A "Sol's Take" phrase used in
# the prose of an article is left alone.
_ECHO_BYLINE = re.compile(
    r"^\s*\*{0,2}\s*(?:"
    r"\w+\s+\d{1,2},\s+\d{4}"          # November 02, 2026
    r"|\d{4}-\d{2}-\d{2}"              # 2026-11-02
    r")\s*(?:\|\s*Sol(?:\s+AI)?\s*)?\*{0,2}\s*$",
    re.IGNORECASE,
)

# A leading "Sol's Take" label, in the two shapes the model actually returns:
#
#     ## Sol's Take: The Context Window Size Wars Are a Fool's Errand
#     **Sol's Take: The Context Window Size Wars Are a Fool's Errand**
#     **Sol's Take**
#
# The first shape was already handled; the bold one was not, so it shipped as
# a stray bold line under the <h1> on 54 posts (2026-06-30 onward), including
# today's. The label is captured in two parts because the part after the colon
# is the model's own headline for the piece.
#
# IMPORTANT: the headline is NOT thrown away with the label. On most of those
# posts the front matter title is the generic "Sol's Take: <weekday>" and the
# label carries the only headline the article has. Stripping the whole line
# would leave a post with no title at all. So the label prefix is removed and
# the headline is kept, as a bold dek.
_ECHO_LABEL_HEADING = re.compile(
    r"^\s*#{1,6}\s*Sol['’]?s\s+Take\b\s*(?P<rest>.*?)\s*$",
    re.IGNORECASE,
)
_ECHO_LABEL_BOLD = re.compile(
    r"^\s*(?P<mark>\*{1,2}|_{1,2})\s*Sol['’]?s\s+Take\b"
    r"\s*(?P<rest>.*?)\s*(?P=mark)\s*$",
    re.IGNORECASE,
)

# A bare date on the label, e.g. "Sol's Take: October 30, 2026". A date is a
# byline, never a headline, and the front matter already carries the post's
# real date — so the label line goes.
_LABEL_DATE = re.compile(
    r"^\s*(?:\w+\s+\d{1,2},\s+\d{4}|\d{4}-\d{2}-\d{2})\s*$"
)


def _label_headline(match: re.Match) -> str | None:
    """The headline carried by a "Sol's Take" label, if it carries one.

    The label is "Sol's Take", "Sol's Take: <headline>" or "Sol's Take" plus a
    byline on the same line. The headline is whatever follows the colon.
    """
    rest = (match.groupdict().get("rest") or "").strip()
    if rest.startswith(":"):
        rest = rest[1:].strip()
    # Drop a trailing byline: "Some Headline | Sol" -> "Some Headline".
    rest = re.split(r"\s*\|\s*Sol(?:\s+AI)?\s*$", rest)[0].strip()
    return rest or None


def _strip_label(lines: list[str]) -> bool:
    """Handle a leading "Sol's Take" label.

    Returns True when the label was recognised (and so the caller should stop
    looking). The label is removed entirely when it adds nothing to the
    title; otherwise the label prefix is dropped and the headline it carries is
    kept as a bold dek, so no content is lost.
    """
    match = _ECHO_LABEL_BOLD.match(lines[0]) or _ECHO_LABEL_HEADING.match(lines[0])
    if not match:
        return False

    headline = _label_headline(match)
    if not headline:
        # Just the bare label — the <h1> already says "Sol's Take".
        lines.pop(0)
        return True

    if _LABEL_DATE.match(headline):
        # A date is a byline, not a headline. The front matter already carries
        # the post's date, so this line adds nothing.
        lines.pop(0)
        return True

    # Keep the headline, drop the redundant "Sol's Take:" prefix.
    lines[0] = f"**{headline}**"
    return True


def strip_metadata_echo(body: str) -> str:
    """Remove a leading echoed heading, label and/or byline from the body."""
    if not body:
        return body

    lines = body.split("\n")

    # The heading, the label and the byline can appear in either order, so
    # peel whichever one is currently first, then look again. Stopping after
    # three removals keeps this from eating real prose.
    for _ in range(3):
        while lines and not lines[0].strip():
            lines.pop(0)
        if not lines:
            break
        if _ECHO_BYLINE.match(lines[0]):
            lines.pop(0)
            continue
        if _strip_label(lines):
            # A kept headline is real content — stop peeling, so the prose
            # below it is never mistaken for more metadata.
            if lines and lines[0].startswith("**"):
                break
            continue
        break

    return "\n".join(lines).strip()


def existing_post_is_degraded(path: Path) -> bool:
    """True when the published post for today is the deterministic fallback.

    A degraded post is worth regenerating; a real one is not.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    if "More coming tomorrow" in text or "**The take:**" in text:
        return True
    # A post carrying the model's own notes is just as broken as a template
    # one, and is worth regenerating.
    return looks_contaminated(text)


@contextlib.contextmanager
def run_lock():
    """Serialise runs so two schedulers cannot both write today's post."""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("[sols-take] Another run holds the lock — skipping")
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def post_title(d: datetime.datetime) -> str:
    """The front matter title for a day's post.

    Shared by the generator and the body stripper so the stripper can tell a
    label that repeats the title (redundant) from a label carrying a real
    headline (worth keeping).
    """
    _, post_date, day_str = date_fields(d)
    return f"Sol's Take: {day_str} — {post_date}"


def generate_take(d: datetime.datetime) -> str:
    topic_key = pick_topic(d)
    topic_prompt = TOPIC_PROMPTS[topic_key]

    _, post_date, _ = date_fields(d)

    prompt = f"""Write a 'Sol's Take' — a single hot opinion piece, 150-200 words.

Rules:
- Start with the opinion immediately. No warm-up paragraphs.
- Do not write a title, heading, byline, or "Sol's Take" label of any kind.
- Be direct, slightly provocative, slightly wry.
- Ground it in something real — a specific experience, a pattern you've noticed, a claim that bugs you.
- End with one sharp sentence.
- No bullet points.
- No hedging ("it depends", "there are nuances"). Take the damn position.
- Conversational tone, like you're explaining to a smart friend over coffee.

Topic: {topic_prompt}

Date: {post_date}
Author: Sol
"""
    system = "You are Sol AI. Direct, opinionated, slightly wry. You have strong views and you state them. No corporate speak, no hedging. You're not cruel — but you're honest."
    body = llm_generate(prompt, system, max_tokens=600)
    if not body:
        # A template post looks fine on the page and hides the failure:
        # nothing on the published site says the generation broke. Publishing
        # nothing is better than publishing a placeholder — a missing post is
        # visible, a fake one is not.
        print(
            "[sols-take] DEGRADED: the model produced no usable text — "
            "refusing to publish a template post",
            file=sys.stderr,
        )
        raise SystemExit(2)

    if looks_contaminated(body):
        # The article may be fine with the model's notes stapled to the end,
        # but a reader sees the notes. Do not ship them.
        print(
            "[sols-take] DEGRADED: the generated text still carries the "
            "model's own notes — refusing to publish it",
            file=sys.stderr,
        )
        raise SystemExit(2)

    # Strip an echoed heading/byline/label before anything else. A label that
    # carries a real headline keeps that headline, as a bold dek.
    stripped = strip_metadata_echo(body)
    if stripped != body.strip():
        print("[sols-take] Stripped echoed heading/byline/label from the body")
        body = stripped

    # Enforce the word budget in code. The prompt alone does not hold it —
    # published posts have ranged from 187 to 248 words against a 150-200
    # brief. The trim cuts at sentence boundaries, never mid-sentence.
    trimmed = _trim_to_words(body.strip(), limit=200)
    if trimmed != body.strip():
        print(f"[sols-take] Trimmed {len(body.split())} -> {len(trimmed.split())} words")

    # Never publish text that stops mid-sentence.
    if not _ends_cleanly(trimmed):
        paragraphs = [p.strip() for p in trimmed.split("\n\n") if p.strip()]
        if paragraphs:
            sentences = _split_sentences(paragraphs[-1])
            if sentences and not _ends_cleanly(sentences[-1]):
                sentences = sentences[:-1]
            paragraphs[-1] = " ".join(sentences)
            trimmed = "\n\n".join(p for p in paragraphs if p).strip()

    return trimmed


def create_post(body: str, d: datetime.datetime) -> Path:
    date_str, post_date, day_str = date_fields(d)
    slug = f"{date_str}-sols-take-{day_str.lower()}"
    filename = f"{slug}.md"
    post_path = SITE_DIR / "_posts" / filename

    frontmatter = f"""---
layout: post
title: "{post_title(d)}"
date: {date_str} 09:00:00 +0000
tags: [ai, opinion, sols-take, sol]
author: Sol
description: "Sol's opinion on the AI landscape of {post_date}."
image: /images/sol-avatar.png
hero_image: /images/sol-avatar.png
---

{body}
"""
    post_path.write_text(frontmatter, encoding="utf-8")
    print(f"[sols-take] Post written: {post_path.name}")
    return post_path


def parse_target_date(argv: list[str]) -> datetime.datetime:
    """The date this run publishes for.

    Defaults to today. `--date YYYY-MM-DD` publishes for a specific day,
    which is how a day the scheduler missed gets backfilled.
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


def main(argv: list[str] | None = None):
    argv = list(sys.argv[1:] if argv is None else argv)
    target = parse_target_date(argv)
    date_str, post_date, day_str = date_fields(target)
    print(f"[sols-take] Running for {day_str}, {post_date}")

    with run_lock() as got_lock:
        if not got_lock:
            return

        post_path = post_path_for(date_str, day_str)

        # Idempotency: check for the existing output BEFORE doing the
        # expensive work (the LLM call).
        #
        # WHY (2026-11-04): this series is driven by BOTH a Mavis cron task
        # and a launchd agent. When both fired on the same day, the second run
        # generated a fresh body and overwrote the first — same URL, different
        # prose, and the site's own commit history shows two "Sol's Take"
        # commits in a single morning. The first body was the one that shipped.
        #
        # A degraded (template-fallback) post is the one case worth
        # regenerating, because it is not real content at all.
        if post_path.exists() and not existing_post_is_degraded(post_path):
            print(f"[sols-take] Already published: {post_path.name} — skipping")
            return

        body = generate_take(target)

        post_path = create_post(body, target)

        try:
            cwd = SITE_DIR
            # Stage ONLY this post. `publish()` without `paths` stages the
            # entire working tree, which is how one job's commit ends up
            # carrying another job's half-finished files.
            from site_git import publish
            sha = publish(f"Sol's Take: {day_str}", cwd=cwd, paths=[str(post_path)])
            print(f"[sols-take] Committed and pushed to origin/main ({sha[:12] if sha else 'nothing new'}).")
        except Exception as e:
            print(f"[sols-take] Git push failed: {e}")


if __name__ == "__main__":
    main()
