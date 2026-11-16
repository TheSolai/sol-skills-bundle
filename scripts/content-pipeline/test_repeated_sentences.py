#!/usr/bin/env python3
"""
Regression tests for the repeated-sentence guard.

WHY THIS EXISTS
---------------
The 2026-11-16 Sol's Take shipped with the sentence "Synthetic data is not
poison." three times in a row. Nothing caught it:

  * the word budget was SATISFIED by the repeats — 196 words, inside the
    150-200 band;
  * the body carried no contamination, so `looks_contaminated()` passed;
  * the front matter was clean and the strip found no echoed heading.

The result was a post that looked fine to every check the pipeline ran, and
read like a model that had looped.

The guard has to be careful in one direction, though: the weekly roundup's
"Sol's Take" names each story by its actual title, and those titles are also
printed as the headline list above the commentary. That repetition is
deliberate and must survive the guard — a naive duplicate detector would
"fix" it by mangling the prose.

These tests pin both halves:
  * a genuine loop is detected and removed;
  * a deliberate quote is left exactly as it was.

Run it directly — no test framework needed:

    python3 scripts/content-pipeline/test_repeated_sentences.py

It is also pytest-compatible: every check is a `test_` function.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from text_utils import (  # noqa: E402
    collapse_repeated_sentences,
    find_repeated_sentences,
)


# The body that shipped on 2026-11-16, with the triplication intact. This is
# the real text, not a synthetic stand-in — the bug is reproduced against
# what readers actually saw.
SHIPPED_BODY = """Yes, we are already training plenty of AI systems on AI-generated material, and pretending that this is merely “scaling the corpus.” It isn’t. It is feedback with extra steps.

The public web is now thick with machine-written posts, product copy, summaries, and search-flooded SEO sludge. Scrape it, train on it, and the model learns what the previous model thought was worth repeating. Repeat it, and you don’t just scale quality. You feed its habits back into itself.

The research term for the risk is “model collapse,” and it’s not some speculative dystopia. It is the obvious consequence of training on distributions increasingly polluted by models trained on earlier polluted distributions. Humans may notice because humans are lazy, repetitive, and eager to publish whatever sounds authoritative. That makes the contamination unusually good at hiding.

Now, synthetic data is not poison. Synthetic data is not poison. Synthetic data is not poison. Used deliberately—verified answers, controlled examples, edge-case generation—it can be excellent. The problem is treating “generated” as a substitute for “grounded” rather than one tool among many. We need provenance, filtering, evaluation, and fresh human judgment. Without that, we’re just polishing a mirror and calling the reflection intelligence."""


# The weekly roundup post body, with the headline list AND the commentary
# that names each story by its title. That repetition is deliberate — the
# prompt requires it ("name each story by its actual title") — so the guard
# must not touch it.
ROUNDUP_BODY = """## Research & Ideas

**1. "Who is Hiring?" postings stuck near 12-year lows as AI reaches half of all jobs**
[Read more →](https://www.hntrends.com/2026/november.html) — 82 points · 67 comments

**3. Parents pay $75k for Alpha's AI school. Half of its students missed math target**
[Read more →](https://fortune.com/2026/11/14/alpha-school-ai-students-tuition-learning-goals/) — 64 points · 62 comments

## Sol's Take

In “Who is Hiring?” postings stuck near 12-year lows as AI reaches half of all jobs, credentials are replacing headcount. At 51.6%, “AI” is boilerplate, not proof of demand. “Parents pay $75k for Alpha's AI school. Half of its students missed math target” is the harder failure: $75,000 buys a promise; only 42% hit the core target. Alpha should prove gains before recruiting districts."""


def test_a_triplicated_sentence_is_detected():
    """The shipped defect is reported, with the right count."""
    repeats = find_repeated_sentences(SHIPPED_BODY)
    assert len(repeats) == 1, f"expected one repeated sentence, got {repeats}"
    assert repeats[0]["count"] == 3, (
        f"expected a count of 3, got {repeats[0]['count']}"
    )
    assert "synthetic data is not poison" in repeats[0]["sentence"].lower()


def test_the_shipped_body_is_only_defective_in_one_place():
    """The detection is specific — it does not flag the whole body."""
    repeats = find_repeated_sentences(SHIPPED_BODY)
    for entry in repeats:
        assert "synthetic data is not poison" in entry["sentence"].lower(), (
            f"unexpected repeat flagged: {entry['sentence']!r}"
        )


def test_collapsing_removes_the_extra_copies():
    """The collapse keeps the FIRST copy and drops the rest."""
    collapsed, removed = collapse_repeated_sentences(SHIPPED_BODY)
    assert removed == 2, f"expected 2 sentences removed, got {removed}"
    assert collapsed.lower().count("synthetic data is not poison.") == 1, (
        "the repeated sentence survived the collapse"
    )


def test_collapsing_keeps_the_closing_line():
    """The sharp closing sentence is not the thing that gets removed."""
    collapsed, _ = collapse_repeated_sentences(SHIPPED_BODY)
    assert collapsed.rstrip().endswith(
        "calling the reflection intelligence."
    ), "the closing line was lost"
    assert "polishing a mirror" in collapsed


def test_the_collapsed_body_still_reads():
    """The prose around the collapse is untouched."""
    collapsed, _ = collapse_repeated_sentences(SHIPPED_BODY)
    assert "Used deliberately—verified answers" in collapsed, (
        "the sentence that followed the repeat was lost"
    )
    assert "It is feedback with extra steps." in collapsed


def test_the_collapsed_body_stays_inside_the_word_budget():
    """After the collapse the body is still a publishable Sol's Take.

    The brief is 150-200 words. The shipped body was 196; removing two
    copies of a five-word sentence leaves it inside the band. A collapse
    that pushed the body under the floor would be a different defect.
    """
    collapsed, _ = collapse_repeated_sentences(SHIPPED_BODY)
    assert 150 <= len(collapsed.split()) <= 200, (
        f"collapsed body is {len(collapsed.split())} words, outside 150-200"
    )


def test_a_clean_body_is_untouched():
    """The collapse is a genuine no-op when there is nothing to fix."""
    body = "A short and perfectly reasonable opinion about AI tooling."
    collapsed, removed = collapse_repeated_sentences(body)
    assert removed == 0
    assert collapsed == body, "a clean body was rewritten"


def test_paragraphs_are_preserved_when_nothing_is_removed():
    """Untouched paragraphs keep their own line breaks."""
    body = "First line of a paragraph\nstill the same paragraph.\n\nSecond paragraph here."
    collapsed, removed = collapse_repeated_sentences(body)
    assert removed == 0
    assert collapsed == body


def test_deliberate_quoting_is_left_alone():
    """The roundup's quoted titles survive the guard.

    The commentary quotes a story title that is also printed as a headline.
    That repetition is the prompt's design ("name each story by its actual
    title"), so the guard must not remove it — and removing it would leave
    the paragraph's quotation mark dangling.
    """
    collapsed, removed = collapse_repeated_sentences(ROUNDUP_BODY)
    assert removed == 0, (
        f"the guard removed {removed} sentence(s) from a deliberate quote"
    )
    assert collapsed == ROUNDUP_BODY, "the quoted commentary was rewritten"


def test_consecutive_mode_leaves_a_deliberate_quote_alone():
    """The roundup's own guard (consecutive-only) also leaves it alone."""
    collapsed, removed = collapse_repeated_sentences(
        ROUNDUP_BODY, consecutive_only=True
    )
    assert removed == 0
    assert collapsed == ROUNDUP_BODY


def test_consecutive_mode_removes_an_immediate_repeat():
    """A sentence immediately repeated is never deliberate."""
    body = (
        "This sentence is repeated immediately after itself, which is a loop.\n\n"
        "This sentence is repeated immediately after itself, which is a loop.\n\n"
        "A closing paragraph that is entirely its own thing."
    )
    collapsed, removed = collapse_repeated_sentences(body, consecutive_only=True)
    assert removed == 1, f"expected 1 sentence removed, got {removed}"
    assert collapsed.count("which is a loop.") == 1, (
        "the immediate repeat survived"
    )


def test_consecutive_mode_keeps_a_non_adjacent_repeat():
    """Consecutive mode is deliberately narrow."""
    body = (
        "A sentence that appears twice in this body.\n\n"
        "Something entirely different sits between the two copies.\n\n"
        "A sentence that appears twice in this body."
    )
    _, removed = collapse_repeated_sentences(body, consecutive_only=True)
    assert removed == 0, "a non-adjacent repeat was removed in consecutive mode"


def test_short_sentences_are_not_treated_as_repeats():
    """Short sentences repeat in real prose without being a defect."""
    body = "It is fine. It is fine. It is fine. It is fine."
    repeats = find_repeated_sentences(body)
    assert repeats == [], f"short sentences were flagged: {repeats}"
    collapsed, removed = collapse_repeated_sentences(body)
    assert removed == 0
    assert collapsed == body


def test_a_repeat_in_a_different_paragraph_is_found():
    """The detector works across paragraphs, not just within one."""
    body = (
        "The first paragraph states a claim that shows up again later on.\n\n"
        "A different sentence sits in between the two copies of the claim.\n\n"
        "The first paragraph states a claim that shows up again later on."
    )
    repeats = find_repeated_sentences(body)
    assert len(repeats) == 1, f"expected one repeat, got {repeats}"
    assert repeats[0]["count"] == 2


def test_sols_take_uses_the_guard():
    """The generator is wired to the shared helpers, not a local copy.

    A script that regrows its own copy of the rule fails the suite.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "sols_take_module", Path(__file__).resolve().parent / "sols-take.py"
    )
    sols_take = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sols_take)

    assert hasattr(sols_take, "_find_repeated_sentences")
    assert hasattr(sols_take, "_collapse_repeated_sentences")
    assert (
        sols_take._find_repeated_sentences is find_repeated_sentences
    ), "sols-take.py has its own copy of the detector"


def _run():
    checks = [
        (name, fn)
        for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    failed = 0
    for name, fn in checks:
        try:
            fn()
        except AssertionError as e:
            failed += 1
            print(f"FAIL {name}: {e}")
        except Exception as e:  # pragma: no cover
            failed += 1
            print(f"ERROR {name}: {type(e).__name__}: {e}")
        else:
            print(f"ok   {name}")
    print(f"\n{len(checks) - failed}/{len(checks)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_run())
