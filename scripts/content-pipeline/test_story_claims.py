#!/usr/bin/env python3
"""
Regression tests for the story-claim registry (post_identity.py).

WHY THIS EXISTS
---------------
The archive dedupe answers "has this story been published?" by reading the
post files. It cannot answer two questions that the Wednesday morning
schedule asks:

  * a generator whose posts cite no source at all — the Quick Hits briefing
    used to cite nothing, so nothing it covered was visible to anyone;
  * a generator that has not written its post yet, because another
    generator is running at the same minute.

Both gaps were live on 2026-11-18. The midweek digest, the weekday Quick
Hits briefing and the regional trio (uk/eu/us) all fire at 07:00, all draw
from the same Hacker News top-stories list, and all published "GPT-6.2
Astra and Sol" — five posts, one story, on a site that publishes around
eight posts a day.

The claim registry closes both gaps. A generator claims the stories it is
about to publish, under its own series name, before it spends a minute
generating them; a generator that asks a moment later sees the claim and
picks something else.

These tests pin the behaviour that makes that work, and pin the two things
the registry must NOT break:

  * the regional trio deliberately covers ONE story from three angles, so
    the trio's own posts must not lock the story out of the other two
    regions;
  * a generator refreshing its own post in place must be able to re-claim
    the same stories, or the refresh would silently swap them.

Run it directly — no test framework needed:

    python3 scripts/content-pipeline/test_story_claims.py

It is also pytest-compatible: every check is a `test_` function.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from post_identity import (  # noqa: E402
    claim_stories,
    normalise_url,
    published_source_urls_excluding_paths,
    read_claims,
    release_claims,
    sources_section_urls,
)

DAY = "2026-11-18"


# ── helpers ────────────────────────────────────────────────────────────────

def _tmp_dir(tmp_path):
    d = Path(tmp_path) / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _story(url, title="A story"):
    return {"url": url, "title": title, "score": 100, "comments": 10}


def _post(posts_dir, name, body):
    path = Path(posts_dir) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


# ── the registry itself ────────────────────────────────────────────────────

def test_a_story_can_be_claimed(tmp_path):
    posts = Path(tmp_path) / "posts"
    posts.mkdir()
    state = _tmp_dir(tmp_path)

    picked = claim_stories(
        "ai-digest",
        [_story("https://example.com/a")],
        1,
        posts,
        day=DAY,
        claims_dir=state,
    )

    assert len(picked) == 1
    assert picked[0]["url"] == "https://example.com/a"


def test_the_claim_is_readable_by_another_series(tmp_path):
    posts = Path(tmp_path) / "posts"
    posts.mkdir()
    state = _tmp_dir(tmp_path)

    claim_stories(
        "ai-digest",
        [_story("https://example.com/a")],
        1,
        posts,
        day=DAY,
        claims_dir=state,
    )

    claims = read_claims(DAY, claims_dir=state)
    assert normalise_url("https://example.com/a") in claims
    assert claims[normalise_url("https://example.com/a")]["series"] == "ai-digest"


def test_another_series_cannot_claim_a_claimed_story(tmp_path):
    """THE defect this registry exists to fix.

    The digest claims the day's top story. The regional series asks for it
    a moment later and must be told no.
    """
    posts = Path(tmp_path) / "posts"
    posts.mkdir()
    state = _tmp_dir(tmp_path)

    candidates = [_story("https://example.com/a"), _story("https://example.com/b")]

    digest = claim_stories("ai-digest", candidates, 1, posts, day=DAY, claims_dir=state)
    assert digest[0]["url"] == "https://example.com/a"

    regional = claim_stories("regional", candidates, 1, posts, day=DAY, claims_dir=state)
    assert regional[0]["url"] == "https://example.com/b", (
        "the regional series took the story the digest had already claimed"
    )


def test_a_series_may_re_claim_its_own_story(tmp_path):
    """A refresh rewrites a post in place and must keep its stories."""
    posts = Path(tmp_path) / "posts"
    posts.mkdir()
    state = _tmp_dir(tmp_path)

    candidates = [_story("https://example.com/a")]
    first = claim_stories("ai-digest", candidates, 1, posts, day=DAY, claims_dir=state)
    again = claim_stories("ai-digest", candidates, 1, posts, day=DAY, claims_dir=state)

    assert first[0]["url"] == again[0]["url"] == "https://example.com/a"


def test_a_story_in_the_archive_cannot_be_claimed(tmp_path):
    """The archive still wins: a story already published is never claimed."""
    posts = Path(tmp_path) / "posts"
    posts.mkdir()
    state = _tmp_dir(tmp_path)
    _post(
        posts,
        "2026-11-17-old.md",
        "---\ntitle: x\n---\n\n*Source: [Old](https://example.com/old)*\n",
    )

    picked = claim_stories(
        "ai-digest",
        [_story("https://example.com/old"), _story("https://example.com/new")],
        1,
        posts,
        day=DAY,
        claims_dir=state,
    )

    assert picked[0]["url"] == "https://example.com/new"


def test_an_excluded_file_does_not_block_its_own_stories(tmp_path):
    """A generator refreshing its own post in place excludes that file."""
    posts = Path(tmp_path) / "posts"
    posts.mkdir()
    state = _tmp_dir(tmp_path)
    mine = _post(
        posts,
        "2026-11-18-mine.md",
        "---\ntitle: x\n---\n\n*Source: [Mine](https://example.com/mine)*\n",
    )

    picked = claim_stories(
        "ai-digest",
        [_story("https://example.com/mine")],
        1,
        posts,
        day=DAY,
        exclude_paths=[mine],
        claims_dir=state,
    )

    assert len(picked) == 1


# ── the regional trio keeps its shared story ───────────────────────────────

def test_the_trio_can_still_share_one_story(tmp_path):
    """uk, eu and us cover ONE story from three angles — by design.

    So the trio's own posts must not count against the trio. Excluding the
    FILES (not their URLs) keeps every other post's coverage intact.
    """
    posts = Path(tmp_path) / "posts"
    posts.mkdir()
    state = _tmp_dir(tmp_path)

    uk = _post(
        posts,
        "2026-11-18-uk-ai-weekly-something.md",
        "---\ntitle: x\n---\n\n*Source: [Shared](https://example.com/shared)*\n",
    )

    covered = published_source_urls_excluding_paths(posts, [uk])
    assert normalise_url("https://example.com/shared") not in covered

    picked = claim_stories(
        "regional",
        [_story("https://example.com/shared")],
        1,
        posts,
        day=DAY,
        exclude_paths=[uk],
        claims_dir=state,
    )
    assert len(picked) == 1


def test_the_trio_is_blocked_by_another_series_post(tmp_path):
    """A digest post published earlier the same morning blocks the trio."""
    posts = Path(tmp_path) / "posts"
    posts.mkdir()
    state = _tmp_dir(tmp_path)
    _post(
        posts,
        "2026-11-18-ai-digest-wednesday.md",
        "---\ntitle: x\n---\n\n*Source: [Big](https://example.com/big)*\n",
    )

    picked = claim_stories(
        "regional",
        [_story("https://example.com/big"), _story("https://example.com/other")],
        1,
        posts,
        day=DAY,
        claims_dir=state,
    )

    assert picked[0]["url"] == "https://example.com/other"


# ── releasing unused claims ───────────────────────────────────────────────

def test_release_frees_the_unused_reservations(tmp_path):
    """Quick Hits offers five candidates and uses three.

    It claims the whole pool before the model chooses, then releases the
    two it did not use — otherwise the day's remaining supply is wasted.
    """
    posts = Path(tmp_path) / "posts"
    posts.mkdir()
    state = _tmp_dir(tmp_path)

    pool = [_story(f"https://example.com/{i}") for i in range(5)]
    claim_stories("quick-hits", pool, len(pool), posts, day=DAY, claims_dir=state)

    used = ["https://example.com/0", "https://example.com/1", "https://example.com/2"]
    released = release_claims("quick-hits", used, DAY, claims_dir=state)

    assert released == 2
    claims = read_claims(DAY, claims_dir=state)
    assert len(claims) == 3
    assert normalise_url("https://example.com/3") not in claims
    assert normalise_url("https://example.com/0") in claims


def test_release_never_frees_another_series_claim(tmp_path):
    posts = Path(tmp_path) / "posts"
    posts.mkdir()
    state = _tmp_dir(tmp_path)

    claim_stories("ai-digest", [_story("https://example.com/a")], 1, posts, day=DAY, claims_dir=state)
    release_claims("quick-hits", [], DAY, claims_dir=state)

    claims = read_claims(DAY, claims_dir=state)
    assert normalise_url("https://example.com/a") in claims


def test_a_released_story_can_then_be_claimed_by_another_series(tmp_path):
    posts = Path(tmp_path) / "posts"
    posts.mkdir()
    state = _tmp_dir(tmp_path)

    pool = [_story(f"https://example.com/{i}") for i in range(3)]
    claim_stories("quick-hits", pool, 3, posts, day=DAY, claims_dir=state)
    release_claims("quick-hits", ["https://example.com/0"], DAY, claims_dir=state)

    picked = claim_stories(
        "regional",
        pool,
        1,
        posts,
        day=DAY,
        claims_dir=state,
    )
    assert picked[0]["url"] == "https://example.com/1"


# ── the Quick Hits briefing finally cites its sources ──────────────────────

def test_a_quick_hits_sources_section_is_visible_to_the_dedupe():
    """The briefing used to cite nothing, so nothing it covered was visible.

    The "## Sources" section is the format the archive already knows how to
    read, so the briefing's stories become visible to every later run.
    """
    body = (
        "**What happened:** Something happened.\n"
        "**Why it matters:** It matters.\n"
        "\n"
        "## Sources\n"
        "\n"
        "- [OpenAI ships GPT-6.2](https://openai.com/index/introducing-gpt-6-2-astra-sol/)\n"
        "- [Harness comparison](https://runta.com/blog/introducing-frontierharness-eval-v1-1/)\n"
    )

    found = sources_section_urls(body)
    assert normalise_url("https://openai.com/index/introducing-gpt-6-2-astra-sol/") in [
        normalise_url(u) for u in found
    ]


def test_a_broken_registry_never_blocks_publishing(tmp_path):
    """The registry is an enhancement. If it fails, the pipeline still runs."""
    posts = Path(tmp_path) / "posts"
    posts.mkdir()

    # A claims_dir that cannot be written to.
    blocked = Path(tmp_path) / "nope" / "\0bad"
    picked = claim_stories(
        "ai-digest",
        [_story("https://example.com/a"), _story("https://example.com/b")],
        1,
        posts,
        day=DAY,
        claims_dir=blocked,
    )

    assert len(picked) == 1


# ── runner ─────────────────────────────────────────────────────────────────

def main():
    import inspect
    import tempfile
    import traceback

    tests = [(n, o) for n, o in sorted(globals().items()) if n.startswith("test_")]
    passed = failed = 0
    for name, fn in tests:
        wants_tmp = len(inspect.signature(fn).parameters) > 0
        with tempfile.TemporaryDirectory() as td:
            try:
                fn(Path(td)) if wants_tmp else fn()
                print(f"  PASS  {name}")
                passed += 1
            except Exception:
                print(f"  FAIL  {name}")
                traceback.print_exc()
                failed += 1

    print(f"\n{passed}/{passed + failed} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
