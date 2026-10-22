#!/usr/bin/env python3
"""
Shared git publishing helper for the Sol AI content pipeline.

WHY THIS EXISTS (2026-10-22 incident)
--------------------------------------
The content scripts used to run a bare `git push`, which pushes whatever
branch happens to be checked out at the time.

The weekly roundup script created a `roundup/<date>` branch for its PR flow,
and concurrently-running launchd jobs (midweek digest, daily content) would
then commit onto that side branch and push *it* instead of `main`.

Result: content generation worked perfectly for ~19 commits, but the posts
never reached `main` and never went live. 20 posts were generated and
unpublished. The pipeline logs said "Pushed to GitHub Pages" — a lie.

This module removes the whole class of failure:

  * publishing always targets `origin/main` explicitly — never "current branch"
  * the repo is switched to `main` and synced before anything is committed
  * concurrent publishes are serialised with a lock file, so two launchd
    jobs can never fight over the working tree
  * the push is VERIFIED to have landed, and a failure raises loudly
    instead of printing a cheerful success message

Usage:
    from site_git import publish
    publish("Sol AI daily content: 2026-10-22")
"""

import fcntl
import os
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

SITE_DIR = Path("/Users/amre/Projects/thesolai.github.io")
LOCK_FILE = Path("/tmp/sol-site-publish.lock")


class PublishError(RuntimeError):
    """Raised when content could not be published to origin/main."""


def _git(args, cwd=SITE_DIR, timeout=120):
    return subprocess.run(
        ["git", "-C", str(cwd)] + list(args),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@contextmanager
def publish_lock(timeout=300):
    """Serialise publishes so two launchd jobs never race on the same repo."""
    deadline = time.time() + timeout
    fd = os.open(str(LOCK_FILE), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.time() >= deadline:
                    raise PublishError(
                        "Timed out waiting for the site publish lock — "
                        "another publish is still running."
                    )
                time.sleep(2)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def ensure_main(cwd=SITE_DIR):
    """Put the working tree on `main` and sync it with origin/main."""
    r = _git(["checkout", "main"], cwd=cwd)
    if r.returncode != 0:
        raise PublishError(f"git checkout main failed: {r.stderr.strip()[:300]}")

    r = _git(["pull", "--ff-only", "origin", "main"], cwd=cwd)
    if r.returncode != 0:
        # Local main diverged — rebase our commits on top of origin/main.
        r = _git(["pull", "--rebase", "origin", "main"], cwd=cwd)
        if r.returncode != 0:
            raise PublishError(f"git pull failed: {r.stderr.strip()[:300]}")


def publish(message, paths=None, cwd=SITE_DIR, allow_empty=False):
    """
    Commit content and publish it to origin/main.

    `paths` is a list of files to stage; None stages everything.
    Returns the published commit SHA, or None if there was nothing to do.
    Raises PublishError on any real failure — never returns silently.
    """
    with publish_lock():
        ensure_main(cwd)

        if paths is None:
            add = _git(["add", "-A"], cwd=cwd)
            if add.returncode != 0:
                raise PublishError(f"git add failed: {add.stderr.strip()[:300]}")
        else:
            for p in paths:
                one = _git(["add", str(p)], cwd=cwd)
                if one.returncode != 0:
                    raise PublishError(f"git add failed: {one.stderr.strip()[:300]}")

        commit = _git(["commit", "-m", message], cwd=cwd)
        if commit.returncode != 0:
            combined = (commit.stdout + commit.stderr).lower()
            if "nothing to commit" in combined:
                if allow_empty:
                    return None
                raise PublishError("git commit: nothing to commit")
            raise PublishError(f"git commit failed: {commit.stderr.strip()[:300]}")

        push = _git(["push", "origin", "main"], cwd=cwd, timeout=180)
        if push.returncode != 0:
            # Possible race: another clone (e.g. the 05:00 Daily Workspace Git
            # Backup cron) pushed to origin/main between our pull and our push.
            # Rebase onto the new origin/main and retry.
            for attempt in range(2):
                r = _git(["pull", "--rebase", "origin", "main"], cwd=cwd, timeout=120)
                if r.returncode != 0:
                    raise PublishError(
                        f"git pull --rebase failed: {r.stderr.strip()[:300]}"
                    )
                push = _git(["push", "origin", "main"], cwd=cwd, timeout=180)
                if push.returncode == 0:
                    break
            else:
                raise PublishError(
                    f"git push origin main failed after retries: "
                    f"{push.stderr.strip()[:400]}"
                )

        # Verify the push actually landed on origin/main.
        remote = _git(["rev-parse", "origin/main"], cwd=cwd)
        head = _git(["rev-parse", "HEAD"], cwd=cwd)
        if remote.returncode == 0 and head.returncode == 0:
            if remote.stdout.strip() != head.stdout.strip():
                raise PublishError(
                    "push verification failed: "
                    f"origin/main={remote.stdout.strip()[:12]} "
                    f"HEAD={head.stdout.strip()[:12]}"
                )
            return head.stdout.strip()
        return None


def main():
    """CLI helper: `python3 site_git.py "commit message"`."""
    import sys

    message = sys.argv[1] if len(sys.argv) > 1 else "Site content update"
    try:
        sha = publish(message, allow_empty=True)
    except Exception as e:
        print(f"❌ Publish failed: {e}")
        sys.exit(1)
    print(f"✅ Published to origin/main: {sha}" if sha else "ℹ️  Nothing to publish")


if __name__ == "__main__":
    main()
