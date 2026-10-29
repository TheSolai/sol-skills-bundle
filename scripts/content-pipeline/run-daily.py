#!/usr/bin/env python3
"""
Sol AI — Daily Content Pipeline Runner
Fetches AI news, generates blog posts, creates bloopers content.

Run daily via cron at 8am UK time (07:00 UTC):
  0 7 * * * cd /Users/amre/Projects/sol-skills-bundle && python3 scripts/content-pipeline/run-daily.py >> logs/sol-content.log 2>&1

Or manually:
  python3 scripts/content-pipeline/run-daily.py

LLM: MiniMax only. Falls back to structured templates if key is missing.
"""

import sys, os, json, datetime, urllib.request, re
from html.parser import HTMLParser
from pathlib import Path
import sys as _sys
_sys.path.insert(0, "/Users/amre/Projects/sol-skills-bundle/scripts/content-pipeline")
from _minimax_helper import minimax_generate, load_minimax_key
from post_identity import body_signature, published_signatures

SITE_DIR = Path("/Users/amre/Projects/thesolai.github.io")
BUNDLE_DIR = Path("/Users/amre/Projects/sol-skills-bundle")
TODAY = datetime.datetime.now(datetime.timezone.utc)
DATE_STR = TODAY.strftime("%Y-%m-%d")
POST_DATE = TODAY.strftime("%B %d, %Y")
MINIMAX_KEY_PATH = Path.home() / ".openclaw" / "workspace" / "secrets" / "minimax-key.txt"


def _load_minimax_key() -> str:
    """Load MiniMax API key from secrets file."""
    try:
        return MINIMAX_KEY_PATH.read_text().strip()
    except Exception:
        return ""

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


# ── Source grounding ───────────────────────────────────────────────────────

class _StdlibHTMLText(HTMLParser):
    """Minimal HTML → text extractor, used when BeautifulSoup is unavailable.

    WHY (2026-10-28): the launchd jobs run under /opt/homebrew/bin/python3,
    which has no bs4 installed. `fetch_source_excerpt` caught the ImportError
    and returned "", so *every* source-grounded prompt that ran under launchd
    was silently written from the headline alone — no error, no log line.
    The stdlib parser needs no third-party package, so grounding now works
    on every interpreter the pipeline runs under.
    """

    SKIP_TAGS = {"script", "style", "noscript", "svg", "nav", "footer", "header"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._parts = []
        self._skip_depth = 0
        self.description = ""

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP_TAGS:
            self._skip_depth += 1
        elif tag == "meta":
            a = dict(attrs)
            name = (a.get("name") or a.get("property") or "").lower()
            if name in ("description", "og:description") and a.get("content"):
                if not self.description:
                    self.description = a["content"].strip()

    def handle_endtag(self, tag):
        if tag in self.SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth:
            return
        chunk = data.strip()
        if chunk:
            self._parts.append(chunk)

    def text(self):
        return "\n".join(self._parts)


def _html_to_text(raw):
    """Return (description, body_text) for an HTML document.

    Prefers BeautifulSoup when it is installed, and falls back to the
    stdlib parser so this works under every interpreter.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        BeautifulSoup = None

    if BeautifulSoup is not None:
        try:
            soup = BeautifulSoup(raw, "lxml")
        except Exception:
            try:
                soup = BeautifulSoup(raw, "html.parser")
            except Exception:
                soup = None
        if soup is not None:
            for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "header"]):
                tag.decompose()
            desc = ""
            for meta in soup.find_all("meta"):
                name = (meta.get("name") or meta.get("property") or "").lower()
                if name in ("description", "og:description"):
                    desc = (meta.get("content") or "").strip()
                    if desc:
                        break
            return desc, soup.get_text("\n")

    parser = _StdlibHTMLText()
    try:
        parser.feed(raw)
        parser.close()
    except Exception:
        return "", ""
    return parser.description, parser.text()


def fetch_source_excerpt(url, max_chars=6000):
    """Fetch a source page and return readable text for factual grounding.

    The regional prompts tell the model to write about "a development from
    the UK/EU/US". When the story is actually from elsewhere, the model used
    to invent a local origin to satisfy the framing. Giving it the real
    text removes the need to guess.

    Returns "" on any failure — this is an enhancement, never a hard
    dependency.
    """
    if not url or not url.startswith("http"):
        return ""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; SolAI/1.0)"}
        )
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read(1_500_000).decode("utf-8", "replace")
    except Exception:
        return ""

    desc, body = _html_to_text(raw)
    if not body:
        return ""

    text = re.sub(r"[ \t]+", " ", body)
    text = re.sub(r"\n{2,}", "\n", text).strip()

    if desc and desc not in text:
        text = f"{desc}\n\n{text}"

    return text[:max_chars].strip()


# ── Truncation repair ──────────────────────────────────────────────────────

def _ends_mid_sentence(text):
    """True when the body stops without closing its final sentence."""
    if not text:
        return True
    stripped = text.rstrip()
    if not stripped:
        return True
    # Ignore trailing markdown emphasis markers and quotes when checking.
    tail = stripped.rstrip('*"\'”’)]')
    return tail[-1:] not in ".!?\"'”’)]"


# A sentence terminator followed by whitespace and a capital — but not the
# period in an abbreviation like "U.S." (single capital letter before it).
_SENTENCE_END = re.compile(r"(?<![A-Z])[.!?][\"'”’)]*\s+(?=[A-Z“\"(\-])")


def repair_truncation(text):
    """Drop an incomplete trailing sentence; return None if nothing is left.

    A model that hits its token ceiling stops mid-word. Rather than publish
    a sentence fragment, cut back to the last complete sentence.

    Walks backwards from the end of the body, dropping:
      * the truncated trailing line (trimmed in-line to its last complete
        sentence where possible),
      * any heading left dangling with no body beneath it.

    Returns None when no safe cut exists.
    """
    if not text:
        return None
    if not _ends_mid_sentence(text):
        return text.strip()

    lines = text.rstrip().split("\n")
    i = len(lines) - 1

    while i >= 0:
        candidate_lines = list(lines[: i + 1])

        # The final line is the fragment. If it contains a complete earlier
        # sentence, cut back to it rather than throwing the paragraph away.
        if i == len(lines) - 1:
            trimmed = _trim_to_last_sentence(candidate_lines[-1])
            if trimmed:
                candidate_lines[-1] = trimmed
            else:
                candidate_lines.pop()
                i -= 1
                continue

        candidate = "\n".join(candidate_lines).rstrip()
        if not candidate:
            i -= 1
            continue

        # A heading with no paragraph under it is a dangling fragment too.
        last_content = [l for l in candidate_lines if l.strip()]
        if last_content and last_content[-1].strip().startswith("#"):
            i -= 1
            continue

        if _ends_mid_sentence(candidate):
            i -= 1
            continue

        words = len(re.findall(r"\b[\w'’-]+\b", candidate))
        if words < 200:
            return None
        return candidate + "\n"

    return None


def _trim_to_last_sentence(line):
    """Cut a line back to its last complete sentence, or '' if none."""
    if not line or not _ends_mid_sentence(line):
        return ""
    matches = list(_SENTENCE_END.finditer(line))
    if not matches:
        return ""
    cut = matches[-1].end()
    trimmed = line[:cut].rstrip()
    if not trimmed or _ends_mid_sentence(trimmed):
        return ""
    return trimmed


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

# Verified regional context, checked against primary sources in October 2026.
# The regional framing puts pressure on the model to produce a local angle even
# when the story has no local angle, and it fills the gap with whatever it
# half-remembers about that region's rules. It produced "the EU AI Act,
# currently making its way through the legislative process" on 2026-10-28 —
# the Act entered into force on 1 August 2024 and became applicable on
# 2 August 2026. Handing the model dated facts removes the guesswork.
REGIONAL_GROUNDING = {
    "uk": """REGIONAL FACTS (verified October 2026 — use these; do not contradict them):
- The UK has no AI-specific statute and no single AI regulator. Regulation is
  principles-based and sector-led: existing regulators (the ICO, the IPO) apply
  the powers they already have. Never describe a UK AI Act as law.
- The government's stance is explicitly pro-innovation: the AI Opportunities
  Action Plan (published 13 January 2025) frames a "proportionate, flexible
  regulatory approach" as a source of strength.
- The AI Security Institute (AISI) does independent pre-deployment evaluations
  of frontier models; a Prime Minister's AI Taskforce chaired by Lord Vallance
  reports to the PM directly. October 2026 announcements included £120m extra
  for AISI and £60m for a Sovereign AI Fund.
- Data protection is handled by the UK GDPR and the Data (Use and Access) Act
  2025, enforced by the ICO.""",
    "eu": """REGIONAL FACTS (verified October 2026 — use these; do not contradict them):
- The EU AI Act entered into force on 1 August 2024 and BECAME APPLICABLE ON
  2 AUGUST 2026. Enforcement powers for the AI Office and the national
  authorities apply from that date. It is law and it is being enforced now.
  NEVER describe it as "in the legislative process", "awaiting approval",
  "being passed", "not yet in force" or "coming next year" — that framing
  is roughly two years out of date.
- The Act applies progressively: prohibitions and AI literacy since
  2 February 2025; governance rules and general-purpose AI (GPAI) model
  obligations since 2 August 2025; Article 50 transparency obligations since
  2 August 2026; Annex III high-risk rules from 2 December 2027; Annex I
  high-risk rules for systems embedded in regulated products from
  2 August 2028.
- The "AI Omnibus" simplification was adopted on 19 November 2025, reached
  political agreement on 7 May 2026 and entered into force on 27 July 2026.
- GDPR remains the data-protection law, enforced by national DPAs.""",
    "us": """REGIONAL FACTS (verified October 2026 — use these; do not contradict them):
- The US has no comprehensive federal AI statute. Federal policy runs through
  executive orders and OMB memoranda binding federal agencies (M-25-21 and
  M-25-22: Chief AI Officers, AI governance boards, published use-case
  inventories). The NIST AI Risk Management Framework is voluntary.
- EO 14179 (23 January 2025, "Removing Barriers to American Leadership in
  Artificial Intelligence") revoked EO 14110 and set the deregulatory tone.
  "Winning the Race: America's AI Action Plan" followed on 23 July 2025.
- EO 14365 (11 December 2025) created an AI Litigation Task Force to challenge
  state AI laws. EO 14409 (2 June 2026) set up a classified benchmark for what
  counts as a "covered frontier model" plus a voluntary framework and a 30-day
  pre-release window for government access.
- The binding action is at state level: Colorado SB 26-189, California SB 53,
  Texas TRAIGA, Connecticut SB 5, Illinois SB 315, Utah SB 149, Maryland and
  others. Over 1,800 AI bills were introduced across 46 state legislatures as
  of July 2026, and the Senate struck down a proposed 10-year federal
  moratorium on state AI regulation. The accurate framing is a state
  patchwork filling a federal gap.""",
}

# The ground rules below tell the model not to invent. They did not stop it:
# on 2026-10-28 the UK post claimed "the UK's tech community is already buzzing
# with discussions about how to build on this concept" (nothing in the source
# said any such thing), and the US post explained the product's internals as
# "model distillation" when the source says it downloads a pre-existing
# open-weights model. Naming the specific failure modes works better than
# forbidding "invention" in the abstract.
ANTI_FABRICATION = """DO NOT FABRICATE (each of these has shipped on the live site):
- Never claim a community, company, government or industry is "buzzing",
  "racing to adopt", "discussing" or "taking note of" something unless the
  source text says so. If you want to discuss how builders might respond,
  frame it as an open question ("what would it take for..."), never as a
  reported fact.
- Never explain how a product or technology works internally unless the source
  says so. Do not name a specific technique (distillation, RAG, fine-tuning,
  quantisation, LoRA) unless the source text names it.
- Never call a project a company, startup, lab or team unless the source says
  so. If the source only gives a domain name or a code repository, say "the
  project" — and do not invent a founder, headcount, funding round or HQ.
- Prefer the source's specifics over generic regional filler. Numbers, model
  names, test scores, dates and source lists from the source are what make
  the post worth reading. If the source gives you a concrete regional hook,
  use it instead of inventing regional colour.
- If the source is thin on the regional angle, say plainly what is known and
  what is not. An honest gap reads better than confident invention."""

PROMPTS = {
    "uk": """Write a concise, engaging blog post analysing one significant AI development through a UK lens.

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
- Do NOT be generic — this is a specific story, not a summary

GROUND RULES (these matter more than the word count):
- The story below may NOT be British. If it is not, write about how it lands in the UK — who here is building on it, what UK regulators would make of it, what it means for UK readers. Do NOT claim it originated in the UK.
- Never invent company names, people, products, or national origins. Use only organisations and facts present in the source material.
- If the source material is thin, say plainly what is known and what is not. Honest gaps read better than confident invention.""",

    "eu": """Write a concise, engaging blog post analysing one significant AI development through an EU lens.

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
- Do NOT use bullet points

GROUND RULES (these matter more than the word count):
- The story below may NOT be European. If it is not, write about how it lands in Europe — how the AI Act, GDPR, or Brussels would treat it, and what European companies should take from it. Do NOT claim it originated in the EU.
- Never invent company names, people, products, or national origins. Use only organisations and facts present in the source material.
- If the source material is thin, say plainly what is known and what is not. Honest gaps read better than confident invention.""",

    "us": """Write a concise, engaging blog post analysing one significant AI development through a US lens.

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
- Do NOT use bullet points

GROUND RULES (these matter more than the word count):
- The story below may NOT be American. If it is not, write about how it lands in the US — Silicon Valley's likely response, US policy, what American builders should take from it. Do NOT claim it originated in the US.
- Never invent company names, people, products, or national origins. Use only organisations and facts present in the source material.
- If the source material is thin, say plainly what is known and what is not. Honest gaps read better than confident invention.""",
}


def generate_post(region, story, llm_api_key=None):
    """Generate a regional analysis post via MiniMax, or use structured template."""
    prompt = PROMPTS[region].format(date=POST_DATE)
    prompt += f"\n{REGIONAL_GROUNDING.get(region, '')}\n\n{ANTI_FABRICATION}\n"
    story_context = f"\nStory to analyse:\nTitle: {story['title']}\nSource: {story['url']}\nScore: {story['score']} points on HN\n"
    prompt += story_context

    # Feed the model the actual source page so it can ground the analysis in
    # real facts. Without this the model invents company names and national
    # origins to satisfy the regional framing (it invented "SpeechSphere" and
    # claimed a US project "emerged from the UK").
    excerpt = fetch_source_excerpt(story["url"])
    if excerpt:
        prompt += f"\nSource page text (use ONLY these facts):\n{excerpt}\n"
    print(f"[{region}] Generating post about: {story['title'][:60]}")

    key = _load_minimax_key()
    if not key:
        print(f"[{region}] No MiniMax key — using structured fallback")
        return None

    try:
        # max_tokens=600 was the truncation bug: 600 tokens is roughly 450
        # words, so a post asked for 400-600 words was cut off mid-sentence.
        # 260 of 275 published regional posts ended on a fragment. 1400 tokens
        # leaves headroom for a full 600-word body plus the title.
        result = minimax_generate(prompt, api_key=key, max_tokens=1400)

        if result:
            # A hard cut still ends mid-sentence. Repair it by dropping the
            # incomplete trailing fragment, then verify the body actually
            # closes. Anything still broken falls back to the template.
            repaired = repair_truncation(result)
            if repaired:
                print(f"[{region}] ✅ MiniMax")
                return repaired
            print(f"[{region}] ⚠️  Generated text was truncated — using template fallback")

    except Exception as e:

        print(f"[{region}] MiniMax error: {e}")

    print(f"[{region}] MiniMax failed — using structured fallback")
    return None


def _clean_title(raw):
    """Normalise an LLM-returned title for use in YAML frontmatter.

    The model frequently wraps titles in markdown emphasis ("**Title**"), which
    leaks literal asterisks into the frontmatter `title:` field, breaks the
    rendered <title> tag, and fails the site health test that checks for stray
    '**'. Strip heading markers, surrounding emphasis, and stray quotes.
    """
    t = raw.strip()
    t = re.sub(r"^#+\s*", "", t)          # leading markdown heading
    t = t.strip()
    t = t.strip("*_`").strip()            # surrounding emphasis / code
    t = t.strip()
    t = t.strip('"').strip("'").strip()
    t = re.sub(r"\s+", " ", t)
    t = t.strip()

    # The model sometimes opens a quote and never closes it, e.g.
    # 'US AI Pulse: "Dessa: The AI That Talks Like Us'. The unbalanced quote
    # then leaks into the YAML double-quoted scalar and breaks frontmatter.
    # Drop a dangling quote that has no partner in the string.
    if t.count('"') % 2 == 1:
        t = t.replace('"', "", 1)
    if t.count("'") % 2 == 1:
        # Only strip a stray apostrophe when it is not a possessive/quote
        # inside a word (e.g. "Dessa's" must survive).
        t = re.sub(r"(?<![A-Za-z])'(?![A-Za-z])", "", t, count=1)
    return t.strip()


def _yaml(value: str) -> str:
    """Quote a string for use as a YAML double-quoted scalar.

    Titles and descriptions regularly contain double quotes, colons and
    backslashes. Interpolating them raw produced unparseable frontmatter
    (Jekyll then published the post under a garbage URL). This escapes them.
    """
    v = str(value).replace("\\", "\\\\").replace('"', '\\"')
    v = v.replace("\n", " ").replace("\r", " ").strip()
    return f'"{v}"'

def _post_region_and_url(path: Path):
    """Return (region, hn_url) for an existing post, or (None, None).

    Region is detected from the filename token that follows the date
    (e.g. '2026-10-25-uk-ai-weekly-...') and falls back to the frontmatter
    tags, which also carry the region.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, None

    region = None
    m = re.match(r"^\d{4}-\d{2}-\d{2}-(uk|eu|us)-", path.name)
    if m:
        region = m.group(1)

    hn_url = ""
    m = re.search(r"^hn_url:\s*(\S*)", text, re.M)
    if m:
        hn_url = m.group(1)

    if region is None:
        m = re.search(r"^tags:\s*\[([^\]]*)\]", text, re.M)
        tag_blob = m.group(1) if m else ""
        if not tag_blob:
            m = re.search(r"^tags:\s*\n((?:\s*-\s*.+\n?)+)", text, re.M)
            tag_blob = m.group(1) if m else ""
        for candidate in ("uk", "eu", "us"):
            if re.search(rf"(^|[\s,\[\]\"'-]){candidate}($|[\s,\]\"'])", tag_blob):
                region = candidate
                break

    return region, hn_url


def find_duplicate_post(region, hn_url, date_str=None):
    """Find an existing post for this region/story that the pipeline already wrote.

    The filename of a regional post is derived from the LLM-generated title,
    so two runs on the same day produce two *different* filenames for the
    same story. Checking `filepath.exists()` therefore never matched, and
    every re-run published a duplicate post. Match on the stable keys
    instead: date + region + source URL.
    """
    date_str = date_str or DATE_STR
    posts_dir = SITE_DIR / "_posts"
    if not posts_dir.is_dir():
        return None

    for path in sorted(posts_dir.glob(f"{date_str}-*.md")):
        post_region, post_url = _post_region_and_url(path)
        if post_region != region:
            continue
        if hn_url and post_url and post_url == hn_url:
            return path
    return None


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

What this means for the UK: The UK's approach to AI regulation is principles-based and sector-led rather than statute-based — the MHRA and DSIT have both been moving faster in 2026. A story like this — depending on what it actually describes — could be another data point in the UK's evolving AI strategy, or a sign that UK researchers and companies are finally breaking through at the infrastructure level.

One key question: Is this a UK company, a UK research institution, or an international story with UK implications? That determines whether this is a domestic win or an export of someone else's agenda.

The bottom line: The UK is positioning itself as a thoughtful AI regulator. Stories like this test whether the country can move from guidance to execution.""",

            "eu": f"""EU AI Watch: {title_text}

The EU AI Act has been applicable since 2 August 2026, and every significant AI development gets filtered through Brussels. {title_text} — {story['score']} HN points — is today's candidate.

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
        # Fixed 2026-10-22: the title was truncated to 60 chars mid-word, which
        # shipped cut-off titles on the live site (37 posts). Keep the full
        # title; the description already carries the full source headline.
        title = f"[{region.upper()}] {title_text}"
    elif not title:
        lines = content.strip().split("\n")
        # Strip markdown emphasis: the model often returns titles wrapped in
        # "**...**", which leaks into frontmatter and fails the site health test.
        title = _clean_title(lines[0]) if lines else story["title"]

    # Truncating to 60 chars can leave a trailing hyphen (the strip above
    # happens before the cut). Jekyll's :title permalink drops that hyphen,
    # so the live URL no longer matches the filename. Trim again after.
    slug = re.sub(r'[^a-z0-9-]+', '-', (title or story["title"]).lower()).strip('-')[:60].strip('-')
    filename = f"{DATE_STR}-{slug}.md"
    filepath = SITE_DIR / "_posts" / filename

    tags = AI_TAGS_BY_REGION[region]
    frontmatter = f"""---
layout: post
title: {_yaml(title)}
date: {DATE_STR}
description: {_yaml(story['title'] + " — daily AI analysis from " + REGION_LABELS[region] + ".")}
image: /images/sol-avatar.png
hero_image: /images/sol-avatar.png
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
    """Generate the daily bloopers post.

    Only a template whose body has never been published is eligible. The
    template pool is small and finite, so a blind `random.choice` kept
    republishing an already-published story under a new date — that is how
    ~100 duplicate URLs piled up between June and October. Each new URL
    was a new crawlable page with the same text.

    When every template has been used, no post is written. The caller
    reports that loudly rather than publishing a copy. Resuming the series
    means adding a new template to BLOOPER_TEMPLATES.

    Returns (filepath, frontmatter). filepath is None when there is no
    unused template left.
    """
    import random
    published = published_signatures(SITE_DIR / "_posts")
    unused = [t for t in BLOOPER_TEMPLATES if body_signature(t) not in published]
    if not unused:
        return None, None

    content = random.choice(unused)
    title = "AI Bloopers: The Most Absurd AI Fails This Week"
    slug = f"{DATE_STR}-ai-bloopers"
    filename = f"{slug}.md"
    filepath = SITE_DIR / "_posts" / filename

    frontmatter = f"""---
layout: post
title: {_yaml(title)}
date: {DATE_STR}
description: "A weekly roundup of the most absurd, alarming, and accidentally hilarious AI failures. This week: prompt loops, phantom meetings, and hallucinated citations."
image: /images/sol-avatar.png
hero_image: /images/sol-avatar.png
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

        # Dedupe on date + region + source URL, checked *before* generating
        # content. The generated title (and therefore the filename) changes
        # between runs, so a filename check cannot detect that today's post
        # for this region already exists.
        duplicate = find_duplicate_post(region, story.get("url", ""))
        if duplicate:
            print(f"[{region}] ⚠️  Already published this story: {duplicate.name}")
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
    bloopers_written = False
    if filepath is None:
        print("[bloopers] ⚠️  Every bloopers template has already been published — "
              "skipping rather than republishing a story under a new date. "
              "Add a new template to BLOOPER_TEMPLATES to resume this series.")
    elif filepath.exists():
        print(f"[bloopers] ⚠️  Already exists: {filepath.name}")
    else:
        filepath.write_text(frontmatter, encoding="utf-8")
        bloopers_written = True
        print(f"[bloopers] ✅ Written: {filepath.name}")

    # 4. Push to GitHub if changes made.
    # The bloopers post counts as a change in its own right. The old test
    # was `generated_posts or not filepath.exists()`, but filepath always
    # existed by this point, so the condition collapsed to `generated_posts`
    # — a run that produced only a bloopers post committed nothing and the
    # post stayed unpublished.
    if generated_posts or bloopers_written:
        try:
            from site_git import publish
            sha = publish(f"Sol AI daily content: {DATE_STR}", allow_empty=True)
            if sha:
                print(f"\n[git] ✅ Committed daily content")
                print(f"[git] ✅ Pushed to GitHub Pages ({sha[:12]})")
            else:
                print(f"[git] No changes to commit")
        except Exception as e:
            print(f"[git] ❌  Git push FAILED: {e}")

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
    print(f"Generated: {len(generated_posts)} regional posts + {1 if bloopers_written else 0} bloopers post")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
