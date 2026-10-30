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
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

SITE_DIR = Path("/Users/amre/Projects/thesolai.github.io")
LOCK_FILE = Path("/tmp/sol-site-publish.lock")
OG_GENERATOR = Path("_scripts/generate_og_images.py")
OG_DIR = "og"
OG_MANIFEST = "og/.manifest.json"

# How long a push may take.
#
# The repo has a pre-push credential gate (publish-gate.sh) that scans every
# tracked file and every blob in the pushed range before git is allowed to
# talk to the network. On this repo that is ~5,600 files, each read with its
# own `git cat-file` subprocess — several minutes under load.
#
# The old 180s budget expired *inside the hook*, so the push was killed
# mid-scan and the commit never reached origin/main. The script then printed
# a cheerful "Git push failed" line and moved on, which is how a whole day of
# content silently failed to publish.
#
# 900s gives the gate room to finish plus the actual network push.
PUSH_TIMEOUT = 900


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


def _worktree_dirty(cwd):
    """True when the working tree has any staged or unstaged change."""
    r = _git(["status", "--porcelain"], cwd=cwd)
    return bool(r.stdout.strip())


def ensure_main(cwd=SITE_DIR):
    """Put the working tree on `main` and sync it with origin/main.

    Only fast-forwards when the worktree is clean and local main is a strict
    ancestor of origin/main. Two failure modes are deliberately avoided here:

      * Pulling over a dirty worktree. `git pull --rebase --autostash`
        stashes the pending content, rebases, and re-applies the stash. When
        the stash application conflicts, git writes CONFLICT MARKERS into the
        file and still exits 0. The next `git add -A` then commits those
        markers into the repository — which is exactly how the OG manifest
        got corrupted with `<<<<<<< Updated upstream` markers and shipped to
        origin/main.

      * Rebasing here. A rebase of a diverged branch can conflict, and this
        function has no way to resolve one. The publish loop owns the
        reconciliation: it rebases a clean tree and, on conflict, resolves
        the shared derived artefact in place.
    """
    r = _git(["checkout", "main"], cwd=cwd)
    if r.returncode != 0:
        raise PublishError(f"git checkout main failed: {r.stderr.strip()[:300]}")

    if _worktree_dirty(cwd):
        # Pending content — do not touch the tree.
        return

    _git(["fetch", "origin", "main"], cwd=cwd)

    # Fast-forward only when HEAD is a strict ancestor of origin/main.
    behind = _git(["merge-base", "--is-ancestor", "HEAD", "origin/main"], cwd=cwd)
    if behind.returncode != 0:
        # Either we are ahead, or the branch has diverged. Both are handled
        # by the publish loop's rebase step.
        return

    ff = _git(["merge", "--ff-only", "origin/main"], cwd=cwd)
    if ff.returncode != 0:
        raise PublishError(f"git merge --ff-only failed: {ff.stderr.strip()[:300]}")


def generate_og_images(cwd=SITE_DIR, timeout=900):
    """Regenerate Open Graph images before content is committed.

    Every post's layout points og:image at og/<source-filename>.png. If a post
    ships without that file, its social card 404s — which is exactly what
    happened to 100% of the posts before the layout and the generator were
    made to agree on the filename.

    The generator is incremental (a manifest of source hashes lives in
    og/.manifest.json), so a normal publish only rasterises the new or edited
    posts. It raises if the rendering tools are missing rather than silently
    publishing a post with a broken image.
    """
    script = cwd / OG_GENERATOR
    if not script.exists():
        return None

    r = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if r.returncode != 0:
        detail = (r.stderr or r.stdout or "").strip()
        raise PublishError(f"OG image generation failed: {detail[:400]}")
    return (r.stdout or "").strip()


def _og_png_for(cwd, post_path):
    """The OG image that must ship alongside a post, or None.

    The layout derives og:image from the post's source filename stem, so a
    post committed without its PNG ships with a 404 social card.

    Only files under `_posts/` have that contract — a Markdown file elsewhere
    in the repo is not a post and must not be blocked by this check.
    """
    p = Path(post_path)
    if p.suffix.lower() != ".md":
        return None
    try:
        rel = p.relative_to(Path(cwd))
    except ValueError:
        return None
    if rel.parts[0] != "_posts":
        return None
    if not _is_renderable_post(p):
        return None
    return Path(cwd) / OG_DIR / (p.stem + ".png")


# The same front-matter rule the OG generator uses. Kept identical so the
# generator and the publish-time guard always agree on what a post is.
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---", re.DOTALL)

# Jekyll only treats a file in `_posts/` as a post when the name starts with
# a date prefix. `clawhub-guide-2026-06-28.md` is not a post.
JEKYLL_POST_NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-")


def _is_renderable_post(path):
    """True when Jekyll will render this file as a post.

    Two conditions, and both matter:

      * the filename starts with a `YYYY-MM-DD-` prefix — Jekyll only
        treats a file in `_posts/` as a post when it does
      * the file carries front matter — without it there is no layout,
        so there is no `og:image` and no social card that could 404

    A file failing either test is not a post and must not be allowed to
    demand an OG image. Demanding one used to wedge every publish: a
    stray draft dropped into `_posts/` made the publish-time guard fail
    for good, because the OG generator skips exactly those same files
    for exactly the same reason. The guard asked for an image that no
    generator would ever write, so the pipeline could not recover.

    The drafts belong in `blog-posts/` (excluded from the build) or the
    workspace's `blog/content/` holding area.
    """
    p = Path(path)
    if not JEKYLL_POST_NAME_RE.match(p.name):
        return False
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return FRONTMATTER_RE.match(text) is not None


def _og_png_paths(cwd, content_paths):
    """The OG images that belong with the given content files."""
    out = []
    for p in content_paths:
        og = _og_png_for(cwd, p)
        if og is not None and og.exists():
            out.append(og)
    return out


def _derived_paths(cwd, content_paths):
    """OG images + manifest that belong with the given content files."""
    out = [str(p) for p in _og_png_paths(cwd, content_paths)]
    manifest = Path(cwd) / OG_MANIFEST
    if manifest.exists():
        out.append(str(manifest))
    return out


def _manifest_png_paths(cwd):
    """OG PNGs the manifest references that git is not yet tracking.

    WHY (2026-10-28): the manifest is committed wholesale, but the PNGs were
    staged selectively — only the ones matching the caller's content paths.
    So a post published by another job (sol-blog.py skips the OG generator
    entirely) had its manifest entry committed while its PNG stayed
    untracked, leaving og:image pointing at a file that is not in the repo
    and returns 404.

    One `git ls-files` call, not one per entry — the pre-push credential scan
    taught us that per-blob subprocesses are unaffordable at this scale.
    """
    manifest = Path(cwd) / OG_MANIFEST
    if not manifest.exists():
        return []
    try:
        entries = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(entries, dict):
        return []

    names = [n for n in entries if n.endswith(".png")]
    if not names:
        return []

    tracked = set()
    r = _git(["ls-files", OG_DIR], cwd=cwd)
    if r.returncode == 0:
        tracked = set(r.stdout.split())

    out = []
    for name in names:
        rel = f"{OG_DIR}/{name}"
        if rel in tracked:
            continue
        png = Path(cwd) / rel
        if png.exists():
            out.append(png)
    return out


def _content_paths(cwd, paths):
    """Resolve the caller's content paths to absolute paths.

    When `paths` is None the caller wants everything staged, and the
    conflicting-artifact problem does not apply — there is no single post to
    protect, so the whole worktree is treated as the content set.
    """
    if paths is None:
        return []
    out = []
    for p in paths:
        p = Path(p)
        if not p.is_absolute():
            p = Path(cwd) / p
        out.append(p)
    return out


def _changed_files(cwd):
    """Files that differ from origin/main: tracked edits plus untracked files.

    Used when the caller stages everything (paths=None). We need the list so
    a hard reset during conflict recovery does not wipe work that has not
    been committed yet.
    """
    out = []
    for args in (
        ["diff", "--name-only", "origin/main"],
        ["ls-files", "--others", "--exclude-standard"],
    ):
        r = _git(args, cwd=cwd)
        if r.returncode != 0:
            continue
        for line in r.stdout.splitlines():
            line = line.strip()
            if line and not line.startswith(OG_DIR + "/"):
                out.append(Path(cwd) / line)
    return out


def _snapshot(paths):
    """Copy content files aside so they survive a hard reset."""
    tmp = tempfile.mkdtemp(prefix="sol-publish-")
    saved = []
    for i, p in enumerate(paths):
        src = Path(p)
        if not src.exists() or not src.is_file():
            continue
        dst = Path(tmp) / ("%03d-%s" % (i, src.name))
        try:
            shutil.copy2(src, dst)
        except OSError:
            continue
        saved.append((dst, src))
    return tmp, saved


def _restore(saved):
    """Put the snapshotted content back after a hard reset.

    `git reset --hard` removes files that the new origin/main does not have —
    including directories that become empty — so the parent directory has to
    be recreated before the copy. A silent failure here would lose the post
    entirely, so it is reported loudly.
    """
    for src, dst in saved:
        try:
            Path(dst).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        except OSError as e:
            print(f"[site_git] WARNING: could not restore {dst}: {e}")


def _stage(cwd, content_paths, derived_paths, stage_all):
    """Stage content + derived OG artefacts."""
    if stage_all:
        add = _git(["add", "-A"], cwd=cwd)
        if add.returncode != 0:
            raise PublishError(f"git add failed: {add.stderr.strip()[:300]}")
        return

    for p in list(content_paths) + list(derived_paths):
        one = _git(["add", str(p)], cwd=cwd)
        if one.returncode != 0:
            raise PublishError(f"git add failed: {one.stderr.strip()[:300]}")


def _assert_no_conflict_markers(cwd):
    """Refuse to commit a tree that still carries conflict markers.

    Safety net for the whole family of "git succeeded but the merge was
    dirty" failures. `git pull --rebase --autostash` applies a conflicting
    stash, writes `<<<<<<< Updated upstream` markers into the file and
    exits 0; the next `git add -A` then commits the markers. This check
    turns that into a loud failure before anything is published.

    A file is only flagged when it carries BOTH halves of a conflict
    marker, so an ordinary `=======` under a Markdown heading is not
    mistaken for one.
    """
    unmerged = _git(["diff", "--name-only", "--diff-filter=U"], cwd=cwd)
    if unmerged.returncode == 0 and unmerged.stdout.strip():
        names = ", ".join(unmerged.stdout.strip().splitlines()[:5])
        raise PublishError(f"unmerged paths in the working tree: {names}")

    def _grep(pattern):
        r = _git(["grep", "--cached", "-l", "-e", pattern], cwd=cwd)
        if r.returncode not in (0, 1):
            return set()
        return {line.strip() for line in r.stdout.splitlines() if line.strip()}

    starts = _grep("<<<<<<< ")
    ends = _grep(">>>>>>> ")
    both = starts & ends
    if both:
        raise PublishError(
            "conflict markers staged for commit in: "
            + ", ".join(sorted(both)[:5])
            + " — refusing to publish"
        )


def _commit(cwd, message, allow_empty):
    """Create the commit. Returns True if a commit was created.

    "Nothing to commit" is not an error: it means the content is already in
    the tree, so it is already published. Callers (bloopers-refresh, the
    analysis pipeline) already treat a None return as "nothing new to
    commit", so raising here only produced false "Git push failed" lines.
    """
    _assert_no_conflict_markers(cwd)
    commit = _git(["commit", "-m", message], cwd=cwd)
    if commit.returncode == 0:
        return True
    combined = (commit.stdout + commit.stderr).lower()
    if "nothing to commit" in combined:
        return False
    raise PublishError(f"git commit failed: {commit.stderr.strip()[:300]}")


def _recover_from_conflict(cwd, saved, content_paths, stage_all=False):
    """Resolve a rebase conflict caused by a shared derived artefact.

    The OG manifest is a pure function of the set of posts on disk. Two
    publishers adding posts in the same window both rewrite the whole file,
    so the merge always conflicts there.

    The old recovery reset the branch onto origin/main. That is destructive:
    this clone often holds unpushed commits from another publisher (a
    weekly roundup mid-flight, say), and resetting silently threw them away.
    So instead we resolve in place:

      * take the upstream version of the derived artefact,
      * continue the rebase,
      * let generate_og_images() rebuild the artefact from the merged post
        set, which yields the correct union of both publishers' work.

    A conflict in a *content* file is a different thing entirely — two
    people edited the same post. That is left for a human rather than
    silently resolved.
    """
    _git(["rebase", "--abort"], cwd=cwd)
    _git(["fetch", "origin", "main"], cwd=cwd)

    for _ in range(5):
        r = _git(["rebase", "origin/main"], cwd=cwd, timeout=120)
        if r.returncode == 0:
            break

        conflicts = _git(["diff", "--name-only", "--diff-filter=U"], cwd=cwd)
        names = [l.strip() for l in conflicts.stdout.splitlines() if l.strip()]
        if not names:
            _git(["rebase", "--abort"], cwd=cwd)
            raise PublishError(f"git rebase failed: {r.stderr.strip()[:300]}")

        for name in names:
            if not name.startswith(OG_DIR + "/"):
                _git(["rebase", "--abort"], cwd=cwd)
                raise PublishError(
                    f"content conflict in {name} — needs manual resolution"
                )
            # 'ours' during a rebase is the upstream side.
            _git(["checkout", "--ours", name], cwd=cwd)
            _git(["add", name], cwd=cwd)

        cont = _git(["-c", "core.editor=true", "rebase", "--continue"], cwd=cwd, timeout=120)
        if cont.returncode != 0:
            _git(["rebase", "--abort"], cwd=cwd)
            raise PublishError(
                f"git rebase --continue failed: {cont.stderr.strip()[:300]}"
            )
    else:
        raise PublishError("git rebase did not converge after 5 attempts")

    # Rebuild the derived artefacts from the merged post set.
    generate_og_images(cwd=cwd)
    _stage(cwd, content_paths, _derived_paths(cwd, content_paths), stage_all)


def _push(cwd):
    """Push local main to origin/main. Returns (ok, error_text)."""
    try:
        push = _git(["push", "origin", "main"], cwd=cwd, timeout=PUSH_TIMEOUT)
    except subprocess.TimeoutExpired:
        return False, (
            f"git push exceeded {PUSH_TIMEOUT}s "
            "(the pre-push credential gate is slow on this repo)"
        )
    if push.returncode == 0:
        return True, ""
    return False, push.stderr.strip()[:400]


def _verify_og_images(cwd, content_paths):
    """Every published post must have its OG image in the committed tree.

    A post whose og:image is missing ships with a 404 social card. That is
    not a cosmetic problem: the post is live but unshareable, and the SEO
    check only finds it after the fact. Verifying at publish time turns a
    silent miss into a loud one.
    """
    missing = []
    stray = []
    for p in content_paths:
        pp = Path(p)
        try:
            rel_post = pp.relative_to(Path(cwd))
        except ValueError:
            continue
        if rel_post.parts[0] == "_posts" and not _is_renderable_post(pp):
            stray.append(rel_post.name)
        og = _og_png_for(cwd, p)
        if og is None:
            continue
        try:
            rel = og.relative_to(cwd)
        except ValueError:
            continue
        r = _git(["cat-file", "-e", "HEAD:%s" % rel], cwd=cwd)
        if r.returncode != 0:
            missing.append(rel.name)
    if missing:
        detail = (
            "post published without its OG image: "
            + ", ".join(missing[:5])
            + " — social cards would 404"
        )
        if stray:
            detail += (
                ". Also in _posts/ but not renderable as posts (no front matter"
                " or no YYYY-MM-DD- name prefix), so no OG image is generated"
                " for them: " + ", ".join(stray[:5])
            )
        raise PublishError(detail)


def publish(message, paths=None, cwd=SITE_DIR, allow_empty=False):
    """
    Commit content and publish it to origin/main.

    `paths` is a list of files to stage; None stages everything. When `paths`
    is given, the matching OG image and the OG manifest are staged with it —
    a post that ships without its social card is a broken post.

    Returns the published commit SHA, or None if there was nothing to do.
    Raises PublishError on any real failure — never returns silently.
    """
    with publish_lock():
        ensure_main(cwd)

        # Must happen before staging so the new images are committed with the
        # post that needs them.
        generate_og_images(cwd=cwd)

        stage_all = paths is None
        content = _content_paths(cwd, paths)
        if stage_all:
            # Stage-everything callers still need a snapshot so a hard reset
            # during conflict recovery cannot destroy uncommitted work.
            content = _changed_files(cwd)
        derived = _derived_paths(cwd, content)

        # The manifest names every OG image; commit the ones git is not yet
        # tracking alongside it, so a committed manifest entry can never
        # point at a PNG that is missing from the repository.
        manifest_pngs = _manifest_png_paths(cwd)
        derived.extend(str(p) for p in manifest_pngs)

        tmpdir, saved = _snapshot(content + _og_png_paths(cwd, content) + manifest_pngs)
        try:
            created_commit = False
            remote_sha_before = ""
            last_error = ""
            ok = False

            for attempt in range(3):
                _stage(cwd, content, derived, stage_all)
                created_commit = _commit(cwd, message, allow_empty)

                if created_commit:
                    # Check BEFORE pushing: a post without its social card
                    # should never go live in the first place.
                    _verify_og_images(cwd, content)

                # Reconcile with origin/main BEFORE pushing. A diverged
                # branch would otherwise fail the push — and every failed
                # push still pays for the full pre-push credential scan.
                _git(["fetch", "origin", "main"], cwd=cwd)
                if not _worktree_dirty(cwd):
                    r = _git(["rebase", "origin/main"], cwd=cwd, timeout=120)
                    if r.returncode != 0:
                        # Conflict — resolve the shared derived artefact
                        # in place rather than resetting the branch.
                        _recover_from_conflict(cwd, saved, content, stage_all)
                        derived = _derived_paths(cwd, content)
                        created_commit = _commit(cwd, message, allow_empty)
                        if created_commit:
                            _verify_og_images(cwd, content)

                # Remember where the remote was before we pushed, so we can
                # tell "published something" apart from "was already up to date".
                remote_before = _git(["rev-parse", "origin/main"], cwd=cwd)
                if remote_before.returncode == 0:
                    remote_sha_before = remote_before.stdout.strip()

                ok, last_error = _push(cwd)
                if ok:
                    break

            if not ok:
                raise PublishError(
                    f"git push origin main failed after retries: {last_error}"
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
                # Nothing was committed AND the remote was already at this
                # commit: there was genuinely nothing to publish.
                if not created_commit and remote_sha_before == head.stdout.strip():
                    return None
                return head.stdout.strip()
            return None
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


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
