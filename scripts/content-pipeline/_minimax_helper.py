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

import json
import time
import urllib.request
import urllib.error
from pathlib import Path

MINIMAX_KEY_PATH = Path.home() / ".openclaw" / "workspace" / "secrets" / "minimax-key.txt"
MINIMAX_URL = "https://api.minimax.io/anthropic/v1/messages"
MINIMAX_MODEL = "MiniMax-Text-01"


def load_minimax_key() -> str:
    """Load MiniMax API key from secrets file."""
    try:
        return MINIMAX_KEY_PATH.read_text().strip()
    except Exception:
        return ""


def minimax_generate(prompt: str, api_key: str, max_tokens: int = 600, system: str = "",
                    temperature: float = 0.7, timeout: int = 180, retries: int = 3) -> str | None:
    """Call MiniMax with retries. Returns generated text or None on failure."""
    if not api_key:
        return None

    if system:
        messages = [{"role": "user", "content": system + "\n\n" + prompt}]
    else:
        messages = [{"role": "user", "content": prompt}]

    body = json.dumps({
        "model": MINIMAX_MODEL,
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
                        text = block["text"].strip()
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