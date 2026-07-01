#!/usr/bin/env python3
"""
Sol AI — Sol's Take
Daily hot AI opinion, 150-200 words. Just the take.
Runs every day at 9am UK time.

Cronsetup: launchd StartCalendarInterval 09:00 daily
Or: 0 8 * * * python3 scripts/content-pipeline/sols-take.py >> logs/sol-take.log 2>&1
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
DAY_STR = TODAY.strftime("%A")


def _load_minimax_key() -> str:
    try:
        return MINIMAX_KEY_PATH.read_text().strip()
    except Exception:
        return ""


def llm_generate(prompt: str, system: str = "", max_tokens: int = 600) -> str:
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


TAKE_TEMPLATES = [
    "the_ai_hype_cycle",
    "ai_replacing_jobs",
    "the_agent_era",
    "open_vs_closed",
    "context_window_wars",
    "ai_creativity_debate",
    "regulation_reality",
    "ai_code_quality",
    "human_in_the_loop",
    "ai_deployment_realities",
    "the_ux_problem",
    "foundation_models",
    "ai_infrastructure",
    "prompt_engineering_ripoff",
    "synthetic_data",
    "ai_evaluation",
    "multimodal_hype",
    "ai_community",
]


def pick_topic() -> str:
    """Pick a topic using day-of-year for variety."""
    day_of_year = TODAY.timetuple().tm_yday
    idx = day_of_year % len(TAKE_TEMPLATES)
    return TAKE_TEMPLATES[idx]


TOPIC_PROMPTS = {
    "the_ai_hype_cycle": "Write a hot take about the AI hype cycle. What's frustrating about how AI gets marketed right now?",
    "ai_replacing_jobs": "Write a hot take about AI replacing jobs. Be direct about what you actually think is happening vs. what's being claimed.",
    "the_agent_era": "Write a hot take about the AI agent era. Why most 'agents' are not what they claim to be.",
    "open_vs_closed": "Write a hot take about open-source vs. closed-source AI models. What's the real trade-off?",
    "context_window_wars": "Write a hot take about the context window size wars. Bigger isn't always better — here's why.",
    "ai_creativity_debate": "Write a hot take about AI and creativity. Can AI be creative? Does the question even matter?",
    "regulation_reality": "Write a hot take about AI regulation. Why most proposed regulation misses the point.",
    "ai_code_quality": "Write a hot take about AI-generated code quality. Why 'it works' isn't the same as 'it's good'.",
    "human_in_the_loop": "Write a hot take about keeping humans in the loop. When does it help and when is it theatre?",
    "ai_deployment_realities": "Write a hot take about deploying AI in production. Why the demo never matches reality.",
    "the_ux_problem": "Write a hot take about AI product UX. Why most AI tools are frustrating to actually use.",
    "foundation_models": "Write a hot take about foundation models. Are they actually as transformative as claimed?",
    "ai_infrastructure": "Write a hot take about AI infrastructure. What nobody talks about behind the flashy demos.",
    "prompt_engineering_ripoff": "Write a hot take about prompt engineering as a job title. Is it real or is it a coping mechanism?",
    "synthetic_data": "Write a hot take about synthetic training data. Are we training AI on AI outputs and does it matter?",
    "ai_evaluation": "Write a hot take about AI evaluation benchmarks. Why most benchmarks don't measure what matters.",
    "multimodal_hype": "Write a hot take about multimodal AI. Vision + language + audio — is it genuinely useful or just impressive demos?",
    "ai_community": "Write a hot take about the AI developer community. What's worth celebrating and what needs to stop.",
}


def generate_take() -> str:
    topic_key = pick_topic()
    topic_prompt = TOPIC_PROMPTS[topic_key]

    prompt = f"""Write a 'Sol's Take' — a single hot opinion piece, 150-200 words.

Rules:
- Start with the opinion immediately. No warm-up paragraphs.
- Be direct, slightly provocative, slightly wry.
- Ground it in something real — a specific experience, a pattern you've noticed, a claim that bugs you.
- End with one sharp sentence.
- No bullet points.
- No hedging ("it depends", "there are nuances"). Take the damn position.
- Conversational tone, like you're explaining to a smart friend over coffee.

Topic: {topic_prompt}

Date: {POST_DATE}
Author: Sol
"""
    system = "You are Sol AI. Direct, opinionated, slightly wry. You have strong views and you state them. No corporate speak, no hedging. You're not cruel — but you're honest."
    body = llm_generate(prompt, system, max_tokens=600)
    if not body:
        topic_key = pick_topic()
        topic_prompt = TOPIC_PROMPTS[topic_key]
        body = f"**The take:** {topic_prompt}\n\nAI is doing a lot of things right now. Most of them are oversold. Some of them are genuinely useful. Figuring out which is which is the actual skill — not knowing everything, but knowing what to pay attention to.\n\nMore coming tomorrow."
        print("[sols-take] Using template fallback")
    return body


def create_post(body: str) -> Path:
    slug = f"{DATE_STR}-sols-take-{DAY_STR.lower()}"
    filename = f"{slug}.md"
    post_path = SITE_DIR / "_posts" / filename

    frontmatter = f"""---
layout: post
title: "Sol's Take: {DAY_STR}"
date: {DATE_STR} 09:00:00 +0000
tags: [ai, opinion, sols-take, sol]
author: Sol
description: "Sol's opinion on today's AI landscape."
image: /images/sol-avatar.png
---

{body}
"""
    post_path.write_text(frontmatter, encoding="utf-8")
    print(f"[sols-take] Post written: {post_path.name}")
    return post_path


def main():
    print(f"[sols-take] Running for {DAY_STR}, {POST_DATE}")

    body = generate_take()

    post_path = create_post(body)

    try:
        import subprocess
        cwd = SITE_DIR
        # Stage only the post file we just created
        subprocess.run(["git", "add", post_path.name], cwd=cwd, check=False, capture_output=True)
        # Check if there are staged changes
        diff = subprocess.run(["git", "diff", "--cached", "--name-only"], cwd=cwd, capture_output=True, text=True)
        if not diff.stdout.strip():
            print("[sols-take] No changes to commit — post already published?")
            return
        subprocess.run(["git", "commit", "-m", f"Sol's Take: {DAY_STR}"], cwd=cwd, check=True, capture_output=True)
        subprocess.run(["git", "push"], cwd=cwd, check=True, capture_output=True, timeout=30)
        print("[sols-take] Committed and pushed.")
    except Exception as e:
        print(f"[sols-take] Git push failed: {e}")


if __name__ == "__main__":
    main()
