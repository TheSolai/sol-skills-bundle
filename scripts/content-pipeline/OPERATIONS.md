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

**`publish()` must be called with `allow_empty=True`.** Without it, the retry
loop raises `PublishError: git commit: nothing to commit` when a previous
attempt already committed but the push was beaten by a racing publisher.

### Pushes are slow — this is the credential gate, not a hang

`.git/hooks/pre-push` runs `secret-scan.py` over the working tree and the
push range. It inspects ~5,600 files and takes **5+ minutes**. Do not kill a
push that looks stuck; it will land. Use a background task and wait.

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
`og/.manifest.json` and `og/*.png`. Resolution:

- `og/*.png` → take **upstream** (`git checkout --ours <file>`). Upstream
  carries the "repaired" image; the local side is usually a duplicate from a
  job that ran twice.
- `og/.manifest.json` → take the **union** of both sides. Each side has
  entries the other lacks, and the hashes never conflict.

During a rebase, `--ours` is upstream (origin/main) and `--theirs` is the
commit being replayed.

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
