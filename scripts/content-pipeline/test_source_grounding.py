#!/usr/bin/env python3
"""
Regression tests for the source-grounding fetch.

WHY THIS EXISTS
---------------
The grounding fetch sent one header — `User-Agent: Mozilla/5.0 (compatible;
SolAI/1.0)` — and returned "" on any failure, in silence.

That combination produced a defect with no visible symptom. openai.com
answers a bot User-Agent with **403 Forbidden** and an empty body, so every
post grounded on an OpenAI announcement was written from the headline alone.
The log said "✅ MiniMax" and nothing about the missing source text.

The three posts of 2026-11-18 are the evidence: all three hedged about what
GPT-6.2 Astra and Sol actually are — "the available details do not establish
how Astra and Sol differ" — when the announcement says plainly that Astra is
the frontier model for the hardest projects and Sol the efficient model for
everyday work. The facts were one header away.

Two things are pinned here:

  1. The request carries a browser header set. Adding `Sec-Fetch-*`,
     `Accept-Language` and `Accept` took openai.com from 403 to a 400KB
     document (verified 2026-11-18).
  2. A failure is never silent. The fetch logs when it falls back to the
     text proxy, and logs when it has no source text at all. A silent ""
     is indistinguishable from a site with nothing to say, and that is
     exactly how this defect stayed invisible for a week.

A script that regrows the bare User-Agent, or that stops logging the
failure, fails this suite.

Run it directly — no test framework needed:

    python3 scripts/content-pipeline/test_source_grounding.py

It is also pytest-compatible: every check is a `test_` function.
"""

from __future__ import annotations

import importlib.util
import io
import sys
import urllib.error
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

_spec = importlib.util.spec_from_file_location(
    "sol_run_daily", Path(__file__).resolve().parent / "run-daily.py"
)
run_daily = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_daily)


class _FakeResponse:
    """Minimal stand-in for the object urlopen() returns."""

    def __init__(self, body: bytes):
        self._body = body

    def read(self, _size=-1):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _blocked(*_args, **_kwargs):
    """Raise the 403 a Cloudflare-fronted site returns to a bot."""
    raise urllib.error.HTTPError(
        "https://example.com/", 403, "Forbidden", None, None
    )


def _html_response(payload: str):
    def _opener(_req, *_args, **_kwargs):
        return _FakeResponse(payload.encode("utf-8"))

    return _opener


def _proxy_markdown(body: str):
    """A r.jina.ai-style response: preamble plus markdown body."""
    payload = (
        "Title: Introducing GPT-6.2 Astra and Sol\n"
        "\n"
        "URL Source: https://openai.com/index/introducing-gpt-6-2-astra-sol/\n"
        "\n"
        "Markdown Content:\n"
        f"{body}\n"
    )

    def _opener(_req, *_args, **_kwargs):
        return _FakeResponse(payload.encode("utf-8"))

    return _opener


def _dispatch(direct, proxy):
    """Route by URL: the direct URL gets `direct`, the proxy URL `proxy`."""
    def _opener(req, *_args, **_kwargs):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        return proxy(url) if url.startswith(run_daily.TEXT_PROXY) else direct(url)

    return _opener


# ── The header set ─────────────────────────────────────────────────────────

def test_headers_carry_a_browser_fingerprint():
    """The request must not look like a script."""
    headers = run_daily.BROWSER_HEADERS
    assert "Sec-Fetch-Mode" in headers, "Sec-Fetch-* is what Cloudflare checks"
    assert "Accept-Language" in headers
    assert "Accept" in headers
    ua = headers["User-Agent"]
    assert "Chrome/" in ua, f"User-Agent must look like a browser, got {ua!r}"
    assert "SolAI" not in ua, "the old bot UA is what got us a 403"


def test_direct_request_sends_browser_headers():
    """The headers must reach urlopen, not just live in a dict."""
    seen = {}

    def _opener(req, *_args, **_kwargs):
        seen.update(req.headers)
        return _FakeResponse(b"<html><body>Hello</body></html>")

    with mock.patch("urllib.request.urlopen", _opener):
        run_daily.fetch_source_excerpt("https://example.com/story")

    sent = {k.lower(): v for k, v in seen.items()}
    assert sent.get("sec-fetch-mode") == "navigate", sent
    assert "Chrome/" in sent.get("user-agent", ""), sent
    assert sent.get("accept-language"), sent


# ── The three outcomes ─────────────────────────────────────────────────────

def test_direct_html_is_extracted_to_text():
    html = (
        "<html><head><meta name='description' content='A short summary.'>"
        "</head><body><p>The first real sentence of the article.</p></body></html>"
    )
    with mock.patch("urllib.request.urlopen", _html_response(html)):
        text = run_daily.fetch_source_excerpt("https://example.com/story")

    assert "The first real sentence of the article." in text
    assert "A short summary." in text, "the meta description is grounding too"


def test_blocked_site_falls_back_to_the_text_proxy():
    """A 403 must not end the story — the proxy gets a turn."""
    body = "Astra is the frontier model. Sol is the efficient model."

    with mock.patch(
        "urllib.request.urlopen",
        _dispatch(_blocked, _proxy_markdown(body)),
    ):
        text = run_daily.fetch_source_excerpt("https://openai.com/index/thing/")

    assert "Astra is the frontier model." in text


def test_proxy_preamble_is_stripped():
    """The proxy's Title:/URL Source: header is not part of the article."""
    body = "The announcement body."

    with mock.patch(
        "urllib.request.urlopen",
        _dispatch(_blocked, _proxy_markdown(body)),
    ):
        text = run_daily.fetch_source_excerpt("https://openai.com/index/thing/")

    assert "The announcement body." in text
    for marker in ("Title:", "URL Source:", "Markdown Content:"):
        assert marker not in text, f"proxy preamble leaked into grounding: {marker}"


def test_proxy_fallback_is_logged():
    """A fallback must be visible in the log."""
    body = "Astra is the frontier model."

    buf = io.StringIO()
    with mock.patch(
        "urllib.request.urlopen",
        _dispatch(_blocked, _proxy_markdown(body)),
    ):
        with redirect_stdout(buf):
            run_daily.fetch_source_excerpt("https://openai.com/index/thing/")

    out = buf.getvalue()
    assert "[grounding]" in out, out
    assert "proxy" in out.lower(), out


def test_no_source_text_is_logged_not_silent():
    """The old defect: "" returned with nothing said."""
    buf = io.StringIO()
    with mock.patch("urllib.request.urlopen", _blocked):
        with redirect_stdout(buf):
            text = run_daily.fetch_source_excerpt("https://openai.com/index/thing/")

    assert text == ""
    out = buf.getvalue()
    assert "[grounding]" in out, f"a failed fetch must log, got {out!r}"
    assert "grounding unavailable" in out, out


def test_direct_success_does_not_use_the_proxy():
    """A working direct fetch must not pay for the proxy."""
    calls = []

    def _opener(req, *_args, **_kwargs):
        calls.append(req.full_url if hasattr(req, "full_url") else str(req))
        return _FakeResponse(b"<html><body>Direct body text.</body></html>")

    with mock.patch("urllib.request.urlopen", _opener):
        text = run_daily.fetch_source_excerpt("https://example.com/story")

    assert "Direct body text." in text
    assert not any(u.startswith(run_daily.TEXT_PROXY) for u in calls), calls


def test_max_chars_is_respected():
    long_body = "word " * 5000
    with mock.patch(
        "urllib.request.urlopen",
        _dispatch(_blocked, _proxy_markdown(long_body)),
    ):
        text = run_daily.fetch_source_excerpt("https://example.com/story", max_chars=200)

    assert len(text) <= 200, len(text)


def test_non_http_url_is_rejected_without_a_request():
    def _boom(*_args, **_kwargs):
        raise AssertionError("must not make a request for a non-http URL")

    with mock.patch("urllib.request.urlopen", _boom):
        assert run_daily.fetch_source_excerpt("") == ""
        assert run_daily.fetch_source_excerpt("not-a-url") == ""


def test_the_shared_loader_reaches_this_fetcher():
    """`source_fetch.py` is the shared entry point for the other two
    generators. It must pick up the fix, not a second copy of the old code.
    """
    import source_fetch

    assert source_fetch.grounding_available(), "run-daily.py failed to load"

    html = "<html><body>Shared loader reached the real fetcher.</body></html>"
    with mock.patch("urllib.request.urlopen", _html_response(html)):
        text = source_fetch.fetch_source_excerpt("https://example.com/story")

    assert "Shared loader reached the real fetcher." in text


def _run_all() -> int:
    tests = [
        (name, fn)
        for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ✅ {name}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  ❌ {name}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_all())
