#!/usr/bin/env python3
"""Remove duplicate blooper cards from bloopers.html and repair the counts.

Why this exists
---------------
bloopers-refresh.py falls back to the CURATED_BLOOPERS list when its queue is
empty. The curated entries carry no "_added" flag of their own, so the
fallback had no way of knowing an entry was already on the page and appended
it again on every run. By 2026-10-28 that had produced 15 copies of
"The Git Commit That Rewrote History" on /bloopers/ — a page that advertised
"45 Real Fails" but held only 31 distinct ones.

The copies were written by an older, buggy insert routine, so they are not
closed where they open: each card's closing </div> tags were deferred to the
end of the run, which left the markup nested and the category count wrong.
This script rebuilds each blooper-grid from its cards, keeping the first
occurrence of every title and giving every card exactly one closing pair.
It then rewrites the category and page totals to match what is really there.

The guard that stops the duplicates coming back lives in bloopers-refresh.py
(existing_titles()); this script only cleans up what is already published.
"""

import argparse
import importlib.util
import re
import sys
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent

# The grid and category closing tags, at the indentation the page uses.
# Card closing tags are deeper (24/20 spaces) and never match this.
GRID_CLOSE = "\n                </div>\n            </div>"
CARD_CLOSE = "\n                        </div>\n                    </div>\n\n"


def load_refresh_module():
    """Import bloopers-refresh.py so we reuse its own helpers."""
    spec = importlib.util.spec_from_file_location(
        "bloopers_refresh", PIPELINE_DIR / "bloopers-refresh.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def split_cards(grid):
    """Split a grid into its cards. Returns [(core, title), ...].

    A card's core runs from its opening tag to the end of its last <p>.
    Whatever follows that — its own closing tags, or the closing tags of the
    cards before it — is dropped and regenerated, so the result is uniform.
    """
    starts = [m.start() for m in re.finditer(r'<div class="blooper-card', grid)]
    if not starts:
        return []

    cards = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(grid)
        region = grid[start:end]

        title_match = re.search(r'<h3 class="blooper-title">(.*?)</h3>', region, re.DOTALL)
        title = title_match.group(1).strip() if title_match else ""

        # The card's own content ends at its last </p>.
        last_p = region.rfind("</p>")
        if last_p == -1:
            core = region.rstrip()
        else:
            core = region[:last_p + len("</p>")].rstrip()
        cards.append((core, title))
    return cards


def rebuild_grid(grid, report):
    """Keep the first card for each title, and re-close every card cleanly."""
    cards = split_cards(grid)
    seen = set()
    kept = []
    for core, title in cards:
        if title in seen:
            report.append(title)
            continue
        seen.add(title)
        kept.append(core)
    return "".join(core + CARD_CLOSE for core in kept)


def set_category_counts(content):
    """Rewrite each category-count so it matches the cards actually present."""
    out = []
    for block in re.split(r'(?=<div class="category">)', content):
        title_match = re.search(r'<span class="category-title">(.*?)</span>', block, re.DOTALL)
        if not title_match:
            out.append(block)
            continue
        label = title_match.group(1)
        actual = len(re.findall(r'<div class="blooper-card', block))
        block, n = re.subn(
            rf'(<span class="category-title">{re.escape(label)}</span>\s*<span class="category-count">)(\d+)(</span>)',
            lambda m: m.group(1) + str(actual) + m.group(3),
            block,
            count=1,
        )
        if n == 0:
            print(f"  ⚠️  Could not find the count for category '{label}'")
        else:
            print(f"  {label}: {actual} card(s)")
        out.append(block)
    return "".join(out)


def main():
    parser = argparse.ArgumentParser(description="Dedupe bloopers.html")
    parser.add_argument("--site", default="/Users/amre/Projects/thesolai.github.io")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-push", action="store_true")
    args = parser.parse_args()

    refresh = load_refresh_module()
    target = Path(args.site) / "bloopers.html"
    if not target.exists():
        print(f"❌ {target} not found")
        return 1

    content = target.read_text(encoding="utf-8")
    original = content

    # Rebuild every blooper-grid, innermost last so earlier offsets stay valid.
    grids = list(re.finditer(r'<div class="blooper-grid">', content))
    if not grids:
        print("❌ No blooper-grid found — wrong file?")
        return 1

    removed = []
    for grid_match in reversed(grids):
        start = grid_match.end()
        close = content.find(GRID_CLOSE, start)
        if close == -1:
            print(f"❌ Could not find the end of a blooper-grid at offset {start}")
            return 1
        grid = content[start:close]

        report = []
        new_grid = rebuild_grid(grid, report)
        if report:
            removed.extend(report)
            content = content[:start] + new_grid + content[close:]

    if not removed:
        print("✅ No duplicate cards found. Nothing to do.")
        return 0

    print(f"Removed {len(removed)} duplicate card(s):")
    for title in removed:
        print(f"  ❌ {title}")

    content = set_category_counts(content)
    total = len(re.findall(r'<div class="blooper-card', content))
    content = refresh.update_page_totals(content, total)

    titles = re.findall(r'<h3 class="blooper-title">(.*?)</h3>', content, re.DOTALL)
    dup_left = len(titles) - len(set(titles))
    print(f"\nCards remaining: {total}")
    print(f"Distinct titles: {len(set(titles))}")
    print(f"Duplicate titles left: {dup_left}")
    if dup_left:
        print("❌ Duplicates remain — aborting without writing.")
        return 1

    # Every grid must balance, and the page as a whole must be no more or less
    # balanced than it was before. (The page's outer wrapper divs are closed
    # after the last category, so the whole-file count is only useful as a
    # before/after comparison.)
    for grid_match in re.finditer(r'<div class="blooper-grid">', content):
        start = grid_match.end()
        close = content.find(GRID_CLOSE, start)
        if close == -1:
            print("❌ Could not find the end of a blooper-grid")
            return 1
        grid = content[start:close]
        opens = len(re.findall(r'<div\b', grid))
        closes = len(re.findall(r'</div>', grid))
        if opens != closes:
            print(f"❌ Unbalanced grid at offset {start}: {opens} open / {closes} close")
            return 1
    print("✅ All grids balanced")

    before_balance = len(re.findall(r'<div\b', original)) - len(re.findall(r'</div>', original))
    after_balance = len(re.findall(r'<div\b', content)) - len(re.findall(r'</div>', content))
    if before_balance != after_balance:
        print(f"❌ Page div balance changed: {before_balance} → {after_balance}")
        return 1
    print(f"✅ Page div balance unchanged ({after_balance})")

    # The page's headline count must agree with the cards on the page.
    for pattern, expected in (
        (r'<title>AI Bloopers 🤡 \| (\d+) Real Fails', total),
        (r'<meta property="og:title" content="AI Bloopers 🤡 \| (\d+) Real Fails', total),
        (r'<meta name="twitter:title" content="AI Bloopers 🤡 \| (\d+) Real Fails', total),
    ):
        found = re.search(pattern, content)
        if not found or int(found.group(1)) != expected:
            print(f"❌ Headline count out of step: {pattern} -> {found and found.group(1)} (want {expected})")
            return 1
    print(f"✅ Headline counts agree ({total})")

    if args.dry_run:
        print("\n[DRY RUN — not saved]")
        return 0

    target.write_text(content, encoding="utf-8")
    print(f"\n✅ Saved {target} ({len(original)} → {len(content)} bytes)")

    if not args.no_push:
        try:
            from site_git import publish

            sha = publish(
                "Bloopers: remove 14 duplicate cards and repair counts",
                paths=["bloopers.html"],
                cwd=Path(args.site),
            )
            if sha is None:
                print("ℹ️  Nothing to commit (file already committed)")
            else:
                print(f"✅ Committed and pushed to origin/main ({sha[:12]})")
        except Exception as exc:  # noqa: BLE001
            print(f"❌ git publish failed: {exc}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
