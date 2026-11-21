"""
WHY THIS EXISTS
---------------
On 2026-11-20 the daily regional pipeline lost an entire day. The HN fetch
was a single attempt: one SSL handshake timeout returned [], the pipeline
printed "No story available, skipping" for uk, eu and us, and the run ended
with "Pipeline complete! Generated: 0 regional posts" and exit code 0.

That is the worst possible shape for a silent failure — a missing day that
looks like a successful one. Two things had to become true:

  1. The fetch retries a transient network error instead of giving up on the
     first attempt.
  2. A run that fetched nothing FAILS LOUDLY and exits non-zero, so the
     scheduler reports it rather than recording a green run.

These tests pin both. They must fail if someone removes the retry or
reverts the fatal-fetch exit.
"""

import importlib.util
import json
from pathlib import Path
from unittest import mock

import pytest

_spec = importlib.util.spec_from_file_location(
    "sol_run_daily", Path(__file__).resolve().parent / "run-daily.py"
)
run_daily = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_daily)


class _FakeResponse:
    """Minimal stand-in for the object urlopen returns."""

    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _scripted(urlopen):
    """Build a urlopen that fails N times, then serves the given payloads.

    The call counter is shared across every URL so a test can say "the first
    two attempts fail, the third succeeds" without caring which URL is hit.
    """

    state = {"calls": 0}

    def _opener(req, *_args, **_kwargs):
        state["calls"] += 1
        if state["calls"] <= urlopen["fail_times"]:
            raise urlopen.get("error", OSError("handshake timed out"))
        url = req.full_url if hasattr(req, "full_url") else str(req)
        if "topstories" in url:
            return _FakeResponse([101, 102, 103])
        # Serve each item with its own id so the assertions can tell the
        # three stories apart.
        item_id = int(url.rsplit("/", 1)[-1].split(".")[0])
        return _FakeResponse({
            "id": item_id,
            "title": f"Anthropic ships something ({item_id})",
            "url": f"https://example.com/{item_id}",
            "score": 250,
            "by": "someone",
            "descendants": 40,
        })

    return _opener, state


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Retries must not actually sleep in the test suite."""
    monkeypatch.setattr(run_daily.time, "sleep", lambda _s: None)


def test_transient_failure_is_retried_and_the_run_still_gets_stories():
    """Two failed attempts then success — the day must survive."""
    opener, state = _scripted({"fail_times": 2})

    with mock.patch("urllib.request.urlopen", opener):
        stories = run_daily.fetch_hn_stories(limit=3)

    assert state["calls"] >= 3, "the fetch gave up before exhausting its retries"
    assert len(stories) == 3, stories
    assert stories[0]["title"] == "Anthropic ships something (101)"


def test_a_permanently_dead_network_still_returns_empty_rather_than_raising():
    """After every attempt fails the caller gets [] — it must not raise."""
    opener, state = _scripted({"fail_times": 999})

    with mock.patch("urllib.request.urlopen", opener):
        stories = run_daily.fetch_hn_stories(limit=3)

    assert stories == []
    # 3 attempts for the list, then 2 attempts each for the items it read.
    assert state["calls"] >= 3


def test_a_healthy_fetch_does_not_retry():
    """A good run must stay fast — one attempt, no sleeping."""
    opener, state = _scripted({"fail_times": 0})

    with mock.patch("urllib.request.urlopen", opener):
        stories = run_daily.fetch_hn_stories(limit=3)

    assert stories, "a healthy fetch returned nothing"
    # 1 for the list + 1 per item, with no retries at all.
    assert state["calls"] == 4, state["calls"]


def test_zero_stories_is_a_fatal_run_with_a_non_zero_exit(capsys):
    """The Nov 20 shape: no stories must NOT look like a successful run.

    This is the regression that matters most — the old code printed
    "Pipeline complete!" and exited 0 after losing a whole day.
    """
    opener, _state = _scripted({"fail_times": 999})

    def _must_not_generate(*_args, **_kwargs):
        raise AssertionError("the pipeline must not generate a post without a story")

    with mock.patch("urllib.request.urlopen", opener), \
         mock.patch.object(run_daily, "generate_post", _must_not_generate):
        status = run_daily.run_pipeline()

    out = capsys.readouterr().out
    assert status == 1, "a run that published nothing must exit non-zero"
    assert "FATAL" in out, out
    assert "Pipeline complete!" not in out, "a dead fetch must not report completion"


def test_a_healthy_pipeline_run_exits_zero(capsys, tmp_path):
    """The success path is unaffected — it still exits 0 and says complete.

    Every publishing seam is stubbed, so this test cannot write a post,
    claim a story, or push to GitHub. `find_post_for_region` returning a
    path means "this region's post already exists", which makes the
    pipeline skip all three regions — the same shape as a normal
    second run on an already-published day.
    """
    opener, _state = _scripted({"fail_times": 0})
    already_published = tmp_path / "2026-11-21-uk-ai-weekly-existing.md"

    def _must_not_generate(*_args, **_kwargs):
        raise AssertionError("an already-published region must not be regenerated")

    with mock.patch("urllib.request.urlopen", opener), \
         mock.patch.object(run_daily, "find_post_for_region", return_value=already_published), \
         mock.patch.object(run_daily, "generate_blooper_post", return_value=(None, "")), \
         mock.patch.object(run_daily, "generate_post", _must_not_generate):
        status = run_daily.run_pipeline()

    out = capsys.readouterr().out
    assert status == 0, status
    assert "Pipeline complete!" in out
    assert "FATAL" not in out

