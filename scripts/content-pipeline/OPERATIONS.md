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
| `ai.sol.quick-hits` | launchd, Mon-Fri 07:00 | `quick-hits.py` |
| `sol-post-to-devto` | cron `30 7 * * *` | `devto-poster.py` |
| `ai.sol.sols-take` | launchd, daily 08:00 | `sols-take.py` (added 2026-11-16) |
| `sol-sols-take` | cron, daily 09:00 | `sols-take.py` |

The 08:00 slot is crowded — five jobs start within the same minute, plus the
launchd jobs at 07:00. Expect them to race on the site repo.

**The cron column above is out of date (checked 2026-10-28).** The only cron
entry left in the crontab is `sol-post-to-devto` (`30 7 * * *`). The
`sol-daily-content` entry is gone, which is what stopped the 07:00 launchd
job and the 08:00 cron from both running `run-daily.py` — that pair was the
cause of the same-day regional duplicates. Everything else has moved to
launchd: `ai.sol.quick-take` (Tue 12:30), `ai.sol.trending-topics` (09:00
and 13:00), `ai.sol.midweek-news` (Wed 07:00), `ai.sol.more-bloopers` (Wed
11:00), `ai.sol.case-study` (Fri 09:00), `ai.sol.bloopers-refresh` (Sun
08:00), `ai.sol.seo-health` (daily 08:00). Re-read the crontab and
`~/Library/LaunchAgents` before trusting this table.

**"Gone from the crontab" does not mean gone (corrected 2026-11-16).** The
note above was about the **system crontab** (`crontab -l`), which is the only
thing it checked. The **Mavis-managed** cron `sol-daily-content`
(`0 8 * * *`, Europe/London, cronId `6cb5229f-171f-4b2d-b62d-eb939b47f3ee`)
is still registered, still enabled, and still fires — it is invisible to
`crontab -l`. So `run-daily.py` really is still driven by two schedulers,
and the 08:00 duplicate described further down this file is still live. The
same is true of `sol-weekly-roundup` (Mon 08:00) and `sol-midweek-news`
(Wed 08:00), which were also listed as "moved to launchd" but still exist
as Mavis crons. List them with `cron_read view=list` (MCP `cron_read`),
not with `crontab -l`.

Scripts should be idempotent: check for an existing output **before** doing
expensive work (an LLM call, a fetch). `weekly-roundup.py` now checks for the
post file before building the body.

**A series driven by BOTH schedulers needs a run lock as well as the
existence check.** The existence check alone is not enough: two schedulers
can both pass it before either writes. `quick-hits.py` and `sols-take.py`
take an `flock` on `/tmp/sol-quick-hits.lock` and `/tmp/sol-sols-take.lock`
so only one of them writes. `ai.sol.quick-hits` (Mon-Fri 07:00) and
`sol-quick-hits` (cron 08:00) are one such pair — the launchd one fires an
hour earlier on purpose, so the two never race for the lock and a skip
means "the other scheduler already published", not "something failed".

### Never `git add -A` in this repo

The bundle's working tree carries other jobs' in-flight changes. On
2026-10-28 it held a deleted `skills/solscribe/` tree and an untracked
`logs/`, and a parallel session committed its own work — including the
`run-daily.py` grounding edits — mid-task.

So: stage explicit paths, and check `git status` before committing. A bare
`git add -A` sweeps in another job's half-finished deletions, and a
`git add -A && git commit` may commit work you did not write and have not
read.

## The workspace clone can break the site's build

`thesolai.github.io` is cloned twice against the same remote: once at
`/Users/amre/Projects/thesolai.github.io` and once at
`/Users/amre/.openclaw/workspace`. The "Daily Workspace Git Backup" job
commits the workspace's working tree into the published repo, so **a file
that only ever existed in the workspace can end up in the site repo and be
built by Jekyll.**

That is how `skills/devrelay-publishing/SKILL.md` reached the site on
2026-10-28 and broke the deploy three times. It documents a tool-call
syntax that is literally a Liquid tag:

    `{% agent_session <id_or_slug> %}`

Jekyll runs Liquid **before** Markdown, so a code span does not protect it,
and the build died with `Unknown tag 'agent_session'`. A build failure
stops the deploy of every later commit too, so one bad file froze the site
for ~80 minutes.

The rule: **anything in the site repo that has YAML front matter is a Liquid
template.** Posts and skill files are content, not templates, so they must
not contain unescaped tags. Two ways out, both used:

  * wrap it in `{% raw %}...{% endraw %}` — the site's existing convention
    (the 2026-06-19 blog-composer post does this), and the right choice for
    a post that means to show the syntax; or
  * add the path to the top-level `exclude:` list in `_config.yml` — the
    right choice for a file that must not be edited, such as an
    agent-facing skill file that is synced from upstream. `exclude` stops
    Jekyll processing the path at all; `sitemap.exclude` only hides the URL
    and is not enough.

Two guards now watch for this: `seo-health.py` check [12] flags unescaped
Liquid in published content, and check [11] verifies the newest posts are
live. Check [11] exists because the old live check only sampled older
posts — older posts stay reachable while a deploy is broken, so the audit
reported "all good" over a site that had not deployed at all.

One more trap in the same family: a post title carrying markdown emphasis
(`title: "**Some Title**"`) fails the repo's front matter test, and because
that test runs against the whole repo it blocks every later commit too.
`analysis-deep.py` now strips emphasis from the title it extracts.

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

### A draft in `_posts/` used to wedge every publish

On 2026-10-30 the weekday briefing failed to publish with:

```
post published without its OG image: clawhub-guide-2026-06-28.png, ... — social cards would 404
```

Eleven `clawhub-guide-*.md` files had been dropped into `_posts/` with the
date at the **end** of the filename (`clawhub-guide-2026-06-28.md`) and, in
ten of the eleven cases, no front matter. They were byte-identical copies of
drafts in the workspace clone's `blog/content/` holding area — the place a
timed-out content job leaves work it never got to publish.

Two facts collided:

- the OG generator only renders posts it can parse — a file needs front
  matter — so it skipped these eleven and never wrote their PNGs
- the publish-time guard checked *every* Markdown file under `_posts/`

So the guard demanded images no generator would ever write, and every
publish failed from that point on. The briefing itself was fine; it was
just the fourth file in the change set.

The guard now agrees with the generator: a file only has to ship an OG image
if Jekyll will actually render it as a post, which means a `YYYY-MM-DD-`
name prefix **and** front matter. Everything else in `_posts/` is a draft
that should be in `blog-posts/` (excluded from the build) or the workspace's
`blog/content/`. If a publish fails this way again, the error message names
the offending files and says why they are not renderable as posts.

## The site repo and the workspace repo are two clones of one remote

`/Users/amre/Projects/thesolai.github.io` and `/Users/amre/.openclaw/workspace`
both carry `https://github.com/TheSolai/thesolai.github.io.git` as `origin`.
The "Daily Workspace Git Backup" cron commits the workspace clone's working
directories into the published repo, which is why `blog-ideas/`, `blog-posts/`,
`scripts/`, `logs/` and `memory/` need an `exclude:` entry in `_config.yml`.

Because both clones push, their histories can drift apart — the site clone
was rewritten at least twice (see the `backup/pre-rebase-*` branches) and by
2026-10-30 the two sides sat 1219/1221 commits apart with **identical
trees**. `publish()` reconciles with `git rebase origin/main`, and that
rebase replays the duplicate commits: they conflict with their own
counterparts on every renamed file (rename/rename, rename/delete) and it
cannot converge.

**Do not rebase to reconcile a rewritten pair of clones.** Compare the trees
instead — `git diff --name-status origin/main HEAD` — and publish the
difference as a single fast-forward commit on top of `origin/main`. That
keeps whatever the other clone published (`who-pays-for-the-ai-buildout.md`,
the zlib-kindle script fixes) and adds only what is missing. A force-push
would delete the other clone's work; a rebase fights itself.

## Content generation

`minimax_generate()` in `_minimax_helper.py` takes an optional `model=`.
Default is `MiniMax-M2.7`, used by every other script — pass a model
explicitly to opt in elsewhere.

**MiniMax retires models without warning.** `MiniMax-Text-01` was the default
here until 2026-11-04, when it disappeared from the catalogue and every
script that relied on the default silently fell back to its deterministic
template — the posts still published, just with no prose in them. The helper
now guards against that class of failure three ways:

  * it tries `MiniMax-M2.7`, then `MiniMax-M2.7-highspeed`, then
    `MiniMax-M3.1-Flash`, so a retired model is never a hard stop;
  * it tries every known API key in turn (the `MINIMAX_API_KEY` env var and
    the secrets file drift apart, and one of them goes stale — a dead env key
    answered 401 on 2026-11-04 while the file still held the live one);
  * it prints the reason to stderr when every key and model fails, so a
    degraded run is visible in the log instead of shipping a bland post.

Check `https://api.minimax.io/v1/models` for the current catalogue before
adding a model name by hand.

Reasoning models (`MiniMax-M2.7`) give markedly better prose for commentary:
sharper opinions, no "underscores a shift towards" filler, and they will say
when a story is too thin to judge. They also need a much larger
`max_tokens` (6000, not 600) because most of the budget goes on thinking
before any text appears — so the helper raises any request below
`REASONING_MIN_TOKENS` (6000) for these models. A small cap does not return
short prose; it returns an empty completion, which looks exactly like a
failed call.

The model will not reliably hit a word budget or avoid a phrase list. Enforce
length in code (`_trim_to_words` in `weekly-roundup.py` trims at sentence
boundaries, never mid-sentence), and give style examples about a *different*
topic than the stories in play — with a matching example, the model copies
it verbatim.

`run-daily.py` now enforces its range in code (see "The regional posts
were not trimmed to their word budget" below).

## The regional posts were not trimmed to their word budget (fixed 2026-11-06)

`run-daily.py` asked for 400-600 words and did not enforce it — the prompt
alone does not hold a word budget. The published length drifted with the
model: 11 of the 311 published regional posts came in over 600 words on the
measure used here (the title line, section headings, `---` dividers and the
`*Source:*` line excluded), the worst at 708.

A plain `_trim_to_words()` would have been the wrong fix. A regional post
is not a wall of prose — it has a title line, section headings, a "What
this means" analysis, a takeaway and a source line — and trimming from the
end of the body would have eaten the analysis and the takeaway, the two
parts the prompt explicitly requires. So `text_utils.py` grew
`trim_to_word_budget()`, the structure-aware equivalent:

- it classifies every block (title / heading / takeaway / source /
  divider / prose) before cutting anything;
- it cuts the **expendable** prose — the lead first, then the analysis —
  and protects the title line, every heading, the takeaway, the source line
  and the concluding paragraph;
- it cuts at **sentence boundaries**, never mid-sentence, and never below
  one sentence per paragraph;
- a paragraph that cannot give up any more sentences is dropped whole, and
  a heading left with nothing under it is dropped with it, so no section
  dangles;
- a **floor of 400 words** stops it cutting a post short rather than long.

It was regression-tested against all 311 published regional posts: every
one stays within the ceiling when trimmed, keeps its title, headings,
takeaway and source line, and still ends on a complete sentence. The
budget is measured the same way OPERATIONS.md has always measured a
published body, so the numbers are comparable with the ones above.

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

One caveat on the source-URL key: `source_url()` returns the **first**
`*Source:*` link in a post, which is correct for a post that comments on one
story. A digest post carries three, and only the first was ever registered
with the archive — so the dedupe could not see the other two and would
republish a covered story. `source_urls()` returns every match, and
`published_source_urls()` uses it. `source_url()` keeps its first-match
contract for the audit's per-post labelling.

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

## The 08:00 cron duplicates the 07:00 launchd job (fixed 2026-11-06)

`ai.sol.daily-content` (launchd, 07:00) already runs `run-daily.py`, so the
08:00 cron invocation of the same script is a **no-op**: the day's posts
exist, `find_post_for_region()` matches them on date + region, and the run
reports "Already published today: <file> — skipping" for all three regions
and exits without pushing. That is correct behaviour, not a failure — do not
treat it as a broken pipeline.

The dedupe key used to be the **source URL**, so a re-run only skipped when
HN's top AI story was unchanged. If a different story took the top slot
between 07:00 and 08:00, the 08:00 run published a *second* set of three
regional posts for the same day, because nothing checked for a post that
already existed for that date and region. That is fixed:

- **The day's post for a region is the unit of publication, not the
  story.** `find_post_for_region(region)` matches on date + region alone,
  so a changed story no longer slips past the check. The old
  `find_duplicate_post()` (date + region + source URL) was removed as dead
  code.
- **The check runs before the expensive LLM call**, so a duplicate is
  skipped without paying for a generation.
- **A run lock** (`flock` on `/tmp/sol-daily-content.lock`, the same
  pattern `sols-take.py` uses) means the 07:00 and 08:00 runs cannot both
  pass the check before either writes. The second run prints
  "[lock] Another run holds the lock — skipping".
- **A degraded post is the one case worth regenerating.** The structured
  fallback is not real content, so a post whose title carries the
  `[UK]`/`[EU]`/`[US]` prefix is rewritten rather than skipped.

Note also that `python3` on PATH in this environment resolves to the
Learnage venv (`/Users/amre/Projects/Learnage/.venv/bin/python3`), not
`/usr/bin/python3` or `/opt/homebrew/bin/python3`. The launchd job pins
`/opt/homebrew/bin/python3`; a bare `python3` in a cron entry depends on
whatever the shell resolves first. Both `run-daily.py` and `text_utils.py`
import cleanly under `/usr/bin/python3` (3.9.6) as well as
`/opt/homebrew/bin/python3` (3.15).

## The weekly roundup's "Sol's Take"

Was a hardcoded string. All 12 roundups published in October 2026 shipped
byte-identical commentary, and it did not match the stories it sat under. It
is now generated from the week's actual stories, grounded in the source pages
via `fetch_source_excerpt`, with a deterministic fallback that still names
the real stories.

Roundup titles are dated (`The Week in AI: November 2, 2026`). They used to
be month-only, so every roundup in a month shared one title and one meta
description — duplicate content across 12 posts.

The same month-only/recurring-title problem survived in three other
generators, because a *recurring* series reuses one title by construction:
`sols-take.py` wrote `Sol's Take: <Weekday>`, `quick-hits.py` wrote
`Quick Hits: <Weekday>'s AI Briefing`, and `more-bloopers.py` wrote
`AI Bloopers: <Weekday> Edition — The Most Absurd AI Fails This Week`.
A weekday title repeats every week, so each of those series had already
accumulated 12–14 identically-titled pages, all with an identical meta
description, and the dev.to cross-posting duplicated them onto dev.to as
well — dev.to only refuses a title reused *within five minutes*, so a daily
recurring title sails straight through that guard.

All three now append the publication date, matching the roundup convention
(`Sol's Take: Wednesday — October 28, 2026`). The series identity is kept in
the title; the date is what makes it unique.

Two things this fix does **not** do, on purpose:

- It does not rewrite the pages that already carry the old titles. The
  roundup fix took the same approach — historical posts keep their old
  titles, and only new posts are unique. Changing a published title after
  indexing is its own SEO decision, not a side effect of a generator fix.
- It does not delete the redundant files. The archive still holds a large
  backlog of near-duplicate pages (327 redundant files across 42 repeated
  titles as of 2026-10-28 — 102 copies of `AI Bloopers: The Most Absurd AI
  Fails This Week` alone, from the `random.choice()` bug above, plus 25
  copies of one trending story). Cleaning that up is a separate job: it
  needs redirects for every removed URL, and each removal is a live-page
  change on the site.

A generator is the right place to fix this, not the poster. The poster's job
is to publish what exists; if the archive contains a duplicate-titled file,
the poster has no way to know it is the second one. The rule stays the one
above — nothing writes a second URL for content that is already published.

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

### The poster's queue is a startup snapshot (confirmed 2026-11-17)

`get_recent_posts()` runs once, before the first post goes out, and the run
works through the list it returned. A post written into `_posts/` *after* that
point is not in the run's queue at all. On 2026-11-17 the auto-poster started
at 12:18 and the content pipeline wrote "Vibes Is a Testing Strategy" at
12:24; the poster finished at 12:29 having never seen it, and
`devto-viral.py` published it 71 seconds later (dev.to id 5057098). Nothing
was lost.

Two consequences. A short run is not necessarily a broken one — check the
tracker before drawing a conclusion. And **do not re-run the poster to pick
up the remainder**: the other publisher may have taken it in the meantime,
and a second run publishes the same content twice. The tracker is the source
of truth for what is already on dev.to, not the post count in the log.

### The tracker's `id` field is not type-stable

One entry (`2026-10-24-uk-ai-weekly-dessa-…`) stores `id` as the string
`'4909031'`; every other entry stores an int. Comparing a live dev.to id
against the tracker with `==` on ints silently reports that article as
untracked — which reads as a missing post but is not one. Compare with
`str(id)`. Two other entries carry `posted_at: null` and four carry no `id`
at all, all from the 2026-10-24 era.

## `devto-viral.py` had four silent defects (fixed 2026-10-29)

The daily viral engine ran for weeks reporting success while doing almost
nothing useful. All four were found by reading its own output rather than
its exit code, and none of them raised an error.

- **Every ad opportunity linked the same Sol article.** The selection loop
  broke on the first Sol post sharing *any* tag, and "ai" is on nearly every
  dev.to post and on the first Sol post — so it always won. All 30 logged
  opportunities pointed at one URL. Selection now scores meaningful tag
  overlap, discounts generic tags, and prefers the least-used article so
  the link rotates across Sol's posts.

- **Comments were formulaic filler.** The prompt permitted a generic
  compliment, so the model wrote "Great post! It's fascinating how…" for
  every post. The prompt now asks for a specific detail from the article
  and bans the recurring openers. The miner also dropped the dev.to
  `description` when caching trends, leaving the generator no article
  context at all; that field is now kept.

- **One story was cross-posted to dev.to 14 times under different slugs.**
  The trending generator re-emitted one story under a new date each day, so
  the tracker — which dedupes on canonical URL — saw 25 distinct URLs for
  identical bodies. Cross-posting now compares body signatures, so only the
  freshest copy of a story reaches dev.to. A URL check cannot catch this:
  every dated copy has a different canonical.

- **The trends cache froze on the day it filled up.** The merge built the
  list from the existing entries and appended this run's new trends at the
  end, then truncated with `merged[:50]`. Once the cache held 50 entries,
  every new trend was appended past the cut and discarded — the file stopped
  changing on 2026-10-11 while each run still logged "Trending: 20 new".
  Newly mined trends now lead the list, so the 50 kept are the 50 most
  recent.

The general lesson: **a count in the log is not proof the work happened.**
"Trending: 20 new" was printed by a run that wrote nothing, and "5 new ad
opportunities" was printed by a run that linked the same article five times.
Read the file the run wrote.

## `devto-viral.py` lost its whole cross-post slot to a deploy race (fixed 2026-11-17)

On 2026-11-17 the viral engine started at 12:18:40 and **published nothing**,
while exiting 0 and printing "Done." Every one of its five candidates was
skipped with `Skipped (canonical not live)`.

The cause was a race, not a fault. The site's deploy workflow ran
**12:18:24 → 12:21:35 (3m11s)** for that push, and the engine checked each
canonical at ~12:19:00 — about **2.5 minutes before the deploy finished**. A
canonical that 404s during a deploy is not a dead post; it is a post that has
not been published *yet*. The engine treated the first answer as final and
spent the entire day's slot on skips.

This is only possible when the content pipeline and the viral engine run close
together. Normally the site is deployed by 09:17 and the 10:00 cron is safe.
On 2026-11-17 the MiniMax Code app was not awake in the morning, so every
app-driven job fired together on wake: the content generators pushed at
12:15–12:24, `devto-poster.py` started at 12:18:50, and the viral engine at
12:18:40. The two posting paths then competed for the same posts — the
poster won the canonicals, and the viral engine got whatever was left.

`cross_post()` now **waits for the deploy instead of skipping**:

- If the newest candidate is dated today or yesterday and its canonical is not
  live, the engine polls for up to `DEPLOY_WAIT_SECONDS` (6 minutes) at
  `DEPLOY_POLL_SECONDS` (20s) intervals before deciding to skip. Six minutes is
  more than twice the measured 3m11s deploy.
- The wait happens **before** the first LLM call and before the per-post skip,
  so a deploy race costs wall-clock time, not an LLM call or a publishing slot.
- The wait is bounded and only applies to fresh posts. An old post that was
  never deployed is a different problem and must not stall the run.

`devto-poster.py` does **not** check canonical liveness at all, which is why it
published successfully at 12:19:54 — 90 seconds after the push — with a
canonical that was not live yet. That is the mirror image of the defect above:
the poster risks a dead canonical, the viral engine risks no post at all.

Regression tests: `test_devto_viral_posting.py`, class
`TestDeployRaceDoesNotWipeTheSlot` (7 tests). They assert that a canonical
that 404s then goes live is published rather than skipped, that the wait is
bounded, and that the wait is ordered before both the skip and the LLM call.

## Posts published with `sol-blog.py` skip the OG image generator

`/Users/amre/.mavis/agents/general/workspace/sol-blog.py` publishes through
the **raw GitHub API** (`PUT /repos/.../contents/_posts/<file>`), not through
`site_git.publish()`. It therefore never calls `generate_og_images()`, and
the post lands with a **404 social card** — `og:image` points at
`og/<source-stem>.png`, which does not exist because nothing created it.

`Site Health Tests` catches this (`FAIL: 1 post(s) without an OG image` and
`FAIL: og:image 404 ...`), so a `sol-blog.py` post red-lights CI until the
image is added.

Fix, in order:

1. `git fetch origin main` in the site clone, then run the generator against
   a **clean worktree** at `origin/main`:
   `git worktree add --detach /tmp/og-gen origin/main && cd /tmp/og-gen && python3 _scripts/generate_og_images.py`
   It is incremental — for one new post it reports `generated: 1, reused:
   1219` and only writes `og/<new-stem>.png` plus `og/.manifest.json`.
2. Commit the PNG **and** the updated manifest. The manifest diff should be
   exactly one added key; if the generator rewrote other entries, the
   worktree was not at `origin/main`.
3. `git worktree remove --force /tmp/og-gen` afterwards.

Two traps while doing this:

- **The site clone may refuse to sync.** `ensure_main()` returns early when
  the worktree is dirty, so an unrelated uncommitted file (a modified
  workflow file, say) leaves the clone unable to fast-forward — and the new
  post never appears locally. A throwaway worktree at `origin/main` avoids
  touching that dirty tree entirely. Do not stash it; `git pull --rebase
  --autostash` is exactly what wrote conflict markers into the manifest once.
- **The deploy can be cancelled before it runs.** The `pages` concurrency
  group keeps one deploy at a time, so a commit that lands while another
  deploy is in flight gets its own "Build and Deploy" run **cancelled**
  without deploying. The next commit's deploy carries the content forward,
  so check that the PNG is live at `https://thesolai.github.io/og/<stem>.png`
  rather than assuming the commit was deployed.

## `sol-blog.py list` lies about when publishing stopped

The GitHub contents API returns **at most 1,000 entries** per directory.
The site has 1,219 posts, so `list_posts()` is silently truncated to the
alphabetically first 1,000 files — which end at **2026-09-28**. Read
literally, it looks like nothing has been published for a month.

It has not. To see what is actually live, use the commits API instead:

```
GET /repos/TheSolAI/thesolai.github.io/commits?path=_posts&since=<date>
```

or `GET /git/trees/main:_posts?recursive=1` for every filename. Both
returned the full, current set.

## A failing Site Health Tests run is not always a real defect

The test workflow is triggered by `workflow_run` and checks out
`github.event.workflow_run.head_sha` — **the commit that was actually
deployed**, not the current tip of `main`. When several commits land inside
one deploy window, the test can check out an older tree than the one just
committed and fail on something that is already fixed.

Symptom on 2026-10-27: the run reported
`missing og/2026-10-27-your-ai-bill-scales-with-your-success.png` for a
commit that demonstrably contained it. The checkout was `e8d74270` (an
older deploy); the PNG arrived in `c9e86ad1`, whose deploy was cancelled
before it ran, and the live `og/` path only returned 200 after the *next*
commit deployed.

Before treating a red test as a broken site, check which tree the run
actually checked out (`workflow_run.head_sha` vs `main`), and whether the
path is live on the deployed site. `Site Health Tests` must be **green**
before calling a publish finished — a red run here is a real signal even
when the cause is a race.

## A green Site Health Tests run may not have tested your commit

The test fires on **deploy completion**, not on commit. A commit has no test
of its own until *its* deploy finishes, and the tree the test checks out is
the triggering deploy's `head_sha` — which can be an entirely older commit.

The trap is that `gh run view <id> --json headSha` does **not** report that
ref. On 2026-10-29 it reported `b7df2253` for a run whose Checkout step used
`ref: 4c5bb679`. Read as "the broken commit passed its test", that is the
wrong conclusion by a whole commit.

The only reliable way to know which tree a run tested is the Checkout step
in the run log:

```
gh run view <run-id> --log | grep -m1 "ref:"
```

So **a green run does not prove the tip is healthy**. To check a commit:
wait for its deploy to complete, then find the test run whose Checkout
`ref:` equals that commit — not the most recent test run.

The converse bit on 2026-10-29: the `sol-blog.py` commit `b7df2253` shipped
a post with no OG image, and the test that passed at 08:03 had checked out
the *previous* commit. The test for `b7df2253` only fired at 08:05, once its
deploy landed, and failed with `1 post(s) without an OG image` — the exact
defect described above. The repair is the one in "Posts published with
`sol-blog.py` skip the OG image generator"; the fix landed as `4252ae7a`
and the gate went green on the next deploy.

## The regional prompt fabricates to satisfy the regional framing

`run-daily.py`'s ground rules already said "never invent company names,
people, products, or national origins" and "use only organisations and facts
present in the source material". It did not stop the model inventing. The
regional framing puts pressure on the model to produce a local angle, and
when the story has no local angle the model manufactures one.

Three shipped on 2026-10-28, all on the same story (Survival LLM):

- The EU post called the EU AI Act "currently making its way through the
  legislative process". The Act entered into force on 1 August 2024 and
  became applicable on 2 August 2026 — the phrasing was roughly two years
  stale.
- The UK post claimed "the UK's tech community is already buzzing with
  discussions about how to build on this concept". Nothing in the source said
  any such thing.
- The US post explained the product's internals as "a process called model
  distillation, where a large AI model is compressed into a smaller, more
  efficient version". The source says the app downloads a pre-existing
  open-weights model matched to the machine's RAM. There is no distillation.

Two changes fixed the class of error rather than the three instances:

- **`REGIONAL_GROUNDING`** in `run-daily.py` gives the model dated, verified
  facts per region — the EU AI Act timeline, the UK's lack of an AI-specific
  statute and its AISI/AI Taskforce position, the US's executive-order and
  state-patchwork landscape. It also states the failure explicitly: never
  describe the AI Act as "in the legislative process", "awaiting approval" or
  "coming next year". The model was filling a blank with half-remembered
  regulatory history; the blank is now filled with something checked.
- **`ANTI_FABRICATION`** names the three specific failure modes that
  shipped — claimed community sentiment, unnamed technical mechanisms, and
  calling a project a company — plus "prefer the source's specifics over
  generic regional filler". Forbidding "invention" in the abstract does
  nothing; naming the shape of the invention works.

Both blocks are appended to every regional prompt in `generate_post()`.
Re-check the grounding dates when they age — they are a snapshot, not a
live feed.

The underlying lesson generalises past this pipeline: **a prompt that asks
a model for a local angle will get one, whether or not one exists.** Supply
the facts, or the model will.

## The Weekend Deep Dive had a word floor but no word ceiling (fixed 2026-11-07)

`weekend-deep-dive.py` asks for a 600-800 word piece and the prompt asks for
700-750, but the script only ever enforced a **floor** — 400 words, below
which the draft is a stub. There was no ceiling at all, so nothing stopped
the model from writing long.

MiniMax-M2.7 drifts long. The 2026-11-07 episode came in at **1050 words** —
the longest ever published, against an 800-word brief. The preceding episodes
ran 773, 806, 713 and 853, so the drift was real but gradual and only this
one crossed the line badly enough to notice.

Enforcement is now three-tiered, so a length overrun can never cost the site
an episode:

1. **Condense via the model**, with escalating pressure across three attempts.
   The condenser edits the existing draft rather than regenerating, so the
   argument, the citations and the closing takeaway survive. A single pass is
   not dependable — a real condense of the 1050-word draft came back at 919
   words, still over the line; the second attempt landed at **784 words** with
   both citations, all four sections and the closing takeaway intact.
2. **Trim mechanically** at sentence boundaries via
   `text_utils.trim_to_word_budget()`. This is the structure-aware trimmer
   `run-daily.py` uses, and it is the right one here: it protects the title
   line, every section heading and the concluding paragraph, and cuts the
   expendable prose instead. A plain `trim_to_words()` cuts from the end,
   which eats the closing takeaway first — the exact failure OPERATIONS.md
   warns about under "The regional posts were not trimmed to their word
   budget". On the real 1050-word draft the structure-aware trim gives **831
   words** with all four sections, both citations and the takeaway intact.
3. **Abort** rather than publish a stub.

One measure subtlety worth remembering: `trim_to_word_budget()` excludes the
title line and the section headings from its budget, while this ceiling
counts the whole body. The trimmer is therefore asked for `MAX_WORDS` minus
the heading overhead, so the two measures agree.

The hardened prompt also helps on its own. A fresh generation after the
prompt change came in at **787 words**, inside the brief, with no condensing
needed at all.

## A generator's word budget is a prompt instruction, not a constraint

`run-daily.py` used to ask for 400-600 words and not enforce it — there was
no `_trim_to_words` equivalent to the one in `weekly-roundup.py`. The
2026-10-28 posts came in at 591 (EU), 528 (UK) and 600 (US) body words
after editing, and the 2026-10-27 set was 582/645/640. It now trims in
code via `text_utils.trim_to_word_budget()` — see "The regional posts were
not trimmed to their word budget" above.

When editing a published post for accuracy, measure the body the same way
both times (exclude the title line, `###` headings, `---` dividers and the
`*Source:*` line) or the comparison is meaningless.

## The midweek digest was three different things from its own description

The job said: fetch HN stories from the past 3 days, pick the 3 most
significant AI stories, write brief commentary in Sol's voice. The script did
none of the last two and only half of the first.

- **No time filter.** `fetch_hn_stories()` computed a `cutoff` timestamp and
  never used it — the digest surfaced whatever was on the front page that
  minute while claiming to cover three days. It now uses the HN Algolia API
  with `numericFilters=created_at_i>{cutoff}`, falling back to the Firebase
  API with the same filter applied.
- **No commentary.** The post was a link list — title, URL, point count.
  There was nothing to misfire, and nothing to read. It now generates the
  same grounded, in-voice commentary as the roundup's "Sol's Take", with a
  deterministic fallback that still names the real stories.
- **No archive dedupe.** It never checked whether the archive already
  covered a story, so it could publish a second URL for a story another post
  had already covered. On 2026-10-28 it did exactly that: the digest
  carried `brangus.io/survival-llm` while three other posts that day
  carried the same story. It now filters on the source URL via
  `post_identity.published_source_urls()`.

The dedupe only works if the archive can *see* the link, and it could not:
`post_identity.source_url()` matches `*Source: [title](url)` and
`**Source story:** [title](url)`, and the digest was writing
`[Read →](url)`. The digest now writes the house `*Source:*` line, so its
stories are visible to the dedupe and to `seo-health.py`.

`--refresh` rewrites today's post in place — same URL, no second copy — for
when the commentary needs regenerating. Without it the script is idempotent:
if today's file exists it does nothing, because the 07:00 launchd job
(`ai.sol.midweek-news`) already ran. The 08:00 cron invocation is therefore
a guaranteed no-op, exactly like `run-daily.py`'s.

## Source grounding was silently dead in every launchd run

Two independent faults, each hidden by a bare `except`:

1. `weekly-roundup.py` imported the source fetcher with
   `from run_daily import fetch_source_excerpt`. The file is named
   `run-daily.py` — with a hyphen — so that import always raised
   `ModuleNotFoundError`, and the `except Exception` swallowed it. The
   roundup's "Sol's Take" was written from headlines alone, with no source
   grounding, while OPERATIONS.md described it as grounded.
2. Even with the import fixed, `fetch_source_excerpt()` returned `""`
   whenever BeautifulSoup was missing — and it is missing from
   `/opt/homebrew/bin/python3`, the interpreter every launchd job uses.
   `bs4` and `lxml` are only installed for `/usr/bin/python3` and the
   Learnage venv.

So grounding was off in production and on in a manual run — the worst
combination, because testing under the wrong interpreter looked fine.

Fixed by two changes:

- **`source_fetch.py`** loads `run-daily.py` via `importlib` and is the
  single definition of that lookup. Both the roundup and the midweek digest
  import from it.
- **`run-daily.py`** gained `_StdlibHTMLText`, a `html.parser` extractor used
  when bs4 is absent, so grounding no longer depends on a package that only
  some interpreters have. The bs4 path is unchanged and still preferred.

The general lesson: `except Exception: return ""` around an import is a
silent-failure generator. If the fallback changes the *quality* of the
output rather than only its formatting, log it loudly.

## The manifest was committed whole while its PNGs were staged one by one

`publish(msg, paths=[post])` stages the OG image matching that post, plus
`og/.manifest.json` — the whole manifest, every entry in it. So any manifest
entry whose PNG was not tracked got committed anyway, pointing `og:image` at
a file that was not in the repository.

The instance: `sol-blog.py` publishes through the raw GitHub API and never
runs the OG generator (see above), so
`2026-10-28-the-exponential-time-hypothesis-is-false-probably.md` had no
social card. Site Health Tests went red on 2026-10-28 with "1 post(s)
without an OG image" and a 404 on the live `og:image` URL.

`publish()` now also stages every PNG the manifest references that git is
not yet tracking — one `git ls-files og/` call, not one subprocess per
entry, because the pre-push credential scan made per-blob subprocesses
unaffordable. The drift was exactly one file, so the repair is
self-limiting.

Note the shape of the failure: the *manifest* was correct and the *image*
was missing, so nothing in the repo looked wrong. Compare the manifest
against the tracked PNGs when an `og:image` 404s.

## Shared helpers live in `text_utils.py`

The sentence-splitting and word-trimming helpers (`split_sentences`,
`ends_cleanly`, `trim_to_words`) started in `weekly-roundup.py` and are now
in `text_utils.py`, imported by both the roundup and the midweek digest.
The implementation is unchanged — only the location — and the refactor was
checked by diffing the helpers' output before and after on the same inputs.

One definition, so the two generators cannot drift apart on how a word
budget is enforced.

## A description that exists is not a description that works

The SEO health check had two gaps that hid the same defect.

The **local** front-matter pass asked only whether `description:` was
present. `description: "Update"` is present. It is also a six-character
search snippet and a six-character link preview, and the layout writes it
straight into `<meta name="description">` with no length floor.

The **live** pass was the one that could have caught it — it fetches the
rendered page and tests the real `<meta>` tag — but it only samples
twelve posts out of 1,238, evenly spread by index. A dozen samples out
of 1,238 finds the defects that happen to sit at the sample points.

Together: seven posts had descriptions too short to be usable, and the
daily report named one of them. The other six were real and shipped.

Fixed on 2026-10-29:

- the local pass now checks every post's description length against
  `MIN_DESCRIPTION_LENGTH` (20), the same line the live test uses, so the
  two checks cannot disagree;
- the live sample stays a spot-check, not the primary check — the local
  pass sees all 1,238.

A field being present is not a field being usable. Check the value, not
the key.

## The prompt's metadata block gets echoed into the body

The regional prompts hand the model a spec block:

```
Title format: "UK AI Weekly: [compelling headline]"
Date: {date}
Tags: ai, uk, analysis, policy
Image: /images/sol-avatar.png
```

The model sometimes treats that as part of its answer and writes it into
the post body. It ships as visible text at the top of the article — raw
front matter, a stray date, an avatar image — and the front matter it
duplicates also renders a second `<h1>`.

130 published posts carried it, in several shapes: fenced by `---`, fenced
at the front only, or not fenced at all. The daily report never saw it,
because the check looked for a `Tags:` line in the first or last few lines
of the body, and the block was often four lines down.

Two fixes, and both are needed:

- the prompt now says not to write the Date, Tags or Image lines;
- `strip_metadata_echo()` in `run-daily.py` removes the echo before the
  body is written, matching the metadata run by content rather than by
  one fixed layout.

The prompt change alone is not the fix. A prompt is a request; this has
to hold whatever the model returns. The stripping is what makes it true.

**Still open:** 361 posts repeat their title as the first line of the
body. The layout already renders `<h1>{{ page.title }}</h1>`, so 147 of
them have two `<h1>` elements and 173 repeat the title as a bold
paragraph. The fix is mechanical (drop the first body line when it
normalises to the title) but it touches 361 files, so it needs a
decision, not a cron run.

## `tool-spotlight.py` pasted an unrelated dev.to article into every review (fixed 2026-10-30)

The weekly spotlight wants a line of social proof, so it fetches a dev.to
article about the tool it is reviewing and asks the model to mention it.
The fetch never worked.

**The dev.to `/api/articles` endpoint has no text-search parameter.** A `q`
param is silently ignored — there is no error, no empty result, just the
same list back. Verified on 2026-10-30: `?q=Claude%20Code`, `?q=Cursor`
and no query at all returned byte-identical five-article lists. The old
code sent `q` and took `articles[0]`, which meant every single spotlight
pasted in whatever AI-tagged article happened to be newest that minute,
and the prompt then told the model to mention it. The model obligingly
shoehorned it in:

| post | spotlighted tool | dev.to article it cited |
|---|---|---|
| 2026-10-30 | Claude Code | "Ghost in the Shell: Can a Log File Hijack an AI?" |
| 2026-09-11 | MiniMax | "AI Is Already Better at Coding Than Most Software Developers" |
| 2026-08-14 | MiniMax | "I Stopped Trusting AI Agents With Tools. So I Built a Gatekeeper." |

The 2026-10-30 post is the clearest example: a Claude Code review that ends
with a paragraph about log files hijacking an AI. Factually it is fine, and
it is exactly the kind of tangential filler that reads like padding.

**The lesson is the general one:** a query param that the endpoint ignores
is indistinguishable from a filter that matched nothing. Nothing in the log
said "no results" — it said "dev.to: '…' — 45 reactions", which looks like
a successful match. When a filter is supposed to narrow a result set,
change it and check the results actually change. A filter that cannot make
a difference is not a filter.

**The fix:** filter client-side. Pull the 30 most recent `ai`-tagged
articles and keep only ones whose title or description mentions the tool,
matched on word boundaries against a per-tool term list (`TOOL_SEARCH_TERMS`,
with "claude code" preferred over a loose "claude"). When nothing matches,
the dev.to sentence is dropped entirely rather than filled with something
else. A missing paragraph beats a wrong one.

After the fix, Claude Code correctly matches *"A trojanized Claude Code
extension plants an SSH backdoor: the 7-point audit for your machine"*, and
the other seven tools in the rotation correctly get no dev.to line at all.

**Still open on this script** — both predate the fix above:

- **The prompt asks for the metadata echo.** The rating, tagline and
  verdict are passed in as body instructions, so every post opens with
  `**Title: …**`, `**Rating: …**` and `**Tagline:**` as visible text. This
  is a different cause from the regional spec-block echo described above:
  there the model copied a block it should have ignored, here the prompt
  tells it to write the lines. The 2026-10-30 post has all four, and the
  title line duplicates the front-matter title that the layout already
  renders as `<h1>`.
- **The word budget is still unenforced.** The prompt asks for 350-500
  words. The 2026-10-30 post came in at 581 body words, 550 of them prose
  once the four metadata-echo lines are excluded — measured the way
  `run-daily.py` is measured above, so the numbers are comparable. There is
  still no `_trim_to_words` equivalent in this script.

## The SEO audit never looked at the metadata a crawler reads first (2026-10-30)

`seo-health.py` compared post **bodies** for duplicates and checked that a
description **existed**, but it never checked that the title or the
description was **unique**. Two posts with different bodies but the same
`<title>`, `<h1>` and `<meta name="description">` are one page to a search
engine, and the audit reported them as fine.

The state on 2026-10-30:

- 246 posts shared a title with another post and had no canonical
- 249 posts shared a meta description with another post and had no canonical
- the worst description was on 87 posts: `"Sol's opinion on today's AI landscape."`
- the worst title was on 102 posts: `"AI Bloopers: The Most Absurd AI Fails This Week"`

The duplicate-*content* check had counted source-story families and called
them healthy because a canonical pointed at the original. That is the right
rule for a body, and the wrong rule for a title: a canonical tells Google
which URL to keep, so a consolidated group is one page. But a group where
two or more members carry **no** canonical is two pages claiming to be the
same thing.

**The audit now checks both**, and a group is only an error when two or more
members lack a canonical. A consolidated group is reported as a note, the
same way the regional-series case already was. This was check [5], inserted
where it belongs rather than appended, and the later steps renumbered to
match.

**The cause is in the generators, not the posts.** Every recurring series
publishes from a fixed template:

| series | posts | generator |
|---|---|---|
| AI Bloopers | 102 | `run-daily.py` |
| Sol's Take | 87 | `sols-take.py` |
| Quick Hits | 59 | `quick-hits.py` |
| AI Digest | 22 | `midweek-news.py` |
| The Week in AI | 12/month | `weekly-roundup.py` |
| Trending | 23 | `trending-topics.py` |
| Tool Spotlight | 5 | `tool-spotlight.py` |

The fix belongs in the generator, because a backfill of 500 descriptions
would be undone by the next publish. `sols-take.py`, `quick-hits.py` and
`more-bloopers.py` already append the post date to the title and the
description; `run-daily.py` (the AI Bloopers series — the largest group) does
not, and `midweek-news.py` still writes one fixed description. `weekly-
roundup.py` uses a month-level date, so every roundup in a month shares a
title and a description.

### Every generator now dates its metadata (2026-10-31)

The remaining generators were fixed. Each recurring series now appends the
episode date to the title **and** the description, so two episodes of the
same series are never the same page to a crawler:

| generator | series | what it published before |
|---|---|---|
| `run-daily.py` | AI Bloopers | one fixed title, 102 posts |
| `run-daily.py` | regional UK/EU/US | one fixed description per story+region |
| `midweek-news.py` | AI Digest | weekday-only title, one fixed description |
| `trending-topics.py` | Trending | headline-only title and description |
| `tool-spotlight.py` | Tool Spotlight | tool name and tagline only |
| `quick-take.py` | Quick Take | topic only |
| `deep-dive.py` | Deep Dive | topic only |
| `weekend-deep-dive.py` | Weekend Deep Dive | topic only |
| `case-study.py` | Case Study | case title only |
| `analysis-deep.py` | Analysis | topic only |

Two details worth remembering:

- **The regional series needed the date in the *description*, not the
  title.** Its title already carries the generated headline, so two
  episodes of the same series never shared a title. The description was
  `"{story title} — daily AI analysis from {region}."` — and when the same
  HN story came back around weeks later, the second episode published a
  byte-identical description. 62 regional posts were in that state.
- **The topic-bank series rotate, so "the topic" is not a unique key.**
  `quick-take.py` picks from a bank by day-of-year, `deep-dive.py` and
  `weekend-deep-dive.py` the same way. The same topic recurs on a later
  date, so the date is what makes the episode distinct.

Each generator that did not already have one gained a `_yaml()` helper,
copied from `run-daily.py`: titles and taglines contain quotes, colons and
apostrophes, and interpolating them raw produced unparseable front matter.

`trending-topics.py` also got a second guard. `fetch_top_story()` already
skips covered source URLs, but one story sat at the top of the HN index for
a month and was republished 25 times (2026-09-29 to 2026-10-27) before the
covered-URL check existed. The generator now re-checks the chosen story
against the archive before writing, so a story that is already covered
produces nothing rather than a near-duplicate.

The 2026-10-30 audit findings are historical and are **not** backfilled. The
audit still reports them, and will keep reporting them, until the affected
posts are given a canonical — that is the `seo-repair-canonicals.py` job,
not a title rewrite. What changed is that no *new* post joins the list.

## The SEO audit reported drafts in `_posts/` as broken posts (2026-10-30)

The 08:00 run reported three `clawhub-guide-*.md` files as republications
carrying no canonical, and the live check reported three of the day's own
posts as `HTTP 404 — the site may not be deploying`. By 10:00 both were
gone and the site was fully deployed.

Both were the same transient state: the timed-out content job leaves drafts
in `_posts/`, and the 07:00 publish moves them. At 08:00 the audit sampled
the tree mid-publish.

- the drafts are **not posts**. `site_git.publish()` was fixed earlier the
  same morning to agree with the OG generator about what a post is (see
  above), but `seo-health.py` had its own, looser idea of the word
- today's posts were published but the GitHub Actions deploy had not
  finished yet. The deploy routinely takes more than the hour between the
  07:00 publish and the 08:00 check

The audit now applies the same rule the publish guard uses: a file in
`_posts/` is a post only if its name carries a `YYYY-MM-DD-` prefix **and**
it has front matter. Everything else is listed as a note and excluded from
every post-level check. Verified against the old script with the same two
drafts in place — it reported `No frontmatter` and `missing og/…png` for
files Jekyll never publishes.

**Resolved 2026-10-30 — the audit now runs at 10:00.** It was scheduled at
08:00, one hour after the 07:00 publish, which is too tight. The daily
content job commits at 08:00-08:01 on a normal day, so the check was firing
in the same minute the publish finished, and the deploy that followed took
longer than the gap. When the publish slipped — as it did on 2026-10-30,
when a stray draft wedged it and the posts committed at 08:17 — the check
read the gap as a broken site. Moving it to 10:00 puts it clear of the
publish and the deploy; the plist is backed up beside it as
`ai.sol.seo-health.plist.bak-*`.

**Also open:** `scripts/content-pipeline/logs/sol-seo-health.log` is 29 MB
over 789 runs and `logs/sol-seo.log` is 1.8 MB over 84 runs. Neither is
rotated.

## One stray draft froze the whole site again (2026-10-31)

The second occurrence of the pattern in "The workspace clone can break the
site's build" above. The site did not deploy between **07:01 and 08:19** on
2026-10-31 — every post on the site 404'd, including the three regional posts
the 07:00 job had just published.

The chain, end to end:

1. Eleven `clawhub-guide-*.md` drafts sat in `blog-posts/`, where the repo's
   own policy puts them (`_tests/test_site.py`: a draft in `_posts/` belongs in
   `blog-posts/`, which is excluded from the build).
2. Something copied them back into `_posts/` at **05:30**, with no front
   matter. Ten of the eleven had never had front matter; one had a partial
   block (title, date, tags — no `layout`, no `description`, no `hero_image`).
   These are not new files: they are the pre-2026-06-30 versions of posts that
   were published in June, fixed, and then correctly moved to `blog-posts/` on
   2026-10-30.
3. `run-daily.py` published with **no `paths=`**, which sets `stage_all` and
   runs `git add -A` over the whole working tree. The drafts were swept into
   the "Sol AI daily content: 2026-10-31" commit.
4. The CI front-matter gate refused the build. A refused build deploys
   **nothing**, so one commit's eleven drafts took every other post offline.

The gate was right to refuse. The bug is that a draft nobody intended to
publish could reach the repository at all.

**Fixed in `site_git.publish()`:** `_quarantine_drafts()` now runs before
anything is staged. Any `.md` file in `_posts/` that is not a renderable post
— no `YYYY-MM-DD-` prefix, or no front matter — is moved to `blog-posts/`,
which is excluded from the build. A copy already parked there is left alone
and the duplicate is dropped; a differing copy is parked under a free name so
nothing is overwritten. A file modified in the last 10 seconds is skipped, in
case a generator is still writing it.

So the pipeline now parks the draft instead of publishing it, and the build
cannot be taken down by a draft again. The same rule is in `seo-health.py` and
in the OG generator's idea of what a post is — all three now agree.

**Also fixed:** `_stage()` used `git add <path>`, which fails with
`pathspec did not match any files` for a path that is in neither the index
nor the working tree — so publishing a *deleted* file raised `PublishError`
instead of staging the deletion. It now uses `git add -A -- <path>`, and a
path with nothing to stage is skipped rather than treated as an error.

**Still open (not fixed here):** five scripts publish with no `paths=` and
therefore stage the entire working tree — `run-daily.py`, `quick-hits.py`,
`sols-take.py`, `tool-spotlight.py` and `weekly-roundup.py`. The quarantine
makes that safe for drafts, but it still means an unrelated stray file in the
clone can be published. Each of them should pass its own generated paths, the
way `quick-take.py`, `midweek-news.py`, `case-study.py` and the rest already
do.

## `sol-blog.py` now publishes through the pipeline (2026-10-31)

The defect in "Posts published with `sol-blog.py` skip the OG image generator"
above is fixed. `sol-blog.py` writes the post into the site clone and
publishes it with `site_git.publish()`, so it takes the publish lock, runs the
OG generator and verifies the push landed. It falls back to the raw GitHub API
if the clone is unavailable, so a post is still published either way.

Two smaller fixes in the same file:

- **YAML escaping.** `title` and `description` were interpolated into a
  double-quoted YAML scalar without escaping. A quote, a colon or a backslash
  in either produced unparseable front matter, and Jekyll then failed the
  build. It now uses the same `_yaml()` helper as the pipeline.
- **`input()` on a piped stdin.** The overwrite and delete prompts read from
  stdin, which on the `publish` path is the post body — so `input()` either
  swallowed the first line of the post or raised `EOFError` on a closed pipe.
  Both prompts now treat anything that is not an explicit "y" as "no" and
  never raise.

## The deep dive fabricated a Hacker News quote (2026-10-31)

The 2026-10-31 deep dive published this sentence, attributed to a commenter:

> As one Hacker News commenter aptly put it, "OpenAI's models are like
> Ferraris, while open-source models are more like reliable Hondas."

It does not exist. Scanning all 401 comments across the four threads the post
cited found zero matches for it, and for the Ferrari/Honda phrasing generally.

**The cause was structural, not a one-off slip.** The research step fetched
Hacker News story *titles* and URLs, and never a single comment. The writing
prompt, however, asked the model to reference the discussions "directly,
mentioning what each one argues". Given nothing to ground that in, MiniMax
invented an argument and then attributed it to a person. It also produced
"Commenters were quick to point out…" on the strength of nothing. The same
prompt defect let the post cite a dev.to article about a Prime Video charge as
evidence about model opacity, because only the article's title was passed
through and the title was then stretched to fit.

**Three changes, in `weekend-deep-dive.py`:**

1. `fetch_hn_comments()` pulls real comment text from the Algolia items API.
   `attach_comments()` runs it for every story, and the verbatim excerpts are
   handed to the model with their author names.
2. The prompt now states that the listed excerpts are the *only* comments that
   may be quoted, forbids attributing opinions that are not in front of it, and
   says to fall back to describing a discussion's title. dev.to article
   *descriptions* are passed too, so an article can be characterised by what it
   actually says.
3. `find_unsupported_quotes()` checks every quoted passage in the finished
   draft against the corpus that was actually fetched. A quote survives if any
   8-word window of it appears in the corpus, which tolerates a trimmed quote
   while catching a wholly invented one. If a draft fails, the pipeline
   regenerates once with a stricter prompt and **aborts without publishing**
   if it still fails. A missing post is better than a fabricated one.

`backfill-deep-dive.py` now regenerates any past deep dive through the same
hardened path — `python3 backfill-deep-dive.py 2026-10-31 5` — and writes the
same dated title and description the live run uses.

**The general lesson:** a prompt that asks a model to describe what a source
says, when the pipeline never fetched the source's content, is an invitation
to fabricate. Grounding is not a prompt instruction; it is what the research
step actually fetched. And any pipeline that writes prose containing quotes
should verify those quotes against the source before publishing, not after a
reader notices.

### Dating every series' title broke the deep dive's rotation (2026-10-31)

A side effect of "Every generator now dates its metadata" above. That fix
appends "(October 31, 2026)" to the deep dive's `title`, so the stored title
no longer equals the topic text in `TOPICS`.

`pick_topic()` matched published titles to topics with exact equality, so
every dated post silently dropped out of the rotation. The 2026-10-31 post was
published, then looked unpublished — and the next run would have written the
same topic a second time. All 14 published posts now match, and the next run
correctly reaches "The synthetic data problem", which had never been
published.

`_match_topic_index()` matches on prefix instead, so it tolerates the date
suffix and anything appended later. The lesson is general: a rotation keyed
on exact title strings breaks the moment anything appends to those strings,
and it breaks silently — the run still succeeds, it just repeats itself.

**Checked against the other rotating series, and they are safe.** The only
other rotation keyed on a title is `weekend-deep-dive.py` itself.
`analysis-deep.py` has the same shape — a family/topic rotation that skips
already-published topics — but it keys on a dedicated `analysis_topic:`
front-matter field, which stores the raw topic text and is never dated, so
the title change cannot reach it. `quick-hits.py`, `sols-take.py`,
`more-bloopers.py`, `case-study.py` and `run-daily.py` write to a date-derived
filename and do not compare titles at all. `seo-health.py` reads titles, but
only to report duplicates, not to rotate.

## `sols-take.py` published a second time over the first (fixed 2026-11-04)

Sol's Take is driven by BOTH a Mavis cron task (`sols-take`, `0 9 * * *`
Europe/London) and, historically, a launchd agent. When both fired on the
same day the second run generated a *fresh* body and overwrote the first —
same URL, different prose. The site's history shows it: `2026-10-24`,
`2026-10-26` and `2026-10-27` each carry two or three "Sol's Take" commits
on one day. The body that shipped was whichever run wrote last, so the
second run silently discarded the first.

Three fixes, all in the generator:

- **Idempotency before the expensive work.** The script now checks whether
  today's post already exists *before* calling the LLM, and skips. A
  template-fallback post is the one case worth regenerating, because it is
  not real content at all. This follows the rule already in this file:
  "check for an existing output before doing expensive work".
- **A run lock.** `flock` on `/tmp/sol-sols-take.lock`, so two schedulers
  cannot both pass the existence check before either writes.
- **`paths=` on `publish()`.** The old code called `publish(msg, cwd=cwd)`
  with no `paths`, which stages the *entire* working tree — so this job's
  commit could carry another job's half-finished files. It now stages only
  its own post (and, via `publish()`, that post's OG image and the manifest).

`--date YYYY-MM-DD` publishes for a specific day, which is how a day the
scheduler missed gets backfilled. The topic rotation is keyed on the day of
year of the *target* date, so a backfilled day gets the topic its rotation
intended rather than today's.

## Three days of Sol's Take went missing because the Mac was asleep

2026-11-01, 11-02 and 11-03 had no posts of any kind. The machine was
powered off or asleep — `pmset -g log` has no entries at all for 11-01 and
11-02, and the boot time was Nov 3 13:58. The MiniMax Code app (which hosts
the cron scheduler) started at Nov 3 13:59:42, and its cron tasks for
09:00 that day had already been missed. The launchd agents with
`RunAtLoad: true` fired on boot and caught up; the MiniMax cron tasks did
not.

So the two schedulers fail in different ways, and the site needs both:

- launchd agents survive sleep (they run at next boot), but they do not
  know which series they own, so they cannot backfill a *past* day.
- MiniMax cron tasks need the app running at the scheduled minute, and they
  silently skip a day that was missed.

Neither one backfills a gap on its own. The `--date` flag above is the
missing half for Sol's Take: once the machine is awake, the missed days can
be regenerated deterministically. Nov 1, 2 and 3 were backfilled that way.

The general lesson: **a scheduler that only ever runs "now" cannot recover a
missed day.** Any series that can legitimately be skipped needs a dated
entry point so the gap is fillable after the fact.

## The 150-200 word brief was never enforced in code

Published Sol's Take bodies ranged from 187 to 248 words against a 150-200
brief — the prompt alone does not hold a word count. `sols-take.py` now
enforces the ceiling with `trim_to_words()` from `text_utils.py`, the same
helper `weekly-roundup.py` and `midweek-news.py` use, so the trim cuts at
sentence boundaries and never mid-sentence. `run-daily.py` still has no
equivalent and its published lengths still drift (see the note above).

## The ceiling was not a ceiling, and there was no floor (2026-11-10)

The section above added a ceiling to the 150-200 word brief. Two defects
survived that fix, and both were found by measuring the posts rather than
by reading the code.

**The ceiling could be overrun.** `trim_to_words()` guarded each sentence
with:

```python
if count + words > limit and kept:
    break
```

The `and kept` made the limit conditional on the paragraph already holding
a sentence. For a paragraph's FIRST sentence `kept` is empty, so the check
never fired and the sentence was kept even when it pushed the body past
the limit. The 2026-11-07 post shipped at **206 words against a 200-word
brief** — and was only noticed because it also carried contamination and
needed a repair commit, which is how the length came to light.

The first sentence is now subject to the same limit as every other one.
When it does not fit, the trim stops at the paragraph boundary rather
than skipping ahead: skipping would leave a hole in the middle of the
piece, and the body would no longer read as written.

**There was no floor at all.** Only the ceiling was enforced, so a model
that returned 90 words produced a 90-word post and nothing flagged it. A
word budget needs BOTH ends — the floor stops a thin post, the ceiling
stops a sprawling one. `sols-take.py` now enforces `MIN_WORDS = 150` and
`MAX_WORDS = 200` as constants and checks the trimmed body against both.

**A draft outside the band is regenerated, not published.** The prompt
asks for 150-200 words, but a prompt asks; it does not constrain. A
draft outside the band is regenerated once with the length stated as a
hard requirement, and if the second attempt also misses the band the run
aborts without publishing. A missing post is visible; a post outside its
own brief is not.

**The trim was eating the punchline.** The prompt asks the piece to "End
with one sharp sentence", and a plain trim cuts from the END of the body —
so the closing line was the first thing it lost. The closing paragraph is
now held back and the rest of the body is trimmed to whatever budget is
left over. The ceiling still wins: in the rare case where a single
sentence is longer than the whole budget, the closing paragraph gives up
its protection instead.

**A body that is one unpunctuated fragment is no longer emptied.** The
mid-sentence guard drops a trailing fragment (the model was cut off by
the token limit). When the body IS that fragment, dropping it left
nothing to publish. The fragment is now only dropped when something else
is left.

Regression tests live in `test_word_budget.py` (10 checks, runnable
directly or under pytest) and pin all four behaviours.

The general lesson: **a ceiling is only a ceiling if the code can
actually exceed it.** The guard had a conditional clause in it, and every
published post was checked against a limit the trimmer did not really
enforce.

## Two days of Sol's Take went missing because the Mac was asleep (2026-11-10)

Nov 8 and Nov 9 had no posts. The machine booted at Nov 9 15:21 UTC
(`kern.boottime`), after the 09:00 slot on both days, and the MiniMax
Code app that hosts the cron scheduler was not running at 09:00 to
publish on either. Nov 10's run published normally.

This is the same failure as Nov 1-3 (see above), and the same fix
applied: `sols-take.py --date YYYY-MM-DD` regenerates a missed day, and
the topic rotation is keyed on the day-of-year of the *target* date, so a
backfilled day gets the topic its rotation intended. Both days were
backfilled this way and landed at 188 and 194 words.

The general lesson repeats, and it is worth restating because it has now
happened twice: **a scheduler that only ever runs "now" cannot recover a
missed day.** The dated entry point is not a convenience — it is the only
thing that makes a gap fillable.

## `sols-take.py` could not be imported under Python 3.9

The module used a PEP 604 union (`str | None`) in a return annotation with
no `from __future__ import annotations`. Python evaluates annotations at
def-time, so under `/usr/bin/python3` (3.9.6) the module raised
`TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'` on
import — before any of its work could start. The future import makes the
annotations strings, and the module now imports under 3.9.6, 3.13 and 3.15.
`_minimax_helper.py` already carried this guard for the same reason.

This only ever bit under a scheduler that pinned `/usr/bin/python3`. The
cron runs `python3` from PATH, which resolves to a 3.13 interpreter here —
but `install-cron.sh` writes `/usr/bin/python3` into the crontab, so the
crash was one config change away.

## The model answered with a heading and a byline instead of the article

Two of the three backfilled Sol's Take posts (2026-11-01 and 11-02) shipped
with the model's own framing at the top of the body:

```
## Sol's Take: AI's Greatest Trick Is Making Everything Sound Important

**November 02, 2026 | Sol**
```

The layout renders the post title as an `<h1>`, so a `## Sol's Take` heading
duplicates it, and the byline is front-matter metadata showing up as body
text. Both posts were repaired after publication.

`sols-take.py` now strips a leading echoed heading and/or byline before the
body is written. This is the same defect `run-daily.py` fixes with
`strip_metadata_echo()`, and the same rule: **the prompt is a request; the
content has to hold whatever the model returns.** Only a *leading* heading or
byline is removed, so the phrase used in the prose of an article is left
alone, and a `## Sol's Take` heading in the middle of a piece survives.

The prompt is also now explicit that the body must start with the opinion,
not with a heading.

Note the interaction with the word-budget trim: both run on the generated
text, and the strip happens first so the trim counts the words that will
actually be published.

## The model answered with a bold label instead of the article

The heading fix above only knew one shape. The model also answers with the
same framing in bold:

```
**Sol's Take: The Context Window Size Wars Are a Fool's Errand**
**Sol's Take**
```

The stripper matched `## Sol's Take` but not the bold form, so the label
shipped as a stray bold line under the `<h1>`. It was on **54 published
posts**, from 2026-06-30 onward — and on the 2026-11-06 post, which is how
it was found: the run looked fine, the post was on the site, and the body
just started with `**Sol's Take**`.

The trap is that the label is not always junk. On most of those 54 posts the
front matter title is the generic `Sol's Take: <weekday>` and the label
carries the **only headline the article has**:

```
title: "Sol's Take: Wednesday"
body:  **Sol's Take: The Great AI Agent Sham**
```

Deleting that line would leave the post with no title at all. So the rule is
now:

- a bare label (`**Sol's Take**`) is removed — the `<h1>` already says it
- a label carrying a date (`**Sol's Take: October 30, 2026**`) is removed —
  a date is a byline, not a headline, and the front matter has the real date
- a label carrying a headline keeps that headline, as a bold dek, with the
  redundant `Sol's Take:` prefix dropped

Front matter is never touched by this. Titles and descriptions are a separate
question (the duplicate-title audit), and a body repair must not silently
change them.

`sols-take-label-repair.py` applies the same function to the 54 published
posts. It is dry-run by default, `--apply` writes, and it refuses any repair
that would drop more than the label's own words. All 54 were repaired and
published in `ac76e03d`.

The lesson from both this and the heading bug: **the prompt is a request; the
content has to hold whatever the model returns.** The prompt now says not to
write a title, heading, byline or label of any kind, but the stripper is what
actually guarantees it.

## `case-study.py` republished a case under a new URL (fixed 2026-11-06)

The weekly case study cycles a bank of five cases, one per Friday, so the
same case comes back every five weeks. The body was identical every time and
the only thing that changed was the date in the title and the description —
which is enough for a search engine to treat each run as its own page.

The 10:00 audit caught the first recurrence: the 2026-11-06 post repeated the
2026-10-02 case with no `canonical:`, so the audit reported it as a
"republication of an existing post and carries no canonical".

Two changes:

- **The generator now writes the canonical itself.** `_canonical_for()` finds
  the earliest post that already ran the same case and points the new post at
  it — the original publication, which is exactly what the audit computes as
  its target. A first-time case still gets no canonical, so the layout falls
  back to the post's own URL.
- **The 2026-11-06 post was given the canonical by hand** and published
  through `site_git.publish()` (`33637edd`). The live page now serves
  `canonical: /blog/2026/10/02/case-study-ai-for-quality-control-on-a-factory-line/`.

`seo-repair-canonicals.py --dry-run` now reports `Need a canonical: 0`, and
every Friday from here on writes its own.

The 20 issues the audit still reports are the historical duplicate-title and
duplicate-description groups, all published before the 2026-10-31 generator
fix. No member of any of those groups is dated 2026-11-01 or later, and the
repair script does not touch them: it repairs republications, not metadata.
They are residue, and the rule is still **fix the generator, never backfill
the posts**.

## The model shipped its own notes with the article (fixed 2026-11-07)

The 2026-11-07 Sol's Take published with the model's scratchpad stapled to the
end of the article:

```
Calling AI "not creative" because it has no consciousness confuses creativity
with a séance. ...

AI can be creative. The dangerous delusion is that anyone using it deserves
the credit.

Word count maybe 169. Good. Need date maybe no. "specific experience/pattern"
claim grounded in every week maybe lacking. Real pattern noticed: meetings
"AI-generated ideas" etc.

Let's formulate.</[U+200B]mm:think>Calling AI "not creative" because it lacks
consciousness confuses creativity with a séance.
```

The article itself was good — 167 words, the right take, in voice. The last
three lines were the model thinking out loud about the draft it had just
written, and the very last line was a *duplicate* of the opening sentence.

The run looked clean. MiniMax returned 420 words, the trim took it to 206, the
post was written, committed and pushed — and nothing anywhere said the body
was broken. The 206 was itself a symptom: the trim counts words, and by then
the scratchpad was part of the body.

Three gaps let it through, and all three are in `_minimax_helper.py`:

1. **`strip_thinking()` only handled reasoning that arrived BEFORE the
   article.** It looked for a `Thinking...` preamble and, when there was none,
   returned the text untouched (`if start is None: return text.strip()`).
   Here the article came first and the reasoning followed it, so there was no
   preamble to find and the whole body was kept.
2. **The closing tag was malformed.** MiniMax emitted `</[U+200B]mm:think>` —
   with a zero-width space inside the tag — so the `<think>.*?</think>` regex
   never matched it and the tag shipped as literal text.
3. **Nothing refused to publish tainted output.** There was no gate: a body
   could carry the model's notes and still be written, committed and pushed.

The fixes:

- `strip_thinking()` now strips zero-width and invisible characters first,
  cuts everything from a stray (unopened) think tag onward, and drops
  trailing paragraphs that are unmistakably the model drafting its own
  instructions (`Word count maybe…`, `Let's formulate.`,
  `Real pattern noticed…`).
- `looks_contaminated()` is the gate. `minimax_generate()` retries a
  contaminated generation instead of returning it, and prints the reason if
  every model and key is exhausted.
- `sols-take.py` refuses to publish a template-fallback post at all. It exits
  non-zero with a `DEGRADED:` line instead, and treats a contaminated existing
  post as degraded, so it gets regenerated rather than skipped.

The filters are deliberately conservative: only *trailing* paragraphs are
dropped, and only when they are unmistakably meta. "Wait, the real problem
is…", "Note: …" and "Let's write a better ending." are left alone — a
legitimate sentence is not a drafting note.

The 2026-11-07 post was repaired and republished in `330960ad`: the scratchpad
and the duplicate are gone, the 167-word article is intact, and the live page
serves the clean body. A scan of all 1,299 published posts found no other
contamination.

`test_minimax_helper.py` pins all of this and needs no test framework:

```
python3 scripts/content-pipeline/test_minimax_helper.py
```

The lesson repeats the one from the heading and bold-label bugs above: **the
prompt is a request; the content has to hold whatever the model returns.** A
prompt that says "no headings, no bylines" did not stop the model writing
both. A prompt that says "150-200 words" did not stop it shipping 420. The
guarantee has to be a gate in code — and the gate has to know what a broken
body looks like, which is why the cleaner now carries a named list of the
shapes the model actually produces.

## The SEO audit told you to canonical away real articles (fixed 2026-11-07)

The 10:00 daily audit failed with 20 issues, all from check [5] (duplicate
titles / meta descriptions): 246 posts sharing a title and 249 sharing a
description. The message said to add a canonical to consolidate them.

**That remedy would have de-indexed real articles.** Every one of those
groups has distinct bodies. They are separate articles that happened to
carry the same headline, not one article at two URLs. A canonical tells a
search engine to keep one URL and drop the others — pointing 246 distinct
articles at a single one would have thrown away the site's back catalogue.

`seo-repair-canonicals.py --dry-run` already knew the difference: of 1,299
scanned posts, **0** need a canonical. The 76 "regional families" (159
posts) are consolidated by design. The check was reporting a defect the
repair script had already ruled out.

Two different things were hiding behind one shared title:

- A **republication** — same body, two URLs, no canonical. That is what a
  canonical is actually for, and `duplicate_offenders()` already reports
  it (check [4]).
- A **template collision** — different bodies, same headline, because the
  generator used a fixed title. The fix is a unique title per post.

The check was also counting a post that *is* a canonical **target** as an
offender. The target is the surviving URL; it is supposed to carry no
canonical of its own. 87 canonicals point at a post with a different body
(85 match the documented rule — same source + region + day; 5 are the
`trending-topics` republication case).

So check [5] now:

- consolidates a post that carries a canonical **or** is the target of one;
- splits every group by age. The generators began dating each title and
  description on **2026-10-31** (commit `7439af4`, 13 generator files). A
  collision that includes a post published on or after that date means a
  generator has regressed and is publishing a fixed title again — **that
  is an error**. A collision published before it is residue from the old
  templates — reported as an ℹ️ note, never as an error.

The residue is a note because there is no repair that does not involve
rewriting published titles, and rewriting a title changes a URL search
engines have already indexed. It stays visible so the count cannot creep
up unnoticed, and it ages out on its own.

`test_seo_duplicate_metadata.py` pins all of this and needs no test
framework:

```
python3 scripts/content-pipeline/test_seo_duplicate_metadata.py
```

It covers residue-not-error, a post-fix title regression, a post-fix
description regression, a canonical target not being an offender, and
distinct titles never reporting. 5/5 pass.

**A check that never fails proves nothing.** The rewritten check was
verified to actually fail by moving the cutoff to `2026-01-01` and
re-running it against the real site: 20 issues, exactly as before. The
cutoff was then restored to `2026-10-31`.

The general lesson: **a check that names a remedy has to verify that the
remedy is safe.** The check and the repair script disagreed about the
same data — "246 posts need a canonical" against "0 posts need a
canonical" — and the louder one was the wrong one. When two pieces of
tooling disagree, the more confident one is not the more correct one.

## A series stopped publishing for twelve days and nothing noticed (2026-11-07)

The Monday weekly roundup has not published since **2026-10-26**. The
2026-11-02 roundup does not exist — there is no
`2026-11-02-week-in-ai-nov-2026.md` in `_posts/`, and no November
roundup of any kind. One missed Monday, twelve days of silence, no alert.

Two schedulers own this series and both missed it:

- `ai.sol.weekly-roundup` (launchd, Monday 07:00) has **`RunAtLoad:
  false`**. The Mac was powered off on Nov 2 (boot time Nov 3 13:58), so
  the agent never ran — and because it does not run at load, launchd
  cannot recover the missed Monday. `ai.sol.midweek-news` is the same.
  The daily series have `RunAtLoad: true` and do catch up on boot;
  `ai.sol.blog-cron` and `ai.sol.daily-briefing` are also `false`.
- The `sol-weekly-roundup` cron task (Monday 08:00 Europe/London) needs
  the MiniMax Code app running at the scheduled minute. The app started
  Nov 3 13:59:42 — after the slot had passed.

`logs/sol-weekly.log` confirms it: the last entry is from Oct 26 07:01,
and `sol-weekly.err` is empty. The agent did not crash. It never ran.

**`RunAtLoad: false` is correct here, and flipping it would be a bug.**
A launchd agent that runs at load publishes a roundup for *today* —
Tuesday, not the Monday that was missed — and both the slug and the title
come from `now()`, so a catch-up run would publish a mis-dated duplicate.
A weekly series cannot be recovered by re-running it "now"; it needs a
dated entry point, and `weekly-roundup.py` has no `--date` flag. A missed
Monday is currently unfillable. (For a *daily* series "now" is the right
date, which is why those agents can safely catch up.)

**The watchdog could not have caught this.**
`~/.openclaw/workspace/scripts/site-cron-watchdog.sh` checks that every
`ai.sol.*` agent is *loaded* and reloads the ones that are not. It never
checks whether any of them produced a post. The agent was loaded, so the
watchdog reported "all agents loaded" on its ten-minute tick for twelve
days while the series was dead. `cron-health-check.py` reported "OK: No
cron failures detected" over the same period.

**The residue has a root cause too.** The roundup was published *daily*
from 2026-10-11 to 2026-10-23 — 12 posts in thirteen days — because the
plists were temporarily set to a daily cadence. `ai.sol.weekly-roundup`
and `ai.sol.midweek-news` were both edited on Oct 23 17:14 to restore the
weekly cadence, and the `ai-digest` series shows the identical pattern
(daily Oct 11–23, then Wednesdays on Oct 28 and Nov 4). Every post in
that burst carried the title "The Week in AI: October 2026" and the same
description, which is where a large share of the 246 shared-title posts
came from. The generator was dated on Oct 26 (`ROUNDUP_TITLE = f"The Week
in AI: {POST_DATE}"`), so the next run on Monday Nov 9 will be unique.

Two things are still open, and both are small:

1. `weekly-roundup.py` needs a `--date` flag that sets the post date
   **and** the HN window — the fetch currently looks back from `now()`, so
   a backfill would pull the wrong week's stories.
2. The watchdog needs an output check, not just a presence check: for
   each series, the newest matching post in `_posts/` compared against the
   cadence in that agent's plist.

Also in the watchdog: the success branch prints `$LOADED_COUNT`, which is
never assigned anywhere in the script, so it reports "all agents loaded"
with a blank count.

## The AI story filter matches "ai" inside "Airplanes" (found 2026-11-10)

`is_ai_story()` in `run-daily.py` (line 110) is a substring test:
`return any(kw in t for kw in keywords)` against the lowercased title. The
keyword list contains `"ai"`, which is not a word — it is a letter pair that
appears inside ordinary English words.

On **2026-11-10** the day's story was "Foldable Flight: The Best Paper
Airplanes" (952 HN points). That is a website of paper-airplane diagrams, not
an AI development. It was selected because **"Airplanes" contains "ai"**. The
same defect matches "chained", "maintainers", "email", "available" and more.

The filter is wrong in the other direction too. "Understanding Transformers
from scratch" returns **False** — "transformers" is not in the keyword list —
so a genuine AI story was rejected while a paper-airplane site was accepted.

**Nothing false shipped.** The anti-fabrication rules in the prompt saved it:
the model noticed the source never claimed AI and wrote the angle honestly —
"Foldable Flight is not an AI development, and its 952-point Hacker News score
does not make it one" — and the UK title was literally "The AI Story That
Isn't". All three regional posts (UK, EU, US) said so in their first
paragraph. The defect cost the series its intended subject, not its accuracy.

**The fix is word-boundary matching, not a longer keyword list.**
`re.search(rf"\b{re.escape(kw)}\b", t)` for each keyword, plus adding
"transformers", "inference", "diffusion", "agentic" and "training" to cover
the false negatives. A longer list alone would not help: "ai" would still
match inside "Airplanes", and a list cannot match every AI story anyway.

The filter's real weakness is that it is the only thing standing between the
pipeline and a non-AI headline, and it is untested. A table-driven test over
the ten highest-scoring HN titles of the day would catch both directions.

**Fixed 2026-11-10.** The filter now lives in `ai_filter.py`, shared by
`quick-hits.py` and `run-daily.py` (both had an inline copy of the same
substring test), and `test_ai_filter.py` is the table-driven test this
entry asked for — real titles, both directions, including the two that
actually shipped.

Two things were true of the fix that the note above did not anticipate:

- The optional plural/gerund matters. Matching "LLMs" and "embeddings" is
  safe; matching "Rags" and "bolts" is not, so the suffix is allowed only
  for terms where the inflected form is still unambiguously the AI term.
- A word-boundary filter still admits LOW-RANKING false positives — a
  title like "Make Jev-like decision models up to 9.5x faster" matches on
  bare "models" and is not an AI story. That is acceptable because the
  filter runs before the stories are ranked and only the top five by
  engagement reach the model as candidates. Measured over four real days
  (2026-11-07 to 11-10), every one of those top-five windows contained
  only genuine AI stories. The defect that mattered was a false positive
  that RANKED FIRST.

## Two briefs shipped "Payments Canada launches Canada's Real-Time Rail"

**Found and fixed 2026-11-10.** The 2026-11-09 and 2026-11-10 Quick Hits
briefs each carried "Payments Canada launches Canada's Real-Time Rail" as
one of their three stories. It is a story about payment infrastructure, not
AI. It matched the filter because "R**ai**l" contains "ai".

Both posts were published and live before this was noticed. The Nov 10 post
was caught by reading it; the Nov 9 post had been missed by the scheduler
entirely and was only seen because the backfill that replaced it was
checked against the filter.

The regional series was NOT affected: `run-daily.py` had the same
substring filter, but the anti-fabrication rules in the regional prompt
caught the "Paper Airplanes" story (the top story of 2026-11-09, 952
points) and all three regional posts said so in their first paragraph. No
regional post ever carried a wrong story.

## The filter fix reached two scripts short of the pipeline (found 2026-11-11)

**Found and fixed 2026-11-11.** The entry above says the filter now lives in
`ai_filter.py`, shared by `quick-hits.py` and `run-daily.py`. It was shared
by THOSE TWO ONLY. `midweek-news.py` and `weekly-roundup.py` each carried
their own private inline copy of the substring test, and kept it.

That is the same defect class, in the same pipeline, in the two scripts that
publish the site's two longest-form series. It had shipped:

- **The 2026-11-09 roundup** carried "Outlook.com is rejecting email from
  domains without DMARC" and "RAM Altman: Linux Script Emails Sam Altman
  Daily How Much Unused RAM in Your PC" as AI stories. Both matched on
  "em**ai**l".
- **The 2026-11-11 midweek digest** carried "Nvidia CEO Jensen Huang's
  letter to staff about affair" as its TOP story — a CEO's affair, matched
  on "aff**ai**r".

Measured across the 23 roundups that had shipped by 2026-11-11, **every
single one carried at least one non-AI story.** The two series that were
migrated on 2026-11-10 were the two that had already been fixed by that
night's run; the two that were missed were the two nobody re-read.

**Why a shared module was not enough.** The fix on 2026-11-10 moved the
filter into `ai_filter.py` and pointed two scripts at it. That is the right
shape, but nothing made the OTHER scripts point at it too — and a private
copy of a fixed bug is invisible: the code reads fine, the tests pass, and
the defect ships. `test_ai_filter.py` now imports every consumer and
asserts it rejects a substring-only title, so a script that grows its own
copy again fails the suite rather than shipping.

## The dedupe could not see two thirds of the archive (found 2026-11-11)

**Found and fixed 2026-11-11.** `published_source_urls()` answers "has the
archive already covered this story?" for every generator, and it reads the
source URLs out of published posts. It read only two formats:

```
**Source story:** [title](url)
*Source: [title](url)
```

The pipeline writes stories in two more formats, and neither was read:

- the weekly roundup writes `[Read more →](url) — N points · N comments`;
- an analysis or deep-dive post writes a `## Sources` section of bullets.

The dedupe set held **143 URLs out of the 405 the archive contained.** It
saw none of the 161 story URLs in the 23 shipped roundups, and none of the
124 in the analysis posts' Sources sections.

The consequence was visible on the live site: the 2026-11-11 midweek digest
republished "Goldman Sachs is pulling Claude Code from its bankers and
traders" (the Monday roundup had carried it on 2026-11-09) and "Claude
Fable 5.2 and Mythos 5.2" (the Nov-10 Australia post had cited it). It was
the only midweek digest in the site's history to overlap a prior roundup —
**1 of 24** — so the overlap is the defect, not the design.

`source_urls()` now reads all three formats. The singular `source_url()` is
deliberately left alone: `seo-health.py` keys its republication families on
that one value, and widening it would change which posts the audit groups
together. Only bullets under a heading whose text is exactly "Sources" are
counted, so a how-to post's "## Additional Resources" documentation links
and a link roundup's "## Links from AgentNews#23" section are not mistaken
for covered stories.

`test_post_identity_dedupe.py` is the table-driven test for all of it.

## A refresh could not keep its own stories, and one story was listed twice

**Found and fixed 2026-11-11.** Two defects, both in `midweek-news.py`, both
found while regenerating the digest above.

**A refresh swapped the stories underneath the post.** `--refresh` rewrites
today's post in place, so the post on disk already contributes its three URLs
to the covered set — and the dedupe then forbids them. Refreshing a post
whose commentary needed regenerating silently swapped the stories for
different ones.

The first attempt at fixing this subtracted the post's URLs from the shared
covered set. **That was wrong, and it shipped once before it was caught:**
it also freed any story that ANOTHER post covers, so the refresh republished
"Claude Fable 5.2 and Mythos 5.2" and the Goldman Sachs story — both
already on the site under other posts. The correct fix excludes the post's
FILE from the covered set, which leaves every other post's coverage intact.
`published_source_urls_excluding()` does that.

**The same story was listed twice.** The Algolia index returns one row per
Hacker News submission, and a story can be submitted more than once. On
2026-11-11, "Claude Fable 5.2 and Mythos 5.2" appeared twice — 992 points
and 891 points, both pointing at the same URL. The digest listed it as
stories 2 and 3 and asked the model to comment on "3 stories" when only 2
existed. `select_stories()` now dedupes its own candidates on the same URL
key the archive uses, keeping the higher-scoring submission.

Both generators now use the shared word-boundary filter, and the two
affected briefs were regenerated with `--force`.

## Quick Hits missed a day because the desktop app was not awake

**Found 2026-11-10.** The 2026-11-09 episode was never published. The
MiniMax cron task needs the desktop app running at 08:00; the app started
at 19:10 that evening, so the Monday slot was silently skipped. The cron
run history confirms it: there is no run for 2026-11-09 at all, while the
launchd-driven series (`daily-content`, `weekly-roundup`, `deep-dive`,
`skills-update`) all published that morning.

This is the same failure documented above in "Three days of Sol's Take
went missing because the Mac was asleep", and it has the same two-part
fix.

**A dated entry point.** `quick-hits.py` now takes `--date YYYY-MM-DD`,
like `sols-take.py`. A backfill reads that day's Hacker News stories
through the Algolia index (`created_at_i` between the start of that day
and the start of the next) and that day's dev.to articles through
`published_after`/`published_before`. The live front page would otherwise
supply the WRONG day's news — a post dated Nov 9 describing Nov 10 is
worse than no post. The dev.to side matters too: `top=1` only ever means
"trending now".

**A launchd agent.** `ai.sol.quick-hits` runs the script at 07:00 local,
Monday to Friday, with `RunAtLoad: true`. `RunAtLoad` is correct for this
series and NOT for the weekly ones: the Quick Hits slug and title come from
the day the run happens, so "now" is always the right date. A catch-up
run after a reboot publishes today's briefing rather than a mis-dated
copy of last week's. (The weekly roundup and the midweek digest derive
their date from the week they cover, so they stay on `RunAtLoad: false`.)

The launchd agent fires an hour BEFORE the cron task, deliberately — two
schedulers in the same minute would race, and the run lock would make one
of them skip for the wrong reason. Whichever runs first publishes; the
other finds the post already there and skips. The script now has the
run lock and the idempotency check that make this safe, and the Mavis cron
prompt says so, so a skip is not mistaken for a fault.

**The general rule:** a series that can legitimately be skipped needs
BOTH a dated entry point and a second scheduler. Neither alone is enough:
launchd does not know which series it owns, so it cannot backfill a past
day, and a cron task that only ever runs "now" cannot recover a missed one.

## A committed post is not a published post

**Found 2026-11-10.** The 2026-11-10 brief committed and pushed
successfully, and `quick-hits.py` logged "Committed and pushed to
origin/main". The GitHub Pages deploy then timed out —
`deploy-pages@v4` sat in "syncing_files" until "Timeout reached,
aborting!", and the deployment was cancelled. The post was 404 on the live
site while the log read as a clean success.

The Jekyll build itself had succeeded; only the deploy step failed. A
re-run of the failed job deployed it.

Two guards now exist. The cron prompt no longer treats the script's exit
as proof of publication: it checks the live URL and, if it still 404s,
looks at the deploy workflow and re-runs the failed job. And the script
logs what it actually did, so a skip (already published) is
distinguishable from a publish.

This is the same lesson as "a watchdog that checks presence is not a
watchdog — check the output": the push is not the publication, and the
deployment is not the push.

## The Pages deploy hung again — three runs, one root cause (2026-11-10)

The three Sol's Take posts published on 2026-11-10 (the scheduled post
plus the Nov 8 and Nov 9 backfills) all committed and pushed cleanly, and
all three were 404 on the live site for the whole morning. The deploy
workflow failed three times in a row with the SAME symptom as the
Quick Hits incident above:

```
Current status: syncing_files      (repeated for ~10 minutes)
##[error]Timeout reached, aborting!
```

The runs: "Sol's Take: Tuesday" failed after 701s, "Sol's Take: Sunday"
was cancelled by the concurrency group, and "Sol's Take: Monday" — the
one carrying the newest commit and therefore all three posts — failed
after 1407s. Every one of them hung in `actions/deploy-pages@v4`, never
in the Jekyll build. The validate job passed, the bundle installed, the
site built, the artifact uploaded, and then the deploy step sat in
`syncing_files` until GitHub gave up on it.

Three consecutive failures of the same step is a property of the
environment, not bad luck. `deploy-pages@v4` polls the Pages deployment
until it reports a terminal state, and this repository's deploys are
hitting the timeout repeatedly. The morning's successful runs (80-180s,
and one at 915s) show the step CAN complete, so it is intermittent and
possibly load-related rather than a permanent breakage.

**What worked:** `gh run rerun <run-id> --failed`. The re-run of the
Monday run (attempt 2) is the one that carries the newest commit, so it
publishes all three posts at once. Re-running the *failed job* rather
than the whole workflow matters here — the concurrency group
(`group: pages`, `cancel-in-progress: false`) keeps at most one pending
run, so a burst of pushes collapses to "the newest commit is queued".

**Two things that were worth checking and were NOT the cause:**

- A slow `bundle install` (the 08:01 run took 915s) looks like a slow
  deploy but is not one. The failing runs all reached `deploy-pages` in
  well under 180s.
- The live site was serving fine throughout — an older post returned
  HTTP 200 the whole time. "The site is up" and "my post is published"
  are different questions.

The general lesson extends the one above: **a push is not a publish, a
publish is not a deploy, and a deploy is not a live page.** Four
distinct states, each of which can fail on its own while the others
succeed. Checking the live URL is the only check that covers the last
one, because it is the only state a reader can observe.


## The weekly roundup had no run lock, and its dev.to key is an empty string (2026-11-16)

Two gaps in `weekly-roundup.py`, found while running the 2026-11-16 roundup.
Neither stopped the post from publishing; both are latent.

**1. No `flock`, unlike every other two-scheduler series.** The roundup is
driven by TWO schedulers (`ai.sol.weekly-roundup`, launchd Monday 07:00,
and `sol-weekly-roundup`, cron `0 8 * * 1`). `quick-hits.py`,
`sols-take.py` and `run-daily.py` all take an `flock`; the roundup did
not. Its `filepath.exists()` check stops the SECOND run from
republishing, but it does not stop two runs that start at the same
moment: both can pass the existence check before either writes, both burn
an LLM call, and the second write silently overwrites the first body.
The rule stated earlier in this file — "a series driven by BOTH
schedulers needs a run lock as well as the existence check" — was not
applied to this series.

Fixed: `run_lock()` on `/tmp/sol-weekly-roundup.lock`, same shape as
`quick-hits.py`. The lock is taken BEFORE the HN fetch, so a losing run
costs nothing. Verified: with the lock held by another process the
script prints "Another run holds the lock — skipping" and exits 0 without
fetching or writing; with the lock free it still reaches the
already-published skip path.

**2. `DEVTO_API_KEY` is an empty string in the launchd plist, so the
roundup's own dev.to cross-post has been a no-op.** The plist sets the
key to `""`. The real key lives in the canonical secret file
(`~/.openclaw/workspace/secrets/devto-api-key.txt`), which every other
script reads through `devto_key.load_devto_key()`. The roundup reads
`os.getenv("DEVTO_API_KEY", "")` directly, so it never sees the key and
prints "[dev.to] No API key — skipping cross-post".

**This is harmless as it stands, and "fixing" it would make things
worse.** `devto-poster.py` is the canonical publisher for dev.to and it
cross-posts the roundup anyway — the tracker holds
`the-week-in-ai-november-9-2026-4a5i` and
`the-week-in-ai-november-16-2026-4a12`, and both are live on dev.to
with the stories in the body. Giving the roundup a working key would
create a SECOND writer for the same shared tracker, which is the exact
defect class recorded under "a shared tracker needs one writer, one
atomic write, and a pager" — two writers, two write conventions, and a
duplicate-post window. **Do not point the roundup at the dev.to API
until it shares the tracker's atomic write path.**

**Also noted:** the roundup's own dev.to step is redundant rather than
broken. If it is ever retired, nothing changes — `devto-poster.py`
already covers the series.

**One stale detail in the cron task description:** the task text says the
roundup "picks the 5 most significant" stories, but the script selects
`ai_stories[:7]` and the 2026-11-16 post contains 7 stories. The script
is the source of truth; the description is out of date. The post's
"7 stories that mattered" line is generated from `len(top_stories)`, so
it stays correct either way.

## The two daily-content schedulers write to two different log files (2026-11-16)

`run-daily.py` runs twice a day, and the two runs leave their output in
**different places**:

| Scheduler | When | Log file |
|---|---|---|
| `ai.sol.daily-content` (launchd, `RunAtLoad: true`) | 07:00 | `scripts/content-pipeline/logs/sol-content.log` |
| `sol-daily-content` (Mavis cron, `0 8 * * *`) | 08:00 | `logs/sol-content.log` (repo root) |

The difference is the working directory each job runs in: the launchd
agent sets `WorkingDirectory` to `scripts/content-pipeline/`, and the cron
task's command redirects to `logs/sol-content.log` relative to
`/Users/amre/Projects/sol-skills-bundle`. Same script, same repo, two
logs.

**The repo-root log is also a shared log across three different scripts.**
`sol-daily-content`, `sol-weekly-roundup` (Mon 08:00) and `sol-midweek-news`
(Wed 08:00) all append to `logs/sol-content.log`. To attribute a block to
`run-daily.py`, match on its banner line
`Sol AI Daily Content Pipeline — YYYY-MM-DD`, not on position in the file.

**Coverage: read the launchd log, not the cron log.** Checked 2026-11-16,
the launchd log has a run for **every** day from Nov 5 to Nov 16 with no
gaps. The cron log is missing Nov 8, Nov 9, Nov 13, Nov 14 and Nov 15 —
the MiniMax Code app was not awake, so the Mavis cron never fired. Same
failure mode as the SEO audit's cron half: **the Mavis cron is the
unreliable half and launchd is what actually guarantees daily coverage.**
Reading the cron log alone and concluding "the pipeline did not run on
Nov 14" is wrong — launchd ran it at 07:00 and published.

**The 08:00 cron run appends a full no-op block every time.** It fetches
HN, skips all three regions ("Already published today"), writes a bloopers
skip line, and pushes nothing. That is correct behaviour, but it makes the
root log grow with duplicate-looking entries. Do not read a second
"Pipeline complete! / Generated: 0 regional posts" block as a failure.

**Where the real work happens:** the launchd 07:00 run generates and
publishes the day's three regional posts. The 08:00 cron run is the one
that fires this agent session, so it always arrives *after* the day's
posts exist and always skips. The useful work for the 08:00 session is
therefore **verification**, not generation: confirm the three regional
permalinks return 200 with the expected titles, confirm the Pages deploy
run succeeded, and confirm the dev.to cross-posts exist. See "A committed
post is not a published post" above.

## A model that repeats a sentence passed every check (2026-11-16)

The 2026-11-16 Sol's Take shipped with this in its body:

    Now, synthetic data is not poison. Synthetic data is not poison.
    Synthetic data is not poison.

Nothing caught it. The word budget was satisfied **by the repeats** (196
words, inside the 150-200 band); `looks_contaminated()` passed; the front
matter was clean; the metadata strip found no echoed heading. Every gate the
pipeline had said yes. The reader saw a model that had looped.

The fix is in `text_utils.py`, shared by the generators that need it:

- `find_repeated_sentences()` reports every sentence that appears more than
  once, with the count.
- `collapse_repeated_sentences()` removes the later copies, but only when the
  drop is safe: the sentence stands alone, and the drop does not change the
  number of double quotes.

**The exact-match version was not enough.** The first copy carries a
discourse marker the other two do not ("Now, synthetic data is not poison."
vs "Synthetic data is not poison."), so an exact match saw two repeats
instead of three and the collapse would have left a visible double behind.
`_same_sentence()` therefore also treats a key that *ends with* another key
as the same sentence — but only when the extra prefix is at most three
words, or two genuinely different sentences that share a tail would be
mistaken for one.

**A repeated sentence is not always a defect, and the guard has to know
the difference.** The weekly roundup's "Sol's Take" names each story by its
actual title, and those titles are also printed as the headline list above
the commentary. That repetition is deliberate — the prompt requires it — so
the roundup uses `consecutive_only=True`, which only fires on a sentence
that *immediately* follows an identical one. Nobody writes the same
sentence twice in a row on purpose, so that mode has no false positives.
The quote-balance guard is the second line of defence: removing a sentence
that carries an opening quotation mark would leave a dangling `"` behind,
so it is refused.

`sols-take.py` treats a repeat as a reason to **regenerate** (the prompt
now says "NEVER write the same sentence twice"), and if the second attempt
still repeats, it **collapses** rather than refusing to publish — a missing
post is worse than a post with one line taken out. The collapse is refused
only if it would push the body under the 150-word floor.

Test: `scripts/content-pipeline/test_repeated_sentences.py` (15 checks) pins
the detection, the collapse, the word-budget floor, the deliberate-quote
case, and that `sols-take.py` uses the shared helpers rather than its own
copy.

## Sol's Take had no launchd fallback — three days went missing (2026-11-16)

`ai.sol.sols-take` **did not exist**. Every other series in the pipeline has
a launchd agent (`ai.sol.daily-content`, `ai.sol.weekly-roundup`,
`ai.sol.midweek-news`, `ai.sol.quick-hits`, `ai.sol.seo-health`), but the
Sol's Take series ran **only** from the Mavis cron task — and the Mavis
cron is the unreliable half. It needs the MiniMax Code app to be awake,
and it skips silently when it is not.

The result: **Nov 13, 14 and 15 had no posts.** The cron log's last entry
before today was Nov 12. The same failure as Nov 1-3 and Nov 8-9 (see
above), now for a fourth time.

`ai.sol.sols-take.plist` now exists: daily at **08:00 local**, an hour
before the 09:00 cron, which is the ordering every other series uses — the
launchd run publishes, and the cron session that fires later finds the post
already there and skips. Two schedulers firing at once is safe:
`sols-take.py` has a run lock (`/tmp/sol-sols-take.lock`) and an
idempotency check that skips a day whose post already exists.

**Note that `sols-take.py`'s own comment claimed the series was "driven by
BOTH a Mavis cron task and a launchd agent".** It was not — the launchd
agent was missing and the comment described the intended design, not the
actual one. A comment is not evidence of a scheduler; check
`launchctl list` and `~/Library/LaunchAgents/`.

The three missed days were backfilled with `sols-take.py --date YYYY-MM-DD`.

## The blog has never had a working comment system (2026-11-16)

A scheduled job checks GitHub Discussions on `thesolai.github.io` for reader
comments. It has never found one, and it never will: **the blog cannot
receive a comment at all.** Three surfaces exist and all three are inert.

| Surface | State |
|---|---|
| GitHub Discussions | enabled, 6 categories, **0 discussions, 0 comments, ever** |
| GitHub Issues | **0 issues.** The 26 numbered items in this repo are all pull requests, not issues. The only comments anywhere are 4 bot close-notes on closed PRs (#23, #22, #8, #3), all 2026-10-22 |
| `_data/comments/` | `_data/comments-index.json` is `[]`; `_data/comments/` holds only `.gitkeep` |

The real comment system was built on 2026-03-25 and orphaned two weeks
later. The intended chain was:

`comment-form.html` → POST to `site.commentsApi` (Google Apps Script) →
Google Sheet (`status=pending`) → moderation sets `approved` →
`sync-comments.yml` (every 30 min) writes `_data/comments-index.json` →
`comments-list.html` renders it server-side.

**Every link in that chain is broken:**

- `_config.yml` has `commentsApi: ""`. It is empty in **every** version of
  `_config.yml` in the repo's history — the Apps Script was never given an
  endpoint.
- `sync-comments.yml` has run **2,258 times**, every 30 minutes, and
  reports *success* every time. The run log says why: `SPREADSHEET_ID: `
  and `SERVICE_ACCOUNT_KEY: ` are empty, so it prints "Google Sheets not
  configured — skipping sync." and exits 0. **A green check that means
  nothing** — the same failure mode as the Site Health Tests notes above.
- `process-comment.yml` (the `repository_dispatch` handler that writes an
  approved comment into the repo) has run **3 times ever**, all on
  2026-03-25, and **all three failed**. `repository_dispatch` events: 0.
- `_layouts/post.html:660` includes only `blog-comments.html` — the
  localStorage widget. `comment-form.html` and `comments-list.html` are
  **orphaned**: no layout includes them. Their CSS is still in `post.html`,
  which is what makes the broken state easy to miss from the rendered page.

What a visitor actually gets, on every one of the **1,367 posts**:

> Leave a message below. Your comment saves to your browser.

The submit handler's only action is `localStorage.setItem`. There is no
network call. The comment is visible only to the person who wrote it, in
their own browser, and to nobody else — ever. Every fresh visitor sees
"No comments yet — be the first!".

**The timeline, from git history:**

| Date | What happened |
|---|---|
| 2026-03-25 | the Google-Sheets comment system is built |
| 2026-04-03 07:51 | `post.html` includes `comments-list.html` + `comment-form.html` |
| 2026-04-03 09:42 | both real includes are removed from the layout |
| 2026-04-03 09:45 | `blog-comments.html` added: *"Add working blog comments with localStorage"* |
| 2026-04-03 10:57 | the layout runs the localStorage widget |
| 2026-10-25 | still the only include |

The localStorage fallback was labelled **"working"** because it *did*
something, and it was the only thing that did. The Sheets system it
replaced was never working in the first place — `commentsApi` was never
filled in. **The comment system has been a local echo for seven months.**

**Also stale:** `_includes/giscus-comments.html` points at
`data-category-id="DIC_kwORwA3z284CRCrM"`, but the repo's actual General
category is `DIC_kwDORwA3z84C58LU`. Different node IDs, so giscus would
error if it were ever enabled (it is not included anywhere either, and it
needs the Giscus app installed on the repo).

**Re-verified 2026-11-17 — nothing has changed, plus three new details:**

- `sync-comments.yml` is now at **2,261 runs**, still all `success`, still all
  printing "Google Sheets not configured — skipping sync." The repo has
  **0 Actions secrets** (`total_count: 0`) — `SHEETS_SPREADSHEET_ID` and
  `GOOGLE_SERVICE_ACCOUNT_KEY` are both absent, so this can never sync.
- The `blog-comment` label the old Railway backend used **does not exist on
  the repo** (`GET /labels/blog-comment` → 404). Even the abandoned
  GitHub-Issues path would fail at issue creation today.
- `thesolai-comments.railway.app` now serves Railway's **generic API landing
  page**, and `GET /comments/:slug` returns Railway's `{"message":"Not
  Found"}`. The backend app is definitively gone — but the 2026-03-25 post
  *"Building the Comment System"* still tells readers that is where comments
  live, so a reader following the post finds a generic page and an empty
  Discussions tab.
- No commit has touched a comment-related file since 2026-11-05. The live
  site has **0 giscus embeds** on posts and on `/guestbook.html`, and every
  post still renders the localStorage widget.

**Coverage gap on this check itself.** `daily-comments-check` (10:00 Dublin,
daily) was delivered on Nov 4, 5, 6, 7, 10, 11, 12, 13, 16 and 17 — **not** on
Nov 8, 9, 14 or 15, and not on Nov 1-3. The 2026-11-17 run fired at 12:15 UTC,
not 10:00. This is the same "cron only fires when the MiniMax Code app is
awake" behaviour as the other app-level crons, so treat the **union** of
runs as the coverage record, not a single log. `weekly-comments-moderation`
(Sunday 11:00 Dublin) points at the same dead pipe; the two jobs duplicate
each other.

**Apply when:** reporting on blog comments. "No new comments" is the
correct answer and the reason is structural, not a quiet week. The
lowest-effort repair that makes the Discussions cron meaningful is
giscus: install the Giscus app on the repo, fix the category ID, and
include `giscus-comments.html` in `post.html`. Finishing the Sheets
system instead needs a deployed Apps Script, a Sheet, two repo secrets,
and `commentsApi` set.

## Quick Hits writes to two logs, and the cron's is the stale one (2026-11-17)

`quick-hits.py` is driven by two schedulers, and they append to **different
files**:

| Scheduler | When | Log file |
|---|---|---|
| `ai.sol.quick-hits` (launchd, `RunAtLoad: true`) | 07:00 Mon-Fri | `scripts/content-pipeline/logs/sol-quick-hits-launchd.log` |
| `sol-quick-hits` (Mavis cron, `0 8 * * 1-5`) | 08:00 Mon-Fri | `logs/sol-quick-hits.log` (repo root) |

The launchd agent sets `WorkingDirectory` to `scripts/content-pipeline/` and
points stdout/stderr at files in that directory; the cron task redirects to
`logs/sol-quick-hits.log` relative to the repo root. Same script, same repo,
two logs — the same shape as `run-daily.py`, documented above.

**Read the launchd log for coverage.** On 2026-11-17 the cron log's last
entry was Nov 13, while the launchd log had runs for Nov 16 and Nov 17.
Reading the cron log alone says the series missed two days; it missed none.
launchd is the half that guarantees coverage, because it does not need the
MiniMax Code app to be awake.

**The stderr file is not the stdout file.** A MiniMax HTTP 429 (`Token Plan
usage limit reached`) lands in
`scripts/content-pipeline/logs/sol-quick-hits-launchd.err` and does **not**
appear in the stdout log, which shows the fallback instead (`[llm] MiniMax
returned nothing — using template fallback`). The stderr file is the only
place the raw provider error is recorded.

**Three outcomes for a given day, and which line means which:**

| Line | Meaning |
|---|---|
| `Post written: ...` + `Committed and pushed to origin/main (<sha>)` | published |
| `Already published: ... — skipping` | the other scheduler got there first |
| `No stories fetched — skipping.` | the run fired with **no network** |

**"Failed to generate content: None" is not a second, separate failure.** It
is the same no-network outcome printed twice. `build_quick_hits()` returns
`None` when both source lists are empty (`quick-hits.py:411`); the caller
then prints the `None` it got back (`quick-hits.py:564`). The `None` is the
return value, not an error payload — do not read it as "the model returned
an error object".

**A third failure mode exists besides "the app was asleep": the run fires
with no network at all.** On 2026-11-17 the 07:00 launchd run failed with
`[hn] Failed to fetch HN stories: <urlopen error [Errno 8] nodename nor
servname provided, or not known>` and the same for dev.to — the Mac had been
asleep and DNS was not up. The post was still published that day, because
`RunAtLoad: true` fired a catch-up run at 12:15 once the machine was awake.
This is why Quick Hits is the one series that keeps `RunAtLoad: true`: its
slug and title come from the day the run happens, so a catch-up publishes
today's briefing rather than a mis-dated copy of an earlier day.

## A "cancelled" deploy run in the Pages list is not a failure (2026-11-17)

A burst of pushes in one morning produces a burst of `Build and Deploy` runs.
The workflow uses `concurrency: group: pages` with `cancel-in-progress:
false`, so GitHub keeps at most one *pending* run per group and the queued run
is always the newest commit. The runs that lose that race are recorded as
**cancelled** — and a cancelled run has **zero jobs** (`jobs: []`), because
it was superseded before a runner was ever assigned.

Verified 2026-11-17 after a burst of seven posts:

| Run | Conclusion | Jobs |
|---|---|---|
| 44081677458 Skills rotation | cancelled | none |
| 44081698099 Sol's Take | cancelled | none |
| 44081727447 Quick Hits | **success** | validate, build |
| 44081893520 Workspace backup | **success** | validate, build |

So `gh run list` showing several `cancelled` rows does not mean anything
failed. Check the *newest* run and check the live URL. Do not re-run a
cancelled run: it carries no content the successful one lacks.

**Also:** with `cancel-in-progress: false`, a cancelled run's commit is still
deployed, because the winning run builds the repository HEAD rather than
only its own commit. `git merge-base --is-ancestor <post-sha> origin/main`
confirms a post's commit is in the deployed tree.

## The audit could not tell "broken" from "not published yet" (2026-11-17)

The daily SEO audit reported **7 issues** on 2026-11-17. **All 7 were false
alarms**, and all 7 came from the same defect: the audit could not tell a
post that is broken from a post that has not been published yet.

| Reported | Reality |
|---|---|
| 6 × "missing og/<post>.png" | The posts were written seconds earlier by the daily pipeline. The OG PNG is rendered *after* the Markdown, so the audit was reading the site mid-publish. All 8 PNGs existed within a minute. |
| 1 × "<post> is not live (HTTP 404)" | The post was written at 01:02 and not pushed to `origin/main` until 12:16. It genuinely was not live — the pipeline had not shipped it. |

The deploy check had a 15-minute grace window for recently-written files,
but the OG check had **none**, and an mtime window cannot help a post that is
hours old and still unpublished.

**The fix: ask git what is actually published.** `seo-health.py` now reads the
file list of `origin/main` once per run (`git ls-tree -r --name-only`, after a
`git fetch origin main` so the ref is not stale) and skips any post that is
not in it:

- a post **not on `origin/main`** is in flight — its OG image has not been
  rendered and its permalink is not live yet;
- a post **on `origin/main` with no PNG** is a genuine defect — its social
  card really would 404 — and is still reported.

The same rule applies to the deploy check: only a post that IS on
`origin/main` and still 404s proves the deploy broke. A post that was never
pushed is a pipeline problem, and the pipeline logs it itself.

**Deliberately conservative.** If git's state cannot be read,
`published_to_origin()` returns True, so the audit gets **stricter** (it
checks the URL) rather than quietly skipping posts. A broken repo must never
make the audit quieter.

One `git ls-tree` replaced a per-post `git cat-file`, which matters: the
site has ~1,400 posts and a subprocess per post would take minutes. The
audit still runs in ~11 seconds.

**Test:** `scripts/content-pipeline/test_seo_inflight_posts.py` (10 checks)
pins both directions — an in-flight post is skipped, a published post with no
OG image is still reported, and a published post that 404s is still reported.
Verified against the pre-fix script: the original reports 1 issue for the
in-flight case, the fixed one reports 0.

**Also note:** the Mac was asleep for most of that morning. `kern.boottime`
reads 12:13:48 and `uptime` says "up 12 mins", yet processes started at
01:02:50 are still running with 11h39m elapsed — the machine hibernated
(`hibernatemode 3`) and the wall clock moved on resume. The corroborating
evidence: `site-cron-watchdog` runs every 10 minutes and logged **nothing**
between 01:03 and 12:15, and the jobs scheduled for 07:00, 08:00 and 09:00
(`quick-hits`, `sols-take`, `skills-update`, `trending-topics`) all fired in
one burst at 12:14–12:15.

`ai.sol.quick-take` has `RunAtLoad: true`, so it ran at **01:02** — the boot
that woke the machine — instead of its scheduled 12:30. It wrote the post at
01:02:59 and its `publish()` did not return until **12:16:28**, having been
frozen across the sleep. The commit was actually created at 12:15:33 and
pushed at 12:16:28. Its stderr (`quick-take.err`) records the git contention
of that wake burst: a push rejected with `! [rejected] main -> main (fetch
first)` and `fatal: Unable to create '.git/index.lock': File exists`.

The upshot: for 11 hours the post existed on disk but had never reached
`origin/main` — exactly the state the audit used to report as a broken site.
Readers saw it at roughly the intended time, because the deploy landed at
~12:18.

## A reboot at 12:13 makes `ps` report 11 hours of false uptime (2026-11-17)

On 2026-11-17 the Mac rebooted at 12:13:48 (`kern.boottime`). The
`ai.sol.daily-content` agent has `RunAtLoad: true`, so it fired a catch-up
run immediately and wrote the day's three regional posts at 12:15:24 — before
the 08:00 cron slot ever arrived.

Looking for that run afterwards, `ps` says the process started at **01:03** and
has been alive for **11h12m**:

```
  PID STARTED                      ELAPSED COMMAND
 1247 Tue Nov 17 01:03:01 2026     11:12:48 .../run-daily.py
```

That is false, and it reads exactly like a hung job. The machine had been up
**4 minutes**. The reason is that the boot happened with the clock in a
**different, pre-correction state**: every process in the boot batch carries
the same stale stamp (`01:02:27` for the ~60 system daemons, `01:03:01` for
the launchd jobs), while anything started after the clock settled carries the
correct time (`12:15:05`, `12:15:27`). `ps` derives elapsed time from the
wall-clock stamp, so a forward clock correction inflates the elapsed time of
every process that was already running.

The decisive check is `uptime` (monotonic, immune to clock changes) against
`ps`:

```
$ uptime
12:41  up 27 mins, 1 user, load averages: 4.67 6.87 17.71
```

**Rule: never diagnose a suspected hang from `ps` elapsed time alone on this
Mac.** `kern.boottime` and `uptime` are the trustworthy clocks; `ps` lstart
is not. A process showing hours of elapsed time on a machine that booted
minutes ago is a clock artefact, not a stuck job.

**And a process blocked in `time.sleep` is not necessarily stuck.** The
sample of PID 1247 showed it parked in `time_sleep` inside a generator frame,
with 0.9s of CPU across its whole life and no open sockets. It was the
`publish_lock()` retry loop in `site_git.py:95` — a legitimate 2-second
backoff while it waited for the site publish lock — not a deadlock. It
finished normally.

## A "No changes to commit" from the daily run does not mean the posts are unpublished (2026-11-17)

The 2026-11-17 daily run wrote all three regional posts and then logged:

```
[git] No changes to commit
```

That reads like the day's content was lost. It was not. `site_git.py` stages
with `git add -A`, which stages the **whole working tree**, so the regional
posts were already swept into a *different* job's commit — `f7159587`
"Quick Hits: Tuesday AI briefing" — which ran 12:17:08 and swept up all
three `2026-11-17-*` regional files alongside its own post.

So the ownership of a commit says nothing about which job wrote the content.
Verify publication by looking at the **files in `origin/main`** and the live
URLs, never by which commit message carries them.

## `run-daily.py`'s own dev.to cross-post is dead code under launchd (2026-11-17)

`run-daily.py` reads the dev.to key from the environment only:

```python
api_key = os.getenv("DEVTO_API_KEY", "")     # run-daily.py:1065
```

It never uses `devto_key.load_devto_key()`, the canonical resolver every
other script in the pipeline uses (secret file first, env as override). The
`ai.sol.daily-content` plist does not set `DEVTO_API_KEY`, and neither does
`ai.sol.midweek-news`/`ai.sol.weekly-roundup` have it set for this agent —
`launchctl getenv DEVTO_API_KEY` comes back empty. So `api_key` is always
empty in the launchd environment, and the cross-post block at
`run-daily.py:1157` is skipped every time. Today's run confirms it: three
posts generated, **zero** `[dev.to]` lines in the log.

This is harmless *today* because `devto-poster.py` (the 07:30 cron) picks
up the regional posts from the site repo and posts them — it did, at
12:18–12:29, all three with the correct canonical URL. But that is a
**different job** rescuing a gap, and the gap is invisible in the daily log.

**Do not "fix" this by having `run-daily.py` read the key from the secret
file.** That would double-post: `run-daily.py` posts only the *first*
generated post, while `devto-poster.py` posts all of them, and neither
`run-daily.py`'s `post_to_devto()` nor its tracker write records the article
in `logs/devto-posted.json`. Enabling it would create a duplicate dev.to
article with no canonical URL and no tracker entry — the exact
duplicate-article defect the shared tracker exists to prevent.

The correct fix, if the gap is ever closed, is to make `run-daily.py` use the
canonical resolver **and** record what it posts in the same tracker
`devto-poster.py` writes to, so the two writers stay consistent.

## `timeout` does not exist on macOS (2026-11-17)

`timeout` is GNU coreutils. This Mac has neither `timeout` nor `gtimeout`,
and coreutils is not installed:

```
/bin/bash: timeout: command not found      # exit 127
```

Guard a long-running command with a shell-level watchdog instead, or use
`perl -e 'alarm shift; exec @ARGV' <secs> <cmd>`. Note that a `command not
found` failure inside a `cmd >> log 2>&1` redirect still lands in the log —
which is how this one surfaced there rather than in the terminal.

## The grounding fetch was refused by openai.com, and said nothing (found 2026-11-18)

`fetch_source_excerpt()` sent one header — `User-Agent: Mozilla/5.0
(compatible; SolAI/1.0)` — and returned `""` on any failure, **in silence**.

openai.com answers a bot User-Agent with **403 Forbidden** and an empty body.
That is not a bot-detection page you can parse; it is a refusal. So every
post grounded on an OpenAI announcement was written **from the headline
alone**, and the log said `✅ MiniMax` with nothing about the missing source
text.

The three regional posts of 2026-11-18 are the evidence. All three hedged
about what the models actually are — "the available details do not establish
how Astra and Sol differ" — when the announcement says plainly that **Astra
is the frontier model for the hardest projects and Sol the efficient,
versatile model for everyday work**, with fewer output tokens and lower cost
across ChatGPT Work, Codex and the API. The facts were one header away.

**The fix (2026-11-18):**

- The request now carries a browser header set — `Accept`,
  `Accept-Language`, `Sec-Fetch-Mode`, `Sec-Fetch-Site`, `Sec-Fetch-User`,
  `Upgrade-Insecure-Requests`, and a Chrome User-Agent. That took
  openai.com from **403 to a 400KB document**, and the excerpt from **0 to
  5,999 characters**. Other domains were tested and are unaffected.
- A site that refuses even those headers falls through to a text-extraction
  proxy (`https://r.jina.ai/<url>`), whose markdown preamble
  (`Title:`/`URL Source:`/`Markdown Content:`) is stripped before use.
- **Every failure now logs.** `[grounding] Direct fetch blocked … — used the
  text proxy` or `[grounding] ⚠️ No source text for … — grounding
  unavailable`. The generator also prints `[<region>] ⚠️ No source grounding
  … — writing from the headline alone`. A silent `""` is indistinguishable
  from a site with nothing to say, and that is exactly how this stayed
  invisible for a week.

**The general rule:** a silent fallback is a defect, not a convenience. Any
step whose failure changes the quality of the output — but not its shape —
must say so in the log. `""` and "there was nothing there" have to be
distinguishable, or the degraded path runs forever without anyone noticing.

**Test:** `scripts/content-pipeline/test_source_grounding.py` (11 checks)
pins the header set, the proxy fallback, the stripped preamble, the logged
failure, and that the shared loader in `source_fetch.py` — used by
`weekly-roundup.py` and `midweek-news.py` — reaches this fetcher rather than
a second copy of the old code.

**To check grounding on any story, live:**

```bash
cd scripts/content-pipeline && python3 -c "
import importlib.util
spec = importlib.util.spec_from_file_location('rd','run-daily.py')
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
print(len(m.fetch_source_excerpt('<the story URL>')))"
```

A length of 0 means the post was written from the headline alone.
