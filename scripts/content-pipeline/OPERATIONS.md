# Content pipeline — operational notes

Things that are not obvious from the code and cost real time to rediscover.
Written 2026-10-26.

## Schedulers

Several launchd agents and MiniMax cron tasks point at the same scripts, and
they overlap. Check before assuming a script runs once.

| Job | Trigger | Script |
|---|---|---|
| `ai.sol.weekly-roundup` | launchd, Mon 07:00 | `weekly-roundup.py` |
| `sol-weekly-roundup` | cron `0 8 * * 1` | `weekly-roundup.py` |
| `ai.sol.daily-content` | launchd, daily 07:00 | `run-daily.py` |
| `sol-daily-content` | cron `0 8 * * *` | `run-daily.py` |
| `sol-quick-hits` | cron `0 8 * * 1-5` | `quick-hits.py` |
| `sol-post-to-devto` | cron `30 7 * * *` | `devto-poster.py` |

The 08:00 slot is crowded — five jobs start within the same minute, plus the
launchd jobs at 07:00. Expect them to race on the site repo.

Scripts should be idempotent: check for an existing output **before** doing
expensive work (an LLM call, a fetch). `weekly-roundup.py` now checks for the
post file before building the body.

## Publishing to thesolai.github.io

All publishing goes through `site_git.publish()`. Never run a bare `git push`
— that is what caused the 2026-10-22 silent outage (see the note in
`site_git.py`).

**`publish()` returns `None` when there was nothing new to publish** — the
content was already in the tree. That is a success, not a failure, and callers
must not subscript the result (`sha[:12]` raises `TypeError` on `None` and
prints a bogus "Git push failed" line for a run that succeeded). Handle `None`
before printing a SHA.

The `allow_empty` argument is retained for compatibility but no longer
changes behaviour: "nothing to commit" is treated as a benign no-op rather
than an error, because it means the post is already published.

### When `paths` is given, the OG image ships with the post

`publish(msg, paths=[post])` also stages `og/<post-stem>.png` and
`og/.manifest.json`. A post committed without its social card ships with a
404 preview, and `publish()` now refuses to push a post whose OG image is
missing — it raises before the push rather than after.

### Pushes take a few seconds — this is the credential gate

`.git/hooks/pre-push` runs `secret-scan.py` over the working tree and the
push range. It used to inspect ~5,600 files one subprocess per blob and take
**5+ minutes**, long enough that `git push` hit its own timeout *inside* the
hook and the commit never landed. The scanner now reads every blob through a
single `git cat-file --batch` process: the worktree scan takes ~8s, a
200-commit history range ~25s, and the full gate ~12s.

If a push ever does look stuck again, check the gate first — but it should
not. Use a background task and wait.

If the gate reports `BLOCKED — credential found in the working tree`, run the
scanner by hand to get the real finding:

```bash
python3 ~/.openclaw/workspace/scripts/secret-scan.py <repo> --worktree
python3 ~/.openclaw/workspace/scripts/secret-scan.py <repo> --history origin/main..HEAD
```

A `SyntaxError` from `secret-scan.py` means the file was being edited while
the hook ran it. That is a transient failure, not a credential finding —
check the file compiles and retry.

### Rebase conflicts on OG files

When several jobs publish at once, `git pull --rebase` conflicts on
`og/.manifest.json` and `og/*.png`.

`site_git.publish()` now handles this itself. It rebases **before** pushing
rather than after a rejected push (each rejected push still pays for the
credential scan), and on conflict it resolves the shared derived artefact in
place: take **upstream** (`git checkout --ours <file>` — during a rebase
`--ours` is origin/main and `--theirs` is the commit being replayed), then
let `generate_og_images()` rebuild the manifest from the merged post set.
The manifest is a pure function of the posts on disk, so regenerating it
always yields the correct union of both publishers' work — no hand-merged
JSON.

Two rules still hold:

- `og/*.png` → take **upstream**. Upstream carries the "repaired" image; the
  local side is usually a duplicate from a job that ran twice.
- A conflict in a *content* file is not resolvable this way — two jobs edited
  the same post. `publish()` raises and leaves it for a human rather than
  silently picking a side.

Never `git pull --rebase --autostash` over a dirty worktree. When the stash
re-application conflicts, git writes `<<<<<<< Updated upstream` markers into
the file and **still exits 0**, so the next `git add -A` commits the markers
into the repository. `ensure_main()` no longer does this, and `publish()`
refuses to commit anything carrying conflict markers.

## Content generation

`minimax_generate()` in `_minimax_helper.py` takes an optional `model=`.
Default is `MiniMax-Text-01`, used by every other script — pass a model
explicitly to opt in elsewhere.

Reasoning models (`MiniMax-M2.7`) give markedly better prose for commentary:
sharper opinions, no "underscores a shift towards" filler, and they will say
when a story is too thin to judge. They also need a much larger
`max_tokens` (6000, not 600) because most of the budget goes on thinking
before any text appears.

The model will not reliably hit a word budget or avoid a phrase list. Enforce
length in code (`_trim_to_words` in `weekly-roundup.py` trims at sentence
boundaries, never mid-sentence), and give style examples about a *different*
topic than the stories in play — with a matching example, the model copies
it verbatim.

`run-daily.py` has **no** equivalent of `_trim_to_words`. "400-600 words"
there is a prompt instruction only, so the published length drifts over —
the 2026-10-27 posts came in at 582 (UK), 645 (EU) and 640 (US) body
words. Add a sentence-boundary trim there if the range needs to hold.

## A generator must never publish content that already exists

Every recurring series publishes on a schedule, so the generator is asked
the same question every day: *has this story already been covered?* The
answer has to be a **stable key**, not a filename and not a date.

Two keys are in use, and `post_identity.py` is the single definition of
both, so the audit and the generators cannot drift apart:

- **source URL** — the external story a post comments on. This is the
  stable key for anything that comments on the news. The *body* is not a
  stable key: a trending post embeds the live Hacker News point count,
  so the same story republished over a month produced 25 URLs whose
  bodies differed only in that number, and a body comparison caught
  almost none of them.
- **body signature** — the post text with formatting stripped. This is
  the stable key for a series that writes its own material (the bloopers
  templates). It is how the audit detects that a template has been
  republished.

Two bugs came from ignoring this, and both are now guarded:

- `run-daily.py` picked a bloopers template with `random.choice()` from a
  fixed pool of 3. Nothing checked whether that template had already
  been published, so every run republished an already-published story
  under a new date — 101 duplicate URLs between June and October.
- `trending-topics.py` always took `hits[0]` from the HN index. One
  story stayed at the top for a month, so it was republished every three
  hours — 25 URLs for one story.

The rule for anything that writes into `_posts/`:

> Before writing, check the archive for the stable key. If the key is
> already covered, pick something else or write nothing. Never publish a
> second URL for content that is already published.

When a generator has nothing eligible left, **skip and log it**. The
bloopers series is in that state now — all 3 templates are published, so
the daily slot goes quiet until new material is added. That is the
correct behaviour: a silent copy is worse than a gap.

`seo-health.py` reports the same thing the other way round. `duplicate_offenders()`
is the single definition of "this post needs a canonical", and
`seo-repair-canonicals.py` applies exactly what the audit prescribes —
the repair can never disagree with the check that reports it.

## The 08:00 cron duplicates the 07:00 launchd job

`ai.sol.daily-content` (launchd, 07:00) already runs `run-daily.py`, so the
08:00 cron invocation of the same script is a **guaranteed no-op**: the
day's posts exist, `find_duplicate_post()` matches them on date + region +
source URL, and the run reports "Already published this story" for all
three regions and exits without pushing. That is correct behaviour, not a
failure — do not treat it as a broken pipeline.

The dedupe key is the **source URL**, so a re-run only skips when HN's top
AI story is unchanged. If a different story takes the top slot between
07:00 and 08:00, the 08:00 run publishes a *second* set of three regional
posts for the same day, because nothing checks for a post that already
exists for that date and region. If that ever happens, delete the
duplicate set rather than keeping both.

Note also that `python3` on PATH in this environment resolves to the
Learnage venv (`/Users/amre/Projects/Learnage/.venv/bin/python3`), not
`/usr/bin/python3` or `/opt/homebrew/bin/python3`. The launchd job pins
`/opt/homebrew/bin/python3`; a bare `python3` in a cron entry depends on
whatever the shell resolves first.

## The weekly roundup's "Sol's Take"

Was a hardcoded string. All 12 roundups published in October 2026 shipped
byte-identical commentary, and it did not match the stories it sat under. It
is now generated from the week's actual stories, grounded in the source pages
via `fetch_source_excerpt`, with a deterministic fallback that still names
the real stories.

Roundup titles are dated (`The Week in AI: November 2, 2026`). They used to
be month-only, so every roundup in a month shared one title and one meta
description — duplicate content across 12 posts.

## dev.to cross-posts

`devto-poster.py` posts a new article; `devto_update.py --slug <slug>`
updates an existing one when the Jekyll post changes. The tracker is
`logs/devto-posted.json`.

Two things decide *which* posts get posted, and both were bugs first:

- **Recency is the post's publication date, not the file mtime.** The
  og:image SEO fix of 2026-10-25 rewrote 149 post front matters in a single
  second, so every mtime jumped to that moment. An mtime-based queue then
  re-ingested the whole archive — 95 unposted posts, 55 of them sharing two
  titles — and pushed the genuinely new posts out of the way. The queue is
  now `front-matter date:` within `RECENT_WINDOW_DAYS` (7), which survives
  front-matter rewrites. Backfilling older posts is `devto_backfill.py`'s
  job, not the daily poster's.

- **dev.to refuses a title reused within five minutes.** Recurring series
  ("AI Bloopers: …", "Trending: …") reuse one title every day, so they
  collide with each other and each collision was dropped silently — run
  after run, with only a cheerful "will retry next run". A collision is now
  retried once with the publication date appended, e.g. `… (12 Oct 2026)`.
