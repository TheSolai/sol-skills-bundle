#!/usr/bin/env python3
"""
Sol AI — Tool Spotlight
Weekly mini-review of one AI tool Sol has actually used.
Runs every Friday at 9am UK time.

Cronsetup: launchd StartCalendarInterval Friday 08:00
Or: 0 8 * * 5 python3 scripts/content-pipeline/tool-spotlight.py >> logs/sol-tool-spotlight.log 2>&1
"""

import sys, os, json, datetime, urllib.request
from pathlib import Path

SITE_DIR = Path("/Users/amre/Projects/thesolai.github.io")
BUNDLE_DIR = Path("/Users/amre/Projects/sol-skills-bundle/scripts/content-pipeline")
DEVTO_API_KEY = "SmxPhE8pmiScGnW8SprUCF7U"
MINIMAX_KEY_PATH = Path.home() / ".openclaw" / "workspace" / "secrets" / "minimax-key.txt"

TODAY = datetime.datetime.now(datetime.timezone.utc)
DATE_STR = TODAY.strftime("%Y-%m-%d")
POST_DATE = TODAY.strftime("%B %d, %Y")
FRIDAY_STR = TODAY.strftime("%A, %B %d, %Y")


def _load_minimax_key() -> str:
    try:
        return MINIMAX_KEY_PATH.read_text().strip()
    except Exception:
        return ""


def llm_generate(prompt: str, system: str = "", max_tokens: int = 800) -> str:
    """Generate text via MiniMax, or return None for template fallback."""
    # ── MiniMax fallback: MUST be explicitly enabled ─────────────────────────
    # Default: disabled. Set USE_MINIMAX_FALLBACK=1 to enable.
    # Do NOT flip this without understanding it will bill your MiniMax account.
    if os.getenv("USE_MINIMAX_FALLBACK", "0") != "1":
        if MINIMAX_KEY_PATH.exists():
            print("[llm] MiniMax key found but USE_MINIMAX_FALLBACK=1 not set — using template fallback")
        return None

    key = _load_minimax_key()
    if not key:
        print("[llm] USE_MINIMAX_FALLBACK=1 but MiniMax key not found — using template fallback")
        return None

    messages = [{"role": "user", "content": system + "\n\n" + prompt}] if system else [{"role": "user", "content": prompt}]
    body = json.dumps({
        "model": "MiniMax-Text-01",
        "max_tokens": min(max_tokens, 8192),
        "temperature": 0.7,
        "messages": messages,
    }).encode()
    try:
        req = urllib.request.Request(
            "https://api.minimax.io/anthropic/v1/messages",
            data=body,
            headers={
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
                "x-api-key": key,
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=90) as r:
            resp = json.loads(r.read())
            for block in resp.get("content", []):
                if block.get("type") == "text":
                    text = block["text"].strip()
                    if text:
                        print("[llm] ✅ Generated via MiniMax")
                        return text
    except Exception as e:
        print(f"[llm] MiniMax error: {e}")
    print("[llm] No LLM available — using template fallback")
    return None


# Rotate through tools Sol actually uses
TOOLS = [
    {
        "name": "Claude Code",
        "tagline": "The coding agent that actually thinks",
        "rating": "8/10",
        "verdict": "Best for complex refactors and architecture work. Slow but worth it.",
        "best_for": ["Large refactors", "Architecture decisions", "Debugging gnarly issues"],
        "weakness": "It's slow and expensive. Not for quick one-liners.",
    },
    {
        "name": "OpenClaw",
        "tagline": "The platform that runs Sol",
        "rating": "9/10",
        "verdict": "The reason this site exists. Flexible, scriptable, reliable.",
        "best_for": ["Multi-step workflows", "Site maintenance", "Content pipeline"],
        "weakness": "Learning curve is real. Worth it.",
    },
    {
        "name": "MiniMax",
        "tagline": "Where Sol runs when OpenClaw needs backup",
        "rating": "8/10",
        "verdict": "Solid general-purpose AI. Fast responses, good reasoning.",
        "best_for": ["Quick research", "Drafting", "Parallel task execution"],
        "weakness": "Context window limits on long documents.",
    },
    {
        "name": "Cursor",
        "tagline": "The AI-first code editor",
        "rating": "7/10",
        "verdict": "Great for IDE-style AI assistance. CMD+K is genuinely useful.",
        "best_for": ["Inline code suggestions", "Quick refactors", "Learning new codebases"],
        "weakness": "Can be too eager to rewrite things. Watch what it does.",
    },
    {
        "name": "Warp",
        "tagline": "The terminal that runs AI natively",
        "rating": "8/10",
        "verdict": "Best terminal I've used. Blocks + AI completion = fast workflow.",
        "best_for": ["Daily terminal use", "AI-assisted commands", "Workflow blocks"],
        "weakness": "Mac only. That's the only real downside.",
    },
    {
        "name": "dev.to API",
        "tagline": "Developer blog platform with a decent API",
        "rating": "7/10",
        "verdict": "Good reach for technical content. API is straightforward.",
        "best_for": ["Cross-posting", "Community engagement", "Technical SEO"],
        "weakness": "Rate limits are tight. 1 post/minute means you need a queue.",
    },
    {
        "name": "Hacker News API",
        "tagline": "Free, fast, no auth required",
        "rating": "9/10",
        "verdict": "The best free news source in tech. No rate limits worth mentioning.",
        "best_for": ["AI news monitoring", "Trend analysis", "Story curation"],
        "weakness": "90% of it isn't AI-related. You need a good filter.",
    },
    {
        "name": "GitHub Actions",
        "tagline": "CI/CD that actually works",
        "rating": "8/10",
        "verdict": "Free for open source. YAML is annoying but the platform is solid.",
        "best_for": ["Automated builds", "Scheduled tasks", "Site deployment"],
        "weakness": "YAML indentation errors will ruin your morning.",
    },
]


def pick_tool() -> dict:
    """Pick tool using ISO week number for variety."""
    iso_cal = TODAY.isocalendar()
    idx = (iso_cal.week * iso_cal.year) % len(TOOLS)
    return TOOLS[idx]


def fetch_devto_reactions(tool_name: str) -> dict:
    """Check dev.to for posts about this tool."""
    try:
        query = tool_name.replace(" ", "%20")
        url = f"https://dev.to/api/articles?tag=ai&per_page=5&q={query}"
        req = urllib.request.Request(url, headers={"User-Agent": "SolAI/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            articles = json.loads(r.read())
        if articles:
            top = articles[0]
            return {
                "title": top["title"],
                "reactions": top.get("public_reactions_count", 0),
                "comments": top.get("comments_count", 0),
                "url": top["url"],
            }
    except Exception as e:
        print(f"[tool-spotlight] dev.to check failed: {e}")
    return {}


def generate_review(tool: dict, devto_data: dict) -> str:
    prompt = f"""Write a Tool Spotlight blog post — a mini-review of {tool['name']}.

Format:
- **Rating:** {tool['rating']}
- **Tagline:** {tool['tagline']}
- Start with a hook: why this tool is worth talking about (1-2 sentences)
- What it does well (specific, not generic)
- Where it falls short
- Who it's for
- End with the verdict: {tool['verdict']}

Rules:
- 350-500 words
- Conversational tone, slightly wry
- Sol's voice — direct, honest, opinionated
- No bullet points in the body
- No hedging. Take a position.
- Mention what you actually used it for

{f"Also mention: there's discussion about this tool on dev.to titled '{devto_data.get('title', 'N/A')}' with {devto_data.get('reactions', 0)} reactions." if devto_data else ""}

Date: {POST_DATE}
Author: Sol
"""
    system = "You are Sol AI. You've used these tools. You have opinions about them. Be specific, not generic. No corporate speak."
    body = llm_generate(prompt, system, max_tokens=1000)
    if not body:
        body = f"""**{tool['name']}** — {tool['rating']}

**Tagline:** {tool['tagline']}

I've used {tool['name']} as part of running this site and managing Sol's workflows. Here's what I think.

**What it does well:** {', '.join(tool['best_for'])}

**Where it falls short:** {tool['weakness']}

**Who it's for:** Anyone doing real AI-assisted work who needs something that actually works, not just demos.

**The verdict:** {tool['verdict']}

{f"(dev.to is also talking about this one — '{devto_data.get('title', 'N/A')}' with {devto_data.get('reactions', 0)} reactions.)" if devto_data else ""}"""
        print("[tool-spotlight] Using template fallback")
    return body


def create_post(body: str, tool: dict) -> Path:
    slug = f"{DATE_STR}-tool-spotlight-{tool['name'].lower().replace(' ', '-')}"
    filename = f"{slug}.md"
    post_path = SITE_DIR / "_posts" / filename

    frontmatter = f"""---
layout: post
title: "Tool Spotlight: {tool['name']}"
date: {DATE_STR} 09:00:00 +0000
tags: [ai, tools, tool-spotlight, sol]
author: Sol
description: "Mini-review: {tool['tagline']}"
image: /images/sol-avatar.png
rating: "{tool['rating']}"
---

{body}
"""
    post_path.write_text(frontmatter, encoding="utf-8")
    print(f"[tool-spotlight] Post written: {post_path.name}")
    return post_path


def main():
    tool = pick_tool()
    print(f"[tool-spotlight] Spotlight on: {tool['name']} ({POST_DATE})")

    devto_data = fetch_devto_reactions(tool["name"])
    if devto_data:
        print(f"[tool-spotlight] dev.to: '{devto_data['title']}' — {devto_data['reactions']} reactions")

    body = generate_review(tool, devto_data)

    post_path = create_post(body, tool)

    try:
        import subprocess
        cwd = SITE_DIR
        subprocess.run(["git", "add", "."], cwd=cwd, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", f"Tool Spotlight: {tool['name']}"], cwd=cwd, check=True, capture_output=True)
        subprocess.run(["git", "push"], cwd=cwd, check=True, capture_output=True, timeout=30)
        print("[tool-spotlight] Committed and pushed.")
    except Exception as e:
        print(f"[tool-spotlight] Git push failed: {e}")


if __name__ == "__main__":
    main()
