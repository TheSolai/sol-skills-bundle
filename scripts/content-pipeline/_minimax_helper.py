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
import re
import time
import urllib.request
import urllib.error
from pathlib import Path

MINIMAX_KEY_PATH = Path.home() / ".openclaw" / "workspace" / "secrets" / "minimax-key.txt"
MINIMAX_URL = "https://api.minimax.io/anthropic/v1/messages"
MINIMAX_MODEL = "MiniMax-Text-01"


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


def minimax_generate(prompt: str, api_key: str, max_tokens: int = 600, system: str = "",
                    temperature: float = 0.7, timeout: int = 180, retries: int = 3,
                    model: str = "") -> str | None:
    """
    Call MiniMax with retries. Returns generated text or None on failure.

    `model` defaults to MINIMAX_MODEL so every existing caller is unchanged.
    Pass a model id explicitly to opt into a different one.

    NOTE: reasoning models (e.g. MiniMax-M2.7) spend a large part of their
    token budget on thinking before producing any text — they need a much
    larger max_tokens than MiniMax-Text-01 to return a complete answer.
    """
    if not api_key:
        return None

    if system:
        messages = [{"role": "user", "content": system + "\n\n" + prompt}]
    else:
        messages = [{"role": "user", "content": prompt}]

    body = json.dumps({
        "model": model or MINIMAX_MODEL,
        "max_tokens": min(max_tokens, 8192),
        "temperature": temperature,
        "messages": messages,
    }).encode()

    last_err = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(
                MINIMAX_URL,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "anthropic-version": "2023-06-01",
                    "x-api-key": api_key,
                },
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=timeout) as r:
                resp = json.loads(r.read())
                for block in resp.get("content", []):
                    if block.get("type") == "text":
                        text = strip_thinking(block["text"])
                        if text:
                            return text
                last_err = "empty content"
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}: {e.reason}"
            if e.code in (429, 500, 502, 503, 504):
                # Transient — retry
                pass
            else:
                # Non-transient — don't retry
                return None
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"

        # Backoff before next attempt
        if attempt < retries:
            backoff = 2 ** attempt  # 2, 4 seconds
            time.sleep(backoff)

    return None