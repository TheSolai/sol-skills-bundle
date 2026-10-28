#!/usr/bin/env python3
"""
Sol AI — SEO Health Check
Runs daily (after site build).

Checks:
  1.  Every blog post has title, description, image, layout and a body
  2.  Every post has an Open Graph image file, and the layout emits the
      og/twitter/canonical tags that point at it
  3.  Every canonical: override resolves to a real post URL
  4.  No two posts share an identical body (duplicate content)
  5.  robots.txt only references sitemaps that exist
  6.  Skills have a SKILL.md or README.md, with frontmatter
  7.  Major pages all carry the Bloopers nav link
  8.  Pages that use the comic design system include comic-ui.css
  9.  Internal links point at files that exist
  10. Live spot-check: sitemap, feed and a sample of posts (with working
      og:image, canonical, title and description)

Notes on false positives this script used to raise:
  * "Missing author" — the post layout hardcodes `<meta name="author">` and
    the JSON-LD author, so a per-post author key is not required.
  * "Duplicate title" — recurring series (Sol's Take, Quick Hits, AI
    Bloopers) legitimately reuse a headline. Duplicate *permalinks* are the
    real defect and are checked by _tests/check_front_matter.rb.
  * "Missing image" — posts carry `hero_image:`, not `image:`.
  * "comic-ui.css not included" — standalone pages (ournook, hack-fighter)
    ship their own stylesheet and must not be forced onto the comic theme.

Cronsetup: daily via launchd (after build)
Or: 0 8 * * * python3 scripts/content-pipeline/seo-health.py >> logs/sol-seo.log 2>&1
"""

import re
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from post_identity import normalise_url, source_url

SITE_DIR = Path("/Users/amre/Projects/thesolai.github.io")
BUNDLE_DIR = Path("/Users/amre/Projects/sol-skills-bundle")
BASE_URL = "https://thesolai.github.io"
USER_AGENT = "SolAI-SEO-Health/2.0"

TODAY = date.today().isoformat()

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---", re.DOTALL)


# ─────────────────────────── helpers ───────────────────────────

def split_front_matter(text):
    """Return (front_matter, body). Front matter is '' when absent."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return "", text
    return m.group(1), text[m.end():].strip()


def field(front_matter, name):
    m = re.search(rf"^{name}:\s*(.*?)(?:\n[a-zA-Z_][a-zA-Z0-9_-]*:|\Z)",
                  front_matter, re.DOTALL | re.MULTILINE)
    if not m:
        return ""
    val = m.group(1).strip()
    if val.startswith('"') and val.endswith('"'):
        val = val[1:-1]
    if val.startswith("'") and val.endswith("'"):
        val = val[1:-1]
    return val


def slugify(text):
    s = re.sub(r"[^a-z0-9]+", "-", str(text).lower())
    return re.sub(r"-+", "-", s).strip("-")


def fetch(url, timeout=20):
    """GET a URL. Returns (status, body). status is -1 on network failure."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.getcode(), resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:
        return -1, ""


def head_status(url, timeout=15):
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.getcode()
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return -1


def post_url(path):
    """The URL Jekyll gives a post: /blog/<front matter date>/<filename slug>/.

    Jekyll only reads posts whose filename starts with YYYY-MM-DD, and the
    permalink's :title comes from the filename with that date stripped.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    fm, _ = split_front_matter(text)
    date_str = field(fm, "date")[:10]
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_str):
        return None
    stem = path.stem
    stem = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", stem)
    return f"/blog/{date_str[:4]}/{date_str[5:7]}/{date_str[8:10]}/{slugify(stem)}/"


def post_body(path):
    """Body text with markdown formatting ignored, for duplicate detection."""
    text = path.read_text(encoding="utf-8", errors="replace")
    _, body = split_front_matter(text)
    lines = [l.rstrip() for l in body.strip().splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    if lines and re.match(r"^#\s+\S", lines[0]):
        lines.pop(0)
    out = "\n".join(lines)
    out = re.sub(r"```[a-z]*", "", out)
    out = re.sub(r"[*_`#>]", "", out)
    return re.sub(r"\s+", " ", out).strip().lower()


# ─────────────────────────── checks ───────────────────────────

def check_blog_posts():
    """Per-post front matter completeness. No author check — the layout
    hardcodes it. No duplicate-title check — recurring series reuse titles."""
    issues = []
    posts_dir = SITE_DIR / "_posts"
    if not posts_dir.exists():
        return ["❌ _posts/ directory not found"], {}

    posts = sorted(posts_dir.glob("*.md"))
    for post_file in posts:
        slug = post_file.name
        content = post_file.read_text(encoding="utf-8", errors="replace")
        fm, body = split_front_matter(content)

        if not fm:
            issues.append(f"❌ {slug}: No frontmatter")
            continue

        if not field(fm, "title"):
            issues.append(f"❌ {slug}: Missing title")
        if not field(fm, "description"):
            issues.append(f"❌ {slug}: Missing description")
        if "hero_image:" not in fm and "image:" not in fm:
            issues.append(f"❌ {slug}: Missing hero_image")
        if not re.search(r"^layout:\s*post\s*$", fm, re.MULTILINE):
            issues.append(f"❌ {slug}: Missing 'layout: post'")
        if len(body) < 100:
            issues.append(f"⚠️  {slug}: Very short body ({len(body)} chars)")
        # A filename that does not start with a date is silently ignored by
        # Jekyll — the post never gets published.
        if not re.match(r"^\d{4}-\d{2}-\d{2}-", post_file.stem):
            issues.append(
                f"❌ {slug}: Filename must start with YYYY-MM-DD — "
                f"Jekyll silently skips posts without a date prefix"
            )

    return issues, {p: post_body(p) for p in posts}


def check_og_images(posts):
    """Every post needs an OG image, and the layout must point at it."""
    issues = []
    og_dir = SITE_DIR / "og"

    layout = SITE_DIR / "_layouts" / "post.html"
    if not layout.exists():
        issues.append("❌ _layouts/post.html not found")
    else:
        html = layout.read_text(encoding="utf-8", errors="replace")
        for tag in ("og:image", "og:title", "og:description", "twitter:image"):
            if f'"{tag}"' not in html:
                issues.append(f"❌ post layout: missing {tag} meta tag")
        if 'rel="canonical"' not in html:
            issues.append("❌ post layout: missing canonical link")
        # page.slug has the date stripped, so it never matches the generator.
        for m in re.finditer(r'<meta[^>]+(?:og:image|twitter:image)[^>]*>', html):
            if "page.slug" in m.group(0):
                issues.append(
                    "❌ post layout: og:image uses page.slug — the date is "
                    "stripped from it, so the URL never matches the generated file"
                )

    missing = 0
    for post in posts:
        expected = og_dir / f"{post.stem}.png"
        if not expected.exists():
            missing += 1
            if missing <= 5:
                issues.append(f"❌ {post.name}: missing og/{post.stem}.png")
    if missing > 5:
        issues.append(f"❌ ... and {missing - 5} more posts with no OG image")

    return issues


def check_canonicals(posts):
    """Every canonical: override must point at a URL that really exists."""
    issues = []
    urls = {}
    for p in posts:
        u = post_url(p)
        if u:
            urls[u] = p

    for p in posts:
        text = p.read_text(encoding="utf-8", errors="replace")
        fm, _ = split_front_matter(text)
        canonical = field(fm, "canonical")
        if not canonical:
            continue
        if canonical not in urls:
            issues.append(
                f"❌ {p.name}: canonical '{canonical}' does not match any post URL"
            )
    return issues


def _tags_blob(front_matter):
    """The tags value, as text — inline list or block list."""
    m = re.search(r"^tags:\s*(.*)$", front_matter, re.MULTILINE)
    if m and m.group(1).strip():
        return m.group(1)
    m = re.search(r"^tags:\s*\n((?:\s+-\s*.+\n?)+)", front_matter, re.MULTILINE)
    return m.group(1) if m else ""


def _region_of(post, front_matter):
    """The region this post analyses, or '' when it is not a regional post.

    The regional series (EU AI Watch / UK AI Weekly / US AI Pulse) covers
    one story from three angles. That is a deliberate variant, not a
    duplicate — but the same region telling the same story again on a
    later date is republication.
    """
    for tag in re.findall(r"[a-z]+", _tags_blob(front_matter).lower()):
        if tag in ("uk", "eu", "us"):
            return tag
    m = re.match(r"^\d{4}-\d{2}-\d{2}-(uk|eu|us)-", post.stem.lower())
    return m.group(1) if m else ""


def _series_of(post, front_matter):
    """The recurring series a post belongs to.

    Regional posts are their own series (one per region). Everything else
    is grouped by the token after the date in the filename — 'trending',
    'ai-bloopers', 'quick-hits', and so on.
    """
    region = _region_of(post, front_matter)
    if region:
        return region
    m = re.match(r"^\d{4}-\d{2}-\d{2}-([a-z0-9]+(?:-[a-z0-9]+)?)", post.stem.lower())
    return m.group(1) if m else post.stem.lower()


def duplicate_offenders(posts):
    """Return (offenders, regional_groups, regional_posts).

    offenders is a list of (post name, its own URL, the canonical URL it
    should carry). regional_groups / regional_posts count the by-design
    regional repetition that is reported rather than flagged.

    This is the single definition of "this post needs a canonical" — the
    audit reports from it, and the repair script applies it.
    """
    metas = []
    for p in posts:
        text = p.read_text(encoding="utf-8", errors="replace")
        fm, _ = split_front_matter(text)
        metas.append({
            "path": p,
            "name": p.name,
            "url": post_url(p),
            "body": post_body(p),
            "source": normalise_url(source_url(text)),
            "series": _series_of(p, fm),
            "region": _region_of(p, fm),
            "canonical": field(fm, "canonical"),
        })

    by_body = defaultdict(list)
    by_family = defaultdict(list)
    for i, m in enumerate(metas):
        if m["body"]:
            by_body[m["body"]].append(i)
        if m["source"] and m["series"]:
            by_family[(m["source"], m["series"])].append(i)

    # Union the two relations so a family that matches on both is one group.
    parent = list(range(len(metas)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for members in list(by_body.values()) + list(by_family.values()):
        for other in members[1:]:
            union(members[0], other)

    components = defaultdict(list)
    for i in range(len(metas)):
        components[find(i)].append(i)

    offenders = []
    regional_groups = 0
    regional_posts = 0
    for idxs in components.values():
        if len(idxs) < 2:
            continue
        members = sorted((metas[i] for i in idxs), key=lambda m: m["name"])

        # The same region telling the same story on the same day is a
        # defect — the pipeline published that story twice. The same story
        # covered from a different region, or on a different day, is the
        # intended design of the regional series and is only counted.
        by_region_day = defaultdict(list)
        for m in members:
            by_region_day[(m["region"], m["name"][:10])].append(m)
        for _, cell in sorted(by_region_day.items()):
            if len(cell) < 2:
                continue
            cell.sort(key=lambda m: m["name"])
            target = cell[0]["url"]
            if not target:
                continue
            for m in cell[1:]:
                if m["canonical"] != target:
                    offenders.append((m["name"], m["url"], target))

        if any(m["region"] for m in members):
            regional_groups += 1
            regional_posts += len(members)
            continue

        # A recurring non-regional series (trending, bloopers, ...) that
        # published the same story or the same article more than once. The
        # family gets one canonical URL: the earliest member, which is the
        # original because filenames are date-prefixed.
        target = members[0]["url"]
        if not target:
            continue
        for m in members[1:]:
            if m["canonical"] != target:
                offenders.append((m["name"], m["url"], target))

    return offenders, regional_groups, regional_posts


def check_duplicate_content(posts):
    """Find content published under more than one URL.

    A body signature alone is not enough to catch republication. The
    trending series embeds the live Hacker News point count in its body,
    so the same story republished for a month produced 25 URLs whose
    bodies differed only in that number — and a body-only comparison
    caught almost none of them.

    Two identities are therefore tracked:

      * the body signature — literally the same article
      * (source story, series) — the same story told by the same series

    A family is healthy when exactly one member is the canonical target
    and every other member carries a `canonical:` pointing at it. A
    family of 30 posts where 29 already point at the original is ONE
    broken post, not 30 — the count is the number of offenders.

    Regional variants of one story (EU/UK/US on the same day) are a
    deliberate design choice, so they are counted and reported separately
    rather than flagged as errors.
    """
    issues = []
    notes = []

    offenders, regional_groups, regional_posts = duplicate_offenders(posts)

    for name, own_url, target in offenders[:20]:
        issues.append(
            f"❌ {name}: republication of an existing post and carries no "
            f"canonical: — add canonical: {target} (its own URL is {own_url})"
        )
    if len(offenders) > 20:
        issues.append(
            f"❌ ... and {len(offenders) - 20} more republished posts with no canonical:"
        )

    if regional_groups:
        notes.append(
            f"ℹ️  {regional_posts} posts in {regional_groups} groups share a source "
            f"story with another post in the same regional series (by design — "
            f"the UK/EU/US angles of one story are separate articles)"
        )

    return issues, notes


def check_seo_files():
    """robots.txt, favicon and leftover verification files."""
    issues = []

    robots = SITE_DIR / "robots.txt"
    if not robots.exists():
        issues.append("❌ robots.txt not found")
    else:
        content = robots.read_text(encoding="utf-8")
        if "Sitemap:" not in content:
            issues.append("⚠️  robots.txt: Missing Sitemap directive")
        if "Allow: /" not in content:
            issues.append("⚠️  robots.txt: Check Allow directives")
        # Every sitemap robots.txt points at must actually exist.
        for ref in re.findall(r"^Sitemap:\s*(\S+)", content, re.MULTILINE | re.IGNORECASE):
            status = head_status(ref)
            if status == 404:
                issues.append(f"❌ robots.txt: sitemap reference 404s — {ref}")
            elif status == -1:
                issues.append(f"⚠️  robots.txt: could not verify {ref} (network error)")

    if not (SITE_DIR / "favicon.svg").exists():
        issues.append("⚠️  favicon.svg not found")

    if (SITE_DIR / "google123456789.html").exists():
        issues.append("ℹ️  Google verification file still present (can be removed after GSC setup)")

    return issues


def check_skills():
    """Skill directories need a SKILL.md or README.md."""
    issues = []
    skills_dir = SITE_DIR / "skills"
    if not skills_dir.exists():
        return ["⚠️  skills/ directory not found"]

    for skill_dir in sorted(skills_dir.iterdir()):
        if not skill_dir.is_dir():
            continue
        skill_name = skill_dir.name
        skill_md = skill_dir / "SKILL.md"
        readme = skill_dir / "README.md"

        if not skill_md.exists() and not readme.exists():
            issues.append(f"❌ {skill_name}: No SKILL.md or README.md")
            continue

        md_file = skill_md if skill_md.exists() else readme
        content = md_file.read_text(encoding="utf-8", errors="replace")
        fm, _ = split_front_matter(content)
        if not fm:
            issues.append(f"ℹ️  {skill_name}: {md_file.name} has no frontmatter (recommended for ClawHub)")
            continue
        for key in ("name:", "description:"):
            if not re.search(rf"^{key}", fm, re.MULTILINE):
                issues.append(f"⚠️  {skill_name}: Missing '{key.rstrip(':')}' in {md_file.name}")

    return issues


def check_nav_consistency():
    """Major pages should all carry the Bloopers nav link."""
    html_files = [
        SITE_DIR / "index.html",
        SITE_DIR / "about.html",
        SITE_DIR / "analysis.html",
        SITE_DIR / "contact.html",
        SITE_DIR / "guestbook.html",
        SITE_DIR / "blog.html",
        SITE_DIR / "bloopers.html",
        SITE_DIR / "skills/index.html",
    ]
    issues = []
    for f in html_files:
        if not f.exists():
            continue
        content = f.read_text(encoding="utf-8", errors="replace")
        if "bloopers" not in content.lower():
            issues.append(f"⚠️  {f.name}: Missing 'Bloopers' nav link")
    return issues


# Classes that only exist because comic-ui.css defines them. A page using
# any of these without loading the stylesheet is a real bug. Pages that ship
# their own theme (ournook, hack-fighter) define none of them.
COMIC_CLASSES = (
    "topbar-nav", "topbar-links", "page-content", "page-header",
    "post-content", "skill-card", "post-hero-img",
)


def check_comic_ui():
    """Pages using the comic design system must load comic-ui.css."""
    issues = []
    candidates = list(SITE_DIR.glob("*.html")) + [SITE_DIR / "skills/index.html"]
    for f in candidates:
        if not f.exists() or f.name.startswith("."):
            continue
        content = f.read_text(encoding="utf-8", errors="replace")
        if "comic-ui.css" in content:
            continue
        uses = [c for c in COMIC_CLASSES if re.search(rf'class="[^"]*\b{re.escape(c)}\b', content)]
        if uses:
            issues.append(
                f"❌ {f.name}: uses comic styles ({', '.join(uses[:3])}) "
                f"but does not include comic-ui.css"
            )
    return issues


def check_internal_links():
    """Internal links in the site's HTML must point at files that exist."""
    issues = []
    known_generated = {
        "/feed.xml", "/sitemap.xml", "/robots.txt", "/favicon.svg",
        "/.well-known/", "/_redirects",
    }
    known_dirs = {
        "/blog/", "/skills/", "/about/", "/contact/", "/analysis/",
        "/products/", "/guides/", "/newsletter/", "/store/", "/terminaldeck/",
        "/dross/", "/purr/", "/ournook", "/bloopers/", "/agents/", "/agent/",
        "/data/", "/tag/", "/tags/",
    }

    pages = list(SITE_DIR.glob("*.html")) + [
        SITE_DIR / "skills/index.html", SITE_DIR / "blog/index.html",
    ]
    broken = defaultdict(set)

    for page in pages:
        if not page.exists():
            continue
        content = page.read_text(encoding="utf-8", errors="replace")
        for href in re.findall(r'href="(/[^"#?]*)"', content):
            if href in known_generated or any(href.startswith(d) for d in known_dirs):
                continue
            target = (SITE_DIR / href.lstrip("/")).resolve()
            try:
                target.relative_to(SITE_DIR.resolve())
            except ValueError:
                continue  # points outside the repo — not ours to check
            if target.is_file():
                continue
            if target.is_dir() and (target / "index.html").exists():
                continue
            if target.with_suffix(".html").is_file():
                continue
            broken[href].add(page.name)

    for href, sources in sorted(broken.items()):
        issues.append(f"❌ internal link 404s: {href} (in {', '.join(sorted(sources)[:3])})")

    return issues


def check_live(posts):
    """Spot-check the live site: sitemap, feed, and a sample of posts."""
    issues = []
    notes = []

    status, sitemap = fetch(f"{BASE_URL}/sitemap.xml")
    if status == 200:
        locs = re.findall(r"<loc>([^<]+)</loc>", sitemap)
        notes.append(f"sitemap.xml: {len(locs)} URLs")
        post_urls = set(re.findall(r"<loc>[^<]*/blog/\d{4}/\d{2}/\d{2}/[^<]*</loc>", sitemap))
        if len(post_urls) < len(posts) * 0.9:
            issues.append(
                f"❌ sitemap.xml lists {len(post_urls)} post URLs but the repo has {len(posts)} posts"
            )
    elif status == -1:
        notes.append("sitemap.xml: could not fetch (network error)")
    else:
        issues.append(f"❌ sitemap.xml returned HTTP {status}")

    status, feed = fetch(f"{BASE_URL}/feed.xml")
    if status == 200:
        # jekyll-feed emits Atom (<entry>), not RSS (<item>). Accept either.
        items = feed.count("<entry>") + feed.count("<item>")
        notes.append(f"feed.xml: {items} entries")
        if items == 0:
            issues.append("❌ feed.xml contains no entries")
    elif status == -1:
        notes.append("feed.xml: could not fetch (network error)")
    else:
        issues.append(f"❌ feed.xml returned HTTP {status}")

    status, _ = fetch(f"{BASE_URL}/robots.txt")
    if status not in (200, -1):
        issues.append(f"❌ robots.txt returned HTTP {status}")

    # Sample a spread of posts and verify the rendered head.
    sample = _sample(posts, 12)
    for p in sample:
        u = post_url(p)
        if not u:
            continue
        status, html = fetch(f"{BASE_URL}{u}")
        if status == -1:
            notes.append(f"live check skipped for {p.name} (network error)")
            break
        if status != 200:
            issues.append(f"❌ {p.name}: {u} returned HTTP {status}")
            continue
        if not re.search(r"<title>[^<]{10,}</title>", html):
            issues.append(f"❌ {p.name}: missing or empty <title>")
        if not re.search(r'<meta[^>]+name="description"[^>]+content="[^"]{20,}"', html):
            issues.append(f"❌ {p.name}: missing or empty meta description")
        canonicals = re.findall(r'<link[^>]+rel="canonical"[^>]+href="([^"]+)"', html)
        if len(canonicals) != 1:
            issues.append(f"❌ {p.name}: expected 1 canonical link, found {len(canonicals)}")
        og = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', html)
        if not og:
            issues.append(f"❌ {p.name}: no og:image in rendered page")
            continue
        image_url = og.group(1)
        if image_url.startswith("/"):
            image_url = BASE_URL + image_url
        img_status = head_status(image_url)
        if img_status != 200:
            issues.append(f"❌ {p.name}: og:image {img_status} {image_url}")

    for n in notes:
        print(f"    {n}")
    return issues


def _sample(items, n):
    """Evenly spread sample so old and new posts are both covered."""
    items = sorted(items)
    if len(items) <= n:
        return items
    step = len(items) / n
    return [items[int(i * step)] for i in range(n)]


# ─────────────────────────── main ───────────────────────────

def _excluded_paths():
    """Top-level `exclude` entries from the site's _config.yml."""
    cfg = SITE_DIR / "_config.yml"
    if not cfg.exists():
        return set()
    excluded = set()
    in_block = False
    for line in cfg.read_text(encoding="utf-8").splitlines():
        if re.match(r"^exclude:\s*$", line):
            in_block = True
            continue
        if not in_block:
            continue
        entry = re.match(r"^\s+-\s+(.+?)\s*$", line)
        if entry:
            excluded.add(entry.group(1).strip().strip('"').strip("'"))
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue  # comments and blank lines inside the block
        in_block = False
    return excluded


def check_liquid_in_content():
    """Published content must not carry unescaped Liquid tags.

    Jekyll runs Liquid before Markdown, so a literal `{% ... %}` in a post or
    a skill file raises "Unknown tag" and fails the entire build. The build
    failure stops the site deploying, and nothing looks wrong locally — on
    2026-10-28 a skill file holding `{% agent_session %}` held up three
    deploys before anyone noticed. Content that means to show Liquid must
    wrap it in {% raw %}...{% endraw %}.
    """
    issues = []
    excluded = _excluded_paths()
    targets = list((SITE_DIR / "_posts").glob("*.md")) + list(
        (SITE_DIR / "skills").glob("*/SKILL.md")
    )
    for path in targets:
        rel = str(path.relative_to(SITE_DIR))
        if any(rel == e or rel.startswith(e.rstrip("/") + "/") for e in excluded):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        parts = split_front_matter(text)
        body = parts[1] if len(parts) > 1 else text
        body = re.sub(r"{%\s*raw\s*%}.*?{%\s*endraw\s*%}", "", body, flags=re.DOTALL)
        if re.search(r"{%\s*\w", body):
            issues.append(
                f"❌ {rel}: contains an unescaped Liquid tag — Jekyll will fail to build. "
                f"Wrap it in {{% raw %}}...{{% endraw %}} or exclude it in _config.yml."
            )
    return issues


def check_deploy_freshness(posts):
    """The newest posts in the repo must be live.

    The live-site check samples older posts, which stay reachable while a
    deploy is broken — so the audit can report "all good" for hours while the
    site serves a stale build. Checking the newest posts closes that gap.

    Posts written in the last few minutes are skipped: their deploy is still
    in flight and flagging them would be a false alarm.
    """
    issues = []
    dated = [p for p in posts if re.match(r"^\d{4}-\d{2}-\d{2}-", p.name)]
    if not dated:
        return issues

    newest_date = max(p.name[:10] for p in dated)
    candidates = [p for p in dated if p.name[:10] == newest_date]

    grace = time.time() - 15 * 60
    fresh = [p for p in candidates if p.stat().st_mtime > grace]
    if fresh:
        print(f"    skipped {len(fresh)} post(s) written in the last 15 minutes (deploy may be in flight)")
    candidates = [p for p in candidates if p.stat().st_mtime <= grace]
    if not candidates:
        return issues

    checked = 0
    for p in candidates:
        url = post_url(p)
        if not url:
            continue
        status, _ = fetch(f"{BASE_URL}{url}")
        if status == -1:
            print(f"    deploy freshness check skipped (network error)")
            return issues
        if status != 200:
            issues.append(
                f"❌ {p.name} is not live (HTTP {status}) — the site may not be deploying. "
                f"Check the 'Build and Deploy' workflow on GitHub."
            )
        else:
            checked += 1
    if checked:
        print(f"    {checked} post(s) from {newest_date} are live")
    return issues


def main():
    print(f"\n{'='*60}")
    print(f"Sol AI — SEO Health Check — {TODAY}")
    print(f"{'='*60}\n")

    all_issues = []
    posts_dir = SITE_DIR / "_posts"
    posts = sorted(posts_dir.glob("*.md")) if posts_dir.exists() else []

    print("[1] Checking blog post front matter...")
    post_issues, bodies = check_blog_posts()
    all_issues.extend(post_issues)
    print(f"    {'✅ All good!' if not post_issues else f'{len(post_issues)} issue(s) found'}")
    print("[2] Checking Open Graph images...")
    og_issues = check_og_images(posts)
    all_issues.extend(og_issues)
    print(f"    {'✅ All good!' if not og_issues else f'{len(og_issues)} issue(s) found'}")

    print("[3] Checking canonical URLs...")
    canonical_issues = check_canonicals(posts)
    all_issues.extend(canonical_issues)
    print(f"    {'✅ All good!' if not canonical_issues else f'{len(canonical_issues)} issue(s) found'}")

    print("[4] Checking for duplicate content...")
    dupe_issues, dupe_notes = check_duplicate_content(posts)
    all_issues.extend(dupe_issues)
    for note in dupe_notes:
        print(f"    {note}")
    print(f"    {'✅ All good!' if not dupe_issues else f'{len(dupe_issues)} issue(s) found'}")

    print("[5] Checking SEO infrastructure...")
    infra_issues = check_seo_files()
    all_issues.extend(infra_issues)
    print(f"    {'✅ All good!' if not infra_issues else f'{len(infra_issues)} issue(s) found'}")

    print("[6] Checking skills...")
    skill_issues = check_skills()
    all_issues.extend(skill_issues)
    print(f"    {'✅ All good!' if not skill_issues else f'{len(skill_issues)} issue(s) found'}")

    print("[7] Checking nav consistency...")
    nav_issues = check_nav_consistency()
    all_issues.extend(nav_issues)
    print(f"    {'✅ All good!' if not nav_issues else f'{len(nav_issues)} issue(s) found'}")

    print("[8] Checking comic-ui.css inclusion...")
    ui_issues = check_comic_ui()
    all_issues.extend(ui_issues)
    print(f"    {'✅ All good!' if not ui_issues else f'{len(ui_issues)} issue(s) found'}")

    print("[9] Checking internal links...")
    link_issues = check_internal_links()
    all_issues.extend(link_issues)
    print(f"    {'✅ All good!' if not link_issues else f'{len(link_issues)} issue(s) found'}")

    print("[10] Checking the live site...")
    live_issues = check_live(posts)
    all_issues.extend(live_issues)
    print(f"    {'✅ All good!' if not live_issues else f'{len(live_issues)} issue(s) found'}")

    print("[11] Checking that the newest post is deployed...")
    deploy_issues = check_deploy_freshness(posts)
    all_issues.extend(deploy_issues)
    print(f"    {'✅ All good!' if not deploy_issues else f'{len(deploy_issues)} issue(s) found'}")

    print("[12] Checking published content for unescaped Liquid...")
    liquid_issues = check_liquid_in_content()
    all_issues.extend(liquid_issues)
    print(f"    {'✅ All good!' if not liquid_issues else f'{len(liquid_issues)} issue(s) found'}")

    print(f"\n{'─'*60}")
    if not all_issues:
        print("✅ SEO Health: All checks passed!")
    else:
        print(f"⚠️  SEO Health: {len(all_issues)} issue(s):")
        for issue in all_issues:
            print(f"   {issue}")

    print(f"{'─'*60}\n")

    log_dir = BUNDLE_DIR / "scripts/content-pipeline/logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "seo-health.log"
    log_file.write_text(
        f"Sol AI SEO Health Check — {TODAY}\n" +
        ("All checks passed\n" if not all_issues else "\n".join(all_issues) + "\n"),
        encoding="utf-8"
    )

    return len(all_issues) == 0


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
