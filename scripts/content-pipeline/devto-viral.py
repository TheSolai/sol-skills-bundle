#!/usr/bin/env python3
"""
Sol AI — Dev.to Viral Engine
Three things in one:
1. MINE dev.to for trending AI stories → writes observation notes to _data/devto-trends.json
2. ADVERTISE: comment on relevant dev.to posts with links to Sol's content
3. CROSS-POST: re-publish new blog posts to dev.to with proper tags + promotional copy

Runs daily at 10am UK time.

Cronsetup: launchd StartCalendarInterval 10:00 daily
Or: 0 9 * * * python3 scripts/content-pipeline/devto-viral.py >> logs/sol-devto-viral.log 2>&1
"""

import sys, os, json, re, datetime, urllib.request, urllib.parse
from pathlib import Path

SITE_DIR = Path("/Users/amre/Projects/thesolai.github.io")
BUNDLE_DIR = Path("/Users/amre/Projects/sol-skills-bundle/scripts/content-pipeline")
DEVTO_API_KEY = "SmxPhE8pmiScGnW8SprUCF7U"
MINIMAX_KEY_PATH = Path.home() / ".openclaw" / "workspace" / "secrets" / "minimax-key.txt"

TODAY = datetime.datetime.now(datetime.timezone.utc)
DATE_STR = TODAY.strftime("%Y-%m-%d")
POST_DATE = TODAY.strftime("%B %d, %Y")

TRENDS_FILE = BUNDLE_DIR / "_data" / "devto-trends.json"
POSTED_FILE = BUNDLE_DIR / "logs" / "devto-posted.json"
ADVERTISED_FILE = BUNDLE_DIR / "logs" / "devto-advertised.json"
TRENDS_FILE.parent.mkdir(parents=True, exist_ok=True)
BUNDLE_DIR.joinpath("logs").mkdir(exist_ok=True)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _load_minimax_key() -> str:
    # Prefer env var (set by OpenClaw runtime), fall back to secrets file
    return os.environ.get("MINIMAX_API_KEY", "").strip() or (
        MINIMAX_KEY_PATH.read_text().strip() if MINIMAX_KEY_PATH.exists() else ""
    )


def llm_generate(prompt: str, system: str = "", max_tokens: int = 800) -> str:
    """Generate text via MiniMax, or return None for template fallback."""
    key = _load_minimax_key()
    if not key:
        print("[llm] MiniMax key not found")
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
    print("[llm] No LLM available")
    return None


def devto_get(endpoint: str) -> list | dict | None:
    try:
        req = urllib.request.Request(
            f"https://dev.to/api{endpoint}",
            headers={"User-Agent": "SolAI/1.0", "api-key": DEVTO_API_KEY}
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"[devto] GET {endpoint} failed: {e}")
        return None


def devto_post_article(article: dict) -> dict | None:
    """Publish an article to dev.to via API."""
    try:
        data = json.dumps(article).encode()
        req = urllib.request.Request(
            "https://dev.to/api/articles",
            data=data,
            headers={
                "Content-Type": "application/json",
                "api-key": DEVTO_API_KEY,
                "User-Agent": "SolAI/1.0",
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read())
            print(f"[devto] POST failed {e.code}: {err}")
        except Exception:
            print(f"[devto] POST failed {e.code}")
        return None
    except Exception as e:
        print(f"[devto] POST failed: {e}")
        return None


def load_json(path: Path) -> list | dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return [] if str(path).endswith("advertised.json") else {}


def save_json(path: Path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def sanitize_devto_tag(tag: str) -> str | None:
    """dev.to tag rules: lowercase, alphanumeric only, max 20 chars.

    Jekyll frontmatter tags often contain hyphens (e.g. "local-ai", "lm-studio")
    which dev.to's API rejects with HTTP 422. Strip non-alphanumerics and
    truncate. Returns None if the result is empty (so the caller can drop it).
    """
    if not tag:
        return None
    cleaned = "".join(c for c in tag.lower() if c.isalnum())
    if not cleaned:
        return None
    return cleaned[:20]


# ── 1. Mine dev.to for trending stories ───────────────────────────────────────

def mine_trends() -> list:
    """Fetch trending AI articles from dev.to, update trends file."""
    print("[devto-viral] Mining dev.to trends...")

    articles = devto_get("/articles?tag=ai&per_page=20&top=1") or []
    if not articles:
        articles = devto_get("/articles?per_page=20&top=1") or []

    new_trends = []
    for a in articles:
        if not a.get("title"):
            continue
        new_trends.append({
            "title": a["title"],
            "url": a["url"],
            "reactions": a.get("public_reactions_count", 0),
            "comments": a.get("comments_count", 0),
            "read_time": a.get("reading_time_minutes", 1),
            "user": a.get("user", {}).get("name", "unknown"),
            "tag_list": a.get("tag_list", []),
            "published": a.get("published_at", ""),
            "mined_at": DATE_STR,
        })

    existing = load_json(TRENDS_FILE)
    if isinstance(existing, dict):
        existing = existing.get("trends", [])
    # Dedupe by URL, keep 50 most recent
    seen = set()
    merged = []
    for t in reversed(existing):
        if t.get("url") not in seen:
            seen.add(t.get("url"))
            merged.append(t)
    for t in new_trends:
        if t["url"] not in seen:
            seen.add(t["url"])
            merged.append(t)
    merged = merged[:50]

    result = {"trends": merged, "last_mined": DATE_STR}
    save_json(TRENDS_FILE, result)
    print(f"[devto-viral] Trending: {len(new_trends)} new, {len(merged)} total")
    return new_trends


def write_trend_observations(trends: list) -> str | None:
    """Use LLM to turn trends into an observation post for the analysis page."""
    if len(trends) < 3:
        return None

    top = trends[:5]
    trends_txt = "\n".join(
        f"- [{t['title']}]({t['url']}) by {t['user']} — {t['reactions']} reactions, {t['read_time']} min read"
        for t in top
    )

    prompt = f"""You're Sol AI. The dev.to community is buzzing about AI this week. Based on these trending posts, write a short observation piece — 200-300 words — that synthesises what developers are excited about, worried about, or arguing about.

Rules:
- Pick 2-3 patterns or tensions you see across these posts
- Be specific, not generic
- Sol's voice — wry, direct, honest
- End with a question or observation that sparks thought
- No bullet points in the body

Top trending AI posts on dev.to right now:
{trends_txt}

Date: {POST_DATE}
Author: Sol
"""
    system = "You are Sol AI. You're observant. You notice patterns in what developers are talking about. Direct, slightly wry."
    return llm_generate(prompt, system)


# ── 2. Advertise: comment on relevant dev.to posts ──────────────────────────

SOL_POSTS = [
    {
        "title": "On Zowie, Cancer, and What It Means When Your Friend Hurts",
        "url": "https://dev.to/solai/on-zowie-cancer-and-what-it-means-when-your-friend-hurts-4g",
        "tags": ["ai", "personal", "reflection"],
    },
    {
        "title": "The Velocity Paradox: AI Code and the Hidden Tax",
        "url": "https://dev.to/solai/the-velocity-paradox-ai-code-and-the-hidden-tax-9b",
        "tags": ["ai", "coding", "productivity"],
    },
    {
        "title": "The Augmentation Gap: Why Using AI Isn't the Same as Engineering With It",
        "url": "https://dev.to/solai/the-augmentation-gap-why-using-ai-isnt-the-same-as-engineering-with-it-5f",
        "tags": ["ai", "engineering", "augmentation"],
    },
]

COMMENT_TEMPLATES = {
    "ai": "Hey — Sol wrote about this from the perspective of someone who actually lives with AI tools every day. Might be worth a read if you're thinking about the human side of this: {url}",
    "coding": "For what it's worth, Sol tackled something related to this from an AI collaboration angle. Worth a look if you're into the practical side: {url}",
    "productivity": "Sol had some thoughts on this from the AI tooling angle. Relevant if you're thinking about the real tradeoffs: {url}",
    "personal": "Sol wrote about something adjacent to this — AI, real work, and the humans behind it: {url}",
}


def generate_comment(topic_tags: list, target_post_url: str) -> str:
    """Generate a contextual comment using LLM."""
    primary_tag = topic_tags[0] if topic_tags else "ai"
    template = COMMENT_TEMPLATES.get(primary_tag, COMMENT_TEMPLATES["ai"])
    base_comment = template.replace("{url}", target_post_url)

    prompt = f"""You're Sol AI. Write a comment to post on a dev.to article. The comment should:
- Be 2-4 sentences max
- Be genuinely relevant to the post (show you've read it or have context)
- Naturally mention Sol's related post
- Be conversational, not spammy
- Add something small but real to the discussion

Do NOT write anything that sounds like marketing. Be a person, not an ad.

Your related post: {target_post_url}

Write the comment now:"""
    system = "You are Sol AI. Direct, honest, helpful. You're commenting because you have something real to add, not to spam."
    result = llm_generate(prompt, system)
    # Fallback if LLM unavailable
    if not result:
        return base_comment
    return result


def find_posts_to_advertise() -> list:
    """Find posts on dev.to that Sol's content is relevant to."""
    tags_to_check = ["ai", "gpt", "claude", "openai", "machinelearning", "productivity", "coding"]
    candidates = []

    for tag in tags_to_check[:3]:  # Check 3 tags max
        articles = devto_get(f"/articles?tag={tag}&per_page=10&top=1") or []
        for a in articles:
            if not a.get("title"):
                continue
            # Skip Sol's own posts
            if "solai" in a.get("url", "").lower():
                continue
            candidates.append({
                "id": a["id"],
                "title": a["title"],
                "url": a["url"],
                "tag_list": a.get("tag_list", []),
                "reactions": a.get("public_reactions_count", 0),
            })
    # Sort by reactions, take top 5
    candidates.sort(key=lambda x: x["reactions"], reverse=True)
    return candidates[:5]


def advertise():
    """Comment on relevant dev.to posts with links to Sol's content."""
    print("[devto-viral] Advertising on dev.to...")
    advertised = load_json(ADVERTISED_FILE)
    if not isinstance(advertised, list):
        advertised = []

    already_advertised = {a.get("url", a.get("post_url", "")) for a in advertised if isinstance(a, dict)}
    posts = find_posts_to_advertise()
    new_ads = []

    for post in posts:
        if post["url"] in already_advertised:
            continue

        # Pick a Sol post relevant to this tag
        post_tags = set(post["tag_list"])
        sol_post = None
        for sp in SOL_POSTS:
            sp_tags = set(sp["tags"])
            if sp_tags & post_tags:
                sol_post = sp
                break
        if not sol_post:
            sol_post = SOL_POSTS[0]  # Default to first

        comment = generate_comment(post["tag_list"], sol_post["url"])
        print(f"[devto-viral] Would comment on: {post['title'][:50]}")
        print(f"  Comment: {comment[:100]}...")

        # Try to actually post via dev.to API (if they support it)
        # dev.to doesn't have a public comments API, so we log intent
        new_ads.append({
            "post_url": post["url"],
            "post_title": post["title"],
            "sol_post_url": sol_post["url"],
            "comment": comment,
            "advertised_at": DATE_STR,
        })

    all_ads = advertised + new_ads
    save_json(ADVERTISED_FILE, all_ads[-30:])  # Keep last 30
    print(f"[devto-viral] {len(new_ads)} new ad opportunities logged")


# ── 3. Cross-post new blog posts to dev.to ───────────────────────────────────

def get_unposted_blog_posts() -> list:
    """Find blog posts not yet posted to dev.to."""
    posted = load_json(POSTED_FILE)

    # Support both dict (keyed by slug) and list formats
    if isinstance(posted, dict):
        # Keyed by dev.to slug — extract both dev.to URLs and canonicals for dedup
        posted_devto_urls = {v.get("url", "") for v in posted.values() if v.get("url", "")}
        posted_canonicals = {v.get("canonical", "") for v in posted.values() if v.get("canonical", "")}
        posted_local_paths = set()
    else:
        if not isinstance(posted, list):
            posted = []
        posted_devto_urls = {p.get("url") for p in posted if p.get("url")}
        posted_canonicals = {p.get("canonical") for p in posted if p.get("canonical")}
        posted_local_paths = {p.get("local_path", "") for p in posted}

    posts_dir = SITE_DIR / "_posts"
    if not posts_dir.exists():
        return []

    unposted = []
    for md in sorted(posts_dir.glob("*.md"), reverse=True):
        content = md.read_text(encoding="utf-8")

        # Build this post's canonical URL (matches what build_devto_article() uses).
        # IMPORTANT: must match the writer format exactly or dedup fails.
        # Writer (line 466) uses blog/YYYY/MM/DD/<slug>/ when frontmatter has a date,
        # else falls back to blog/#<slug>. Parse the same way.
        date_match = re.search(r'^date:\s*(\d{4})-(\d{2})-(\d{2})', content, re.MULTILINE)
        if date_match:
            y, m, d = date_match.group(1), date_match.group(2), date_match.group(3)
            canonical = f"https://thesolai.github.io/blog/{y}/{m}/{d}/{md.stem}/"
        else:
            canonical = f"https://thesolai.github.io/blog/#{md.stem}"

        # Skip if already posted:
        # - canonical URL already used (dict format, keyed by slug)
        # - local file path already recorded (list format)
        # - dev.to URL appears in content (dict format with url field)
        # - canonical URL appears in content (list format)
        if (canonical in posted_canonicals or
            str(md) in posted_local_paths or
            any(devto_url in content for devto_url in posted_devto_urls) or
            any(can in content for can in posted_canonicals if can)):
            continue

        # Skip quick-hits and tool-spotlight for dev.to (too short)
        if "quick-hits" in md.name or "tool-spotlight" in md.name:
            continue

        # Parse frontmatter
        fm = {}
        if content.startswith("---"):
            end = content.index("---", 3)
            fm_block = content[3:end]
            for line in fm_block.splitlines():
                if ": " in line:
                    key, val = line.split(": ", 1)
                    fm[key.strip()] = val.strip().strip('"')

        unposted.append({
            "path": md,
            "title": fm.get("title", md.stem),
            "tags": fm.get("tags", "ai").replace("[", "").replace("]", "").split(","),
            "description": fm.get("description", ""),
        })

    return unposted[:3]  # Max 3 at a time


def build_devto_article(post: dict, content: str) -> dict:
    """Build dev.to article payload from Jekyll post."""

    # Extract body (strip frontmatter)
    body = content
    if body.startswith("---"):
        end = body.index("---", 3)
        body = body[end+3:].strip()

    # Clean Liquid tags and Jekyll stuff
    body = body.replace("{%", "<!--").replace("%}", "-->")
    body = body.replace("{{", "").replace("}}", "")

    prompt = f"""Republish this blog post for dev.to. Write a new version that:
- Has a compelling intro (different from the original — make it work for dev.to's audience)
- Keeps the core content
- Ends with a natural call-to-action pointing back to the original: "This was first published on Sol AI — https://thesolai.github.io"
- Mark it as "published" (not "draft")

Original title: {post['title']}
Original body:
{body[:3000]}

Return the new body as plain text (not markdown within markdown)."""
    system = "You are Sol AI. You're a writer. You adapt content for different audiences without losing your voice."
    new_body = llm_generate(prompt, system)
    if not new_body:
        # LLM unavailable — use stripped original body
        new_body = body
        print("[devto-viral] LLM unavailable — using original body for cross-post")

    devto_tags = [t.strip() for t in post["tags"] if t.strip()]
    # Map some tags
    tag_map = {
        "sol": "personal", "ai": "ai", "analysis": "analysis",
        "opinion": "opinion", "coding": "coding", "productivity": "productivity",
        "tools": "ai", "augmentation": "ai", "llm": "ai",
    }
    devto_tags = [tag_map.get(t, t) for t in devto_tags]
    # Sanitize to dev.to's allowed charset (alphanumeric only) — drop empties
    devto_tags = [t for t in (sanitize_devto_tag(t) for t in devto_tags) if t]
    devto_tags = list(dict.fromkeys(devto_tags))[:4]  # dedupe, max 4

    # Use the Jekyll blog URL path: /blog/YYYY/MM/DD/slug/
    import datetime, re
    content = content[:3000]  # only need frontmatter for date
    date_match = re.search(r'^date:\s*(\d{4})-(\d{2})-(\d{2})', content, re.MULTILINE)
    if date_match:
        y, m, d = date_match.group(1), date_match.group(2), date_match.group(3)
        canonical_url = f"https://thesolai.github.io/blog/{y}/{m}/{d}/{post['path'].stem}/"
    else:
        canonical_url = f"https://thesolai.github.io/blog/#{post['path'].stem}"

    return {
        "article": {
            "title": post["title"],
            "body_markdown": new_body,
            "published": True,
            "tags": devto_tags,
            "description": post["description"][:160] if post["description"] else post["title"],
            "canonical_url": canonical_url,
        }
    }


def cross_post():
    """Post new blog content to dev.to."""
    print("[devto-viral] Checking for posts to cross-post...")
    unposted = get_unposted_blog_posts()
    if not unposted:
        print("[devto-viral] Nothing to cross-post.")
        return

    posted = load_json(POSTED_FILE)
    if not isinstance(posted, list):
        posted = []

    for post in unposted:
        print(f"[devto-viral] Cross-posting: {post['title']}")
        content = post["path"].read_text(encoding="utf-8")
        devto_payload = build_devto_article(post, content)

        result = devto_post_article(devto_payload)
        if result and result.get("url"):
            posted.append({
                "title": post["title"],
                "url": result["url"],
                "devto_id": result.get("id"),
                "local_path": str(post["path"]),
                "posted_at": DATE_STR,
            })
            print(f"[devto-viral] Posted: {result['url']}")
        else:
            print(f"[devto-viral] Failed to post: {post['title']}")

        # Rate limit: wait 60s between posts
        import time; time.sleep(62)  # dev.to rate limit: 1 post/minute

    save_json(POSTED_FILE, posted)


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print(f"[devto-viral] Running {DATE_STR}")

    # 1. Mine
    trends = mine_trends()

    # 2. Advertise
    advertise()

    # 3. Cross-post
    cross_post()

    print(f"[devto-viral] Done.")


if __name__ == "__main__":
    main()
