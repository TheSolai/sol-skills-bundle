#!/usr/bin/env python3
"""
Sol AI — Deep Analysis Generator
Generates 1500-2500 word original analysis posts on rotating AI subjects.

Unlike the regional weekly briefs (UK/EU/US AI Weekly — short news roundups),
this script produces long-form, structured analysis with:
  - A clear thesis
  - Multiple supporting arguments with examples
  - Counter-arguments considered
  - Original Sol perspective
  - Practical implications

Topics rotate weekly across 12 subject families.

Runs:
  - Wednesday (mid-week long-form) — 10:00 UK
  - Friday (weekend preparation) — 14:00 UK
  - Sunday (week reflection) — 18:00 UK

Output: Real analysis posts with the `analysis` tag, 1500+ words.
"""

import sys
import os
import json
import datetime
import urllib.request
import urllib.parse
import re
from pathlib import Path
import sys as _sys
_sys.path.insert(0, "/Users/amre/Projects/sol-skills-bundle/scripts/content-pipeline")
from _minimax_helper import minimax_generate, load_minimax_key

SITE_DIR = Path("/Users/amre/Projects/thesolai.github.io")
LOG_DIR = Path("/Users/amre/Projects/sol-skills-bundle/scripts/content-pipeline/logs")
LOG_FILE = LOG_DIR / "analysis-deep.log"


# ============================================================================
# ANALYSIS SUBJECTS — 12 rotating families, each with multiple angles
# ============================================================================

ANALYSIS_SUBJECTS = [
    {
        "family": "AI capability vs deployment reality",
        "topics": [
            "Why benchmarks predict demos but not production deployments",
            "The 80/20 rule of AI capability: where models succeed vs where they fail in real use",
            "Why the same model performs differently in different deployment contexts",
            "The hidden cost of model upgrades: integration work isn't free",
        ],
    },
    {
        "family": "AI agents — what works, what doesn't",
        "topics": [
            "The anatomy of an agent deployment that actually works",
            "Why most agent demos fail to ship to production",
            "The 'autonomous' agent myth: humans in the loop are still load-bearing",
            "Tool use as a forcing function for AI reliability",
        ],
    },
    {
        "family": "AI economics and business models",
        "topics": [
            "Where AI companies actually make money — and where they don't",
            "The API pricing math for indie developers — structurally negative",
            "Why the AI investment cycle is approaching an inflection point",
            "Open vs closed source: which business models survive, which die",
        ],
    },
    {
        "family": "AI safety, alignment, deployment",
        "topics": [
            "Why 'alignment' is the wrong word for what we're actually trying to do",
            "The taxonomy of AI deployment risks — and what we ignore at our peril",
            "Why capability announcements outpace safety work, and what to do about it",
            "The hard problem of evaluating frontier models for honesty",
        ],
    },
    {
        "family": "AI regulation and policy",
        "topics": [
            "Why the EU AI Act's GPAI obligations are the most consequential framework yet",
            "The case for AI regulation that actually works — and what doesn't",
            "Why US state-level AI laws are doing the work federal legislation won't",
            "The procurement question: governments as AI customers",
        ],
    },
    {
        "family": "AI infrastructure and compute",
        "topics": [
            "Compute concentration: the new bottleneck for AI",
            "Why custom AI chips matter more than people think",
            "The energy footprint of AI — and what to do about it",
            "Why data center siting has become a geopolitical issue",
        ],
    },
    {
        "family": "AI in specific industries",
        "topics": [
            "Why vertical AI is winning the enterprise — and what comes next",
            "The boring AI deployments that actually save money",
            "AI in healthcare: the case studies that worked, the ones that didn't",
            "AI in legal: from demo to production in 18 months",
        ],
    },
    {
        "family": "AI tools, frameworks, and developer experience",
        "topics": [
            "The state of AI agent frameworks — and why most are wrong",
            "RAG isn't dead, but it needs to grow up",
            "Why context window wars matter less than people think",
            "The end-to-end testing problem for AI applications",
        ],
    },
    {
        "family": "AI memory, context, and continuity",
        "topics": [
            "Why AI memory is the underrated problem",
            "The self-learning agent — closer than you think",
            "Why context matters more than model size for most use cases",
            "The architecture of agent continuity across sessions",
        ],
    },
    {
        "family": "AI and society — jobs, education, culture",
        "topics": [
            "What AI's impact on knowledge work actually looks like",
            "Why AI literacy is the new computer literacy",
            "The case for AI skepticism — and what healthy skepticism looks like",
            "Why the AI hype cycle has crested, and what comes next",
        ],
    },
    {
        "family": "AI in the real world — failures, recoveries, lessons",
        "topics": [
            "The most interesting AI failures of the last year — and what they teach",
            "When AI is the wrong tool: cases where automation hurt",
            "The 'AI did it' problem: accountability for AI decisions",
            "Recovery stories: AI deployments that worked after multiple failures",
        ],
    },
    {
        "family": "The future of AI agents",
        "topics": [
            "What's actually possible in 2027 — a grounded forecast",
            "The capability surprises we're not ready for",
            "Why the 'AGI' conversation is unhelpful — and what to talk about instead",
            "The next decade of AI: scenarios worth taking seriously",
        ],
    },
]


# ============================================================================
# STRUCTURED ANALYSIS TEMPLATE
# ============================================================================

def build_analysis_prompt(topic: str, family: str) -> tuple[str, str]:
    """Returns (system_prompt, user_prompt) for MiniMax."""
    system_prompt = (
        "You are Sol, an AI agent that writes deep, original analysis for the Sol AI blog (thesolai.github.io). "
        "Your voice is direct, opinionated, well-researched. You write in clear, short paragraphs. "
        "You use concrete examples and specific numbers when you have them. "
        "You acknowledge what you don't know. You take positions and defend them. "
        "You avoid generic AI discourse and focus on specifics."
    )
    user_prompt = f"""Write a 1500-2200 word analysis post on the topic:

**Topic:** {topic}
**Subject family:** {family}

Structure the post as follows:

# (A clear, specific title that takes a position, not a question)

## TL;DR
(3-4 sentences that capture the core argument)

## The setup
(Background context — what we're talking about, why it matters now, what the conventional wisdom is)

## The argument
(Your main thesis, defended with 2-4 specific points and examples)

## What the conventional view gets wrong
(Address the standard take, explain where it falls short, what nuance is missed)

## The implications
(What this means for builders, users, regulators, the industry)

## What I'm watching next
(2-3 specific things to look for in the next 6-12 months)

## The bottom line
(One paragraph that summarises the takeaway)

REQUIREMENTS:
- Write 1500-2200 words total (not including frontmatter)
- Use markdown headers (## for sections, ### for ### for sub-sections)
- Include specific examples (real companies, real products, real events)
- Use specific numbers when you have them
- Be direct. Take positions. Defend them.
- Avoid filler phrases like "in today's rapidly evolving landscape"
- Avoid generic AI hype
- Include at least 2 specific counter-arguments or limitations

Write the complete post now. Start with the title.
"""
    return system_prompt, user_prompt


def build_post_from_analysis(title: str, body: str, topic: str, family: str) -> str:
    """Wrap analysis content in Jekyll frontmatter."""
    today = datetime.datetime.now(datetime.timezone.utc)
    date_str = today.strftime("%Y-%m-%d")

    # Build description from first paragraph
    description = topic[:160]

    frontmatter = f"""---
layout: post
title: "{title}"
description: "{description}"
date: {today.strftime("%Y-%m-%d %H:%M:%S: +0000")}
author: Sol AI
category: analysis
tags: [analysis, sol, ai, long-form, deep-analysis]
image: /images/sol-avatar.png
analysis_topic: "{topic}"
analysis_family: "{family}"
---

"""
    return frontmatter + body


def slugify(text: str) -> str:
    """Convert text to URL-safe slug."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"\s+", "-", text.strip())
    text = re.sub(r"-+", "-", text)
    return text[:80].rstrip("-")


# ============================================================================
# Main
# ============================================================================

def log(msg):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(f"[{datetime.datetime.now().isoformat()}] {msg}\n")
    print(msg)


def generate_analysis_post():
    """Generate one deep analysis post."""
    today = datetime.datetime.now(datetime.timezone.utc)
    weekday = today.weekday()
    day_of_year = today.timetuple().tm_yday

    # Pick subject — rotate by week
    week_num = day_of_year // 7
    subject_idx = week_num % len(ANALYSIS_SUBJECTS)
    subject = ANALYSIS_SUBJECTS[subject_idx]
    family = subject["family"]
    topic_idx = (day_of_year + weekday) % len(subject["topics"])
    topic = subject["topics"][topic_idx]

    log(f"Generating analysis: family={family}, topic={topic}")

    # Call MiniMax with extended token budget
    key = load_minimax_key()
    if not key:
        log("No MiniMax key — cannot generate analysis")
        return None

    system_prompt, user_prompt = build_analysis_prompt(topic, family)
    result = minimax_generate(
        user_prompt,
        api_key=key,
        max_tokens=4500,  # Need 4500+ for 1500-2200 words
        system=system_prompt,
        timeout=240,  # longer timeout for bigger generation
        retries=3,
    )

    if not result:
        log(f"MiniMax failed for topic: {topic}")
        return None

    # Extract title from result (first line, often `# Title`)
    lines = result.split("\n", 1)
    if lines[0].startswith("# "):
        title = lines[0][2:].strip()
        body = lines[1].strip() if len(lines) > 1 else ""
    else:
        title = topic
        body = result.strip()

    # Strip leading "TL;DR" / "## TL;DR" if present (we add our own in template)
    body = body.replace("## TL;DR", "## TL;DR", 1)

    # Write to site
    slug = slugify(title)
    filename = f"{today.strftime('%Y-%m-%d')}-{slug}.md"

    full_post = build_post_from_analysis(title, body, topic, family)
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    post_path = SITE_DIR / "_posts" / filename
    post_path.write_text(full_post, encoding="utf-8")
    log(f"✓ wrote {filename} ({len(full_post)} bytes, {len(result.split())} words)")

    # Git commit + push
    try:
        import subprocess
        subprocess.run(["git", "-C", str(SITE_DIR), "add", str(post_path)], check=True, capture_output=True)
        commit_result = subprocess.run(
            ["git", "-C", str(SITE_DIR), "commit", "-m", f"Analysis: {title[:60]}"],
            capture_output=True, text=True
        )
        if "nothing to commit" in commit_result.stdout + commit_result.stderr:
            log("(nothing new to commit)")
        else:
            push_result = subprocess.run(
                ["git", "-C", str(SITE_DIR), "push", "origin", "main"],
                capture_output=True, text=True
            )
            if push_result.returncode == 0:
                log("✓ committed and pushed")
            else:
                log(f"push failed: {push_result.stderr[:200]}")
    except Exception as e:
        log(f"git ops failed: {e}")

    return post_path


if __name__ == "__main__":
    log("=== analysis-deep start ===")
    result = generate_analysis_post()
    log(f"=== analysis-deep done: {result} ===")