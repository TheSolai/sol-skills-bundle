#!/usr/bin/env python3
"""
Regression tests for the SEO audit's duplicate-metadata check.

WHY THIS EXISTS
---------------
The daily SEO audit reported 20 issues every day for weeks: 246 posts
"share a title with another post and have no canonical to consolidate
them". Every one of them was historical residue published before the
generators were fixed (commit 7439af4, 2026-10-31), and every one of
them had a DIFFERENT body — they were distinct articles that a fixed
template gave the same headline to.

The check was wrong twice over:

  1. It reported a canonical as the remedy. A canonical tells Google to
     keep one URL and drop the others. For 246 distinct articles that
     would de-index real, indexable posts.
  2. It could not tell residue from a live regression, so it stayed
     permanently red. A check that always fails tells you nothing: the
     exit code carried no signal, and a real regression would have been
     invisible in the noise.

The check now distinguishes the two, and this file pins that behaviour.

Run it directly — no test framework needed:

    python3 scripts/content-pipeline/test_seo_duplicate_metadata.py

It is also pytest-compatible: every check is a `test_` function.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "seo_health", Path(__file__).resolve().parent / "seo-health.py"
)
seo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(seo)


def _write_post(directory: Path, filename: str, title: str, description: str,
               body: str, canonical: str = "") -> Path:
    """Write a minimal but complete Jekyll post and return its path."""
    fm = [
        "---",
        "layout: post",
        f'title: "{title}"',
        f'description: "{description}"',
        f"date: {filename[:10]}",
        "hero_image: /images/sol-avatar.png",
        "tags: [ai, analysis]",
        "author: Sol AI",
    ]
    if canonical:
        fm.append(f"canonical: {canonical}")
    fm.append("---")
    path = directory / filename
    path.write_text("\n".join(fm) + "\n\n" + body + "\n", encoding="utf-8")
    return path


def test_distinct_articles_sharing_a_title_are_residue_not_an_error():
    """Two posts, same title, DIFFERENT bodies, both published before the
    generator fix.

    This is the shape of all 246 historical offenders. It must be a
    note, not an error: a canonical would de-index one of the two.
    """
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_post(
            d, "2026-09-01-sols-take-thursday.md",
            "Sol's Take: Thursday",
            "Sol's opinion on today's AI landscape.",
            "# Sol's Take: Thursday\n\nThe first article, about model routing.",
        )
        _write_post(
            d, "2026-09-08-sols-take-thursday.md",
            "Sol's Take: Thursday",
            "Sol's opinion on today's AI landscape.",
            "# Sol's Take: Thursday\n\nThe second article, about agents.",
        )
        issues, notes = seo.check_duplicate_metadata(sorted(d.glob("*.md")))
        assert not issues, f"expected no errors, got: {issues}"
        assert any("share a title" in n for n in notes), notes


def test_a_post_published_after_the_fix_is_a_regression():
    """A post published on or after the generator fix that shares a title
    with another post means a generator regressed.

    This is the failure the check exists to catch, and it must be an
    error — otherwise the audit cannot go red for the right reason.
    """
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_post(
            d, "2026-11-01-sols-take-thursday.md",
            "Sol's Take: Thursday",
            "Sol's opinion on today's AI landscape.",
            "# Sol's Take: Thursday\n\nA post published after the generator fix.",
        )
        _write_post(
            d, "2026-11-08-sols-take-thursday.md",
            "Sol's Take: Thursday",
            "Sol's opinion on today's AI landscape.",
            "# Sol's Take: Thursday\n\nA later post with the same fixed title.",
        )
        issues, _ = seo.check_duplicate_metadata(sorted(d.glob("*.md")))
        assert issues, "expected a regression error, got none"
        assert any("regressed" in i for i in issues), issues


def test_a_shared_description_after_the_fix_is_also_a_regression():
    """The description is the metadata Google reads second, so a fixed
    description is the same regression as a fixed title."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_post(
            d, "2026-11-01-quick-hits-monday.md",
            "Quick Hits: Monday's AI Briefing",
            "Three AI stories worth knowing about today.",
            "# Quick Hits\n\nFirst body.",
        )
        _write_post(
            d, "2026-11-08-quick-hits-monday.md",
            "Quick Hits: Monday's AI Briefing",
            "Three AI stories worth knowing about today.",
            "# Quick Hits\n\nSecond body.",
        )
        issues, _ = seo.check_duplicate_metadata(sorted(d.glob("*.md")))
        assert issues, "expected a regression error, got none"
        assert any("meta description" in i for i in issues), issues


def test_a_canonical_target_is_not_counted_as_an_offender():
    """The surviving URL of a family carries no canonical of its own — that
    is what makes it the target. It must not be flagged as needing one,
    or every consolidated family would report a permanent error."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        original = _write_post(
            d, "2026-06-01-ai-bloopers.md",
            "AI Bloopers: The Most Absurd AI Fails This Week",
            "A weekly roundup of absurd AI failures.",
            "# AI Bloopers\n\nThe original body.",
        )
        original_url = seo.post_url(original)
        _write_post(
            d, "2026-06-08-ai-bloopers.md",
            "AI Bloopers: The Most Absurd AI Fails This Week",
            "A weekly roundup of absurd AI failures.",
            "# AI Bloopers\n\nThe original body.",
            canonical=original_url,
        )
        issues, notes = seo.check_duplicate_metadata(sorted(d.glob("*.md")))
        assert not issues, f"expected no errors, got: {issues}"
        assert any("carry a canonical" in n for n in notes), notes


def test_distinct_titles_never_report():
    """The check must stay quiet on a healthy site — otherwise it is
    noise, and a permanently-red audit is the failure mode this whole
    change exists to end."""
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        _write_post(
            d, "2026-11-01-first.md", "First Post", "About the first thing.",
            "# First Post\n\nBody one.",
        )
        _write_post(
            d, "2026-11-02-second.md", "Second Post", "About the second thing.",
            "# Second Post\n\nBody two.",
        )
        issues, notes = seo.check_duplicate_metadata(sorted(d.glob("*.md")))
        assert not issues, f"expected no errors, got: {issues}"
        assert not notes, f"expected no notes, got: {notes}"


def _run_all() -> int:
    tests = [
        (name, obj) for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    failures = []
    for name, fn in tests:
        try:
            fn()
            print(f"  ✅ {name}")
        except AssertionError as exc:
            failures.append((name, exc))
            print(f"  ❌ {name}: {exc}")
        except Exception as exc:  # pragma: no cover - a crash is a failure
            failures.append((name, exc))
            print(f"  ❌ {name}: raised {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run_all())
