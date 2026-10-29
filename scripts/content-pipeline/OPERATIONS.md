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

Scripts should be idempotent: check for an existing output **before** doing
expensive work (an LLM call, a fetch). `weekly-roundup.py` now checks for the
post file before building the body.

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

## A generator's word budget is a prompt instruction, not a constraint

`run-daily.py` asks for 400-600 words and does not enforce it — there is no
`_trim_to_words` equivalent to the one in `weekly-roundup.py`. The 2026-10-28
posts came in at 591 (EU), 528 (UK) and 600 (US) body words after editing,
and the 2026-10-27 set was 582/645/640. When editing a published post for
accuracy, measure the body the same way both times (exclude the title line,
`###` headings, `---` dividers and the `*Source:*` line) or the comparison
is meaningless.

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
