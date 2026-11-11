#!/usr/bin/env python3
"""
Regression tests for the shared AI-title filter (ai_filter.py).

WHY THIS EXISTS
---------------
The filter was a plain substring test, so "ai" matched inside ordinary
English words. On 2026-11-09 the top HN story of the day was "Foldable
Flight: The Best Paper Airplanes" (952 points) — a website of paper-airplane
diagrams, not an AI development — and it was selected because "Airplanes"
contains "ai".

That one did not ship as a wrong story (the anti-fabrication rules in the
prompt caught it), but the Quick Hits filter DID ship wrong stories: the
2026-11-09 and 2026-11-10 briefs each carried "Payments Canada launches
Canada's Real-Time Rail", because "Rail" contains "ai".

OPERATIONS.md says of the original defect: "The filter's real weakness is
that it is the only thing standing between the pipeline and a non-AI
headline, and it is untested. A table-driven test over the ten highest-
scoring HN titles of the day would catch both directions." This file is
that test: real titles, both directions, fixed.

Run it directly — no test framework needed:

    python3 scripts/content-pipeline/test_ai_filter.py

It is also pytest-compatible: every check is a `test_` function.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ai_filter import is_ai_title  # noqa: E402


# Every script that picks AI stories must use the SHARED filter. Two scripts
# kept private inline copies of the substring test until 2026-11-11, and the
# copy is exactly how the defect survives a fix elsewhere: quick-hits.py and
# run-daily.py were migrated on 2026-11-10, while midweek-news.py and
# weekly-roundup.py were missed and went on shipping non-AI stories — the
# 2026-11-09 roundup carried "Outlook.com is rejecting email from domains
# without DMARC" and "RAM Altman: Linux Script Emails Sam Altman" as AI
# stories, and the 2026-11-11 midweek digest carried the Nvidia CEO affair
# story.
#
# This test fails if any of them goes back to a private copy, so the fix
# cannot be undone one script at a time.
FILTER_CONSUMERS = ["quick-hits.py", "run-daily.py", "midweek-news.py", "weekly-roundup.py"]

# A title that only the substring test would match, and one that only a
# word-boundary-aware filter matches.
SUBSTRING_ONLY_TITLE = "Outlook.com is rejecting email from domains without DMARC"
WORD_ONLY_TITLE = "Understanding Transformers from scratch"


# Real HN titles that must NOT match. Each one contains a keyword as a
# substring of an ordinary word — the defect this file pins shut.
#
# The first two are the ones that actually shipped.
MUST_NOT_MATCH = [
    "Payments Canada announces launch of Canada's Real-Time Rail (RTR) payments",
    "Foldable Flight: The Best Paper Airplanes",
    "A Bit about Email",
    "The Fastest Object Storage Client in the World",
    "Humans will never again create a project quite like Wikipedia",
    "Semi-transparent, colored cadmium telluride photovoltaic panels in agrivoltaics",
    "More than 400k Russians killed fighting in Ukraine war, leaked data suggests",
    "Norway has purchased 500k drones for Ukraine (Norwegian)",
    "USPS now offers official ZIP Code boundary polygons",
    "Unlocking any car from X brand in most of the world",
    # SHIPPED in the 2026-11-09 roundup. "email" contains "ai".
    "Outlook.com is rejecting email from domains without DMARC",
    # SHIPPED in the 2026-11-09 roundup. "Emails" contains "ai".
    "RAM Altman: Linux Script Emails Sam Altman Daily How Much Unused RAM in Your PC",
    # SHIPPED in the 2026-11-09 roundup and the 2026-11-11 midweek digest.
    # "affair" contains "ai" — and the story is a CEO's affair, not an AI
    # development. The shared filter rejects it.
    "Nvidia CEO Jensen Huang's letter to staff about affair",
    # SHIPPED in the 2026-10-18 roundup. "train" contains "ai".
    "Microsoft and CMU team up to train early career software engineers",
    # SHIPPED in the 2026-10-22 roundup. "Railway" contains "ai".
    "Railway Security Disclosure",
]

# Real HN titles that MUST match. These are the ones the old list rejected:
# "transformers" was not in the list, so a genuine AI story was refused
# while a paper-airplane site was accepted.
MUST_MATCH = [
    "Claude Fable 5.2 and Mythos 5.2",
    "Google Gemini routes personal Drive files and emails to human annotators",
    "Anthropic must face lawsuit over Claude subscription marketing",
    "Understanding Transformers from scratch",
    "LLMs are everywhere",
    "AI-assisted security research surfaces old vulnerabilities",
    "OpenAI raises $11B at a $1.25T valuation",
    "Muse Ember: Our smallest, most efficient model",
    "ChatGPT Desktop forwards your SSH agent to remote machines",
    "RAG pipelines explained",
    "CoreWeave bond yield tops 16% as AI credit stress rises",
    "DeepSeek released a new open model",
    "local LLM serving on consumer hardware",
    "Apple capped engineers at a $200 daily AI budget",
    "GPT-2 implemented in CSS",
    "Show HN: Thrum - run Claude Code, Codex and other coding agents as services",
    "The Components of Agent Memory",
    # A Stanford research post. It matched "Modeling" — the gerund of
    # "model" — and it is a genuine AI paper about text generation.
    "Next-Concept Modeling: Fast, Parallel Coarse-to-Fine Text Generation",
]

# A KNOWN LIMITATION, recorded so it is not mistaken for a regression.
#
# "Hinton Lectures 2026 Day 1 Livestream" is a genuine AI story, but it
# contains no AI vocabulary at all. A keyword filter cannot catch a title
# whose only AI signal is WHO is speaking — that needs a name list, and a
# name list is over-fitting to whichever titles happen to be read. The
# filter's job is not to catch every AI story; it is to stop a
# high-engagement NON-AI story from ranking at the top of the candidate
# list, which is exactly the defect this file pins shut.
KNOWN_UNCAUGHT_AI = [
    "Hinton Lectures 2026 Day 1 Livestream",
]



def test_non_ai_titles_are_rejected():
    """Every title whose keyword only appears mid-word must be rejected."""
    for title in MUST_NOT_MATCH:
        assert not is_ai_title(title), (
            f"{title!r} matched on a substring, not a word"
        )


def test_ai_titles_are_accepted():
    """Every genuine AI title must be accepted, including the ones the old
    keyword list rejected."""
    for title in MUST_MATCH:
        assert is_ai_title(title), f"{title!r} was wrongly rejected"


def test_the_two_shipped_stories_are_rejected():
    """The two false positives that reached the live site."""
    assert not is_ai_title(
        "Payments Canada launches Canada's Real-Time Rail"
    ), "the Real-Time Rail story shipped twice; it must not match"
    assert not is_ai_title(
        "Foldable Flight: The Best Paper Airplanes"
    ), "the paper-airplane story was selected; it must not match"


def test_case_insensitive():
    assert is_ai_title("LLMs ARE EVERYWHERE")
    assert is_ai_title("claude fable 5.2")
    assert not is_ai_title("REAL-TIME RAIL AND EMAIL STORAGE")


def test_plural_and_gerund_forms():
    """The plural/gerund of an AI term still matches."""
    assert is_ai_title("LLMs everywhere")
    assert is_ai_title("embeddings are broken")
    assert is_ai_title("agents that reason")
    assert is_ai_title("models that hallucinate")


def test_ordinary_word_plurals_do_not_match():
    """The plural/gerund of an ordinary word must not match."""
    assert not is_ai_title("Rags to Riches")
    assert not is_ai_title("Hex bolts for 2026")
    assert not is_ai_title("Storage of objects")


def test_word_boundary_not_substring():
    """'ai' inside a longer word must not match; a standalone 'ai' must."""
    assert is_ai_title("AI mandate nobody understands")
    assert not is_ai_title("maintainers of the chain")
    assert not is_ai_title("available now")


def test_known_uncaught_ai_titles():
    """The filter cannot catch a title with no AI vocabulary at all.

    This is a documented limitation, not a regression. It is pinned here
    so that a future change which "fixes" it by adding a person's name to
    the keyword list is a visible, deliberate act rather than an accident.
    """
    for title in KNOWN_UNCAUGHT_AI:
        assert not is_ai_title(title), (
            f"{title!r} is now caught — if that was deliberate, update this "
            f"test and the note in ai_filter.py"
        )


def test_low_ranking_false_positives_exist_but_are_harmless():
    """Some non-AI titles match on an ordinary word ("decision models").

    That is acceptable: the filter is not the only guard. It runs BEFORE
    the stories are ranked, and only the top five by engagement are offered
    to the model as candidates. Measured over four real days, every one of
    those top-five windows contained only genuine AI stories.

    The defect this file exists for was different in kind: a false
    positive that RANKED FIRST, at 952 points, above every real AI story
    of the day. A low-ranked false positive never reaches the model.

    This test pins the residual behaviour so that a change which made
    bare "model"/"models" match nothing would show up here.
    """
    assert is_ai_title("Show HN: Flide: Make Jev-like decision models up to 9.5x faster"), (
        "bare 'models' is expected to match — the ranking window, not this "
        "filter, is what keeps a low-scoring false positive away from the "
        "model. If this now fails, the ranking guard needs checking."
    )




def test_every_consumer_uses_the_shared_filter():
    """No script may keep a private copy of the AI filter.

    Each consumer is imported and asked whether it accepts a title that
    only a substring test would accept. A script with its own inline copy
    says yes, and this test fails.
    """
    import importlib.util

    here = Path(__file__).resolve().parent
    for script in FILTER_CONSUMERS:
        path = here / script
        assert path.exists(), f"{script} is missing from the pipeline"

        spec = importlib.util.spec_from_file_location(f"_filter_probe_{script[:-3]}", path)
        module = importlib.util.module_from_spec(spec)
        # The scripts read sys.argv at import time in some cases; neutralise it.
        old_argv = sys.argv
        sys.argv = [script]
        try:
            spec.loader.exec_module(module)
        finally:
            sys.argv = old_argv

        # Every consumer exposes the same predicate name.
        predicate = None
        for attr in ("is_ai_story", "is_ai_title"):
            if hasattr(module, attr):
                predicate = getattr(module, attr)
                break
        assert predicate is not None, (
            f"{script} exposes no AI filter predicate — the filter must not "
            f"be removed from a consumer"
        )

        assert not predicate(SUBSTRING_ONLY_TITLE), (
            f"{script} still matches a title on a substring: {SUBSTRING_ONLY_TITLE!r}. "
            f"It must use the shared ai_filter.is_ai_title()."
        )
        assert predicate(WORD_ONLY_TITLE), (
            f"{script} rejects {WORD_ONLY_TITLE!r} — it must use the shared "
            f"ai_filter.is_ai_title()."
        )


def _run():
    failures = []
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as exc:
            failures.append((name, str(exc)))
            print(f"  FAIL  {name}: {exc}")
    print()
    if failures:
        print(f"{len(failures)} test(s) FAILED")
        return 1
    print("All tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(_run())
