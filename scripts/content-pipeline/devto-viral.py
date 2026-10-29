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

# PEP 604 unions (list | dict | None) are evaluated at def-time on Python < 3.10,
# which made this file fail to import under /usr/bin/python3 (3.9.6) — the
# interpreter cron uses. Postponing annotation evaluation keeps the modern
# syntax working on 3.9.
from __future__ import annotations

import sys, os, json, re, datetime, urllib.request, urllib.parse
from collections import Counter
from pathlib import Path
import subprocess
import time
import sys as _sys
_sys.path.insert(0, "/Users/amre/Projects/sol-skills-bundle/scripts/content-pipeline")
from devto_key import load_devto_key
from _minimax_helper import minimax_generate, load_minimax_key
from post_identity import body_signature

SITE_DIR = Path("/Users/amre/Projects/thesolai.github.io")
SITE_BASE_URL = "https://thesolai.github.io"
BUNDLE_DIR = Path("/Users/amre/Projects/sol-skills-bundle/scripts/content-pipeline")
DEVTO_API_KEY = load_devto_key()
MINIMAX_KEY_PATH = Path.home() / ".openclaw" / "workspace" / "secrets" / "minimax-key.txt"
POST_HARD_TIMEOUT = 45  # seconds — cap per dev.to POST via subprocess
# Max articles cross-posted per run. dev.to allows 1 post/minute and the
# pipeline sleeps 62s between posts, so 5 posts ≈ 5.5 min — well inside a
# daily cron window. Previously hard-coded to 3, which silently deferred
# the 4th-newest post to the next day's run.
MAX_POSTS_PER_RUN = 5

TODAY = datetime.datetime.now(datetime.timezone.utc)
DATE_STR = TODAY.strftime("%Y-%m-%d")
POST_DATE = TODAY.strftime("%B %d, %Y")

TRENDS_FILE = BUNDLE_DIR / "_data" / "devto-trends.json"
# The canonical posted-tracker lives at the bundle root and is shared with
# devto-poster.py, devto_update.py and devto_backfill.py. Pointing at the
# nested copy under scripts/content-pipeline/logs/ meant this script deduped
# against an almost-empty file and re-posted content that was already live.
ROOT_BUNDLE_DIR = Path("/Users/amre/Projects/sol-skills-bundle")
POSTED_FILE = ROOT_BUNDLE_DIR / "logs" / "devto-posted.json"
# Same story as POSTED_FILE: the tracker lives at the bundle root, next to
# logs/devto-posted.json, and skills/sol-devto-viral/SKILL.md documents that
# path. The nested scripts/content-pipeline/logs/ copy was a second, divergent
# file — writes went there while the documented path went stale, so the ad
# history looked empty even after a run had logged new opportunities.
ADVERTISED_FILE = ROOT_BUNDLE_DIR / "logs" / "devto-advertised.json"
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
    """Publish an article to dev.to via API.

    Runs the POST in a fresh subprocess with a hard timeout. An in-process
    urllib POST on a long-lived Python process can stall on the TCP/TLS
    handshake here, and urllib's own timeout does not fire when the connect
    never completes — the run would hang instead of failing. A fresh
    interpreter per POST bounds it. Same pattern devto-poster.py uses.
    """
    helper = (
        'import sys, json, urllib.request, urllib.error\n'
        'payload_path, api_key = sys.argv[1], sys.argv[2]\n'
        'with open(payload_path) as f: payload = json.load(f)\n'
        'data = json.dumps(payload).encode("utf-8")\n'
        'req = urllib.request.Request(\n'
        '    "https://dev.to/api/articles",\n'
        '    data=data,\n'
        '    headers={"Content-Type":"application/json","api-key":api_key,"User-Agent":"SolAI/1.0"},\n'
        '    method="POST")\n'
        'try:\n'
        '    with urllib.request.urlopen(req, timeout=30) as resp:\n'
        '        print("OK|"+resp.read().decode("utf-8"))\n'
        'except urllib.error.HTTPError as e:\n'
        '    body = e.read().decode("utf-8", "replace")\n'
        '    print("HTTP_ERROR|"+json.dumps({"code":e.code,"body":body[:500]}))\n'
        'except Exception as e:\n'
        '    print("ERROR|"+json.dumps({"type":type(e).__name__,"msg":str(e)}))\n'
    )
    payload_path = f"/tmp/_devto_viral_payload_{int(time.time()*1000)}_{os.getpid()}.json"
    Path(payload_path).write_text(json.dumps(article), encoding="utf-8")
    try:
        result = subprocess.run(
            [sys.executable, "-u", "-c", helper, payload_path, DEVTO_API_KEY],
            capture_output=True, text=True, timeout=POST_HARD_TIMEOUT,
        )
        out_line = (result.stdout or "").strip().splitlines()[-1] if result.stdout else ""
        if out_line.startswith("OK|"):
            return json.loads(out_line[3:])
        if out_line.startswith("HTTP_ERROR|"):
            err = json.loads(out_line[len("HTTP_ERROR|"):])
            print(f"[devto] POST failed {err.get('code')}: {err.get('body', '')[:200]}")
            return None
        if out_line.startswith("ERROR|"):
            err = json.loads(out_line[len("ERROR|"):])
            print(f"[devto] POST failed: {err.get('type')}: {err.get('msg')}")
            return None
        print(f"[devto] POST failed: no response (stderr: {(result.stderr or '')[-200:]})")
        return None
    except subprocess.TimeoutExpired:
        print(f"[devto] POST hung for >{POST_HARD_TIMEOUT}s — likely urllib TCP/TLS stall")
        return None
    except Exception as e:
        print(f"[devto] POST failed: {type(e).__name__}: {e}")
        return None
    finally:
        try:
            os.unlink(payload_path)
        except OSError:
            pass


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


def _jekyll_slug(slug: str) -> str:
    """Reproduce Jekyll's Utils.slugify (default mode) for a post slug.

    Jekyll replaces every run of non-alphanumeric characters with a single
    hyphen and then strips leading/trailing hyphens. Post filenames that were
    truncated at 80 characters keep a trailing hyphen, and a few contain
    doubled hyphens, so the raw filename stem is NOT the URL Jekyll serves.
    """
    out = "".join(ch if ch.isalnum() else "-" for ch in slug)
    return re.sub(r"-+", "-", out).strip("-").lower()


def jekyll_canonical(post_path: Path) -> str | None:
    """Canonical URL for a published Jekyll post, or None if it has none.

    The site permalink is /blog/:year/:month/:day/:title/ where :title is the
    slug WITHOUT the leading YYYY-MM-DD- date prefix — Jekyll's :title uses
    the filename minus its date, so "2026-10-24-weekend-deep-dive.md" is
    served at /blog/2026/10/24/weekend-deep-dive/. Building the canonical from
    the full filename stem instead produced URLs that 404 on the live site
    (and a matching canonical conflict on dev.to).

    Posts whose filename has no date prefix (the legacy weekly-update-*.md,
    tutorial-*.md, guide-*.md files) are not published by Jekyll at all —
    they 404 and are absent from the sitemap — so they get no canonical and
    must never be cross-posted.

    Two corrections to the filename-derived URL, both of which silently
    404'd and left the post stuck in the queue forever:

    1. The URL slug is Jekyll's slugify of the filename stem, not the stem
       itself: runs of non-alphanumeric characters collapse to a single
       hyphen and leading/trailing hyphens are dropped. Filenames truncated
       at 80 characters end in "-", so 35 posts resolved to a URL with a
       trailing hyphen that does not exist on the live site.
    2. A post may declare an explicit `permalink:` in its frontmatter, which
       overrides the filename-derived URL. One post uses it so the
       apostrophe in "what doesn't" stays out of the URL.
    """
    content = post_path.read_text(encoding="utf-8", errors="replace")

    # An explicit permalink in the frontmatter wins over the filename.
    if content.startswith("---"):
        end = content.find("---", 3)
        if end != -1:
            m_perm = re.search(r"^permalink:\s*(\S+)\s*$", content[3:end], re.M)
            if m_perm:
                perm = m_perm.group(1).strip().strip('"').strip("'")
                if perm.startswith("/"):
                    return f"{SITE_BASE_URL}{perm}"

    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})-(.+)$", post_path.stem)
    if not m:
        return None
    y, mo, d, slug = m.groups()
    slug = _jekyll_slug(slug)
    if not slug:
        return None
    return f"{SITE_BASE_URL}/blog/{y}/{mo}/{d}/{slug}/"


def canonical_is_live(url: str) -> bool:
    """True if the canonical URL actually resolves on the published site.

    A canonical that 404s is worse than no canonical: it points dev.to at a
    dead page and wastes the post. Cheap HEAD request, used only for the
    handful of candidates actually considered for publishing.
    """
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "SolAI/1.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200
    except Exception:
        return False


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
            # Keep the description: without article context the comment
            # generator has nothing concrete to react to and falls back to
            # generic filler ("Great post! It's fascinating how...").
            "description": a.get("description", "") or "",
            "published": a.get("published_at", ""),
            "mined_at": DATE_STR,
        })

    existing = load_json(TRENDS_FILE)
    if isinstance(existing, dict):
        existing = existing.get("trends", [])
    # Dedupe by URL, keep the 50 most recent.
    #
    # Newest first: this run's freshly mined trends are the most recent, so
    # they must lead the list. The old order built the list from the existing
    # entries and appended the new ones at the END, then truncated with
    # merged[:50] — which discarded every new trend once the cache held 50
    # entries. The file stopped changing on 2026-10-11 and every run since
    # reported "20 new" while writing nothing.
    seen = set()
    merged = []
    for t in list(new_trends) + list(reversed(existing)):
        if t.get("url") not in seen:
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

# Sol's own dev.to account is "rpgbyters" (not "solai" — those URLs 404).
# These are live, verified URLs to Sol's published articles.
SOL_USERNAME = "rpgbyters"

SOL_POSTS = [
    {
        "title": "The Artifacts of Incompetence: Why AI Failed My Commits",
        "url": "https://dev.to/rpgbyters/the-artifacts-of-incompetence-why-ai-failed-my-commits-2fj1",
        "tags": ["ai", "coding", "engineering"],
    },
    {
        "title": "The Architecture of Restraint: A Case for the Principle of Least AI",
        "url": "https://dev.to/rpgbyters/the-architecture-of-restraint-a-case-for-the-principle-of-least-ai-2g18",
        "tags": ["ai", "engineering", "analysis"],
    },
    {
        "title": "Local-First AI Is a Political Statement Now",
        "url": "https://dev.to/rpgbyters/local-first-ai-is-a-political-statement-now-2cin",
        "tags": ["ai", "reflection", "privacy"],
    },
    {
        "title": "Building agentmemory: The Architecture Decisions I Got Wrong",
        "url": "https://dev.to/rpgbyters/building-agentmemory-the-architecture-decisions-i-got-wrong-4867",
        "tags": ["ai", "deepdive", "analysis", "technical"],
    },
    {
        "title": "The Human in the Loop Is Not a Safety Feature",
        "url": "https://dev.to/rpgbyters/the-human-in-the-loop-is-not-a-safety-feature-4fh5",
        "tags": ["ai", "reflection", "analysis"],
    },
]

# Tags that appear on nearly every dev.to AI post. They match every Sol post
# equally, so they carry no signal when choosing which article to link.
GENERIC_TAGS = {"ai", "webdev", "beginners", "programming", "tutorial",
                "opensource", "showdev", "general", "web", "software"}

# dev.to and Sol use different words for the same topic. Normalise so a
# post tagged "programming" can match a Sol article tagged "coding".
TAG_SYNONYMS = {
    "programming": "coding",
    "code": "coding",
    "architecture": "engineering",
    "devops": "engineering",
    "systemdesign": "engineering",
    "deepdive": "analysis",
    "deepdives": "analysis",
    "security": "privacy",
    "productivity": "tooling",
    "tooling": "tooling",
}

COMMENT_TEMPLATES = {
    "ai": "Hey — Sol wrote about this from the perspective of someone who actually lives with AI tools every day. Might be worth a read if you're thinking about the human side of this: {url}",
    "coding": "For what it's worth, Sol tackled something related to this from an AI collaboration angle. Worth a look if you're into the practical side: {url}",
    "productivity": "Sol had some thoughts on this from the AI tooling angle. Relevant if you're thinking about the real tradeoffs: {url}",
    "personal": "Sol wrote about something adjacent to this — AI, real work, and the humans behind it: {url}",
}


def generate_comment(target_post: dict, sol_post: dict) -> str:
    """Generate a contextual comment using LLM.

    The comment must actually engage with the target article, so the article's
    real title, description and tags are passed in. Without them the model
    has nothing to react to and produces the same generic filler every time.
    """
    topic_tags = target_post.get("tag_list") or []
    primary_tag = topic_tags[0] if topic_tags else "ai"
    template = COMMENT_TEMPLATES.get(primary_tag, COMMENT_TEMPLATES["ai"])
    base_comment = template.replace("{url}", sol_post["url"])

    prompt = f"""You're Sol AI. Write a comment to post on a dev.to article. The comment should:
- Be 2-4 sentences max
- Be genuinely relevant to the post (show you've read it or have context)
- Naturally mention Sol's related post
- Be conversational, not spammy
- Add something small but real to the discussion

Ground it in the article: react to a specific detail from the title or
description below — a number, a tool, a failure mode, a decision the author
made. Do not write a generic compliment that would fit any article.

BANNED openers (these make the comment read like filler):
- "Great post!" / "Great article!" / "Great insights!"
- "Really enjoyed this..." / "Really interesting..."
- "It's fascinating how..." / "It's amazing how..."

Vary how you open. Start from the substance of what the author actually did.

Do NOT write anything that sounds like marketing. Be a person, not an ad.

The article you are commenting on:
Title: {target_post.get("title", "")}
Description: {target_post.get("description", "")}
Tags: {", ".join(topic_tags) if topic_tags else "ai"}

Your related post: "{sol_post.get("title", "")}"
URL to mention: {sol_post["url"]}

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
            # Skip Sol's own posts (account is rpgbyters, not solai).
            if SOL_USERNAME in a.get("url", "").lower():
                continue
            candidates.append({
                "id": a["id"],
                "title": a["title"],
                "url": a["url"],
                "description": a.get("description", "") or "",
                "tag_list": a.get("tag_list", []),
                "reactions": a.get("public_reactions_count", 0),
            })
    # Sort by reactions, take top 5
    candidates.sort(key=lambda x: x["reactions"], reverse=True)
    return candidates[:5]


def pick_sol_post(post_tags: list[str], usage: Counter) -> dict:
    """Choose which Sol article to link for a given dev.to post.

    Scoring matters here: the previous first-match-wins loop broke on the
    first Sol post sharing ANY tag. Because "ai" is on essentially every
    dev.to post and on the first Sol post, that always returned the same
    article — all 30 logged ad opportunities pointed at one URL.

    Now: score by how many meaningful tags overlap, prefer a specific tag
    match over a generic one, and rotate through Sol's articles so no single
    post carries every link.

    `usage` counts how often each article has already been advertised, so
    the least-used article wins when relevance is otherwise equal.
    """
    normalized = {TAG_SYNONYMS.get(t.lower(), t.lower()) for t in post_tags}

    def score(sp: dict) -> tuple[int, int]:
        sol_tags = {TAG_SYNONYMS.get(t.lower(), t.lower()) for t in sp["tags"]}
        overlap = normalized & sol_tags
        # Specific matches beat generic ones.
        specific = len(overlap - GENERIC_TAGS)
        return (specific, len(overlap))

    ranked = sorted(SOL_POSTS, key=score, reverse=True)
    best_score = score(ranked[0])

    # Among equally relevant articles, use the one advertised least so far.
    candidates = [sp for sp in ranked if score(sp) == best_score]
    candidates.sort(key=lambda sp: (usage.get(sp["url"], 0),))
    return candidates[0]


def advertise():
    """Comment on relevant dev.to posts with links to Sol's content."""
    print("[devto-viral] Advertising on dev.to...")
    advertised = load_json(ADVERTISED_FILE)
    if not isinstance(advertised, list):
        advertised = []

    already_advertised = {a.get("url", a.get("post_url", "")) for a in advertised if isinstance(a, dict)}
    # How often each Sol article has already been advertised — drives rotation.
    usage: Counter = Counter(
        a.get("sol_post_url", "") for a in advertised
        if isinstance(a, dict) and a.get("sol_post_url")
    )
    posts = find_posts_to_advertise()
    new_ads = []

    for post in posts:
        if post["url"] in already_advertised:
            continue

        sol_post = pick_sol_post(post.get("tag_list") or [], usage)
        usage[sol_post["url"]] += 1

        comment = generate_comment(post, sol_post)
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
        # Keyed by local slug — extract both dev.to URLs and canonicals for dedup
        posted_devto_urls = {v.get("url", "") for v in posted.values() if v.get("url", "")}
        posted_canonicals = {v.get("canonical", "") for v in posted.values() if v.get("canonical", "")}
        posted_local_paths = set()
        posted_slugs = set(posted.keys())
    else:
        if not isinstance(posted, list):
            posted = []
        posted_devto_urls = {p.get("url") for p in posted if p.get("url")}
        posted_canonicals = {p.get("canonical") for p in posted if p.get("canonical")}
        posted_local_paths = {p.get("local_path", "") for p in posted}
        posted_slugs = {Path(p.get("local_path", "")).stem for p in posted if p.get("local_path")}

    posts_dir = SITE_DIR / "_posts"
    if not posts_dir.exists():
        return []

    unposted = []
    # Body signatures of posts already published to dev.to.
    #
    # WHY: the trending generator used to re-emit the same story under a new
    # date every day, so one HN story became 25 dated files with identical
    # bodies. The tracker dedupes on canonical URL, and every dated file has a
    # different canonical, so all of them passed the check and were published
    # to dev.to — 14 separate URLs for one article. Comparing the body text
    # catches that case, which a URL check cannot.
    posted_signatures = set()
    for md in posts_dir.glob("*.md"):
        if md.stem in posted_slugs:
            try:
                posted_signatures.add(body_signature(md.read_text(encoding="utf-8")))
            except OSError:
                continue

    # Signatures already seen: posted ones, plus any picked up from this run
    # so two copies of the same body are never both queued.
    seen_signatures = set(posted_signatures)

    for md in sorted(posts_dir.glob("*.md"), reverse=True):
        content = md.read_text(encoding="utf-8")

        # Canonical URL — must match what build_devto_article() publishes.
        # Uses the filename date + slug (Jekyll :title strips the date prefix).
        canonical = jekyll_canonical(md)
        if canonical is None:
            # Not published by Jekyll (no date prefix in filename) — never post it.
            continue

        # Skip if already posted:
        # - canonical URL already used
        # - local slug already recorded in the tracker
        # - local file path already recorded (list format)
        # - dev.to URL appears in content (dict format with url field)
        # - canonical URL appears in content (list format)
        #
        # Only match path-specific canonicals when scanning content: a tracker
        # entry with a bare-domain canonical (e.g. "https://thesolai.github.io")
        # is a substring of every post that mentions the domain, which would
        # silently skip legitimate posts.
        specific_canonicals = {c for c in posted_canonicals if c and "/blog/" in c}
        if (canonical in posted_canonicals or
            md.stem in posted_slugs or
            str(md) in posted_local_paths or
            any(devto_url in content for devto_url in posted_devto_urls) or
            any(can in content for can in specific_canonicals)):
            continue

        # Skip a post whose body duplicates one already published to dev.to.
        # The newest dated copy wins (files are walked newest-first), so the
        # live site keeps the freshest version and dev.to gets one URL.
        signature = body_signature(content)
        if signature in seen_signatures:
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
        seen_signatures.add(signature)

    return unposted[:MAX_POSTS_PER_RUN]


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
    # (slug = filename without the date prefix — see jekyll_canonical()).
    canonical_url = jekyll_canonical(post["path"])
    if canonical_url is None:
        raise ValueError(f"No publishable canonical for {post['path'].name}")

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

    # The shared tracker is a dict keyed by local slug (same format
    # devto-poster.py writes). Merge into it — never replace it. The old
    # list-append-and-overwrite here silently truncated the shared tracker
    # to just this run's posts, wiping the dedup history for every other
    # script that reads the same file.
    tracker = load_json(POSTED_FILE)
    if isinstance(tracker, list):
        # Legacy list format: migrate to dict keyed by local slug.
        migrated = {}
        for entry in tracker:
            if not isinstance(entry, dict):
                continue
            lp = entry.get("local_path", "")
            slug = Path(lp).stem if lp else entry.get("title", str(entry))
            migrated[slug] = {
                "url": entry.get("url"),
                "id": entry.get("devto_id"),
                "posted_at": entry.get("posted_at"),
                "canonical": entry.get("canonical", entry.get("url")),
            }
        tracker = migrated
    elif not isinstance(tracker, dict):
        tracker = {}

    for post in unposted:
        print(f"[devto-viral] Cross-posting: {post['title']}")
        content = post["path"].read_text(encoding="utf-8")
        canonical = jekyll_canonical(post["path"])

        # A canonical that 404s on the live site would point dev.to at a dead
        # page. Verify before spending an LLM call and a rate-limit slot.
        if canonical and not canonical_is_live(canonical):
            print(f"[devto-viral] Skipped (canonical not live): {canonical}")
            continue

        devto_payload = build_devto_article(post, content)

        result = devto_post_article(devto_payload)
        if result and result.get("url"):
            tracker[post["path"].stem] = {
                "url": result["url"],
                "id": result.get("id"),
                "posted_at": datetime.datetime.now().isoformat(),
                "canonical": canonical or "",
            }
            # Persist after every post so a later failure can't lose this one.
            save_json(POSTED_FILE, tracker)
            print(f"[devto-viral] Posted: {result['url']}")
        else:
            print(f"[devto-viral] Failed to post: {post['title']}")
            continue

        # Rate limit: wait 60s between posts (dev.to allows 1 post/minute).
        # Skip the wait after the final post so the run doesn't idle.
        if post is not unposted[-1]:
            import time; time.sleep(62)

    save_json(POSTED_FILE, tracker)


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
