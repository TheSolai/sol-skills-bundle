"""
Shared prose helpers for the Sol AI content pipeline.

WHY THIS EXISTS
---------------
`weekly-roundup.py` grew a careful set of sentence-aware text helpers while
fixing the "Sol's Take" section (see OPERATIONS.md, "The weekly roundup's
'Sol's Take'"). The model consistently writes ~60% over the word budget no
matter how the prompt is worded, so the ceiling has to be enforced in code —
and it has to cut only at sentence boundaries, never mid-sentence.

`midweek-news.py` now generates the same kind of commentary and needs exactly
the same helpers. Rather than copy them (two definitions that drift) the
functions live here, and both generators import them.

The implementation is unchanged from the roundup's original — only the names
were made public (`split_sentences`, `ends_cleanly`, `trim_to_words`).
"""

import re


# Titles and abbreviations that end in a period but do not end a sentence.
_ABBREV_TAIL = re.compile(
    r"(?:\bDr|\bMr|\bMrs|\bMs|\bProf|\bInc|\bLtd|\bCo|\bCorp|\bJr|\bSr|"
    r"\bSt|\bvs|\bapprox|\bEst|\bNo|\bMt|\bFt|\be\.g|\bi\.e)\.$",
    re.IGNORECASE,
)
# A single capital letter + period is an initial ("J. Smith", "U.S.").
_INITIAL_TAIL = re.compile(r"\b[A-Z]\.$")


def _needs_merge(fragment):
    """True if this fragment ends in an abbreviation/initial, not a real stop."""
    if not fragment:
        return False
    return bool(_ABBREV_TAIL.search(fragment) or _INITIAL_TAIL.search(fragment))


def split_sentences(para):
    """
    Split a paragraph into sentences, preserving every original character.

    Uses finditer + slicing rather than re.split: re.split consumes the
    matched delimiter, which silently ate the sentence-ending punctuation
    and left the trimmed text running on mid-sentence.

    The lookbehind requires the terminator NOT be preceded by a capital
    letter, so "U.S." does not split. Fragments ending in a title or an
    initial ("Dr." / "J.") are re-merged with the sentence that follows.
    """
    terminator = re.compile(r"(?<![A-Z])[.!?][\"'”’)]*(?=\s+[A-Z“\"(\-])")
    sentences = []
    start = 0
    for m in terminator.finditer(para):
        chunk = para[start:m.end()].strip()
        if chunk:
            sentences.append(chunk)
        start = m.end()
    tail = para[start:].strip()
    if tail:
        sentences.append(tail)

    # Re-merge abbreviation fragments into the sentence that follows them.
    merged = []
    for sentence in sentences:
        if merged and _needs_merge(merged[-1]):
            merged[-1] = merged[-1] + " " + sentence
        else:
            merged.append(sentence)
    return merged


def ends_cleanly(sentence):
    """True if the sentence ends with terminal punctuation, not mid-thought."""
    return bool(sentence) and sentence.rstrip()[-1:] in ".!?\"'”’)"


def trim_to_words(text, limit=200):
    """
    Trim prose to a word ceiling, cutting only at sentence boundaries.

    The model consistently writes ~60% over the brief no matter how the
    prompt is worded, so the ceiling is enforced here instead. Working
    paragraph-by-paragraph preserves the blank lines between them.
    """
    if len(text.split()) <= limit:
        return text

    kept_paragraphs = []
    count = 0

    for para in [p.strip() for p in text.split("\n\n") if p.strip()]:
        if count >= limit:
            break
        kept = []
        for sentence in split_sentences(para):
            words = len(sentence.split())
            if count + words > limit and kept:
                break
            kept.append(sentence)
            count += words
        # A trailing fragment (no terminal punctuation) means the model was
        # cut off mid-sentence — drop it rather than publish it.
        if kept and not ends_cleanly(kept[-1]):
            count -= len(kept.pop().split())
        if kept:
            kept_paragraphs.append(" ".join(kept))

    trimmed = "\n\n".join(kept_paragraphs).strip()
    # If trimming gutted the piece, keep the original — a short comment is
    # worse than a slightly long one.
    if len(trimmed.split()) < 80:
        return text
    return trimmed
