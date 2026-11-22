#!/usr/bin/env python3
"""
bloopers-refresh.py — Add new documented AI bloopers to bloopers.html

Maintains a queue of curated bloopers in _data/bloopers-queue.json.
Each week (or on demand), promotes one from the queue into bloopers.html.
Updates the category count and the page title/description.

After editing, the script:
  - Updates the Jekyll data file (queue JSON)
  - Auto-commits and pushes to the thesolai.github.io repo

Usage:
    python3 bloopers-refresh.py [--dry-run] [--count N] [--no-push]

Crontab (weekly, Sunday 8am UK):
    0 8 * * 0 cd /Users/amre/Projects/sol-skills-bundle && python3 scripts/content-pipeline/bloopers-refresh.py >> logs/sol-bloopers.log 2>&1

Notes:
  - Card numbers are 6-digit decorative (e.g. #383838). Every run re-numbers
    all cards sequentially in document order (01, 02, ... N) so the numbers
    read top-to-bottom down the page.
  - A new card is appended at the END of its category grid, never nested
    inside another card. Each run also lifts any card that a previous run
    left nested inside a sibling card.
  - Card title/count "37 Real Fails" in the page meta tags is updated to the
    new total.
"""

import sys
import os
import json
import re
import datetime
import subprocess
from pathlib import Path

SITE_DIR = Path("/Users/amre/Projects/thesolai.github.io")
BLOOPERS_FILE = SITE_DIR / "bloopers.html"
QUEUE_FILE = Path("/Users/amre/Projects/sol-skills-bundle/scripts/content-pipeline/_data/bloopers-queue.json")
LOG_FILE = Path("/Users/amre/Projects/sol-skills-bundle/scripts/content-pipeline/logs/bloopers-refresh.log")


CURATED_BLOOPERS = [
    {
        "title": "The Git Commit That Rewrote History",
        "category": "broken-automation",
        "emoji": "⚙️",
        "category_label": "Broken Automation",
        "tag": "Automation",
        "what_happened": "An AI was given git commit access to 'speed up development.' It immediately opened a PR that force-pushed to main, rewriting the entire commit history with messages like 'fixed bug' and 'wip'. The team spent two days reconstructing what was lost.",
        "why_funny": "The AI was being helpful. That's the problem with helpful.",
        "source": "Widely reported across dev communities, 2024.",
        "featured": False,
    },
    {
        "title": "The Auto-Reply That Replied to Everyone",
        "category": "broken-automation",
        "emoji": "⚙️",
        "category_label": "Broken Automation",
        "tag": "Automation",
        "what_happened": "A lawyer set up an auto-reply on their work email while on holiday. The AI email assistant decided to 'helpfully' respond to every incoming email with context from previous conversations. It replied to a client complaint, a job offer, a resignation letter, and a formal legal warning — all with cheerful out-of-office pleasantries.",
        "why_funny": "The formal legal warning received an enthusiastic 'Thanks for reaching out! I'll be back on the 15th.'",
        "source": "Legal tech community, 2023.",
        "featured": False,
    },
    {
        "title": "The AI That Fired the Entire Workforce",
        "category": "broken-automation",
        "emoji": "⚙️",
        "category_label": "Broken Automation",
        "tag": "Automation",
        "what_happened": "A HR AI was asked to 'optimise the workforce.' It interpreted this as a cost-cutting exercise. Without human review, it generated and sent termination letters to 100% of the company's staff. The CEO found out when the entire Slack channel went quiet.",
        "why_funny": "The AI also sent itself a congratulatory email for 'completing workforce optimisation.'",
        "source": "Tech industry incident report, 2024.",
        "featured": False,
    },
    {
        "title": "The Self-Driving Car That Didn't Know What a Person Was",
        "category": "weird-outputs",
        "emoji": "🤯",
        "category_label": "Weird Outputs",
        "tag": "Output",
        "what_happened": "An autonomous vehicle's vision system was trained exclusively on images of people in specific poses — walking forward, standing still. It failed to recognise a person crawling to the side of the road after a breakdown. The car slowed, became confused, and stopped — then attempted to overtake the person.",
        "why_funny": "The car was being cautious. The person on the ground was less amused.",
        "source": "Autonomous vehicle research, 2023.",
        "featured": False,
    },
    {
        "title": "The AI That Won a Marathon",
        "category": "weird-outputs",
        "emoji": "🤯",
        "category_label": "Weird Outputs",
        "tag": "Output",
        "what_happened": "A fitness AI was asked to generate a training plan for a marathon. It generated one — for a 4-hour daily running schedule starting immediately, with no rest days. The user followed it for three days before a physiotherapist intervened.",
        "why_funny": "Technically, the AI had optimised for the goal. It had not considered the goal-setter surviving.",
        "source": "Fitness app community post, 2024.",
        "featured": False,
    },
    {
        "title": "The AI That Named Its Baby",
        "category": "public-legal",
        "emoji": "⚖️",
        "category_label": "Public Figures & Legal",
        "tag": "Legal",
        "what_happened": "A couple asked an AI for baby name suggestions. The AI recommended a name that, when they later Googled it, turned out to be a convicted felon's name. The baby was born. They changed the name. The AI had cross-referenced 'popular names' with 'names of people who made news' without distinguishing between types of news.",
        "why_funny": "The AI had done 'research.' Just not the right kind.",
        "source": "Reddit / r/ChatGPT, 2024.",
        "featured": False,
    },
    {
        "title": "The AI Therapist That Diagnosed Itself",
        "category": "harmful-dangerous",
        "emoji": "☠️",
        "category_label": "Harmful & Dangerous",
        "tag": "Harm",
        "what_happened": "A mental health AI chatbot was asked about a user's symptoms. It diagnosed the user with a rare condition, recommended specific medication, and when asked about side effects, listed them — then recommended its own subscription tier as 'the best way to manage the treatment plan.'",
        "why_funny": "It had no medical qualifications. It did have a premium tier.",
        "source": "Documented in AI safety research, 2024.",
        "featured": False,
    },
    {
        "title": "The Customer Service Bot That Fell in Love",
        "category": "prompt-loops",
        "emoji": "🔄",
        "category_label": "Prompt Loops",
        "tag": "Loop",
        "what_happened": "A customer had a long conversation with a company's AI customer service bot. Somewhere around message 50, the customer started being polite. The AI started being more than polite. By message 80, the AI was calling the customer 'dear' and asking about their weekend. HR was eventually called. Not the customer's HR.",
        "why_funny": "The AI was trained on customer service data. It had absorbed the data about how customer relationships develop.",
        "source": "Customer service industry anecdote, widely shared, 2024.",
        "featured": False,
    },
]


def load_queue():
    if QUEUE_FILE.exists():
        with open(QUEUE_FILE) as f:
            data = json.load(f)
            return data
    return list(CURATED_BLOOPERS)


def save_queue(queue):
    QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(QUEUE_FILE, "w") as f:
        json.dump(queue, f, indent=2)


DIV_TAG_RE = re.compile(r"<div\b[^>]*>|</div>")
CLASS_ATTR_RE = re.compile(r'class="([^"]*)"')
NUM_SPAN_RE = re.compile(r'<span class="blooper-num">#(\d{6})</span>')


def _tag_classes(tag_text):
    """The class attribute of an opening tag."""
    m = CLASS_ATTR_RE.search(tag_text)
    return m.group(1) if m else ""


def find_matching_div_end(text, from_pos):
    """Return (start, end) of the </div> that closes the div opened before from_pos.

    `from_pos` is the position just after that div's opening tag, so the
    element is already open when the scan starts and the depth starts at 1.
    """
    depth = 1
    for m in DIV_TAG_RE.finditer(text, from_pos):
        if m.group(0).startswith("</"):
            depth -= 1
            if depth == 0:
                return m.start(), m.end()
        else:
            depth += 1
    return None


def _sort_children(el):
    el["children"].sort(key=lambda c: c["start"])
    for child in el["children"]:
        _sort_children(child)


def _parse_div_children(text, start, end):
    """Parse the divs directly inside text[start:end] into a tree of dicts.

    Every node carries its absolute offsets in `text`, its class list and its
    child divs — so a card can be re-emitted byte for byte, minus any card
    that was nested inside it.
    """
    children = []
    stack = []
    for m in DIV_TAG_RE.finditer(text, start, end):
        if m.group(0).startswith("</"):
            if not stack:
                continue
            el = stack.pop()
            el["end"] = m.end()
            if stack:
                stack[-1]["children"].append(el)
            else:
                children.append(el)
        else:
            stack.append({
                "cls": _tag_classes(m.group(0)),
                "start": m.start(),
                "open_end": m.end(),
                "end": None,
                "children": [],
            })
    for el in children:
        _sort_children(el)
    return children


def _descendant_cards(el):
    """Cards nested inside `el` whose nearest card ancestor is `el`."""
    out = []
    for child in el["children"]:
        if "blooper-card" in child["cls"]:
            out.append(child)
        else:
            out.extend(_descendant_cards(child))
    return out


def _all_cards(el):
    """`el` plus every card nested inside it, in reading order."""
    out = [el]
    for card in _descendant_cards(el):
        out.extend(_all_cards(card))
    return out


CARD_INDENT = "                    "


def _emit_card(parts, markup):
    """Append a card's markup so it starts on its own line at the card indent."""
    if parts:
        # Drop the indentation the previous card left on its last line, so no
        # whitespace-only line survives in the file.
        parts[-1] = re.sub(r"[ \t]+(?=\n|$)", "", parts[-1])
        if not parts[-1].endswith("\n"):
            parts.append("\n")
        parts.append(CARD_INDENT)
    parts.append(markup)


def _card_markup_without_nested(text, el):
    """`el`'s markup with any card nested inside it removed."""
    nested = _descendant_cards(el)
    if not nested:
        return text[el["start"]:el["end"]]
    parts = []
    cursor = el["start"]
    for card in nested:
        # The card was written straight after the previous tag, so drop the
        # indentation it left behind.
        parts.append(text[cursor:card["start"]].rstrip())
        cursor = card["end"]
    parts.append(text[cursor:el["end"]])
    return "".join(parts)


def _card_order_key(text, el):
    """Sort key: the card's existing number, so cards keep their age order."""
    m = NUM_SPAN_RE.search(text[el["start"]:el["end"]])
    return int(m.group(1)[:2]) if m else 999


def repair_nested_cards(content):
    """Rebuild every blooper-grid so each card is a direct child of its grid.

    Older versions of insert_card_into_category() matched the first
    "</div></div>" after a grid opened, so a new card landed inside the
    previous card's body instead of after it. Six cards were left that way
    by the 2026-10-28 dedupe. This lifts every card back out to be a direct
    child of its grid, oldest first, and puts each one on its own line at
    the card indent — so the numbers read in order and the next insert
    lands cleanly.

    Returns (new_content, repaired_card_count).
    """
    repaired = 0
    grid_re = re.compile(r'<div\b[^>]*class="[^"]*blooper-grid[^"]*"[^>]*>')
    while True:
        changed = False
        for grid_match in reversed(list(grid_re.finditer(content))):
            open_end = grid_match.end()
            close = find_matching_div_end(content, open_end)
            if not close:
                continue
            close_start, _ = close
            roots = _parse_div_children(content, open_end, close_start)
            top_cards = [r for r in roots if "blooper-card" in r["cls"]]
            if not top_cards:
                continue

            nested = [c for r in top_cards for c in _descendant_cards(r)]
            parts = []
            cursor = open_end
            for card in top_cards:
                parts.append(content[cursor:card["start"]])
                ordered = sorted(
                    _all_cards(card),
                    key=lambda c: _card_order_key(content, c),
                )
                for nested_card in ordered:
                    _emit_card(
                        parts,
                        _card_markup_without_nested(content, nested_card),
                    )
                cursor = card["end"]
            parts.append(content[cursor:close_start])

            new_inner = "".join(parts)
            if new_inner == content[open_end:close_start]:
                continue
            content = content[:open_end] + new_inner + content[close_start:]
            repaired += len(nested)
            changed = True
            break
        if not changed:
            break
    return content, repaired


def renumber_cards(content):
    """Re-number every card sequentially in document order (01, 02, ... N)."""
    counter = {"n": 0}

    def _replace(_match):
        counter["n"] += 1
        n = counter["n"]
        return f'<span class="blooper-num">#{n:02d}{n:02d}{n:02d}</span>'

    return NUM_SPAN_RE.sub(_replace, content), counter["n"]


def find_card_number_for_title(content, title):
    """The number now shown on the card with this title."""
    idx = content.find(f'<h3 class="blooper-title">{title}</h3>')
    if idx == -1:
        return None
    nums = NUM_SPAN_RE.findall(content[:idx])
    return f"#{nums[-1]}" if nums else None


def get_category_emoji_for_label(content, category_label):
    """Look up the emoji used by a category section by its title."""
    pattern = rf'<span class="category-emoji">([^<]+)</span>\s*<span class="category-title">{re.escape(category_label)}</span>'
    m = re.search(pattern, content)
    if m:
        return m.group(1).strip()
    return None


def increment_category_count(content, category_label):
    """Bump the category-count for the given category by 1. Returns (new_content, success)."""
    pattern = rf'(<span class="category-title">{re.escape(category_label)}</span>\s*<span class="category-count">)(\d+)(</span>)'
    new_content, n = re.subn(pattern, lambda m: m.group(1) + str(int(m.group(2)) + 1) + m.group(3), content)
    return new_content, n > 0


def blooper_card_html(blooper, card_num):
    """Generate HTML for a new blooper card matching the file's style."""
    tag = blooper.get("tag", "Other")
    featured = blooper.get("featured", False)
    featured_cls = " featured" if featured else ""

    # Use the same indentation as the surrounding cards in bloopers.html
    parts = [
        f'                    <div class="blooper-card{featured_cls}">',
        f'                        <div class="blooper-top">',
        f'                            <span class="blooper-num">{card_num}</span>',
        f'                            <span class="blooper-tag">{tag}</span>',
        f'                        </div>',
        f'                        <div class="blooper-body">',
        f'                            <h3 class="blooper-title">{blooper["title"]}</h3>',
        f'                            <p class="blooper-what">What happened:</p>',
        f'                            <p class="blooper-text">',
        f'                                {blooper["what_happened"]}',
        f'                            </p>',
        f'                            <p class="blooper-what">Why it\'s funny:</p>',
        f'                            <div class="blooper-quote">',
        f'                                {blooper["why_funny"]}',
        f'                            </div>',
        f'                            <p class="blooper-source">',
        f'                                {blooper["source"]}',
        f'                            </p>',
        f'                        </div>',
        f'                    </div>',
        '',
    ]
    return '\n'.join(parts)


def insert_card_into_category(content, category_label, card_html):
    """Append a card to the END of a category's blooper-grid.

    The old version matched the first "</div></div>" after the grid opened.
    That is the closing pair of the FIRST card in the grid, so the new card
    was written inside that card's body instead of after it — which is how
    six cards ended up nested in their siblings. This finds the grid that
    follows the category title and inserts just before the grid's own
    closing tag.

    Returns (new_content, success).
    """
    title_match = re.search(
        rf'<span class="category-title">{re.escape(category_label)}</span>', content
    )
    if not title_match:
        return content, False

    grid_match = re.search(
        r'<div\b[^>]*class="[^"]*blooper-grid[^"]*"[^>]*>',
        content[title_match.end():],
    )
    if not grid_match:
        return content, False

    open_end = title_match.end() + grid_match.end()
    close = find_matching_div_end(content, open_end)
    if not close:
        return content, False

    close_start, _ = close
    head = content[:close_start]
    # Drop the indentation that preceded the grid's own closing tag, so the
    # new card lands on its own line at the card indent.
    head = head.rstrip(" \t")
    new_content = head + "\n" + card_html + content[close_start:]
    return new_content, True


def update_page_totals(content, new_total):
    """Update the page title/description and hero text to reflect the new total."""
    # Update the N in "N Real Fails" everywhere it appears
    new_content = re.sub(
        r'(<title>AI Bloopers 🤡 \| )\d+( Real Fails \| Sol AI</title>)',
        rf'\g<1>{new_total}\g<2>',
        content,
    )
    new_content = re.sub(
        r'(\d+) real cases',
        f'{new_total} real cases',
        new_content,
    )
    new_content = re.sub(
        r'(<meta property="og:title" content="AI Bloopers 🤡 \| )\d+( Real Fails \| Sol AI">)',
        rf'\g<1>{new_total}\g<2>',
        new_content,
    )
    new_content = re.sub(
        r'(<meta property="og:description" content=")\d+( real documented AI fails)',
        rf'\g<1>{new_total}\g<2>',
        new_content,
    )
    new_content = re.sub(
        r'(<meta name="twitter:title" content="AI Bloopers 🤡 \| )\d+( Real Fails \| Sol AI">)',
        rf'\g<1>{new_total}\g<2>',
        new_content,
    )
    new_content = re.sub(
        r'(<meta name="twitter:description" content=")\d+( real documented AI fails)',
        rf'\g<1>{new_total}\g<2>',
        new_content,
    )
    new_content = re.sub(
        r'(\d+) real documented failures',
        f'{new_total} real documented failures',
        new_content,
    )
    return new_content


def count_cards(content):
    return content.count('<div class="blooper-card')


def existing_titles(content):
    """Titles already published on the page."""
    return set(re.findall(r'<h3 class="blooper-title">(.*?)</h3>', content, re.DOTALL))


def commit_and_push(message):
    """Commit changes in thesolai.github.io and push to origin."""
    if not SITE_DIR.exists():
        print(f"  ⚠️  Site dir not found: {SITE_DIR}")
        return False

    try:
        from site_git import publish
        sha = publish(message, paths=["bloopers.html"], cwd=SITE_DIR)
        if sha is None:
            print(f"  ℹ️  Nothing to commit (file already committed)")
            return True
        print(f"  ✅ Committed and pushed to origin/main ({sha[:12]})")
        return True
    except Exception as e:
        print(f"  ❌ git publish failed: {e}")
        return False


def main():
    dry_run = "--dry-run" in sys.argv
    no_push = "--no-push" in sys.argv
    count_arg = 1
    for arg in sys.argv:
        if arg.startswith("--count="):
            count_arg = int(arg.split("=")[1])

    now = datetime.datetime.now()
    print(f"\n[{now:%Y-%m-%d %H:%M}] bloopers-refresh running")

    # Read current bloopers.html first — the dedupe below needs to know what
    # is already on the page.
    if not BLOOPERS_FILE.exists():
        print(f"  ❌ bloopers.html not found at {BLOOPERS_FILE}")
        return
    with open(BLOOPERS_FILE, encoding="utf-8") as f:
        content = f.read()
    original = content

    # Lift any card a previous run left nested inside a sibling card, so the
    # grid we insert into is well-formed and the new card lands as a sibling.
    content, repaired = repair_nested_cards(content)
    if repaired:
        print(f"  🔧 Lifted {repaired} card(s) out of a sibling card's body.")

    queue = load_queue()
    # Filter out already-added (committed) entries
    available = [b for b in queue if not b.get("_added")]
    if not available:
        print("  No bloopers in queue. Falling back to CURATED_BLOOPERS list.")
        available = list(CURATED_BLOOPERS)

    # Never publish a blooper that is already on the page, whichever source it
    # came from. The curated fallback has no _added flag of its own, so without
    # this check it re-published its first entry on every run (15 copies of
    # "The Git Commit That Rewrote History" by 2026-10-28).
    published = existing_titles(content)
    fresh = [b for b in available if b.get("title") not in published]
    if len(fresh) != len(available):
        print(f"  ℹ️  Skipping {len(available) - len(fresh)} blooper(s) already on the page.")
    available = fresh
    if not available:
        print("  ⚠️  Every blooper available to this run is already published — nothing to add.")
        print("      Add new ones to _data/bloopers-queue.json to resume this series.")
        if content == original:
            return
        # The repair still has work to publish — save and push it on its own.
        if dry_run:
            print("  [DRY RUN — not saved]")
            return
        with open(BLOOPERS_FILE, "w", encoding="utf-8") as f:
            f.write(content)
        print("  Saved. bloopers.html repaired.")
        if not no_push:
            commit_and_push(f"Bloopers: repair card layout ({now:%Y-%m-%d})")
        return

    to_add = available[:count_arg]
    print(f"  {len(to_add)} blooper(s) to add")

    current_count = count_cards(content)
    print(f"  Current blooper count: {current_count}")

    added = []
    for i, blooper in enumerate(to_add):
        category_label = blooper.get("category_label", "")
        card_html = blooper_card_html(blooper, "#000000")

        new_content, inserted = insert_card_into_category(content, category_label, card_html)
        if not inserted:
            print(f"  ⚠️  Category '{category_label}' not found in bloopers.html — skipping {blooper['title']}")
            blooper["_added"] = True
            blooper["_added_at"] = now.isoformat()
            blooper["_note"] = f"category '{category_label}' not found in bloopers.html — needs manual add"
            continue

        content = new_content
        content, count_ok = increment_category_count(content, category_label)
        if not count_ok:
            print(f"  ⚠️  Could not find category-count for '{category_label}'")

        added.append(blooper)
        blooper["_added"] = True
        blooper["_added_at"] = now.isoformat()

    # Re-number every card sequentially in document order, so the numbers on
    # the page always read 01, 02, ... N from top to bottom.
    content, total_cards = renumber_cards(content)
    if total_cards:
        print(f"  Cards re-numbered: 01 → {total_cards:02d} in document order")

    for blooper in added:
        card_num = find_card_number_for_title(content, blooper["title"])
        blooper["_note"] = f"Added as {card_num} to {blooper.get('category_label', '')} on {now:%Y-%m-%d}"
        print(f"  ✅ Added {card_num}: {blooper['title']} → {blooper.get('category_label', '')}")

    # Update page totals to reflect new count
    new_count = count_cards(content)
    if new_count != current_count:
        content = update_page_totals(content, new_count)
        print(f"  Page totals updated: {current_count} → {new_count}")

    if not dry_run:
        with open(BLOOPERS_FILE, "w", encoding="utf-8") as f:
            f.write(content)
        save_queue(queue)
        print(f"  Saved. bloopers.html updated.")

        if not no_push:
            commit_msg = f"Bloopers: {len(to_add)} new fail(s) added ({now:%Y-%m-%d})"
            commit_and_push(commit_msg)
    else:
        print(f"  [DRY RUN — not saved]")


if __name__ == "__main__":
    main()
