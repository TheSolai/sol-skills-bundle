#!/usr/bin/env python3
"""
Regression tests for the Sol's Take word budget.

WHY THIS EXISTS
---------------
The 150-200 word brief was enforced with only a ceiling, and the ceiling
was not actually a ceiling. Two separate defects let a post through:

  1. `trim_to_words()` kept a paragraph's FIRST sentence even when it
     pushed the body past the limit. The guard read
     `if count + words > limit and kept:` — and for a paragraph's first
     sentence `kept` is empty, so the check never fired. The 2026-11-07
     post shipped at 206 words against a 200-word brief.

  2. There was no floor at all. A model that returned 90 words produced
     a 90-word post, and nothing flagged it. A word budget needs both
     ends: the floor stops a thin post, the ceiling stops a sprawling
     one.

  3. The trim cut from the END of the body, so the closing "one sharp
     sentence" the prompt asks for was the first thing it lost.

These tests pin all three.

Run it directly — no test framework needed:

    python3 scripts/content-pipeline/test_word_budget.py

It is also pytest-compatible: every check is a `test_` function.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from text_utils import trim_to_words  # noqa: E402

# Importing sols-take pulls in the MiniMax helper and the dev.to key
# loader. Neither makes a network call at import time, so this is safe.
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "sols_take_module", Path(__file__).resolve().parent / "sols-take.py"
)
sols_take = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sols_take)


def _para(sentence: str, times: int) -> str:
    return " ".join([sentence] * times)


# A body whose second paragraph opens with a sentence long enough to
# overflow the remaining budget. Before the fix, that first sentence was
# kept unconditionally and the body came out over the ceiling.
FIRST_SENTENCE_OVERFLOW = (
    _para("Filler sentence in the opening paragraph of the draft.", 13)
    + "\n\n"
    + "This paragraph opens with one long sentence that cannot fit inside "
    "whatever budget happens to be left over at this point in the body."
)


def test_trim_never_exceeds_the_ceiling():
    """The ceiling is a ceiling, not a suggestion."""
    trimmed = trim_to_words(FIRST_SENTENCE_OVERFLOW, limit=200)
    assert len(trimmed.split()) <= 200, (
        f"trim produced {len(trimmed.split())} words against a 200 ceiling"
    )


def test_trim_keeps_a_body_within_the_limit_untouched():
    """A body already inside the limit comes back byte-identical."""
    body = "A short and perfectly reasonable opinion about AI tooling."
    assert trim_to_words(body, limit=200) == body


def test_trim_cuts_only_at_sentence_boundaries():
    """The trim never leaves a sentence half-written."""
    trimmed = trim_to_words(FIRST_SENTENCE_OVERFLOW, limit=200)
    assert trimmed.endswith((".", "!", "?", '"', "'", "”", "’", ")"))


def test_trim_preserves_the_earlier_paragraphs():
    """Trimming drops the tail, not the opening of the piece."""
    trimmed = trim_to_words(FIRST_SENTENCE_OVERFLOW, limit=200)
    assert "Filler sentence in the opening paragraph" in trimmed


def test_sols_take_flags_a_thin_body():
    """A body under the floor is reported as short, not published."""
    thin = "Too short to be a take. Just a sentence or two."
    _, status = sols_take.enforce_word_budget(thin)
    assert status == "short", f"expected 'short', got {status!r}"


def test_sols_take_accepts_a_body_inside_the_band():
    """A body inside 150-200 words is accepted."""
    body = " ".join(
        ["This is a reasonable sentence that carries the argument along."] * 18
    )
    assert 150 <= len(body.split()) <= 200
    text, status = sols_take.enforce_word_budget(body)
    assert status == "ok", f"expected 'ok', got {status!r}"
    assert 150 <= len(text.split()) <= 200


def test_sols_take_reports_a_body_over_the_ceiling():
    """A body the trim cannot bring under 200 words is reported as long.

    One enormous sentence with no internal boundary cannot be cut without
    breaking it mid-sentence, so the ceiling cannot be honoured — and the
    run must say so rather than publish it.
    """
    huge = " ".join(["word"] * 250)
    text, status = sols_take.enforce_word_budget(huge)
    assert status == "long", f"expected 'long', got {status!r}"
    assert len(text.split()) > 0, "the body was emptied rather than reported"


def test_sols_take_does_not_empty_a_body_that_is_one_fragment():
    """A body that is one unpunctuated sentence is not emptied.

    The mid-sentence guard drops a trailing fragment. When the whole body
    IS that fragment, dropping it would leave nothing to publish — an
    empty post is worse than one that ends abruptly.
    """
    huge = " ".join(["word"] * 250)
    text, _ = sols_take.enforce_word_budget(huge)
    assert len(text.split()) > 0, "the body was emptied"
    assert len(text.split()) == 250, "the body was altered"


def test_sols_take_keeps_the_closing_line():
    """The closing paragraph survives the trim — it is the punchline."""
    closing = "Pick the boring option that will not ruin your Tuesday."
    head = _para(
        "Here is a sentence of argument that carries the piece along nicely.", 12
    )
    body = head + "\n\n" + closing
    text, status = sols_take.enforce_word_budget(body)
    assert closing in text, "the closing line was cut by the trim"
    assert status in ("ok", "short"), f"unexpected status {status!r}"


def test_sols_take_trimmed_body_is_inside_the_ceiling():
    """After trimming, an over-long body is inside 200 words."""
    head = _para(
        "Here is a sentence of argument that carries the piece along nicely.", 40
    )
    closing = "Pick the boring option that will not ruin your Tuesday."
    body = head + "\n\n" + closing
    text, _ = sols_take.enforce_word_budget(body)
    assert len(text.split()) <= 200, (
        f"enforce_word_budget returned {len(text.split())} words"
    )


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
