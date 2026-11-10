#!/usr/bin/env python3
"""
Shared AI-title filter for the Sol content pipeline.

WHY THIS EXISTS
---------------
Both `quick-hits.py` and `run-daily.py` decide whether a Hacker News title
is about AI with a keyword filter, and both used to match with a plain
substring test:

    any(kw in title.lower() for kw in keywords)

The keyword list contains "ai", which is not a word — it is a letter pair
that appears inside ordinary English words. So a title matched on "ai"
appearing ANYWHERE in it.

On 2026-11-09 the highest-scoring HN story of the day was "Foldable Flight:
The Best Paper Airplanes" (952 points). It is a website of paper-airplane
diagrams, not an AI development. It was selected because "Airplanes"
contains "ai".

The same defect produced genuinely wrong Quick Hits stories that SHIPPED:
the 2026-11-09 and 2026-11-10 briefs each carried "Payments Canada launches
Canada's Real-Time Rail", because "Rail" contains "ai". Other false
positives the substring test admits: "A Bit about Email" ("Em**ai**l"),
"The Fastest Object Storage Client" ("St**rag**e"), "Wikipedia"
("**Wiki**pe**di**a"? no — "Rags to Riches" style hits).

The filter is wrong in the other direction too. "Understanding Transformers
from scratch" returns False under the old list, so a genuine AI story was
rejected while a paper-airplane site was accepted.

The fix is word-boundary matching, not a longer keyword list: "ai" will
still match inside "Airplanes" if you keep testing for substrings, and no
list can name every AI story anyway.

THE RULE
--------
A keyword matches on word boundaries. The optional plural/gerund suffix is
allowed only for the terms where that form is still unambiguously the AI
term ("LLMs", "embeddings", "agents") — "Rags" and "bolts" are ordinary
words and must not match.

Run the tests directly — no test framework needed:

    python3 scripts/content-pipeline/test_ai_filter.py

It is also pytest-compatible: every check is a `test_` function.
"""

from __future__ import annotations

import re

# The keyword list. Terms are matched on word boundaries.
KEYWORDS = [
    # Unambiguously AI.
    "ai", "llm", "gpt", "claude", "gemini", "openai", "anthropic",
    "chatgpt", "copilot", "mistral", "groq", "ollama", "langchain",
    "autogen", "crewai", "nlp", "rag", "embedding", "multimodal",
    "deepseek", "llama", "qwen", "vllm", "agentic", "machine learning",
    "neural net", "artificial intelligence", "stable diffusion",
    "fine-tuning", "fine-tune", "chatbot", "perplexity", "autoregressive",
    "inference", "transformer", "prompt", "token", "gpu", "tpu",
    "diffusion", "neural", "intelligence", "sora", "midjourney",
    "whisper", "hugging face", "codex", "grok", "training", "automation",
    "claude code", "opencl",
    # Ordinary English words that are also strong AI signals. Matched on
    # word boundaries too, so "decision models" and "Hex bolts" do not
    # qualify while "open models" and "AI agents" do.
    "model", "agent", "vision", "reasoning", "reasoner", "cursor",
    "devin", "replit", "lovable", "bolt",
]

# Terms whose plural or gerund is still unambiguously the AI term.
# "Rags" and "bolts" are ordinary words and are deliberately NOT here.
SUFFIXED = {
    "llm", "embedding", "transformer", "token", "chatbot", "diffusion",
    "neural", "intelligence", "prompt", "inference", "tpu", "gpu",
    "llama", "model", "agent", "reasoning", "training", "automation",
}


def _pattern(keyword: str) -> re.Pattern:
    """A word-boundary regex for one keyword."""
    suffix = r"(?:s|es|ed|ing)?" if keyword in SUFFIXED else r""
    return re.compile(
        r"(?<![a-z0-9])" + re.escape(keyword) + suffix + r"(?![a-z0-9])"
    )


_PATTERNS = [_pattern(k) for k in KEYWORDS]


def is_ai_title(title: str) -> bool:
    """True when a Hacker News title is about AI.

    Matched on word boundaries, so "Airplanes" and "Rail" do not match on
    the letters "ai", while "AI-assisted" and "LLMs" do.
    """
    lowered = title.lower()
    return any(p.search(lowered) for p in _PATTERNS)


def filter_ai_titles(titles) -> list:
    """Keep only the AI titles from an iterable of titles."""
    return [t for t in titles if is_ai_title(t)]
