#!/usr/bin/env python3
"""
Regression tests for headline hygiene — the Hacker News chrome and the
title-length ceiling.

WHY THIS EXISTS
---------------
The daily SEO audit reported "✅ All checks passed!" on a site that was
publishing broken headlines. Two defects reached the live site because every
generator copied its source headline straight into the front matter:

  1. Hacker News chrome. The HN/Algolia feed annotates a story's title with
     the kind of link it points at — "[pdf]", "[video]", "[image]", "[text]",
     "[url]", "[audio]" — and prefixes a user-submitted post with "Ask HN:",
     "Show HN:", "Tell HN:", "Launch HN:". None of that is part of the
     headline, and all of it was being published: 11 markers across 7 posts
     in front matter, 3 "Launch HN:" prefixes, and 9 posts whose body
     carried them too.

  2. Runaway title length. The front-matter `title:` became the rendered
     `<title>` and the Open Graph card headline. The Trending series cut its
     headline at a fixed 67 characters — mid-word, with a "..." — and then
     added a prefix and a full date, so every episode shipped a 75-103
     character title. 350 of 1411 published posts exceed 70 characters.

These tests pin both, plus the audit that is supposed to catch them.

Run it directly — no test framework needed:

    python3 scripts/content-pipeline/test_headline_hygiene.py

It is also pytest-compatible: every check is a `test_` function.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from text_utils import (  # noqa: E402
    clamp_title,
    find_hn_markers,
    strip_hn_markers,
    TITLE_MAX_LENGTH,
)

_spec = importlib.util.spec_from_file_location(
    "seo_health_module", Path(__file__).resolve().parent / "seo-health.py"
)
seo_health = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(seo_health)


# ── Hacker News chrome ─────────────────────────────────────────────────────

# The exact strings that reached the live site.
PUBLISHED_CHROME = [
    "There Will Come Soft Rains (1950) [pdf]",
    "Claude Code and Codex reverse engineered my 1994 Jeep's ECU over a "
    "$3 Arduino [video]",
    "A Privacy Analysis of Web and Mobile Conversational AI Agents [pdf]",
    "Launch HN: Momentic (YC W24) – Agents that catch bugs by using your app",
    "Ask HN: What's your current AI agentic setup?",
    "Show HN: A terminal that does the work",
    "Tell HN: Why we moved off the default model",
]


def test_every_published_chrome_shape_is_detected():
    """Each chrome shape the HN feed produces is found by the detector."""
    for value in PUBLISHED_CHROME:
        hits = find_hn_markers(value)
        assert hits, f"chrome not detected in {value!r}"


def test_a_clean_headline_carries_no_chrome():
    """A headline with no HN chrome is left alone."""
    clean = "There Will Come Soft Rains (1950)"
    assert find_hn_markers(clean) == []
    assert strip_hn_markers(clean) == clean


def test_the_marker_is_removed_and_the_headline_is_kept():
    """Only the annotation goes; the publisher's words stay."""
    assert strip_hn_markers("There Will Come Soft Rains (1950) [pdf]") == (
        "There Will Come Soft Rains (1950)"
    )
    assert strip_hn_markers(
        "Claude Code and Codex reverse engineered my 1994 Jeep's ECU over a "
        "$3 Arduino [video]"
    ) == (
        "Claude Code and Codex reverse engineered my 1994 Jeep's ECU over a "
        "$3 Arduino"
    )


def test_the_submission_prefix_is_removed():
    """The "Show HN:" / "Launch HN:" prefix is chrome too."""
    assert strip_hn_markers("Launch HN: Momentic catches bugs by using your app") == (
        "Momentic catches bugs by using your app"
    )
    assert strip_hn_markers("Ask HN: What's your current AI agentic setup?") == (
        "What's your current AI agentic setup?"
    )


def test_a_prefix_inside_a_sentence_is_not_chrome():
    """"Ask HN:" in the middle of prose is prose, not a submission prefix."""
    text = "We asked on Ask HN: whether the model would hold up."
    assert find_hn_markers(text) == []
    assert strip_hn_markers(text) == text


def test_stripping_changes_nothing_but_the_chrome():
    """The strip is surgical — whitespace and punctuation are untouched.

    A tidy-up that also collapsed a double space would make the audit's
    "does this string contain a marker?" test impossible to trust.
    """
    text = "Two years ago today, ... over faith, ..."
    assert strip_hn_markers(text) == text


def test_the_marker_is_removed_where_a_description_puts_it():
    """The description puts the marker in the middle, not at the end."""
    description = (
        "Claude Code and Codex reverse engineered my 1994 Jeep's ECU over a "
        "$3 Arduino [video] — daily AI analysis from EU."
    )
    assert find_hn_markers(description) == ["[video]"]
    assert "[video]" not in strip_hn_markers(description)


# ── The title-length ceiling ───────────────────────────────────────────────


def test_a_short_title_is_returned_unchanged():
    """A title inside the ceiling comes back byte-identical."""
    title = "Sol's Take: Sunday — November 22, 2026"
    assert clamp_title(title) == title


def test_the_ceiling_is_a_ceiling():
    """The clamp never returns a title over the limit."""
    long_title = (
        "Restricting AI agents in mobile operating systems platforms from "
        "causalworld"
    )
    clamped = clamp_title(long_title)
    assert len(clamped) <= TITLE_MAX_LENGTH, (
        f"clamp returned {len(clamped)} characters against a "
        f"{TITLE_MAX_LENGTH} ceiling"
    )


def test_the_cut_is_never_mid_word():
    """The old cut sliced at a fixed 67 characters and shipped "from ca...".

    This is the defect that made the Trending series unreadable.
    """
    long_title = (
        "Restricting AI agents in mobile operating systems platforms from "
        "causalworld"
    )
    clamped = clamp_title(long_title)
    assert not clamped.endswith("ca…"), f"cut mid-word: {clamped!r}"
    assert " causalworld" not in clamped


def test_a_truncated_title_says_so():
    """A shortened title ends in an ellipsis."""
    long_title = "Week in the Life: On Email, Memory, and the Things That Feel Like Living"
    assert clamp_title(long_title).endswith("…")


def test_a_truncated_title_does_not_end_on_a_function_word():
    """"… Into a Very" reads as broken copy, not as a shortened title."""
    title = (
        "UK AI Weekly: A £3 Arduino Turned Claude and Codex Into a Very "
        "Unofficial Mechanic"
    )
    clamped = clamp_title(title)
    tail = clamped.rstrip("…").split()[-1].lower().strip(".,;:!?")
    assert tail not in {
        "a", "an", "the", "and", "or", "into", "to", "of", "with", "very",
        "into", "is", "was", "for", "in", "on", "at", "that", "which",
    }, f"clamped title ends on a function word: {clamped!r}"


def test_every_published_title_fits_after_a_clamp():
    """The ceiling holds for the whole corpus, not for a sample.

    This is the invariant the audit depends on: any title the generators
    write, clamped, is inside the limit.
    """
    posts_dir = seo_health.SITE_DIR / "_posts"
    if not posts_dir.exists():
        return  # site not checked out — nothing to assert against
    over = []
    for post in sorted(posts_dir.glob("*.md")):
        text = post.read_text(encoding="utf-8", errors="replace")
        front_matter, _ = seo_health.split_front_matter(text)
        title = seo_health.field(front_matter, "title")
        if not title:
            continue
        if len(clamp_title(title)) > TITLE_MAX_LENGTH:
            over.append(post.name)
    assert not over, f"{len(over)} title(s) survive a clamp over the limit: {over[:5]}"


# ── The audit check ────────────────────────────────────────────────────────


def test_the_audit_flags_hacker_news_chrome():
    """A post with chrome in its title is a failure, not a note."""
    posts_dir = seo_health.SITE_DIR / "_posts"
    if not posts_dir.exists():
        return
    posts = [p for p in sorted(posts_dir.glob("*.md")) if seo_health.is_renderable_post(p)]
    issues, _ = seo_health.check_headline_hygiene(posts)
    assert not issues, (
        "the published site still carries Hacker News chrome: "
        + "; ".join(issues[:5])
    )


def test_the_audit_reports_legacy_over_length_titles_as_residue():
    """An old over-length title is a note, not a failure.

    Before the ceiling was enforced the generators did not clamp, so those
    posts are history. Failing on them would make the audit permanently red
    and teach everyone to ignore it.
    """
    posts_dir = seo_health.SITE_DIR / "_posts"
    if not posts_dir.exists():
        return
    posts = [p for p in sorted(posts_dir.glob("*.md")) if seo_health.is_renderable_post(p)]
    issues, notes = seo_health.check_headline_hygiene(posts)
    assert not issues, f"unexpected headline failures: {issues[:3]}"
    assert any("before" in n for n in notes), (
        f"expected a residue note about pre-fix titles, got {notes}"
    )


def test_a_post_published_after_the_fix_date_fails_on_a_long_title():
    """The gate is armed: a new post over the ceiling fails the audit."""
    posts_dir = seo_health.SITE_DIR / "_posts"
    if not posts_dir.exists():
        return
    posts = [p for p in sorted(posts_dir.glob("*.md")) if seo_health.is_renderable_post(p)]
    long_title = (
        "A Headline That Runs Well Past The Ceiling And Should Never Have "
        "Shipped Like This"
    )
    assert len(long_title) > TITLE_MAX_LENGTH

    # A stand-in post: the check reads the file, so the name carries the
    # date and the body carries the title.
    stand_in = seo_health.SITE_DIR / "_posts" / (
        f"{seo_health.TITLE_LENGTH_FIX_DATE}-headline-hygiene-stand-in.md"
    )
    if stand_in.exists():
        return  # a real file already occupies the name — do not touch it
    try:
        stand_in.write_text(
            "---\n"
            "layout: post\n"
            f'title: "{long_title}"\n'
            "description: \"A stand-in used by the headline hygiene test.\"\n"
            "date: 2026-11-23\n"
            "---\n\n"
            "Stand-in.\n",
            encoding="utf-8",
        )
        issues, _ = seo_health.check_headline_hygiene([stand_in])
        assert any("ceiling" in i for i in issues), (
            f"a post published on {seo_health.TITLE_LENGTH_FIX_DATE} with a "
            f"{len(long_title)}-character title did not fail the audit"
        )
    finally:
        if stand_in.exists():
            stand_in.unlink()


def _run():
    checks = [
        (name, fn)
        for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    failed = 0
    for name, fn in checks:
        try:
            fn()
        except AssertionError as e:
            failed += 1
            print(f"FAIL {name}: {e}")
        except Exception as e:  # pragma: no cover
            failed += 1
            print(f"ERROR {name}: {type(e).__name__}: {e}")
        else:
            print(f"ok   {name}")
    print(f"\n{len(checks) - failed}/{len(checks)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run())
