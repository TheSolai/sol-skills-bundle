r"""
WHY THIS EXISTS
---------------
`existing_post_is_degraded()` is how `run-daily.py` recognises a published
post that is really just the deterministic template fallback, so it can be
regenerated instead of left as filler. Its pattern only allowed a double
quote after `title:`:

    ^title:\s*"?\[(UK|EU|US)\]\s

`_yaml()` double-quotes today, but 54 posts published in June-September
carry `title: '[EU] ...'` — a single quote. Those posts were reported as
healthy, so an audit built on this detector under-counted the degraded
posts by more than a third, and a single-quoted fallback on any given day
would not have been regenerated.

These tests pin the detector against all three quote styles, and against a
real post, so the hole cannot reopen.
"""

import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "sol_run_daily", Path(__file__).resolve().parent / "run-daily.py"
)
run_daily = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_daily)


def _write(tmp_path, frontmatter):
    p = tmp_path / "2026-11-08-uk-something.md"
    p.write_text(frontmatter, encoding="utf-8")
    return p


@pytest.mark.parametrize(
    "title_line",
    [
        'title: "[UK] The end of something"',
        "title: '[UK] The end of something'",
        "title: [UK] The end of something",
    ],
)
def test_a_fallback_title_is_degraded_in_every_quote_style(tmp_path, title_line):
    p = _write(tmp_path, f"---\nlayout: post\n{title_line}\ndate: 2026-11-08\n---\n\nBody.\n")
    assert run_daily.existing_post_is_degraded(p) is True


@pytest.mark.parametrize(
    "title_line",
    [
        'title: "UK AI Weekly: Anthropic\'s IPO lands with a splat"',
        "title: 'UK AI Weekly: a real post'",
        'title: "EU AI Watch: a real post"',
        'title: "Something [bracket] in the middle"',
    ],
)
def test_a_real_post_is_not_degraded(tmp_path, title_line):
    p = _write(tmp_path, f"---\nlayout: post\n{title_line}\ndate: 2026-11-08\n---\n\nBody.\n")
    assert run_daily.existing_post_is_degraded(p) is False


def test_a_missing_file_is_not_degraded(tmp_path):
    """An unreadable path must not raise — it means 'nothing to regenerate'."""
    assert run_daily.existing_post_is_degraded(tmp_path / "does-not-exist.md") is False
