#!/usr/bin/env python3
"""
Regression tests for the Tool Spotlight rotation.

WHY THIS EXISTS
---------------
The rotation picked its tool with `(iso_week * iso_year) % len(TOOLS)`. The
ISO year is 2026 for most of the year and 2026 is even, so the product was
always even and the index could only ever land on 0, 2, 4 or 6.

That left OpenClaw, Cursor, dev.to API and GitHub Actions mathematically
unreachable. The published history agreed: 16 spotlights, and only four
distinct tools had ever been reviewed (Hacker News API 5, Claude Code 4,
MiniMax 4, Warp 3). The skill promises a rotation over all eight.

The picker now chooses the tool with the fewest published reviews, which
covers every tool and repairs itself after a missed week.

Run it directly — no test framework needed:

    python3 scripts/content-pipeline/test_tool_spotlight_rotation.py

It is also pytest-compatible: every check is a `test_` function.
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

_spec = importlib.util.spec_from_file_location(
    "tool_spotlight_module", Path(__file__).resolve().parent / "tool-spotlight.py"
)
tool_spotlight = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool_spotlight)

TOOLS = tool_spotlight.TOOLS


def _fake_posts_dir(files: list[str]) -> Path:
    """A throwaway `_posts` directory holding the named files."""
    tmp = Path(tempfile.mkdtemp(prefix="spotlight-rotation-"))
    posts = tmp / "_posts"
    posts.mkdir()
    for name in files:
        (posts / name).write_text("---\ntitle: \"x\"\n---\n\nbody\n", encoding="utf-8")
    return posts


# ── The defect that was there ──────────────────────────────────────────────


def test_the_old_formula_could_only_reach_half_the_rotation():
    """The old index was even-only for the whole of 2026.

    This pins the reason the picker was rewritten, so nobody restores the
    old formula thinking it was merely "unlucky".
    """
    reachable = set()
    for week in range(1, 54):
        reachable.add((week * 2026) % len(TOOLS))
    assert reachable == {0, 2, 4, 6}, (
        "the old formula's reach changed — the history in the docstring "
        "needs revisiting"
    )
    unreachable = [TOOLS[i]["name"] for i in range(len(TOOLS)) if i not in reachable]
    assert set(unreachable) == {
        "OpenClaw",
        "Cursor",
        "dev.to API",
        "GitHub Actions",
    }, f"unexpected set of unreachable tools: {unreachable}"


# ── The new picker ─────────────────────────────────────────────────────────


def test_the_picker_returns_a_tool_from_the_rotation():
    """It never returns something that is not on the list."""
    chosen = tool_spotlight.pick_tool(posts_dir=_fake_posts_dir([]))
    assert chosen in TOOLS


def test_a_tool_that_has_never_been_reviewed_wins():
    """An unreviewed tool beats one that has had five reviews."""
    posts = _fake_posts_dir(
        [
            f"2026-10-23-tool-spotlight-{tool_spotlight._tool_slug(t['name'])}.md"
            for t in TOOLS
            if t["name"] == "Hacker News API"
        ]
    )
    chosen = tool_spotlight.pick_tool(posts_dir=posts)
    assert chosen["name"] == "Claude Code", (
        f"expected the first never-reviewed tool, got {chosen['name']}"
    )


def test_ties_break_on_the_order_of_the_rotation():
    """With nothing published, the first tool in the list is chosen.

    This is what sends the four never-covered tools out first.
    """
    chosen = tool_spotlight.pick_tool(posts_dir=_fake_posts_dir([]))
    assert chosen["name"] == TOOLS[0]["name"]


def test_the_counts_match_the_published_posts():
    """Every published spotlight is counted against its tool."""
    names = ["Claude Code", "MiniMax", "Warp"]
    files = [
        f"2026-07-10-tool-spotlight-{tool_spotlight._tool_slug(n)}.md"
        for n in names
    ]
    counts = tool_spotlight.spotlight_counts(_fake_posts_dir(files))
    for n in names:
        assert counts[n] == 1, f"{n} counted {counts[n]} times, expected 1"
    for t in TOOLS:
        if t["name"] not in names:
            assert counts[t["name"]] == 0, (
                f"{t['name']} counted {counts[t['name']]} times, expected 0"
            )


def test_every_tool_is_reviewed_before_any_tool_repeats():
    """Eight consecutive runs cover all eight tools.

    This is the invariant the old formula broke: the rotation had to work
    its way through the whole list, not through a fixed half of it.
    """
    posts = _fake_posts_dir([])
    seen = []
    for _ in range(len(TOOLS)):
        chosen = tool_spotlight.pick_tool(posts_dir=posts)
        seen.append(chosen["name"])
        (posts / (
            f"2027-01-0{len(seen)}-tool-spotlight-"
            f"{tool_spotlight._tool_slug(chosen['name'])}.md"
        )).write_text("---\ntitle: \"x\"\n---\n\nbody\n", encoding="utf-8")
    assert len(set(seen)) == len(TOOLS), (
        f"eight runs covered only {len(set(seen))} of {len(TOOLS)} tools: {seen}"
    )


def test_a_missed_week_does_not_strand_a_tool():
    """The picker reads the disk, so a gap in the history is self-correcting.

    If the rotation were driven by a date formula, a skipped week would
    just advance the sequence. Counting published posts means the next run
    picks up whatever was left out.
    """
    posts = _fake_posts_dir([
        f"2026-10-23-tool-spotlight-{tool_spotlight._tool_slug(t['name'])}.md"
        for t in TOOLS
        if t["name"] in ("Claude Code", "MiniMax")
    ])
    counts = tool_spotlight.spotlight_counts(posts)
    assert counts["Claude Code"] == 1
    assert counts["MiniMax"] == 1
    chosen = tool_spotlight.pick_tool(posts_dir=posts)
    assert counts[chosen["name"]] == 0, (
        f"picked {chosen['name']} which has already been reviewed"
    )


def test_the_slug_written_by_the_poster_matches_the_slug_the_picker_counts():
    """The two cannot drift apart.

    If the poster filed a post under a different slug than the picker
    counts, the rotation would lose track of its own history — a post
    would be published and then counted as a different tool's review.
    """
    for tool in TOOLS:
        slug = tool_spotlight._tool_slug(tool["name"])
        assert "/" not in slug and " " not in slug
        posts = _fake_posts_dir([f"2026-11-27-tool-spotlight-{slug}.md"])
        counts = tool_spotlight.spotlight_counts(posts)
        assert counts[tool["name"]] == 1, (
            f"{tool['name']} was not counted from its own slug '{slug}'"
        )


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
