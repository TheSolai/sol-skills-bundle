"""
Shared MiniMax API helper for content-pipeline scripts.

Provides a single robust call with:
  - 180s timeout (up from 90s)
  - 3 retries with exponential backoff
  - Proper Anthropic-compatible message format
  - Clean error reporting

Usage:
    from _minimax_helper import minimax_generate, load_minimax_key

    key = _load_minimax_key()
    text = minimax_generate(prompt, api_key=key, max_tokens=600, system="...")
"""

# PEP 604 unions (str | None) are evaluated at def-time on Python < 3.10 and
# break this module under /usr/bin/python3 (3.9.6), the interpreter cron uses.
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

MINIMAX_KEY_PATH = Path.home() / ".openclaw" / "workspace" / "secrets" / "minimax-key.txt"
MINIMAX_URL = "https://api.minimax.io/anthropic/v1/messages"

# The default model, and the fallbacks tried after it.
#
# WHY THE MODEL LIST EXISTS (2026-11-04 incident)
# -----------------------------------------------
# `MiniMax-Text-01` was the default here until MiniMax retired it from the
# catalogue. The API answered `404 not_found_error: The model
# "MiniMax-Text-01" is no longer available` — but `minimax_generate()` caught
# the HTTPError, stashed it in `last_err` and returned None, and the caller
# quietly fell back to its deterministic template. The posts still published
# and still looked plausible on the page: six scripts' worth of them shipped
# as link dumps with no prose at all. A retired model must never be a silent
# failure, so every call now tries the preferred model and then these in
# order, and prints the reason when all of them fail.
MINIMAX_MODEL = "MiniMax-M2.7"
MINIMAX_MODEL_FALLBACKS = ("MiniMax-M2.7-highspeed", "MiniMax-M3.1-Flash")

# Every current MiniMax model is a reasoning model: it spends much of its
# token budget thinking before it produces any text. Asked for a small cap it
# returns an EMPTY completion rather than prose — the thinking consumed the
# whole budget — which looks identical to a failed call. So the cap is raised
# to a floor for these models. 600 tokens, the old default, is always too few.
REASONING_MIN_TOKENS = 6000


def _is_reasoning_model(model: str) -> bool:
    """True for models that spend their token budget on thinking first."""
    return model.startswith(("MiniMax-M1", "MiniMax-M2", "MiniMax-M3"))


# ── Thinking-block stripping ────────────────────────────────────────────────
# Reasoning models (MiniMax M2.7, qwen3.5) emit their scratchpad inline:
#
#     Thinking...
#     Thinking Process:
#     1.  **Analyze the Request:**
#         *   **Topic:** ...
#     Let's write it.
#     ...done thinking.
#     <the actual article>
#
# Left in place this publishes the model's planning notes as the post body.
# 13 live posts carried it. Strip at the source so every caller benefits.

_PREAMBLE_START = re.compile(r"^\s*\*{0,2}(Thinking\.\.\.|Thinking Process:)")
_TERMINATOR = re.compile(
    r"^\s*(?:\*{0,2}\s*)?(?:Let's write it\.?|Let's write\.?|Let's assemble\.?|"
    r"Let's draft it\.?|\.\.\.done thinking\.?)\s*$",
    re.IGNORECASE,
)
# Lines that are unmistakably scratchpad rather than article prose.
_SCRATCH = re.compile(
    r"^(?:\d+\.\s+\*\*|\*{1,2}\(|\*{0,2}Thinking|Let's\b|\.\.\.|"
    r"\*{1,2}[A-Z][A-Za-z0-9 /]{0,30}:\*?)"
)


def _is_scratch(line: str) -> bool:
    s = line.strip()
    if not s:
        return True
    if line.startswith("    ") or line.startswith("\t"):
        return True
    return bool(_SCRATCH.match(s))


def strip_thinking(text: str) -> str:
    """Remove leaked model reasoning from generated text.

    Returns the article only. If no preamble is detected the text is
    returned unchanged — this must never eat legitimate content.
    """
    if not text:
        return text

    # Fenced form: ```\nThinking Process: ... ```
    text = re.sub(r"```\s*\n?\s*Thinking Process:.*?```", "", text, flags=re.DOTALL)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)

    lines = text.split("\n")
    start = next((i for i, l in enumerate(lines) if _PREAMBLE_START.match(l)), None)
    if start is None:
        return text.strip()

    # Use the LAST terminator: models often write "Let's write it." mid-scratchpad
    # and only close with "...done thinking."
    terms = [i for i in range(start + 1, len(lines)) if _TERMINATOR.match(lines[i])]
    if not terms:
        return text.strip()
    last = terms[-1]

    out = []
    for line in lines[last + 1:]:
        # Drop leading blank lines and residual scratchpad notes, but keep the
        # byline and everything after it.
        if not out:
            if _is_scratch(line):
                if line.strip().startswith("**By ") or line.strip().startswith("*By "):
                    out.append(line)
                continue
        out.append(line)

    result = "\n".join(out).strip()
    return result if result else text.strip()


def load_minimax_key() -> str:
    """Load MiniMax API key from secrets file."""
    try:
        return MINIMAX_KEY_PATH.read_text().strip()
    except Exception:
        return ""


def load_minimax_key_candidates() -> list:
    """Every known MiniMax key, most-preferred first.

    The MINIMAX_API_KEY env var and the secrets file can drift apart: a key
    gets rotated in one place but not the other, and the stale one answers
    401 "invalid api key". Callers must try each candidate in turn instead of
    trusting whichever comes first — on 2026-10-31 the env var was a dead key
    while the file still held the live one, which silently degraded every
    MiniMax call in the pipeline to a template fallback.
    """
    keys = []
    env = os.environ.get("MINIMAX_API_KEY", "").strip()
    if env:
        keys.append(env)
    file_key = load_minimax_key()
    if file_key and file_key not in keys:
        keys.append(file_key)
    return keys


def _key_candidates(api_key: str = "") -> list:
    """Every key worth trying, preferred first.

    The env var and the secrets file drift apart — a key gets rotated in one
    place but not the other. On 2026-11-04 the env var was a dead key (401)
    while the file still held the live one. Callers must try each in turn
    instead of trusting whichever comes first.
    """
    keys = []
    if api_key:
        keys.append(api_key)
    for k in load_minimax_key_candidates():
        if k and k not in keys:
            keys.append(k)
    return keys


def _model_candidates(model: str = "") -> list:
    """The preferred model first, then the live fallbacks."""
    models = []
    for m in ([model] if model else [MINIMAX_MODEL]) + list(MINIMAX_MODEL_FALLBACKS):
        if m and m not in models:
            models.append(m)
    return models


def _call_once(body: bytes, key: str, timeout: int):
    """One API attempt. Returns (text, error, retryable, truncated)."""
    try:
        req = urllib.request.Request(
            MINIMAX_URL,
            data=body,
            headers={
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
                "x-api-key": key,
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            resp = json.loads(r.read())
        for block in resp.get("content", []):
            if block.get("type") == "text":
                text = strip_thinking(block["text"])
                if text:
                    if resp.get("stop_reason") == "max_tokens":
                        # The answer was cut off by the token limit. It is
                        # still usable, but a larger budget almost always
                        # produces a complete one, so the caller is told.
                        return text, "truncated at the token limit", True, True
                    return text, "", True, False
        return None, "empty content", True, False
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:
            pass
        err = f"HTTP {e.code} {e.reason} {detail}".strip()
        if e.code == 404:
            # The model is gone from the catalogue — trying it again is
            # pointless, move on to the next model.
            return None, err, False, False
        if e.code in (401, 403):
            # The key is bad — trying it again is pointless, move on to the
            # next key.
            return None, err, False, False
        if e.code in (429, 500, 502, 503, 504):
            return None, err, True, False
        return None, err, False, False
    except Exception as e:
        return None, f"{type(e).__name__}: {e}", True, False


def minimax_generate(prompt: str, api_key: str = "", max_tokens: int = 600, system: str = "",
                    temperature: float = 0.7, timeout: int = 180, retries: int = 3,
                    model: str = "") -> str | None:
    """
    Call MiniMax with retries. Returns generated text or None on failure.

    `model` defaults to MINIMAX_MODEL so every existing caller is unchanged.
    Pass a model id explicitly to opt into a different one.

    The call is resilient in three ways, because each of these has already
    cost the pipeline a day of good content:

      * every known API key is tried in turn (the env var and the secrets
        file drift apart, and one of them goes stale),
      * every known model is tried in turn (MiniMax retires models without
        warning), and
      * the reason for a total failure is printed, so a degraded run is
        visible in the log instead of showing up as a bland template post.

    NOTE: reasoning models (e.g. MiniMax-M2.7) spend a large part of their
    token budget on thinking before producing any text — the requested
    `max_tokens` is raised to REASONING_MIN_TOKENS for them, because a small
    cap yields an empty completion rather than prose.
    """
    keys = _key_candidates(api_key)
    if not keys:
        print("[minimax] no API key available", file=sys.stderr)
        return None

    if system:
        messages = [{"role": "user", "content": system + "\n\n" + prompt}]
    else:
        messages = [{"role": "user", "content": prompt}]

    last_err = ""
    for m in _model_candidates(model):
        budget = max_tokens
        if _is_reasoning_model(m):
            budget = max(budget, REASONING_MIN_TOKENS)
        body = json.dumps({
            "model": m,
            "max_tokens": min(budget, 8192),
            "temperature": temperature,
            "messages": messages,
        }).encode()

        for k in keys:
            for attempt in range(1, retries + 1):
                text, err, retryable, truncated = _call_once(body, k, timeout)
                if text is not None and not truncated:
                    return text
                last_err = f"{m} with key ...{k[-4:]}: {err}"
                if truncated:
                    # The model ran out of budget mid-article. Give it the
                    # full allowance and ask again — a short post is worse
                    # than a slow one. If it truncates again, the partial
                    # text is returned rather than thrown away: the callers
                    # that care about length have their own repair step.
                    if budget < 8192:
                        budget = 8192
                        body = json.dumps({
                            "model": m,
                            "max_tokens": min(budget, 8192),
                            "temperature": temperature,
                            "messages": messages,
                        }).encode()
                        continue
                    return text
                if not retryable:
                    break
                if attempt < retries:
                    time.sleep(2 ** attempt)  # 2, 4 seconds

    # A failure here used to be invisible: the caller fell back to a
    # template and the log said nothing at all.
    print(f"[minimax] generation failed: {last_err}", file=sys.stderr)
    return None