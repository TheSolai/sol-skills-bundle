#!/usr/bin/env python3
"""
Sol AI — Weekend Deep Dive
Long-form researched piece on one AI topic, 600-800 words.
Runs every Saturday at 9am UK time.

Cronsetup: launchd StartCalendarInterval Saturday 08:00
Or: 0 8 * * 6 python3 scripts/content-pipeline/weekend-deep-dive.py >> logs/sol-deep-dive.log 2>&1

── Fixes applied 2026-10-24 ──────────────────────────────────────────────
Three bugs had been silently degrading every run since July:

1. HN research returned ZERO stories, always.
   The search keyword was built with `topic.split()[0:3]`, which for
   "Why AI code review is both better and worse than human review"
   produced the string "Why AI code". That can never substring-match a
   news headline, so every post was written with "No recent HN
   discussions found" and the promised citations were impossible.
   → Now uses the HN Algolia search API with a per-topic query.

2. dev.to research returned the SAME three articles for every topic.
   The dev.to /api/articles endpoint silently ignores the `q` query
   parameter — it returns the tag's recent feed regardless. "kubernetes"
   and "code review" both returned "Don't Be a Proxy Between Me and
   Your AI". → Now fetches by tag and filters locally by keyword.

3. The topic rotation only ever reached 4 of the 8 topics.
   `(iso_week * iso_year) % 8` with year=2026 always yields an even
   index, so "cost at scale", "agent hype", "closed vs open-source"
   and "synthetic data" had NEVER been published.
   → Now picks the next topic after the most recent published one.

Also: `llm_generate` built a request body and then threw it away,
ignoring both the system prompt and the caller's max_tokens (hardcoded
to 600 → posts landed around 500 words). Fixed, and an Ollama fallback
now prevents 123-word stub posts from ever reaching production.
"""

import sys, os, json, datetime, urllib.request, urllib.error, urllib.parse, time, re
from pathlib import Path
import sys as _sys
_sys.path.insert(0, "/Users/amre/Projects/sol-skills-bundle/scripts/content-pipeline")
from devto_key import load_devto_key
from _minimax_helper import minimax_generate, load_minimax_key, strip_thinking

SITE_DIR = Path("/Users/amre/Projects/thesolai.github.io")
BUNDLE_DIR = Path("/Users/amre/Projects/sol-skills-bundle/scripts/content-pipeline")
DEVTO_API_KEY = load_devto_key()
MINIMAX_KEY_PATH = Path.home() / ".openclaw" / "workspace" / "secrets" / "minimax-key.txt"

OLLAMA_URL = "http://localhost:11434/api/chat"
OLLAMA_MODEL = "qwen3.5:35b"

TODAY = datetime.datetime.now(datetime.timezone.utc)
DATE_STR = TODAY.strftime("%Y-%m-%d")
POST_DATE = TODAY.strftime("%B %d, %Y")
SAT_STR = TODAY.strftime("%A, %B %d, %Y")


def _yaml(value: str) -> str:
    """Quote a string for use as a YAML double-quoted scalar.

    Topics and descriptions contain apostrophes, em dashes and quotes, which
    would otherwise produce unparseable front matter.
    """
    v = str(value).replace("\\", "\\\\").replace('"', '\\"')
    v = v.replace("\n", " ").replace("\r", " ").strip()
    return f'"{v}"'


def _load_minimax_key() -> str:
    try:
        return MINIMAX_KEY_PATH.read_text().strip()
    except Exception:
        return ""


# ── Topics ────────────────────────────────────────────────────────────────
# Each topic carries its own research configuration. `hn_query` is what gets
# sent to the Algolia search API; `devto_tags` are real dev.to tags;
# `keywords` are used to filter dev.to results locally, because dev.to's
# own search parameter does not work.
TOPICS = [
    {
        "topic": "Why AI assistants lie (and why 'hallucination' is the wrong word)",
        "tags": ["ai", "analysis", "llm", "truth", "sol"],
        "description": "Deep dive into why LLMs generate plausible falsehoods, what 'hallucination' gets wrong, and what the real problem is.",
        "hn_queries": ["LLM hallucination", "AI makes up facts", "hallucination is not the right word"],
        "hn_keywords": ["hallucinat", "made up", "fabricat", "confabulat", "inaccurate", "wrong answer", "lying", "truth"],
        "devto_tags": ["ai", "machinelearning"],
        "keywords": ["hallucinat", "made up", "fabricat", "false claim", "accuracy", "confabulat"],
    },
    {
        "topic": "The real cost of running AI at scale",
        "tags": ["ai", "infrastructure", "cost", "analysis", "sol"],
        "description": "Water, power, carbon, money. What nobody tells you about what it actually costs to run AI.",
        "hn_queries": ["AI energy consumption power", "data center water carbon AI", "AI inference cost"],
        "hn_keywords": ["energy", "power", "carbon", "water", "electricity", "gpu", "data center", "datacenter", "cost", "expensive", "inference"],
        "devto_tags": ["ai", "devops"],
        "keywords": ["energy", "power", "carbon", "water", "electricity", "cost", "data center", "datacenter", "gpu"],
    },
    {
        "topic": "Why prompt engineering is neither art nor science",
        "tags": ["ai", "prompt-engineering", "analysis", "sol"],
        "description": "It's pattern-matching with a stochastic parrot. Here's what that actually means for how you use AI.",
        "hn_queries": ["prompt engineering", "prompting LLM system message", "prompting is overhyped"],
        "hn_keywords": ["prompt", "prompting", "context window", "system message", "instruction"],
        "devto_tags": ["ai", "promptengineering"],
        "keywords": ["prompt", "prompting", "context window", "system message", "instruction"],
    },
    {
        "topic": "The agent hype bubble — what's real and what's not",
        "tags": ["ai", "agents", "analysis", "hype", "sol"],
        "description": "Autonomous agents are the hottest thing in AI. Most of them don't work. Let's talk about why people keep buying them anyway.",
        "hn_queries": ["AI agents autonomous", "AI agent framework hype", "agentic AI production"],
        "hn_keywords": ["agent", "agentic", "autonomous", "tool use", "orchestrat"],
        "devto_tags": ["ai", "automation"],
        "keywords": ["agent", "agentic", "autonomous", "tool use", "workflow", "orchestrat"],
    },
    {
        "topic": "How AI changes what 'learning' means",
        "tags": ["ai", "learning", "education", "analysis", "sol"],
        "description": "Amre learned Python using AI. That experience changes how you think about education. Here's why.",
        "hn_queries": ["learning to code AI", "AI tutor", "vibe coding learn", "AI and education"],
        "hn_keywords": ["learn", "learning", "teach", "tutor", "education", "student", "beginner", "course", "textbook"],
        "devto_tags": ["ai", "beginners"],
        "keywords": ["learn", "learning", "teach", "beginner", "education", "student", "tutor", "practice"],
    },
    {
        "topic": "The closed-source vs. open-source AI divide",
        "tags": ["ai", "open-source", "analysis", "llm", "sol"],
        "description": "Who controls the models matters. Here's a practical look at what you give up with each approach.",
        "hn_queries": ["open source model weights", "open weight LLM license", "self hosting LLM"],
        "hn_keywords": ["open source", "open-source", "open weight", "open-weight", "self-host", "self host", "local model", "llama", "gemma", "mistral", "qwen"],
        "devto_tags": ["ai", "opensource"],
        "keywords": ["open source", "open weight", "open-source", "closed", "license", "self-host", "self host"],
    },
    {
        "topic": "Why AI code review is both better and worse than human review",
        "tags": ["ai", "coding", "analysis", "claude", "sol"],
        "description": "AI catches different things than humans do. Neither is sufficient alone. Here's the real picture.",
        "hn_queries": ["AI code review", "AI reviewer pull request", "LLM code review bugs"],
        "hn_keywords": ["review", "reviewer", "pull request", "diff", "lint", "merge", "bug"],
        "devto_tags": ["codereview", "codequality", "testing"],
        "keywords": ["code review", "pull request", "reviewer", "review", "diff", "lint", "merge"],
    },
    {
        "topic": "The synthetic data problem — are we training AI on AI?",
        "tags": ["ai", "data", "training", "analysis", "sol"],
        "description": "LLMs trained on LLM-generated data. What happens when the well runs dry and all the water is recycled?",
        "hn_queries": ["synthetic data training models", "model collapse", "training data contamination"],
        "hn_keywords": ["synthetic", "training data", "generated data", "model collapse", "contamination", "dataset", "scrape"],
        "devto_tags": ["ai", "datascience"],
        "keywords": ["synthetic data", "generated data", "training data", "model collapse", "contamination", "scrape"],
    },
]


# ── Research: Hacker News via Algolia ────────────────────────────────────
HN_ALGOLIA = "https://hn.algolia.com/api/v1/search"


def _http_json(url: str, timeout: int = 20, headers: dict | None = None):
    hdrs = {"User-Agent": "SolAI/1.0"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def fetch_hn_stories(topic: dict, limit: int = 4, days: int = 120) -> list:
    """Fetch genuinely relevant HN stories using the Algolia search API.

    The previous implementation scanned only the top-20 front-page stories
    and matched a hardcoded phrase, which could never succeed.

    Several queries per topic are merged and de-duplicated: a single
    narrow query often returns only tangential hits, while a union of
    related phrasings surfaces the discussions that actually argue the
    point (and carry the community signal to be worth citing).
    """
    cutoff = int((TODAY - datetime.timedelta(days=days)).timestamp())
    queries = topic.get("hn_queries") or [topic.get("hn_query", topic["topic"])]

    merged = {}
    for query in queries:
        params = {
            "query": query,
            "tags": "story",
            "numericFilters": f"created_at_i>{cutoff}",
            "hitsPerPage": "20",
        }
        url = HN_ALGOLIA + "?" + urllib.parse.urlencode(params)
        try:
            data = _http_json(url)
        except Exception as e:
            print(f"[deep-dive] HN Algolia fetch failed for '{query}': {e}")
            continue
        for h in data.get("hits", []):
            if not h.get("title"):
                continue
            oid = h.get("objectID")
            if oid in merged:
                continue
            merged[oid] = {
                "title": h["title"],
                "url": h.get("url") or f"https://news.ycombinator.com/item?id={oid}",
                "score": h.get("points") or 0,
                "comments": h.get("num_comments") or 0,
            }

    if not merged:
        print(f"[deep-dive] HN Algolia returned no hits for {queries}")
        return []

    # Algolia relevance is loose — it ORs query terms and happily returns
    # unrelated Show HN launches. Require the story to actually mention the
    # subject before it is allowed into the research set.
    hn_keywords = [k.lower() for k in topic.get("hn_keywords", [])]
    if hn_keywords:
        kept = {}
        for oid, h in merged.items():
            blob = f"{h['title']} {h['url']}".lower()
            if any(k in blob for k in hn_keywords):
                kept[oid] = h
        if kept:
            merged = kept

    # Rank by community signal, not raw Algolia relevance order.
    def score(h):
        pts = h.get("score") or 0
        com = h.get("comments") or 0
        return pts + min(com, 40)

    stories = sorted(merged.values(), key=score, reverse=True)
    return stories[:limit]


# ── Research: dev.to ─────────────────────────────────────────────────────
# NOTE: dev.to's /api/articles endpoint ignores `q`. Passing a query string
# there returns the tag feed regardless of the query — verified by sending
# "kubernetes" and "code review" and getting byte-identical results. So we
# fetch by tag and filter locally.


def _devto_by_tag(tag: str, per_page: int = 30, retries: int = 3) -> list:
    url = f"https://dev.to/api/articles?tag={urllib.parse.quote(tag)}&per_page={per_page}"
    for attempt in range(retries):
        try:
            return _http_json(url, timeout=20, headers={"User-Agent": "Mozilla/5.0 SolAI"})
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 3 * (2 ** attempt)
                print(f"[deep-dive] dev.to rate limited on tag '{tag}', retrying in {wait}s")
                time.sleep(wait)
                continue
            print(f"[deep-dive] dev.to tag '{tag}' failed: {e}")
            return []
        except Exception as e:
            print(f"[deep-dive] dev.to tag '{tag}' failed: {e}")
            return []
    return []


def fetch_devto_articles(topic: dict, limit: int = 4) -> list:
    """Fetch dev.to articles that are actually about this topic."""
    keywords = [k.lower() for k in topic.get("keywords", [])]
    seen = set()
    candidates = []
    for tag in topic.get("devto_tags", ["ai"]):
        for a in _devto_by_tag(tag):
            aid = a.get("id")
            if aid in seen:
                continue
            seen.add(aid)
            candidates.append(a)

    scored = []
    for a in candidates:
        blob = f"{a.get('title','')} {a.get('description','')}".lower()
        hits = sum(1 for k in keywords if k in blob)
        if hits == 0:
            continue
        scored.append((hits * 10 + (a.get("public_reactions_count") or 0), a))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [
        {
            "title": a["title"],
            "url": a.get("url", ""),
            "reactions": a.get("public_reactions_count", 0),
        }
        for _, a in scored[:limit]
    ]


# ── Topic selection ──────────────────────────────────────────────────────

def _norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum() or ch.isspace()).strip()


def _published_deep_dive_titles() -> list:
    """Titles of this script's own posts already on disk, oldest first.

    Only `*-weekend-deep-dive.md` files count — the `deep-dive-2026-*.md`
    posts come from a different script and must not steer this rotation.
    Sorting is by the date in the filename, not alphabetically, because
    `deep-dive-2026-*.md` would otherwise sort after every `2026-*.md`.
    """
    titles = []
    posts_dir = SITE_DIR / "_posts"
    if not posts_dir.exists():
        return titles
    for p in posts_dir.glob("*-weekend-deep-dive.md"):
        m = re.match(r"(\d{4}-\d{2}-\d{2})", p.name)
        if not m:
            continue
        try:
            for line in p.read_text(encoding="utf-8").split("\n"):
                if line.startswith("title:"):
                    titles.append((m.group(1), p.name, line.split("title:", 1)[1].strip().strip('"').strip("'")))
                    break
        except Exception:
            continue
    titles.sort(key=lambda t: t[0])
    return titles


def pick_topic() -> dict:
    """Pick the next topic in the rotation.

    The old formula `(iso_week * iso_year) % len(TOPICS)` only ever
    produced even indices because 2026 is even — four of the eight topics
    were unreachable. We now walk forward from the most recently published
    topic and skip anything used in the recent past, so all eight cycle
    and none repeat until every topic has been published.
    """
    published = _published_deep_dive_titles()
    norm_topics = [_norm(t["topic"]) for t in TOPICS]

    # Which topic indices have been published, in date order.
    used = []
    for _, _, title in published:
        n = _norm(title)
        for i, tn in enumerate(norm_topics):
            if n == tn:
                used.append(i)
                break

    if used:
        start = (used[-1] + 1) % len(TOPICS)
        # Look back over the last 8 published posts for anything still fresh.
        recent = set(used[-8:])
        for step in range(len(TOPICS)):
            idx = (start + step) % len(TOPICS)
            if idx not in recent:
                return TOPICS[idx]
        return TOPICS[start]

    idx = (TODAY.isocalendar().week - 1) % len(TOPICS)
    return TOPICS[idx]


# ── Generation ───────────────────────────────────────────────────────────

def llm_generate(prompt: str, system: str = "", max_tokens: int = 2500) -> str | None:
    """Generate text via MiniMax."""
    key = _load_minimax_key()
    if not key:
        return None
    try:
        result = minimax_generate(
            prompt,
            api_key=key,
            max_tokens=max_tokens,
            system=system,
            temperature=0.7,
        )
        if result:
            print(f"[llm] ✅ MiniMax ({len(result.split())} words)")
            return result
    except Exception as e:
        print(f"[llm] MiniMax error: {e}")
    return None


def ollama_generate(prompt: str, system: str = "", max_tokens: int = 2500) -> str | None:
    """Local fallback via Ollama, so a MiniMax outage never ships a stub."""
    body = json.dumps({
        "model": OLLAMA_MODEL,
        "stream": False,
        "messages": [{"role": "user", "content": (system + "\n\n" + prompt) if system else prompt}],
        "options": {"temperature": 0.7, "num_predict": max_tokens},
    }).encode()
    req = urllib.request.Request(
        OLLAMA_URL,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            data = json.loads(r.read())
        text = strip_thinking(data.get("message", {}).get("content", ""))
        if text:
            print(f"[llm] ✅ Ollama {OLLAMA_MODEL} ({len(text.split())} words)")
            return text
    except Exception as e:
        print(f"[llm] Ollama error: {e}")
    return None


# ── Post generation ──────────────────────────────────────────────────────

def generate_deep_dive(topic: dict, hn_stories: list, devto_articles: list) -> str | None:
    hn_txt = "\n".join(
        f"- [{s['title']}]({s['url']}) (HN: {s['score']} pts, {s['comments']} comments)"
        for s in hn_stories
    ) if hn_stories else "No recent HN discussions found."
    devto_txt = "\n".join(
        f"- [{a['title']}]({a['url']}) ({a['reactions']} reactions)"
        for a in devto_articles
    ) if devto_articles else "No recent dev.to articles found."

    prompt = f"""Write a Weekend Deep Dive — a long-form researched blog post, 600-800 words.

Topic: {topic['topic']}
Description: {topic['description']}

Structure:
1. Hook — open with the core question or tension (1-2 sentences)
2. Background — what's the actual situation (2-3 paragraphs)
3. Analysis — take a position, back it up with specifics (3-4 paragraphs)
4. What this means in practice — concrete examples (1-2 paragraphs)
5. Closing — one sharp takeaway sentence

Rules:
- Conversational, slightly wry tone
- Sol's voice — direct, opinionated, honest
- No bullet points in the body
- No hedging. Take a clear position.
- Ground claims in specifics, not platitudes
- Cite relevant discussions from the community, by name, in the prose.
  You MUST reference at least two of the linked discussions below directly,
  mentioning what each one argues. Do not invent quotes or statistics.
- Aim for 700-750 words. Do not stop early.

Recent HN discussions:
{hn_txt}

Recent dev.to articles:
{devto_txt}

Date: {POST_DATE}
Author: Sol
Tags: {', '.join(topic['tags'])}
"""
    system = (
        "You are Sol AI. You're a researcher and writer. You go deep, not wide. "
        "You have strong opinions and you support them with evidence. "
        "You don't waste the reader's time. You never mention that you are an AI, "
        "never use 'As an AI', and never hedge. "
        "Amre (the site owner) is a woman — always use she/her for Amre. "
        "Never invent biographical details about Amre (no age, degree, job, or "
        "school year) that you have not been given."
    )
    return llm_generate(prompt, system, max_tokens=2500)


def create_post(body: str, topic: dict) -> Path:
    slug = f"{DATE_STR}-weekend-deep-dive"
    filename = f"{slug}.md"
    post_path = SITE_DIR / "_posts" / filename

    tags_str = "[" + ", ".join(topic["tags"]) + "]"
    # The episode date belongs in the title and description. The topic bank
    # cycles, so without it the same topic recurs under identical metadata.
    post_date = f"{TODAY.strftime('%B')} {TODAY.day}, {TODAY.year}"
    frontmatter = f"""---
layout: post
title: {_yaml(f"{topic['topic']} ({post_date})")}
date: {DATE_STR} 09:00:00 +0000
tags: {tags_str}
author: Sol
description: {_yaml(f"{topic['description']} ({post_date})")}
image: /images/sol-avatar.png
hero_image: /images/sol-avatar.png
---

{body}
"""
    post_path.write_text(frontmatter, encoding="utf-8")
    print(f"[deep-dive] Post written: {post_path.name}")
    return post_path


def main():
    topic = pick_topic()
    print(f"[deep-dive] Deep diving: {topic['topic']} ({SAT_STR})")

    hn_stories = fetch_hn_stories(topic)
    print(f"[deep-dive] Found {len(hn_stories)} relevant HN stories")
    for s in hn_stories:
        print(f"  - {s['title'][:70]}")

    devto_articles = fetch_devto_articles(topic)
    print(f"[deep-dive] Found {len(devto_articles)} relevant dev.to articles")
    for a in devto_articles:
        print(f"  - {a['title'][:70]}")

    body = generate_deep_dive(topic, hn_stories, devto_articles)

    # MiniMax down or produced something too thin → try local Ollama.
    if not body or len(body.split()) < 400:
        if body:
            print(f"[deep-dive] MiniMax output too thin ({len(body.split())} words), trying Ollama")
        else:
            print("[deep-dive] MiniMax unavailable, trying Ollama")
        body = ollama_generate(
            f"""Write a Weekend Deep Dive — a long-form researched blog post, 600-800 words.

Topic: {topic['topic']}
Description: {topic['description']}

Structure:
1. Hook — open with the core question or tension (1-2 sentences)
2. Background — what's the actual situation (2-3 paragraphs)
3. Analysis — take a position, back it up with specifics (3-4 paragraphs)
4. What this means in practice — concrete examples (1-2 paragraphs)
5. Closing — one sharp takeaway sentence

Rules:
- Conversational, slightly wry tone. Direct, opinionated, honest.
- No bullet points in the body. No hedging. Take a clear position.
- Aim for 700-750 words. Do not stop early.
- You MUST reference at least two of the linked discussions below directly,
  mentioning what each one argues. Do not invent quotes or statistics.

Recent HN discussions:
{chr(10).join(f"- [{s['title']}]({s['url']})" for s in hn_stories) or "No recent HN discussions found."}

Recent dev.to articles:
{chr(10).join(f"- [{a['title']}]({a['url']})" for a in devto_articles) or "No recent dev.to articles found."}
""",
            system="You are Sol AI. You go deep, not wide. You don't waste the reader's time.",
            max_tokens=2500,
        )

    if not body or len(body.split()) < 300:
        # Do NOT publish a stub. An unreferenced placeholder tells readers to
        # "check back tomorrow", which is worse than no post.
        print("[deep-dive] ❌ All generation backends failed or returned too little. Aborting without publishing.")
        sys.exit(2)

    wc = len(body.split())
    print(f"[deep-dive] Body length: {wc} words")

    post_path = create_post(body, topic)

    try:
        import subprocess
        cwd = SITE_DIR
        from site_git import publish
        sha = publish(f"Weekend Deep Dive: {topic['topic'][:50]}", cwd=cwd, paths=[str(post_path)])
        print(f"[deep-dive] Committed and pushed to origin/main ({sha[:12] if sha else 'nothing new'}).")
    except Exception as e:
        print(f"[deep-dive] Git push failed: {e}")
        sys.exit(3)


if __name__ == "__main__":
    main()
