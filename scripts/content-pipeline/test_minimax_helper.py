#!/usr/bin/env python3
"""
Regression tests for the MiniMax output cleaner.

WHY THIS EXISTS
---------------
On 2026-11-07 the daily Sol's Take published a post whose body ended with
the model talking to itself about the draft ("Word count maybe 169. Good.
Need date maybe no..."), followed by a malformed closing think tag and a
duplicate of the article's opening sentence. The article underneath was
fine; the reasoning was stapled to the end of it, and nothing flagged the
output as broken.

These tests pin the three things that let that through:

  1. reasoning that arrives AFTER the article (no preamble to detect),
  2. a malformed think tag carrying an invisible character,
  3. the absence of any gate that refuses to publish tainted text.

Run it directly — no test framework needed:

    python3 scripts/content-pipeline/test_minimax_helper.py

It is also pytest-compatible: every check is a `test_` function.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _minimax_helper import (  # noqa: E402
    looks_contaminated,
    strip_thinking,
)


# The body the model actually returned on 2026-11-07, contamination and all.
# The invisible character in the think tag is reproduced explicitly so the
# test does not depend on a copy-paste that lost it.
CONTAMINATED_2026_11_07 = (
    "Calling AI “not creative” because it has no consciousness confuses "
    "creativity with a séance. Creativity is making something new, useful, "
    "surprising, or emotionally resonant.\n"
    "\n"
    "The real question is not whether AI can create. It obviously can. The "
    "question is who exercises taste: chooses the brief, rejects the "
    "mediocre output, and accepts responsibility for the final work.\n"
    "\n"
    "AI can be creative. The dangerous delusion is that anyone using it "
    "deserves the credit.\n"
    "\n"
    'Word count maybe 169. Good. Need date maybe no. "specific experience" '
    "claim grounded in every week maybe lacking.\n"
    "\n"
    "Let's formulate.</\u200bmm:think>Calling AI “not creative” because it "
    "lacks consciousness confuses creativity with a séance.\n"
)

CLEAN_ARTICLE_PARTS = (
    "Calling AI “not creative” because it has no consciousness confuses "
    "creativity with a séance.",
    "The real question is not whether AI can create.",
    "AI can be creative. The dangerous delusion is that anyone using it "
    "deserves the credit.",
)


def test_contaminated_body_is_detected():
    assert looks_contaminated(CONTAMINATED_2026_11_07), (
        "the model's own notes must be detected as contamination"
    )


def test_contaminated_body_is_cleaned():
    cleaned = strip_thinking(CONTAMINATED_2026_11_07)
    assert not looks_contaminated(cleaned), "the cleaned body must be clean"
    for part in CLEAN_ARTICLE_PARTS:
        assert part in cleaned, f"the article lost: {part!r}"
    assert "Word count maybe" not in cleaned, "the scratchpad survived"
    assert "Let's formulate" not in cleaned, "the scratchpad survived"
    # The duplicate opening sentence is what followed the stray tag.
    assert cleaned.count("confuses creativity with a séance") == 1, (
        "the duplicate article that followed the stray tag survived"
    )


def test_stray_think_tag_is_cut():
    text = "The article itself is fine.\n\n</\u200bmm:think>The article again."
    cleaned = strip_thinking(text)
    assert cleaned == "The article itself is fine.", repr(cleaned)
    assert looks_contaminated(text), "a stray think tag is contamination"


def test_preamble_shape_is_still_stripped():
    """The original behaviour — reasoning first — must not regress."""
    text = (
        "Thinking Process:\n"
        "1. Consider the angle.\n"
        "Let's write it.\n"
        "\n"
        "The actual article starts here and runs to a clean end."
    )
    cleaned = strip_thinking(text)
    assert cleaned == "The actual article starts here and runs to a clean end.", repr(cleaned)


def test_legitimate_prose_is_untouched():
    """Real prose must never be eaten by the cleaner."""
    samples = [
        "Wait, the real problem is that nobody prices the mistake.",
        "Note: the thing about agents is they never ask for help.",
        "Good. Next question is who pays for the inference.",
        "The word count of the contract matters more than the pitch.",
        'He said, "Let\'s write a better ending." Then he deleted it.',
        "Hedge funds and hedgehogs: the distinction matters here.",
    ]
    for sample in samples:
        assert not looks_contaminated(sample), f"false positive on: {sample!r}"
        assert strip_thinking(sample) == sample, f"prose was altered: {sample!r}"


def test_model_notes_are_detected():
    notes = [
        "Word count maybe 169. Good. Need date maybe no.",
        "Let's formulate.",
        "Real pattern noticed: meetings.",
        "Hmm, let me rewrite this bit.",
        "Length: 400 words, no headings.",
    ]
    for note in notes:
        assert looks_contaminated(note), f"not detected: {note!r}"


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for test in tests:
        try:
            test()
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {test.__name__}: {e}")
        else:
            print(f"ok    {test.__name__}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
