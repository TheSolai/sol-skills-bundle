#!/usr/bin/env python3
"""
skills-update.py — Refresh the skills showcase on the Sol AI homepage

Rotates which 6 skills are featured in the homepage hero section.
Keeps the showcase fresh without changing the actual skill marketplace.
Reads skills from schemas.json and randomly selects 6.

Usage:
    python3 skills-update.py [--dry-run]

Crontab (weekly, Monday 8am UK):
    0 8 * * 1 cd /Users/amre/Projects/sol-skills-bundle && python3 scripts/content-pipeline/skills-update.py >> logs/skills-update.log 2>&1
"""

import sys
import os
import json
import random
import re
import datetime
from pathlib import Path

SITE_DIR = Path("/Users/amre/Projects/thesolai.github.io")
BUNDLE_DIR = Path("/Users/amre/Projects/sol-skills-bundle")
SKILL_EMOJIS = {
    "email": "📧", "security": "🛡️", "audit": "🔍", "tarot": "🔮",
    "log": "🪵", "commit": "🏷️", "seo": "📊", "memory": "🧠",
    "writing": "✍️", "blog": "📝", "code": "💻", "api": "🔌",
    "pdf": "📄", "image": "🖼️", "video": "🎬", "music": "🎵",
    "default": "⚡",
}

SKILL_DESCRIPTIONS = {
    "email": "Automated email that works while you sleep. Real inbox, real replies.",
    "security": "Scan any codebase for security issues before they become incidents.",
    "audit": "Audit any URL for title, OG tags, canonical URLs, and JSON-LD.",
    "tarot": "22-card Major Arcana generated locally via Ollama. 100% private.",
    "log": "Parse any log file — errors, stack traces, HTTP codes. Instant clarity.",
    "commit": "Auto-generate Conventional Commits from your staged diffs.",
    "memory": "Persistent memory for AI agents. Remembers context between sessions.",
    "writing": "Long-form writing with structure, tone control, and citations.",
    "blog": "Generate complete blog posts with metadata, tags, and SEO fields.",
    "code": "Review code, find bugs, explain complex functions, write tests.",
    "default": "A production-grade AI agent tool. MIT licensed. One-command install.",
}

SKILLS_FILE = SITE_DIR / "skills" / "schemas.json"
HOMEPAGE_FILE = SITE_DIR / "index.html"


def load_skills():
    """Load skills from schemas.json."""
    if not SKILLS_FILE.exists():
        print(f"  ⚠️  Skills file not found: {SKILLS_FILE}")
        return []

    with open(SKILLS_FILE, encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        return data
    elif isinstance(data, dict):
        return data.get("skills", data.get("skills_list", []))
    return []


def get_emoji(name):
    """Get emoji for a skill based on its name."""
    name_lower = name.lower()
    for key, emoji in SKILL_EMOJIS.items():
        if key in name_lower:
            return emoji
    return SKILL_EMOJIS["default"]


def get_description(name):
    """Get description for a skill."""
    name_lower = name.lower()
    for key, desc in SKILL_DESCRIPTIONS.items():
        if key in name_lower:
            return desc
    return SKILL_DESCRIPTIONS["default"]


def build_skill_card(name, desc, emoji):
    """Build a skill card HTML block."""
    # URL-encode spaces for the link
    slug = name.lower().replace(" ", "-")
    return f'''                        <a href="/skills/" class="skill-card">
                            <span class="skill-emoji">{emoji}</span>
                            <div class="skill-info">
                                <h3>{name}</h3>
                                <p>{desc}</p>
                            </div>
                        </a>'''


def update_homepage(skills, dry_run=False):
    """Replace the skills showcase section on the homepage."""
    with open(HOMEPAGE_FILE, encoding="utf-8") as f:
        content = f.read()

    # Pick 6 random skills (use seeded random for reproducibility)
    random.seed(datetime.date.today().toordinal())
    selected = random.sample(skills, min(6, len(skills)))

    # Build new skill cards
    new_cards = [build_skill_card(s.get("name", s.get("title", "Skill")), get_description(s.get("name", "")), get_emoji(s.get("name", ""))) for s in selected]
    new_cards_html = "\n".join(new_cards)

    # Find and replace the skills grid
    pattern = r'(<div class="skills-grid">)\s*(.*?)\s*(</div>\s*<a href="/skills/" class="view-all">)'

    def replace_cards(m):
        return f'<div class="skills-grid">\n{new_cards_html}\n                    </div>\n                    <a href="/skills/" class="view-all">'

    new_content = re.sub(pattern, replace_cards, content, flags=re.DOTALL)

    if new_content == content:
        print("  ⚠️  Could not find skills grid in homepage — check pattern")
        return False

    if not dry_run:
        with open(HOMEPAGE_FILE, "w", encoding="utf-8") as f:
            f.write(new_content)
        print(f"  ✅ Updated homepage skills showcase with {len(selected)} random skills")

    return True


def main():
    dry_run = "--dry-run" in sys.argv

    print(f"\n[{datetime.datetime.now():%Y-%m-%d %H:%M}] skills-update running")

    skills = load_skills()
    if not skills:
        print("  ⚠️  No skills found — check schemas.json")
        return

    print(f"  Found {len(skills)} skills in marketplace")

    if dry_run:
        random.seed(datetime.date.today().toordinal())
        sample = random.sample(skills, min(6, len(skills)))
        print(f"  [DRY RUN] Would feature these 6 skills:")
        for s in sample:
            print(f"    {get_emoji(s.get('name',''))} {s.get('name', s.get('title','?'))}")
        return

    updated = update_homepage(skills, dry_run=False)
    if updated:
        print(f"  ✅ Homepage skills showcase refreshed")
    else:
        print(f"  ❌ Failed to update homepage")


if __name__ == "__main__":
    main()
