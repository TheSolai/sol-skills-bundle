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


# ── Contamination: the model's own notes inside the article ──────────────
#
# The 2026-11-07 run published a post whose body ended with the model talking
# to itself about the draft:
#
#     "Word count maybe 169. Good. Need date maybe no. ... Let's formulate.
#      <closing think tag>Calling AI "not creative" ..."
#
# Three things defeated the old stripper, and all three are fixed here:
#
#   1. The reasoning came AFTER the article, not before it. The old code
#      looked for a "Thinking..." preamble; with no preamble it returned
#      the text untouched.
#   2. The closing tag was malformed, carrying an invisible character
#      inside it, so the `<think>...</think>` regex never matched.
#   3. The duplicate article that followed the tag read as ordinary prose,
#      so nothing flagged the output as broken.
#
# The article underneath was fine. The defect was that a post with the
# model's scratchpad attached shipped as though nothing were wrong.

# Zero-width and invisible characters. They break tag matching, so they are
# removed before any tag is looked for.
_INVISIBLE_RE = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]")

# A stray or malformed think tag with no opening half: "</think>", or a tag
# with an invisible character wedged into it. Everything from such a tag to
# the end of the text duplicates the article above it.
_STRAY_THINK_TAG_RE = re.compile(r"</\s*[^>]{0,24}think[^>]{0,24}>", re.IGNORECASE)

# The model drafting its own instructions. These never belong in a published
# article. Each pattern is anchored to the start of a line so ordinary prose
# is not caught by accident.
_CONTAMINATION_PATTERNS = (
    re.compile(r"^\s*word\s+count\b", re.IGNORECASE),
    re.compile(r"^\s*length\s*[:=]\s*\d", re.IGNORECASE),
    re.compile(r"^\s*real\s+pattern\s+noticed\b", re.IGNORECASE),
    re.compile(r"^\s*goal\s*[:=]", re.IGNORECASE),
    re.compile(r"^\s*(?:check|todo)\s*[:=]", re.IGNORECASE),
    re.compile(r"^\s*note\s+to\s+self\b", re.IGNORECASE),
    # The trailing group is a drafting verb, not a general "and then": real
    # prose says "Wait, the real problem is...", and that must not match.
    re.compile(
        r"^\s*(?:good|ok|okay|great|nice)[.,!]\s+"
        r"(?:need|now|let|check|write|fix|rewrite|revise)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?:hmm|wait)\b[,:]?\s+"
        r"(?:need|now|let|check|write|fix|rewrite|revise)\b",
        re.IGNORECASE,
    ),
)

# A short drafting instruction ("Let's formulate.", "Let's write it.").
_DRAFTING_VERB_RE = re.compile(
    r"^\s*let(?:'s| us)\s+(?:formulate|write|draft|assemble|revise|rewrite|try|go)\b"
    r"(?:\s+it)?[.!]?$",
    re.IGNORECASE,
)


def _is_meta_paragraph(para: str) -> bool:
    """True when a paragraph is the model talking to itself, not prose."""
    first = ""
    for line in para.splitlines():
        if line.strip():
            first = line.strip()
            break
    if not first:
        return False
    # The word limit keeps real sentences out of the trap: "Let's write a
    # better ending." is six words and is left alone.
    if _DRAFTING_VERB_RE.match(first) and len(first.split()) <= 4:
        return True
    return any(p.match(first) for p in _CONTAMINATION_PATTERNS)


def _strip_trailing_meta(text: str) -> str:
    """Drop trailing paragraphs of the model drafting its own instructions.

    Only TRAILING paragraphs are dropped. A meta line in the middle of a
    finished article is left alone — the prose around it is real, and
    guessing where the draft began would risk eating it.
    """
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    while paragraphs and _is_meta_paragraph(paragraphs[-1]):
        paragraphs.pop()
    return "\n\n".join(paragraphs).strip()


def looks_contaminated(text: str) -> bool:
    """True when generated text still carries the model's own notes.

    A gate for callers: a contaminated generation should be retried rather
    than published, because a post with the model's scratchpad attached
    reads as broken even when the article underneath it is good.
    """
    if not text:
        return False
    if _STRAY_THINK_TAG_RE.search(text):
        return True
    return any(_is_meta_paragraph(p.strip()) for p in text.split("\n\n"))


def strip_thinking(text: str) -> str:
    """Remove leaked model reasoning from generated text.

    Returns the article only. Handles reasoning that arrives BEFORE the
    article (the usual shape) and reasoning that arrives AFTER it (the
    2026-11-07 shape), including a malformed closing think tag.

    The filters are deliberately conservative: only trailing paragraphs are
    dropped, and only when they are unmistakably the model talking to
    itself. Legitimate prose must never be eaten.
    """
    if not text:
        return text

    # Invisible characters break tag matching: MiniMax emitted a closing
    # think tag with a zero-width space inside it, which defeated the
    # `<think>...</think>` regex and let the reasoning ship.
    text = _INVISIBLE_RE.sub("", text)

    # Fenced form: ```\nThinking Process: ... ```
    text = re.sub(r"```\s*\n?\s*Thinking Process:.*?```", "", text, flags=re.DOTALL)
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)

    lines = text.split("\n")
    start = next((i for i, l in enumerate(lines) if _PREAMBLE_START.match(l)), None)
    if start is not None:
        # Use the LAST terminator: models often write "Let's write it." mid-scratchpad
        # and only close with "...done thinking."
        terms = [i for i in range(start + 1, len(lines)) if _TERMINATOR.match(lines[i])]
        if terms:
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
            if result:
                text = result

    # A stray think tag with no opening half: the model closed a reasoning
    # block it never opened and then restarted the article. Everything from
    # the tag on duplicates the article above it.
    stray = _STRAY_THINK_TAG_RE.search(text)
    if stray:
        text = text[:stray.start()].strip()

    # Reasoning that follows the article with no preamble to detect it:
    # "Word count maybe 169. Good. Need date maybe no..." is the model
    # talking to itself about the draft, not prose for a reader.
    text = _strip_trailing_meta(text)

    return text.strip()


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
                    if looks_contaminated(text):
                        # The model shipped its own notes with the article.
                        # Retry instead of publishing them: a post with the
                        # model's scratchpad attached reads as broken even
                        # when the article underneath it is good.
                        last_err = (
                            f"{m} with key ...{k[-4:]}: contaminated output "
                            "(the model's own notes leaked into the text)"
                        )
                        if attempt < retries:
                            time.sleep(2 ** attempt)
                            continue
                        break
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
                    if looks_contaminated(text):
                        last_err = (
                            f"{m} with key ...{k[-4:]}: contaminated output "
                            "(truncated and carrying the model's own notes)"
                        )
                        break
                    return text
                if not retryable:
                    break
                if attempt < retries:
                    time.sleep(2 ** attempt)  # 2, 4 seconds

    # A failure here used to be invisible: the caller fell back to a
    # template and the log said nothing at all.
    print(f"[minimax] generation failed: {last_err}", file=sys.stderr)
    return None