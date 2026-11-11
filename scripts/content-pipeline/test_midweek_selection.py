#!/usr/bin/env python3
"""
Regression tests for the mid-week digest's story selection (midweek-news.py).

WHY THIS EXISTS
---------------
Two defects, both found on 2026-11-11 while fixing the AI filter.

1. THE SAME STORY WAS LISTED TWICE.
   The Algolia index returns one row per Hacker News submission, and a
   story can be submitted more than once. "Claude Fable 5.2 and Mythos 5.2"
   appeared twice on 2026-11-11 — at 992 points and at 891 points, both
   pointing at anthropic.com/claude-fable-and-mythos-5-2. The digest listed
   it as stories 2 and 3, and asked the model to comment on "3 stories" when
   only 2 existed. The digest's own URL key was the fix: the same URL must
   select one story.

2. A REFRESH COULD NOT KEEP ITS OWN STORIES.
   `--refresh` rewrites today's post in place, so the post on disk already
   contributes its three URLs to the covered set. The dedupe then forbids
   the post's own stories, so refreshing a post whose commentary needed
   regenerating silently swapped the stories underneath it.

   The first attempt at fixing this subtracted the post's URLs from the
   shared covered set. That was wrong: it also freed any story that ANOTHER
   post covers, so the refresh republished "Claude Fable 5.2 and Mythos 5.2"
   and "Goldman Sachs is pulling Claude Code" — both already on the site.
   The fix excludes the post's FILE from the covered set instead, which
   leaves every other post's coverage intact.

Run it directly — no test framework needed:

    python3 scripts/content-pipeline/test_midweek_selection.py

It is also pytest-compatible: every check is a `test_` function.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import post_identity  # noqa: E402


def _load_midweek():
    """Import midweek-news.py without running main()."""
    path = Path(__file__).resolve().parent / "midweek-news.py"
    spec = importlib.util.spec_from_file_location("_midweek_probe", path)
    module = importlib.util.module_from_spec(spec)
    old_argv = sys.argv
    sys.argv = ["midweek-news.py"]
    try:
        spec.loader.exec_module(module)
    finally:
        sys.argv = old_argv
    return module


mw = _load_midweek()


def _story(title, url, score, comments):
    return {
        "title": title,
        "url": url,
        "score": score,
        "comments": comments,
        "time": 0,
    }


# ── Test 1: the same story submitted twice ────────────────────────────────

def test_the_same_story_is_not_selected_twice():
    """Two HN submissions of one story must produce one selected story."""
    stories = [
        _story("Claude Fable 5.2 and Mythos 5.2",
               "https://www.anthropic.com/claude-fable-and-mythos-5-2", 992, 8),
        _story("Claude Fable 5.2 and Mythos 5.2",
               "https://www.anthropic.com/claude-fable-and-mythos-5-2", 891, 1014),
        _story("A genuinely different AI story",
               "https://example.com/other", 100, 100),
    ]
    selected = mw.select_stories(stories)

    urls = [mw.normalise_url(s["url"]) for s in selected]
    assert len(urls) == len(set(urls)), (
        f"the same URL was selected twice: {urls}"
    )
    assert len(selected) == 2, f"expected 2 distinct stories, got {len(selected)}"


def test_the_higher_scoring_submission_wins():
    """When one story is submitted twice, the stronger submission is kept."""
    stories = [
        _story("Some AI story", "https://example.com/story", 100, 5),
        _story("Some AI story", "https://example.com/story", 900, 500),
    ]
    selected = mw.select_stories(stories)
    assert len(selected) == 1, selected
    assert selected[0]["score"] == 900, (
        f"expected the 900-point submission, got {selected[0]['score']}"
    )


def test_duplicate_urls_are_filtered_even_without_a_title_match():
    """The dedupe is on the URL, not the title.

    Two submissions with different titles but the same target URL are still
    one story.
    """
    stories = [
        _story("Claude Fable 5.2 and Mythos 5.2",
               "https://example.com/launch", 900, 100),
        _story("Anthropic launches Claude Fable 5.2",
               "https://example.com/launch", 800, 100),
    ]
    selected = mw.select_stories(stories)
    assert len(selected) == 1, selected


# ── Test 2: refresh semantics ─────────────────────────────────────────────

def test_excluding_a_post_leaves_other_posts_coverage_intact():
    """Excluding one post's file must not free the URLs other posts cover.

    This is the regression for the bug that subtracted the post's URLs from
    the shared covered set: "Claude Fable 5.2 and Mythos 5.2" is cited by
    the Nov-10 Australia post, so excluding the digest's own file must leave
    it covered.
    """
    import tempfile
    from pathlib import Path as P

    with tempfile.TemporaryDirectory() as tmp:
        posts = P(tmp)
        (posts / "2026-11-10-australia.md").write_text(
            "## Sources\n\n"
            "- [Anthropic — *Claude Fable 5.2*](https://www.anthropic.com/claude-fable-and-mythos-5-2)\n",
            encoding="utf-8",
        )
        (posts / "2026-11-11-digest.md").write_text(
            "*Source: [Claude Fable 5.2 and Mythos 5.2]"
            "(https://www.anthropic.com/claude-fable-and-mythos-5-2) — 992 points*\n",
            encoding="utf-8",
        )

        everything = post_identity.published_source_urls(posts)
        without_digest = post_identity.published_source_urls_excluding(
            posts, posts / "2026-11-11-digest.md"
        )

        key = post_identity.normalise_url(
            "https://www.anthropic.com/claude-fable-and-mythos-5-2"
        )
        assert key in everything
        assert key in without_digest, (
            "excluding the digest's own file freed a URL that the Australia "
            "post also covers — the refresh would republish it"
        )

        # A story covered ONLY by the excluded post is freed.
        (posts / "2026-11-11-digest.md").write_text(
            "*Source: [Only here]"
            "(https://example.com/only-in-digest) — 10 points*\n",
            encoding="utf-8",
        )
        without_digest = post_identity.published_source_urls_excluding(
            posts, posts / "2026-11-11-digest.md"
        )
        only_key = post_identity.normalise_url("https://example.com/only-in-digest")
        assert only_key not in without_digest, (
            "a story covered only by the refreshed post must be selectable"
        )


def test_refresh_keeps_a_story_that_only_this_post_covers():
    """A refresh can keep its own stories — the point of `--refresh`.

    A story that no other post covers, and that still passes the AI filter,
    must survive the refresh. Before the fix the post's own URLs were in
    the covered set, so it could not.
    """
    import tempfile
    from pathlib import Path as P

    with tempfile.TemporaryDirectory() as tmp:
        posts = P(tmp)
        digest = posts / "2026-11-11-digest.md"
        digest.write_text(
            "*Source: [Claude Fable 5.2 and Mythos 5.2]"
            "(https://www.anthropic.com/claude-fable-and-mythos-5-2) — 992 points*\n",
            encoding="utf-8",
        )

        stories = [
            _story("Claude Fable 5.2 and Mythos 5.2",
                   "https://www.anthropic.com/claude-fable-and-mythos-5-2", 992, 8),
            _story("Another AI story nobody else covered",
                   "https://example.com/fresh", 400, 100),
        ]

        refresh_covered = post_identity.published_source_urls_excluding(posts, digest)
        selected = mw.select_stories(stories, exclude_urls=refresh_covered)
        titles = [s["title"] for s in selected]

        assert "Claude Fable 5.2 and Mythos 5.2" in titles, (
            "the refresh dropped a story that only this post covered"
        )
        assert len(titles) == 2, titles


def _run():
    failures = []
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as exc:
            failures.append((name, str(exc)))
            print(f"  FAIL  {name}: {exc}")
    print()
    if failures:
        print(f"{len(failures)} test(s) FAILED")
        return 1
    print("All tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run())
