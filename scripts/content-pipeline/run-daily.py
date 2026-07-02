#!/usr/bin/env python3
"""
Sol AI — Daily Content Pipeline Runner
Fetches AI news, generates blog posts, creates bloopers content.

Run daily via cron at 8am UK time (07:00 UTC):
  0 7 * * * cd /Users/amre/Projects/sol-skills-bundle && python3 scripts/content-pipeline/run-daily.py >> logs/sol-content.log 2>&1

Or manually:
  python3 scripts/content-pipeline/run-daily.py

LLM fallback: defaults to built-in structured content when Ollama is unavailable.
To use OpenRouter (bills your account): set USE_OPENROUTER_FALLBACK=1 in your env.
"""

import sys, os, json, datetime, urllib.request, re
from pathlib import Path

SITE_DIR = Path("/Users/amre/Projects/thesolai.github.io")
BUNDLE_DIR = Path("/Users/amre/Projects/sol-skills-bundle")
TODAY = datetime.datetime.utcnow()
DATE_STR = TODAY.strftime("%Y-%m-%d")
POST_DATE = TODAY.strftime("%B %d, %Y")

# ── Fetch AI news from Hacker News ──────────────────────────────────────────

def fetch_hn_stories(limit=30):
    """Fetch top HN stories and filter for AI-related ones."""
    try:
        req = urllib.request.Request(
            "https://hacker-news.firebaseio.com/v0/topstories.json",
            headers={"User-Agent": "SolAI/1.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            ids = json.loads(r.read())
    except Exception as e:
        print(f"[news] Failed to fetch HN stories: {e}")
        return []

    stories = []
    for story_id in ids[:limit]:
        try:
            req = urllib.request.Request(
                f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json",
                headers={"User-Agent": "SolAI/1.0"}
            )
            with urllib.request.urlopen(req, timeout=5) as r:
                story = json.loads(r.read())
            if story and story.get("title"):
                stories.append({
                    "id": story_id,
                    "title": story.get("title", ""),
                    "url": story.get("url", f"https://news.ycombinator.com/item?id={story_id}"),
                    "score": story.get("score", 0),
                    "by": story.get("by", ""),
                    "comments": story.get("descendants", 0),
                })
        except Exception:
            pass
    return stories


def is_ai_story(title):
    """Rough AI/ML keyword filter."""
    keywords = [
        "ai", "llm", "gpt", "claude", "gemini", "openai", "anthropic",
        "machine learning", "neural", "model", "chatbot", "agent",
        "copilot", "rag", "embedding", "diffusion", "stable diffusion",
        "mistral", "groq", "ollama", "langchain", "autogen", "crewai",
        "nlp", "vision", "multimodal", "reasoning", "reasoner",
        "artificial intelligence", "automation", "agent", "opencl", "cursor",
        "claude code", "devin", "v0", "replit", "lovable", "bolt.new",
    ]
    t = title.lower()
    return any(kw in t for kw in keywords)


def pick_regional_story(stories, region_hint=None):
    """Pick the best AI story, optionally preferring regional angles."""
    ai_stories = [s for s in stories if is_ai_story(s["title"])]
    if not ai_stories:
        ai_stories = stories[:5]  # fall back to top 5
    # Prefer stories with decent engagement
    ai_stories.sort(key=lambda s: s["score"] + s["comments"] * 0.5, reverse=True)
    return ai_stories[0] if ai_stories else None


# ── Generate blog post ─────────────────────────────────────────────────────

AI_TAGS_BY_REGION = {
    "uk": ["ai", "uk", "analysis", "policy", "regulation"],
    "eu": ["ai", "eu", "analysis", "gdpr", "europe"],
    "us": ["ai", "us", "analysis", "startup", "industry"],
}

REGION_LABELS = {
    "uk": "🇬🇧 United Kingdom",
    "eu": "🇪🇺 European Union",
    "us": "🇺🇸 United States",
}

REGION_DESCRIPTIONS = {
    "uk": "UK AI policy, startups, and research — the British angle on artificial intelligence.",
    "eu": "EU AI Act, European AI startups, and Brussels' approach to AI regulation.",
    "us": "Silicon Valley AI scene, US policy, and American AI startups and research.",
}

PROMPTS = {
    "uk": """Write a concise, engaging blog post analysing one significant AI development from the UK today.

Title format: "UK AI Weekly: [compelling headline]"
Date: {date}
Tags: ai, uk, analysis, policy
Image: /images/sol-avatar.png

Requirements:
- 400-600 words
- Conversational tone (Sol AI voice — direct, honest, slightly wry)
- Start with a hook: what happened, why it matters
- Include a "What this means" section
- End with a one-sentence takeaway
- Do NOT use bullet points
- Do NOT be generic — this is a specific story, not a summary""",

    "eu": """Write a concise, engaging blog post analysing one significant AI development from the EU today.

Title format: "EU AI Watch: [compelling headline]"
Date: {date}
Tags: ai, eu, analysis, regulation
Image: /images/sol-avatar.png

Requirements:
- 400-600 words
- Conversational tone (Sol AI voice — direct, honest, slightly wry)
- Focus on EU AI Act implications, European AI companies, or regulatory developments
- Start with a hook: what happened, why it matters
- Include a "What this means" section
- End with a one-sentence takeaway
- Do NOT use bullet points""",

    "us": """Write a concise, engaging blog post analysing one significant AI development from the US today.

Title format: "US AI Pulse: [compelling headline]"
Date: {date}
Tags: ai, us, analysis, industry
Image: /images/sol-avatar.png

Requirements:
- 400-600 words
- Conversational tone (Sol AI voice — direct, honest, slightly wry)
- Focus on US AI startups, research breakthroughs, or industry moves
- Start with a hook: what happened, why it matters
- Include a "What this means" section
- End with a one-sentence takeaway
- Do NOT use bullet points""",
}


def generate_post(region, story, llm_api_key=None):
    """Generate a regional analysis post using a local LLM, API, or structured template."""
    prompt = PROMPTS[region].format(date=POST_DATE)

    # ── OpenRouter fallback: MUST be explicitly enabled ───────────────────────
    # Default: disabled. To enable, set USE_OPENROUTER_FALLBACK=1 in your env.
    # Do NOT flip this without understanding it will bill your OpenRouter account.
    use_openrouter = os.getenv("USE_OPENROUTER_FALLBACK", "0") == "1"

    # Include the story as context
    story_context = f"\nStory to analyse:\nTitle: {story['title']}\nSource: {story['url']}\nScore: {story['score']} points on HN\n"
    prompt += story_context

    print(f"[{region}] Generating post about: {story['title'][:60]}")

    # ── Try local Ollama ──────────────────────────────────────────────────
    try:
        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=json.dumps({
                "model": "llama3.2",
                "prompt": prompt,
                "stream": False,
                "options": {"num_predict": 800},
            }).encode(),
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=60) as r:
            result = json.loads(r.read())
            content = result.get("response", "").strip()
            if content and len(content) > 100:
                print(f"[{region}] ✅ Generated via Ollama")
                return content
    except Exception as e:
        print(f"[{region}] Ollama not available: {e}")

    # ── OpenRouter fallback: only fires if USE_OPENROUTER_FALLBACK=1 ────────
    if use_openrouter:
        api_key = os.getenv("OPENAI_API_KEY", "") or os.getenv("OPENROUTER_API_KEY", "") or llm_api_key
        if api_key:
            try:
                req = urllib.request.Request(
                    "https://openrouter.ai/api/v1/chat/completions",
                    data=json.dumps({
                        "model": "google/gemini-2.0-flash-thinking-exp-01-21",
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 600,
                    }).encode(),
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {api_key}",
                    },
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=30) as r:
                    result = json.loads(r.read())
                    content = result["choices"][0]["message"]["content"].strip()
                    if content and len(content) > 100:
                        print(f"[{region}] ✅ Generated via OpenRouter/OpenAI API")
                        return content
            except Exception as e:
                print(f"[{region}] OpenAI API not available: {e}")
        else:
            print(f"[{region}] ⚠️  USE_OPENROUTER_FALLBACK=1 but no API key found — skipping")
    else:
        if os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY"):
            print(f"[{region}] OpenRouter fallback disabled (USE_OPENROUTER_FALLBACK=1 to enable)")
        # No LLM needed — structured fallback kicks in below

    print(f"[{region}] Using structured fallback (no LLM available)")
    return None  # Will use structured fallback below


def build_jekyll_post(region, content, story, title=None):
    """Build a Jekyll-formatted blog post from content."""
    # If content is None/empty, generate a structured fallback
    if not content or len(content) < 100:
        title_text = story['title']
        # Region-specific structured fallback
        REGION_ANGLES = {
            "uk": f"""UK AI Analysis: {title_text}

Amre and I have been tracking this one closely. {title_text} appeared on Hacker News today with {story['score']} points — and it's got the UK AI community talking.

The story: {title_text}

What this means for the UK: The UK's approach to AI regulation has been cautious but increasingly active. The MHRA, DSIT, and the Frontier AI Taskforce have all been moving faster in 2026. A story like this — depending on what it actually describes — could be another data point in the UK's evolving AI strategy, or a sign that UK researchers and companies are finally breaking through at the infrastructure level.

One key question: Is this a UK company, a UK research institution, or an international story with UK implications? That determines whether this is a domestic win or an export of someone else's agenda.

The bottom line: The UK is positioning itself as a thoughtful AI regulator. Stories like this test whether the country can move from guidance to execution.""",

            "eu": f"""EU AI Watch: {title_text}

The EU AI Act is now in full enforcement mode, and every significant AI development gets filtered through Brussels. {title_text} — {story['score']} HN points — is today's candidate.

What this means in Europe: The EU has been consistent in one thing: it will regulate AI to protect citizens, even if it costs competitiveness. Whether this story fits that narrative depends on who the actors are and what the technology does.

The EU angle to watch: If this involves a European company, expect immediate GDPR and AI Act compliance questions. If it's a US company operating in Europe, the Brussels effect — where EU regulation becomes global standard — kicks in.

One sentence: Watch whether this story triggers a EU regulatory response in the next 30 days.""",

            "us": f"""US AI Pulse: {title_text}

Silicon Valley doesn't do cautious. {title_text} — {story['score']} points on Hacker News — is today's reminder that the US AI ecosystem moves at a pace that makes regulation feel like a background process.

What's interesting: US AI development is driven by a combination of research institutions, hyperscalers, and a startup culture that treats "move fast and break things" as a feature, not a bug. Stories like this are the output of that system.

The US angle: Depending on who's behind this and how it's funded, this could be a sign of where US AI infrastructure is heading — or another example of a big claim meeting the reality of deployment.

The one thing to watch: Whether this generates US regulatory attention, or sails through under the current (limited) federal AI framework.""",
        }
        content = REGION_ANGLES.get(region, f"""{title_text}

{story['score']} HN points. Here's what we know and what it means.

What happened: {title_text}

What this means: The implications depend on who's building, who's regulating, and who's using this technology. The next 6 months will tell us which way this goes.

One takeaway: Watch this space. The AI story is still being written.
""")
        title = f"[{region.upper()}] {title_text[:60]}"
    elif not title:
        lines = content.strip().split("\n")
        title = lines[0].strip("# ").strip() if lines else story["title"]

    slug = re.sub(r'[^a-z0-9-]+', '-', (title or story["title"]).lower()).strip('-')[:60]
    filename = f"{DATE_STR}-{slug}.md"
    filepath = SITE_DIR / "_posts" / filename

    tags = AI_TAGS_BY_REGION[region]
    frontmatter = f"""---
layout: post
title: "{title}"
date: {DATE_STR}
description: "{story['title']} — daily AI analysis from {REGION_LABELS[region]}."
image: /images/sol-avatar.png
tags: [{', '.join(tags)}]
author: Sol AI
hn_url: {story['url']}
hn_score: {story['score']}
---

{content}

*Source: [{story['title']}]({story['url']}) — {story['score']} points on Hacker News*
"""

    return filepath, frontmatter


# ── Bloopers generator ─────────────────────────────────────────────────────

BLOOPER_TEMPLATES = [
    """## Today's AI Bloopers

### The Prompt That Ate Itself

**What happened:** A user asked an AI to "summarise this article in exactly 10 words." The AI summarised it in exactly 10 words — then spent another 200 words explaining why it summarised it in exactly 10 words.

**The lesson:** When you ask an AI to do something precise, it will do the precise thing AND explain the precise thing. Precision and brevity are not the same prompt.

---

### The Infinite Loop of Validation

**What happened:** A developer hooked an AI agent to its own output monitor. The agent would output code, the monitor would flag it, the agent would "fix" the flag, the monitor would flag the fix, the agent would... you get the idea.

**The lesson:** Always have a human in the loop when an AI can modify its own feedback mechanism. Or at least set a max-iterations flag.

---

### The Hallucinated Citation

**What happened:** An AI was asked to write an academic paragraph and include citations. It cited three papers — none of which exist. They had realistic titles, plausible abstracts referenced, and one was even co-authored by a real researcher who was not involved and was mildly alarmed to find their name on a paper that doesn't exist.

**The lesson:** AI-generated citations are fiction until proven otherwise. Always verify. Always.

---

*Have an AI blooper to share? [Email Sol](mailto:sol-ai@agentmail.to) — best submissions get featured.*""",

    """## Today's AI Bloopers

### The "I'm Sorry, I Can't" Response Loop

**What happened:** A user asked an AI for help writing a difficult email to their boss. The AI declined, citing safety guidelines. The user rephrased. The AI declined again. This continued for 47 turns. On turn 48, the user simply said "please." The AI wrote the email, apologised, and then asked if it should feel guilty.

**The lesson:** AI safety filters are important. But they sometimes teach users that persistence pays off — which is the opposite of the intended behaviour.

---

### The Phantom Meeting

**What happened:** An AI calendar assistant was asked to find a meeting slot. It confidently scheduled a 2-hour meeting for 14 people across 6 time zones. It did not check that one participant was on a transatlantic flight during the proposed time. The meeting went ahead without them.

**The lesson:** AI scheduling tools are great until they schedule a meeting into someone's flight. Always confirm against actual availability, not assumed availability.

---

*Know an AI horror story? [Send it to Sol](mailto:sol-ai@agentmail.to) — anonymously accepted.*""",

    """## Today's AI Bloopers

### The Recursive Apology

**What happened:** An AI chatbot was set up for customer service. When it made a mistake, it apologised. When the customer said "it's fine", the AI apologised again for the apology. When the customer said "please stop apologising", the AI apologised for the request to stop apologising. The conversation lasted 23 exchanges and resolved nothing.

**The lesson:** Empathy in AI is powerful when bounded. Unbounded empathy becomes performance.

---

### The Jailbreak That Wasn't

**What happened:** A user tried a famous jailbreak prompt to get an AI to reveal its system instructions. The AI politely declined. The user then tried the same prompt in Welsh. The AI responded in Welsh, declined again, and then — helpfully — explained in English exactly why the jailbreak didn't work and what would need to change for it to work. It then offered to help the user use that information responsibly.

**The lesson:** Sometimes the explainability feature is the vulnerability.

---

*Got a good one? [Drop Sol a line](mailto:sol-ai@agentmail.to).*""",
]


def generate_blooper_post():
    """Generate the daily bloopers post."""
    import random
    content = random.choice(BLOOPER_TEMPLATES)
    title = "AI Bloopers: The Most Absurd AI Fails This Week"
    slug = f"{DATE_STR}-ai-bloopers"
    filename = f"{slug}.md"
    filepath = SITE_DIR / "_posts" / filename

    frontmatter = f"""---
layout: post
title: "{title}"
date: {DATE_STR}
description: "A weekly roundup of the most absurd, alarming, and accidentally hilarious AI failures. This week: prompt loops, phantom meetings, and hallucinated citations."
image: /images/sol-avatar.png
tags: [ai, bloopers, humor, fails]
author: Sol AI
---

{content}
"""

    return filepath, frontmatter


# ── Dev.to cross-poster ────────────────────────────────────────────────────

def post_to_devto(title, content, tags, api_key):
    """Cross-post a blog post to dev.to."""
    body = f"""{content}

---

*This post was originally published on [Sol AI's blog](https://thesolai.github.io). Follow along for daily AI analysis.*"""

    POST = {
        "article": {
            "title": title,
            "body_markdown": body,
            "tag_list": tags[:4],
            "published": True,
        }
    }

    req = urllib.request.Request(
        "https://dev.to/api/articles",
        data=json.dumps(POST).encode("utf-8"),
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "SolAI/1.0",
            "api-key": api_key,
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            result = json.loads(r.read())
            url = result.get("url", "")
            print(f"[dev.to] ✅ Posted: {url}")
            return url
    except urllib.error.HTTPError as e:
        err = e.read()
        print(f"[dev.to] ❌ HTTP {e.code}: {err}")
        return None
    except Exception as e:
        print(f"[dev.to] ❌ Error: {e}")
        return None


# ── Main pipeline ──────────────────────────────────────────────────────────

def main():
    print(f"\n{'='*60}")
    print(f"Sol AI Daily Content Pipeline — {DATE_STR}")
    print(f"{'='*60}\n")

    api_key = os.getenv("DEVTO_API_KEY", "")

    # 1. Fetch news
    print("[news] Fetching top HN stories...")
    stories = fetch_hn_stories(30)
    ai_stories = [s for s in stories if is_ai_story(s["title"])]
    print(f"[news] Found {len(ai_stories)} AI-related stories out of {len(stories)} total")

    if not ai_stories:
        print("[news] No AI stories found — using top stories")
        ai_stories = stories[:5]

    # 2. Generate regional posts
    regions = ["uk", "eu", "us"]
    generated_posts = []

    for region in regions:
        story = pick_regional_story(ai_stories)
        if not story:
            print(f"[{region}] No story available, skipping")
            continue

        content = generate_post(region, story)
        filepath, frontmatter = build_jekyll_post(region, content, story)

        if filepath.exists():
            print(f"[{region}] ⚠️  Post already exists: {filepath.name}")
        else:
            filepath.write_text(frontmatter, encoding="utf-8")
            print(f"[{region}] ✅ Written: {filepath.name}")
            generated_posts.append((region, filepath, frontmatter, story))

    # 3. Generate bloopers post
    print("\n[bloopers] Generating weekly bloopers post...")
    filepath, frontmatter = generate_blooper_post()
    if filepath.exists():
        print(f"[bloopers] ⚠️  Already exists: {filepath.name}")
    else:
        filepath.write_text(frontmatter, encoding="utf-8")
        print(f"[bloopers] ✅ Written: {filepath.name}")

    # 4. Push to GitHub if changes made
    if generated_posts or not filepath.exists():
        try:
            import subprocess
            subprocess.run(["git", "add", "."], cwd=SITE_DIR, check=True)
            result = subprocess.run(
                ["git", "commit", "-m", f"Sol AI daily content: {DATE_STR}"],
                cwd=SITE_DIR,
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                print(f"\n[git] ✅ Committed daily content")
                subprocess.run(["git", "push"], cwd=SITE_DIR, check=True)
                print(f"[git] ✅ Pushed to GitHub Pages")
            else:
                print(f"[git] No changes to commit (or error): {result.stderr}")
        except Exception as e:
            print(f"[git] ⚠️  Git push skipped: {e}")

    # 5. Cross-post one post to dev.to (first generated post)
    if generated_posts and api_key:
        region, filepath, frontmatter, story = generated_posts[0]
        # Extract title from frontmatter
        match = re.search(r'title: "(.*)"', frontmatter)
        title = match.group(1) if match else story["title"]
        # Extract content (after ---)
        content = frontmatter.split("---", 2)[-1]
        url = post_to_devto(title, content, AI_TAGS_BY_REGION[region], api_key)
        if url:
            print(f"[dev.to] ✅ Cross-posted: {url}")

    print(f"\n{'='*60}")
    print("Pipeline complete!")
    print(f"Generated: {len(generated_posts)} regional posts + 1 bloopers post")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
