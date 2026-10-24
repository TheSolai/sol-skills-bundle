#!/usr/bin/env python3
"""
Update existing dev.to cross-posts to match the repaired Jekyll source.

WHY
---
The daily pipeline cross-posts to dev.to with `canonical_url` pointing at
the Jekyll post. When a post is regenerated (fixed truncation, new slug),
the dev.to article still carries the old truncated body and points at a
stale canonical URL.

dev.to's API updates an article with `PUT /api/articles/{id}`. This script
re-publishes the corrected body for a given local post.

Usage:
  python3 devto_update.py <local-post-path> [<local-post-path> ...]
  python3 devto_update.py --slug <slug> [...]
"""

import json
import re
import subprocess
import sys
import time
import importlib.util
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from devto_key import load_devto_key  # noqa: E402

# devto-poster.py has a hyphen, so it cannot be imported by name.
_spec = importlib.util.spec_from_file_location(
    "devto_poster", Path(__file__).parent / "devto-poster.py")
_devto_poster = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_devto_poster)

TRACKER_FILE = _devto_poster.TRACKER_FILE
load_tracker = _devto_poster.load_tracker
save_tracker = _devto_poster.save_tracker
load_post = _devto_poster.load_post
body_to_devto = _devto_poster.body_to_devto
devto_footer = _devto_poster.devto_footer
jekyll_url = _devto_poster.jekyll_url
devto_tags = _devto_poster.devto_tags

DEVTO_API = "https://dev.to/api/articles"
SITE_DIR = Path("/Users/amre/Projects/thesolai.github.io")


def devto_update(article_id, title, body, tags, description, api_key, canonical_url="",
                 max_retries=4):
    """PUT an updated article. Returns (ok, message).

    dev.to rate-limits aggressively. A 429 can also surface as a confusing
    404 from the API, so both are retried with a backoff rather than
    treated as a hard failure.
    """
    payload = {"article": {
        "title": title,
        "body_markdown": body,
        "tags": tags,
        "description": description[:500] if description else title[:200],
    }}
    if canonical_url:
        payload["article"]["canonical_url"] = canonical_url

    helper = (
        'import sys, json, urllib.request, urllib.error\n'
        'payload_path, api_key, article_id = sys.argv[1], sys.argv[2], sys.argv[3]\n'
        'with open(payload_path) as f: payload = json.load(f)\n'
        'data = json.dumps(payload).encode("utf-8")\n'
        'req = urllib.request.Request(\n'
        # NOTE: this must be an f-string inside the helper source, otherwise
        # the literal text "{article_id}" is sent as the URL and the API
        # answers 404 for an article that exists.
        '    f"https://dev.to/api/articles/{article_id}",\n'
        '    data=data,\n'
        '    headers={"Content-Type":"application/json","api-key":api_key,"User-Agent":"SolAI/1.0"},\n'
        '    method="PUT")\n'
        'try:\n'
        '    with urllib.request.urlopen(req, timeout=40) as resp:\n'
        '        result = json.loads(resp.read().decode("utf-8"))\n'
        '        print("OK|"+json.dumps({"status":resp.status,"id":result.get("id"),"url":result.get("url")}))\n'
        'except urllib.error.HTTPError as e:\n'
        '    print("HTTP_ERROR|"+json.dumps({"code":e.code,"body":e.read().decode("utf-8")[:400]}))\n'
        'except Exception as e:\n'
        '    print("ERROR|"+json.dumps({"type":type(e).__name__,"msg":str(e)}))\n'
    )
    payload_path = f"/tmp/_devto_update_{int(time.time()*1000)}_{article_id}.json"
    Path(payload_path).write_text(json.dumps(payload))
    try:
        last = ""
        for attempt in range(1, max_retries + 1):
            result = subprocess.run(
                ["python3", "-u", "-c", helper, payload_path, api_key, str(article_id)],
                capture_output=True, text=True, timeout=60,
            )
            out = (result.stdout or "").strip().splitlines()[-1] if result.stdout else ""
            last = out
            if out.startswith("OK|"):
                return True, out[3:]
            # 429 = rate limited; dev.to also answers 404 for some
            # rate-limited requests, so treat both as retryable.
            if re.search(r'"code":\s*(429|404)', out):
                if attempt < max_retries:
                    wait = 20 * attempt
                    print(f"     ⏳ rate limited (attempt {attempt}), waiting {wait}s…")
                    time.sleep(wait)
                    continue
            return False, out
        return False, last
    except subprocess.TimeoutExpired:
        return False, "timeout"
    finally:
        try:
            Path(payload_path).unlink()
        except OSError:
            pass


def _old_canonicals(post_path):
    """Every URL this post has ever had, including pre-rename ones.

    When a post is regenerated its slug changes, but the Jekyll post keeps a
    `redirect_from` entry pointing at the old URL. The dev.to tracker is
    keyed on the old canonical, so matching on that is what identifies the
    right dev.to article. Matching on the date alone is NOT safe — several
    posts share a date and they would be crossed.
    """
    urls = []
    try:
        raw = post_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        raw = ""
    if raw.startswith("---"):
        end = raw.find("\n---", 3)
        if end != -1:
            fm = raw[:end]
            for line in fm.split("\n"):
                if line.startswith("redirect_from:"):
                    val = line.split(":", 1)[1].strip().strip('"').strip("'")
                    if val:
                        urls.append(val)
    return urls


def _tracker_entry_for(tracker, post_path):
    """Find the tracker entry for a post, even if its slug changed.

    Priority:
      1. exact slug match
      2. match on a `redirect_from` (old canonical) the tracker still knows
      3. match on the current canonical
    """
    slug = post_path.stem
    if slug in tracker:
        return slug, tracker[slug]

    candidates = set(_old_canonicals(post_path))
    candidates.add(jekyll_url(post_path))

    # Exact canonical match first — unambiguous.
    for want in candidates:
        for k, v in tracker.items():
            if v.get("canonical") == want:
                return k, v

    # Fall back to the date-directory, but only when it is unambiguous.
    def dir_key(u):
        if not u:
            return ""
        return "/".join(u.rstrip("/").split("/")[:-1])

    target_dirs = {dir_key(c) for c in candidates if c}
    hits = [(k, v) for k, v in tracker.items()
            if dir_key(v.get("canonical", "")) in target_dirs]
    if len(hits) == 1:
        return hits[0]
    return None, None


def update_post(post_path, tracker, api_key, forced_id=None):
    meta, body = load_post(post_path)
    title = meta.get("title", "")
    description = meta.get("description", "")
    tags = devto_tags(meta.get("tags", []))
    canonical = jekyll_url(post_path)
    devto_body = body_to_devto(body, canonical_base="https://thesolai.github.io")
    devto_body += devto_footer(canonical)

    key, entry = _tracker_entry_for(tracker, post_path)
    if forced_id:
        article_id = forced_id
        if not entry or entry.get("id") != forced_id:
            entry = entry or {}
            entry["id"] = forced_id
    else:
        if not entry or not entry.get("id"):
            return "no-tracker-entry", None
        article_id = entry["id"]

    ok, info = devto_update(article_id, title, devto_body, tags, description,
                            api_key, canonical_url=canonical)
    if not ok:
        return "failed", info

    data = json.loads(info)
    entry["url"] = data.get("url") or entry.get("url")
    entry["canonical"] = canonical
    entry["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    if key and key != post_path.stem:
        tracker.pop(key, None)
    tracker[post_path.stem] = entry
    return "updated", data.get("url")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    forced = None
    for a in sys.argv[1:]:
        if a.startswith("--id="):
            forced = a.split("=", 1)[1]
    if not args:
        print(__doc__)
        sys.exit(1)

    api_key = load_devto_key()
    if not api_key:
        print("❌ No dev.to API key found")
        sys.exit(1)

    tracker = load_tracker()
    for a in args:
        p = Path(a)
        if not p.is_absolute():
            p = SITE_DIR / "_posts" / a
        if not p.exists():
            p = SITE_DIR / "_posts" / (a if a.endswith(".md") else a + ".md")
        if not p.exists():
            print(f"⏭  Not found: {a}")
            continue
        status, detail = update_post(p, tracker, api_key, forced_id=forced)
        print(f"  {status:18s} {p.stem[:58]:58s} {detail or ''}")
        if status == "updated":
            time.sleep(5)  # be gentle with the API

    save_tracker(tracker)
    print(f"\nTracker updated: {TRACKER_FILE}")


if __name__ == "__main__":
    main()
