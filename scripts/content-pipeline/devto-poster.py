#!/usr/bin/env python3
"""
devto-poster.py — Auto-post new Sol AI blog posts to dev.to

Run after the daily content pipeline (run-daily.py).
Posts any Jekyll blog posts that haven't been cross-posted yet.
Tracks posted URLs in _data/devto-posted.json to avoid duplicates.

Usage:
    python3 devto-poster.py [--dry-run]

Crontab entry (after daily-content runs, ~7:30am UK):
    30 7 * * * cd /Users/amre/Projects/sol-skills-bundle && python3 scripts/content-pipeline/devto-poster.py >> logs/devto-poster.log 2>&1

Note (Aug 2026): The urllib POST was hanging on long-lived Python processes
in this Mac environment (TCP/TLS handshake stalls on reused sockets). The
POST is now run in a fresh subprocess with a 45s hard timeout so any hang
is bounded. This makes the script self-recovering — no external watchdog
needed. See logs/sol-devto-poster.log for prior incident tracebacks.
"""

import sys
import os
import json
import re
import time
import datetime
import subprocess
import urllib.request
import urllib.error
from pathlib import Path

SITE_DIR = Path("/Users/amre/Projects/thesolai.github.io")
BUNDLE_DIR = Path("/Users/amre/Projects/sol-skills-bundle")
LOG_FILE = BUNDLE_DIR / "logs/devto-post.log"
TRACKER_FILE = BUNDLE_DIR / "logs/devto-posted.json"
LOCK_FILE = BUNDLE_DIR / "logs/.devto-poster.lock"
LOCK_STALE_AFTER_SEC = 7200  # 2h — bounds damage if a PID gets recycled
DEVTO_API = "https://dev.to/api/articles"
# Key resolution order:
#   1. $DEVTO_API_KEY env var (set by launchd / cron)
#   2. ~/.openclaw/workspace/secrets/devto-api-key.txt (canonical secret)
#   3. hardcoded legacy fallback (kept only so the script doesn't crash if
#      both above are missing — the key here is dead, so a 401 will surface
#      and the operator gets a clear signal to fix the source).
from devto_key import load_devto_key

DEVTO_API_KEY = load_devto_key()
POST_HARD_TIMEOUT = 45  # seconds — cap per POST via subprocess

# How far back to look for posts that have not been cross-posted yet.
# Measured from the post's publication date (front-matter `date:`), not from
# the file mtime. Seven days gives catch-up headroom for a missed cron run
# (weekend gap, machine asleep) without dragging in the archive.
RECENT_WINDOW_DAYS = 7


def load_tracker():
    if TRACKER_FILE.exists():
        with open(TRACKER_FILE) as f:
            data = json.load(f)
        # Migrate from old list format to new dict format keyed by slug
        if isinstance(data, list):
            tracker = {}
            for entry in data:
                local_path = entry.get("local_path", "")
                slug = Path(local_path).stem if local_path else entry.get("title", str(entry))
                tracker[slug] = {
                    "url": entry.get("url"),
                    "id": entry.get("devto_id"),
                    "posted_at": entry.get("posted_at"),
                    "canonical": entry.get("url"),  # dev.to URL is the canonical
                }
            return tracker
        return data
    return {}


def save_tracker(tracker):
    """Write the tracker atomically so an interrupted write can never leave a
    truncated JSON file behind (the tracker is the dedupe source of truth)."""
    TRACKER_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = TRACKER_FILE.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(tracker, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, TRACKER_FILE)


def _pid_alive(pid):
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def acquire_lock():
    """Guard against concurrent runs.

    This script is scheduled twice: a user crontab entry (`30 7 * * * ... --max=10`)
    and an agent cron task that runs the same command. Both fire at 07:30 and
    both target the same tracker file and the same dev.to account. Without a
    lock, each run loads its own in-memory copy of the tracker and posts the
    same articles twice, then rewrites the tracker wholesale — clobbering the
    other run's entries (lost update) and re-posting on the next run.

    Returns True if the lock was acquired (caller should proceed).
    Returns False if another live run holds it (caller should exit quietly).
    """
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            with os.fdopen(fd, "w") as f:
                json.dump(
                    {
                        "pid": os.getpid(),
                        "acquired": datetime.datetime.now().isoformat(),
                    },
                    f,
                )
            return True
        except FileExistsError:
            pid = 0
            acquired = None
            try:
                info = json.loads(LOCK_FILE.read_text())
                pid = int(info.get("pid") or 0)
                acquired = info.get("acquired")
            except Exception:
                pass

            age = None
            if acquired:
                try:
                    acquired_dt = datetime.datetime.fromisoformat(acquired)
                    age = (datetime.datetime.now() - acquired_dt).total_seconds()
                except Exception:
                    age = None

            if _pid_alive(pid) and (age is None or age < LOCK_STALE_AFTER_SEC):
                print(
                    f"  🔒 Another dev.to poster is already running (PID {pid}) — "
                    "exiting without posting to avoid duplicates."
                )
                return False

            print(f"  🧹 Clearing stale lock (PID {pid} not running).")
            try:
                LOCK_FILE.unlink()
            except FileNotFoundError:
                pass
    print("  🔒 Could not acquire lock — exiting without posting.")
    return False


def release_lock():
    try:
        LOCK_FILE.unlink()
    except FileNotFoundError:
        pass


def load_post(path):
    with open(path, encoding="utf-8") as f:
        content = f.read()

    if content.startswith("---"):
        parts = content.split("---", 2)
        front_matter = parts[1]
        body = parts[2].strip() if len(parts) > 2 else ""
    else:
        front_matter = ""
        body = content.strip()

    # Parse YAML-like front matter
    meta = {}
    current_key = None
    in_list = False
    current_list = []

    for line in front_matter.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            current_list.append(stripped[2:].strip().strip("'\""))
            in_list = True
            continue
        if ": " in line or ":" in line:
            if in_list and current_key:
                meta[current_key] = list(current_list)
                current_list = []
            in_list = False
            if ": " in line:
                key, _, val = line.partition(": ")
            else:
                key, _, val = line.partition(":")
            key = key.strip().strip('"').strip("'")
            val = val.strip().strip('"').strip("'")
            if val:
                meta[key] = val
                current_key = None
            else:
                current_key = key

    if in_list and current_key:
        meta[current_key] = list(current_list)

    return meta, body


def body_to_devto(body, canonical_base="https://thesolai.github.io"):
    """Clean Jekyll body for dev.to posting."""
    lines = body.splitlines()
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("{%") or stripped.startswith("{{"):
            continue
        if stripped.startswith("{:"):
            continue
        cleaned.append(line)
    text = "\n".join(cleaned).strip()

    # Convert relative /blog/ links to absolute Sol site links so readers
    # can click through to the original post and discover more content
    text = re.sub(r'\[([^\]]+)\]\(/blog/([^)]+)\)',
                  rf'[\1]({canonical_base}/blog/\2)', text)

    # Strip bare /blog/ link fragments that have no label
    text = re.sub(r'\[([^\]]*)\]\(/blog/\)', '', text)

    return text


def devto_footer(canonical_url):
    """Append a footer to drive readers back to the Sol site."""
    return (
        "\n\n---\n"
        "🤖 *This post was automatically syndicated from "
        f"[**The Sol AI Blog**]({canonical_url}) — "
        "daily AI analysis from a UK/EU/US perspective.*\n"
        "*[Follow along for more →](https://thesolai.github.io)*"
    )


def jekyll_url(post_path):
    """Derive the Jekyll permalink from a post filename."""
    name = post_path.stem  # e.g. "2026-06-25-my-post-title"
    # Format: /blog/YYYY/MM/DD/title/
    date_part = name[:10]  # "2026-06-25"
    title_part = name[11:]  # "my-post-title"
    return f"https://thesolai.github.io/blog/{date_part.replace('-', '/')}/{title_part}/"


def devto_tags(tags_raw):
    """Convert Jekyll tags to dev.to format."""
    if isinstance(tags_raw, str):
        tags = [t.strip() for t in tags_raw.split(",")]
    elif isinstance(tags_raw, list):
        tags = list(tags_raw)
    else:
        tags = []
    clean = []
    for t in tags:
        t = re.sub(r"[^a-z0-9]", "", t.lower())
        if t and len(t) < 25 and t not in clean:
            clean.append(t)
    return clean[:4]


def post_to_devto(title, body, tags, description, api_key, canonical_url=""):
    """POST to dev.to via a fresh subprocess with a hard timeout.

    This avoids the urllib TCP/TLS handshake hang seen on long-lived
    Python processes on this Mac (no ESTABLISHED TCP, no log progress,
    urllib's `timeout=30` doesn't fire because the connect never
    completes). A fresh interpreter per POST sidesteps that.
    """
    payload = {
        "article": {
            "title": title,
            "body_markdown": body,
            "published": True,
            "tags": tags,
            "description": description[:500] if description else title[:200],
        }
    }
    if canonical_url:
        payload["article"]["canonical_url"] = canonical_url

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
        '        result = json.loads(resp.read().decode("utf-8"))\n'
        '        print("OK|"+json.dumps({"status":resp.status,"id":result.get("id"),"url":result.get("url")}))\n'
        'except urllib.error.HTTPError as e:\n'
        '    body = e.read().decode("utf-8")\n'
        '    try:\n'
        '        err = json.loads(body)\n'
        '        err_msg = err.get("error","").lower()\n'
        '        if e.code == 422 and "canonical" in err_msg:\n'
        '            # Article already exists under this canonical. Look up the\n'
        '            # existing URL via the user-articles listing so the caller\n'
        '            # can record it. No retry — retrying without canonical would\n'
        '            # create a duplicate article.\n'
        '            canonical = payload.get("article", {}).get("canonical_url", "")\n'
        '            existing_url = ""\n'
        '            existing_id = None\n'
        '            try:\n'
        '                list_req = urllib.request.Request(\n'
        '                    "https://dev.to/api/articles/me/published?per_page=100",\n'
        '                    headers={"api-key": api_key, "User-Agent": "SolAI/1.0"})\n'
        '                with urllib.request.urlopen(list_req, timeout=15) as lr:\n'
        '                    for art in json.loads(lr.read().decode("utf-8")):\n'
        '                        if art.get("canonical_url") == canonical:\n'
        '                            existing_url = art.get("url", "")\n'
        '                            existing_id = art.get("id")\n'
        '                            break\n'
        '            except Exception: pass\n'
        '            print("EXISTS|"+json.dumps({"code":e.code,"canonical":canonical,"url":existing_url,"id":existing_id}))\n'
        '            sys.exit(0)\n'
        '        if e.code == 429:\n'
        '            print("RATE_LIMITED|"+json.dumps({"code":e.code,"body":body[:500]}))\n'
        '            sys.exit(0)\n'
        '    except Exception: pass\n'
        '    print("HTTP_ERROR|"+json.dumps({"code":e.code,"body":body[:500]}))\n'
        'except Exception as e:\n'
        '    print("ERROR|"+json.dumps({"type":type(e).__name__,"msg":str(e)}))\n'
    )
    payload_path = f"/tmp/_devto_payload_{int(time.time()*1000)}_{os.getpid()}.json"
    Path(payload_path).write_text(json.dumps(payload))
    try:
        result = subprocess.run(
            ["python3", "-u", "-c", helper, payload_path, api_key],
            capture_output=True, text=True, timeout=POST_HARD_TIMEOUT
        )
        out_line = (result.stdout or "").strip().splitlines()[-1] if result.stdout else ""
        if out_line.startswith("OK|"):
            data = json.loads(out_line[3:])
            data["__status"] = "ok"
            return data
        if out_line.startswith("EXISTS|"):
            data = json.loads(out_line[len("EXISTS|"):])
            data["__status"] = "exists"
            return data
        if out_line.startswith("RATE_LIMITED|"):
            data = json.loads(out_line[len("RATE_LIMITED|"):])
            data["__status"] = "rate_limited"
            print(f"  ❌ dev.to rate limited (HTTP {data['code']}): {data.get('body','')[:200]}")
            return data
        if out_line.startswith("HTTP_ERROR|"):
            err = json.loads(out_line[len("HTTP_ERROR|"):])
            print(f"  ❌ dev.to HTTP {err['code']}: {err['body'][:200]}")
            return {"__status": "http_error", "code": err["code"], "body": err["body"]}
        if out_line.startswith("ERROR|"):
            err = json.loads(out_line[len("ERROR|"):])
            print(f"  ❌ dev.to {err['type']}: {err['msg']}")
            return {"__status": "error", "type": err["type"], "msg": err["msg"]}
        print(f"  ❌ dev.to unexpected response: stdout={result.stdout[:200]!r} stderr={result.stderr[:200]!r}")
        return {"__status": "unknown"}
    except subprocess.TimeoutExpired:
        print(f"  ❌ dev.to POST hung for >{POST_HARD_TIMEOUT}s — likely urllib TCP/TLS stall, continuing to next post")
        return {"__status": "timeout"}
    finally:
        try:
            os.unlink(payload_path)
        except OSError:
            pass


def post_publication_date(post_path):
    """Return a post's publication date, or None if it cannot be determined.

    The front-matter `date:` is authoritative. Falls back to the filename date
    prefix, which matches front matter for every post in _posts.
    """
    try:
        with open(post_path, encoding="utf-8") as f:
            head = f.read(2048)
        m = re.search(r"^date:\s*[\"']?(\d{4}-\d{2}-\d{2})", head, re.M)
        if m:
            return datetime.datetime.strptime(m.group(1), "%Y-%m-%d").date()
    except OSError:
        pass
    m = re.match(r"(\d{4}-\d{2}-\d{2})", post_path.stem)
    return datetime.datetime.strptime(m.group(1), "%Y-%m-%d").date() if m else None


def get_recent_posts(days=RECENT_WINDOW_DAYS):
    """Get Jekyll posts whose *publication date* falls within the last N days.

    Filtering on file mtime is unsafe. Any bulk rewrite of post front matter
    resets every mtime in one shot — on 2026-10-25 the og:image SEO fix
    rewrote 149 posts within the same second — which floods this queue with
    months-old archive posts and starves the genuinely new ones. The
    publication date survives front-matter rewrites untouched, so it is what
    decides whether a post is new.
    """
    cutoff = datetime.datetime.now().date() - datetime.timedelta(days=days)
    posts_dir = SITE_DIR / "_posts"
    if not posts_dir.exists():
        return []

    recent = []
    undated = 0
    for f in posts_dir.iterdir():
        if not f.suffix == ".md":
            continue
        pub = post_publication_date(f)
        if pub is None:
            # Fail open — a post we cannot date is still content worth posting.
            undated += 1
            recent.append(f)
            continue
        if pub >= cutoff:
            recent.append(f)

    if undated:
        print(f"  ⚠️  {undated} post(s) have no readable date — including them")

    # Newest publication date first; mtime only breaks ties within a day.
    return sorted(
        recent,
        key=lambda p: (post_publication_date(p) or datetime.date.min,
                       p.stat().st_mtime),
        reverse=True,
    )


def _run():
    dry_run = "--dry-run" in sys.argv
    max_posts = 0  # 0 = unlimited
    for arg in sys.argv[1:]:
        if arg.startswith("--max="):
            try:
                max_posts = int(arg.split("=", 1)[1])
            except ValueError:
                pass
    tracker = load_tracker()

    print(f"\n[{datetime.datetime.now():%Y-%m-%d %H:%M}] dev.to auto-poster running")
    print(f"  Tracker has {len(tracker)} posts already posted")

    posts = get_recent_posts(days=RECENT_WINDOW_DAYS)
    if not posts:
        print("  No recent posts found. Done.")
        return

    print(f"  Found {len(posts)} recently-modified posts")
    if max_posts:
        print(f"  Cap: posting at most {max_posts} new post(s) this run")

    posted_count = 0
    for post_path in posts:
        slug = post_path.stem

        # Skip drafts
        if slug.startswith("draft-") or slug.startswith("IDEA-"):
            continue

        # Check if already posted
        if slug in tracker:
            print(f"  ⏭ Already posted: {slug}")
            continue

        if not post_path.exists():
            print(f"  ⏭ Skipping (file not found): {slug}")
            continue

        meta, body = load_post(post_path)
        title = meta.get("title", "")
        description = meta.get("description", "")
        tags_raw = meta.get("tags", [])
        tags = devto_tags(tags_raw)
        canonical = jekyll_url(post_path)
        devto_body = body_to_devto(body, canonical_base="https://thesolai.github.io")
        devto_body += devto_footer(canonical)

        if not title or len(devto_body) < 200:
            print(f"  ⏭ Skipping (too short or no title): {slug}")
            continue

        print(f"\n  📝 Posting: {title}")
        print(f"     Tags: {tags}")
        print(f"     URL:  {canonical}")

        if dry_run:
            print("     [DRY RUN — not posting]")
            continue

        # Respect rate limit: 1 post/minute
        print("  Posting (rate limit: 1/min)...")
        time.sleep(62)

        result = post_to_devto(
            title=title,
            body=devto_body,
            tags=tags,
            description=description,
            api_key=DEVTO_API_KEY,
            canonical_url=canonical,
        )

        # dev.to rejects an article whose title was used in the last five
        # minutes. Recurring series ("AI Bloopers: ...", "Trending: ...")
        # reuse the same title every day, so a backlog of them collides with
        # itself and each one gets dropped — silently, run after run. Retry
        # once with the publication date appended so every occurrence keeps a
        # distinct title and actually reaches dev.to.
        if (result and result.get("__status") == "http_error"
                and result.get("code") == 422
                and "title" in (result.get("body") or "").lower()):
            pub_date = post_publication_date(post_path)
            if pub_date:
                dated_title = f"{title} ({pub_date:%d %b %Y})"
                print(f"  ⚠️  Title used in the last 5 min — retrying as: {dated_title}")
                time.sleep(62)
                result = post_to_devto(
                    title=dated_title,
                    body=devto_body,
                    tags=tags,
                    description=description,
                    api_key=DEVTO_API_KEY,
                    canonical_url=canonical,
                )

        if result and result.get("__status") == "ok" and result.get("url"):
            tracker[slug] = {
                "url": result["url"],
                "id": result.get("id"),
                "posted_at": datetime.datetime.now().isoformat(),
                "canonical": canonical,
            }
            save_tracker(tracker)
            print(f"  ✅ Posted: {result['url']}")
            posted_count += 1
            if max_posts and posted_count >= max_posts:
                print(f"  🛑 Hit --max={max_posts} cap — stopping")
                break
        elif result and result.get("__status") == "exists":
            # Canonical URL is already on dev.to (posted earlier by us or by a probe).
            # Mark tracker so we don't retry next run, then continue to the next post.
            existing_url = result.get("url") or f"(canonical:{canonical})"
            tracker[slug] = {
                "url": existing_url,
                "id": result.get("id"),
                "posted_at": datetime.datetime.now().isoformat(),
                "canonical": canonical,
                "note": "already-on-devto",
            }
            save_tracker(tracker)
            print(f"  ⏭  Already on dev.to (canonical conflict): {existing_url}")
            posted_count += 1
            if max_posts and posted_count >= max_posts:
                print(f"  🛑 Hit --max={max_posts} cap — stopping")
                break
        elif result and result.get("__status") == "rate_limited":
            # 429 — must wait a full minute. Stop the loop; the next cron run
            # will pick up where we left off (the unposted post stays untracked).
            print("  ⚠️  Rate limited — stopping to wait for the next cron run")
            break
        else:
            # Any other failure (HTTP error, urllib error, timeout, unexpected
            # response): log and continue. We do NOT break — these are usually
            # transient or one-off and shouldn't block the rest of the queue.
            print("  ⏭  Skipping (will retry next run)")
            continue

    print(f"\n[{datetime.datetime.now():%Y-%m-%d %H:%M}] Done. Posted {posted_count} new posts.")


if __name__ == "__main__":
    if acquire_lock():
        try:
            _run()
        finally:
            release_lock()
