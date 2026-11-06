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


# ── Structure-aware trimming ───────────────────────────────────────────────

# A regional post is not a wall of prose: it has a title line, section
# headings, a "What this means" analysis, a takeaway and a source line.
# `trim_to_words()` cuts from the end of a block of prose, which for one
# of these bodies would eat the takeaway and the analysis — the parts the
# prompt explicitly requires. The helpers below trim the *expendable*
# prose instead, and never touch the structural lines.

_HEADING_RE = re.compile(r"^#{1,6}\s")
_DIVIDER_RE = re.compile(r"^---+$")
_SOURCE_RE = re.compile(r"^\*Source:")
# "**Takeaway:** ...", "**The takeaway**", "**One-sentence takeaway**".
_TAKEAWAY_RE = re.compile(r"^\*\*[^*]{0,40}\btakeaway\b", re.IGNORECASE)


def split_blocks(text):
    """Split a post body into its blocks — paragraphs separated by blank lines."""
    return text.split("\n\n")


def _first_line(block):
    for line in block.splitlines():
        if line.strip():
            return line.strip()
    return ""


def classify_blocks(blocks):
    """Label each block so the trim knows what it may cut.

    Labels: `blank`, `divider`, `source`, `title`, `heading`, `takeaway`,
    `prose`. The first non-blank block is the `title` — it is protected
    whatever shape it takes (an H1, an H3 or a bold line).
    """
    kinds = []
    seen_body = False
    for block in blocks:
        line = _first_line(block)
        if not line:
            kinds.append("blank")
        elif _DIVIDER_RE.match(line):
            kinds.append("divider")
        elif _SOURCE_RE.match(line):
            kinds.append("source")
        elif not seen_body:
            kinds.append("title")
        elif _HEADING_RE.match(line):
            kinds.append("heading")
        elif _TAKEAWAY_RE.match(line):
            kinds.append("takeaway")
        else:
            kinds.append("prose")
        if kinds[-1] not in ("blank", "divider"):
            seen_body = True
    return kinds


def budget_words(blocks, kinds):
    """Words that count against the budget.

    The measure is the one OPERATIONS.md uses when comparing published
    posts: the title line, the section headings, the `---` dividers and
    the `*Source:*` line are excluded, and everything else counts.
    """
    total = 0
    for block, kind in zip(blocks, kinds):
        if kind in ("title", "divider", "source", "heading", "blank"):
            continue
        total += len(block.split())
    return total


def trim_to_word_budget(body, limit=600, floor=400):
    """Trim a structured post body to a word ceiling, keeping its structure.

    WHY (2026-11-06): `run-daily.py` asks for 400-600 words and does not
    enforce it — the prompt alone does not hold a word budget. Published
    regional posts ran to 708 words. This is the structure-aware
    equivalent of `trim_to_words()` for a body that has sections.

    What is protected: the title line, every section heading, the
    takeaway, the source line, and the final paragraph of the body (which
    carries the conclusion). What is cut: whole sentences from the
    expendable prose — the lead first, then the analysis — never
    mid-sentence, and never below one sentence per paragraph.

    Returns the body unchanged when it is already within the limit.
    """
    blocks = split_blocks(body)
    kinds = classify_blocks(blocks)
    total = budget_words(blocks, kinds)
    if total <= limit:
        return body

    prose = [i for i, k in enumerate(kinds) if k == "prose"]
    if not prose:
        return body

    # The first section heading splits the lead from the analysis.
    first_heading = next((i for i, k in enumerate(kinds) if k == "heading"), None)
    if first_heading is None:
        lead, analysis = prose, []
    else:
        lead = [i for i in prose if i < first_heading]
        analysis = [i for i in prose if i > first_heading]

    # The last prose paragraph holds the conclusion — cut it only as a
    # last resort, once the lead and the analysis have given up their
    # expendable sentences.
    final_prose = prose[-1]

    cut_order = [i for i in reversed(lead) if i != final_prose]
    cut_order += [i for i in reversed(analysis) if i != final_prose]
    if final_prose not in cut_order:
        cut_order.append(final_prose)

    over = total - limit
    current = total
    dropped_blocks = set()

    for i in cut_order:
        if over <= 0:
            break
        sentences = split_sentences(blocks[i].strip())
        if not sentences:
            continue

        # The paragraph's full word count, and how many of its words are
        # already committed to being cut.
        para_words = len(blocks[i].split())
        shed = 0

        # First, cut whole sentences from the end of the paragraph, keeping
        # at least one sentence.
        while len(sentences) > 1 and over - shed > 0:
            dropped = len(sentences[-1].split())
            # Never cut the body below the floor: a short post is a failed
            # post, and the ceiling is the thing being fixed.
            if current - shed - dropped < floor:
                break
            sentences.pop()
            shed += dropped

        if over - shed <= 0:
            # This paragraph alone brings the body under the ceiling.
            blocks[i] = " ".join(sentences)
            current -= shed
            over -= shed
            continue

        # Still over the ceiling and the paragraph cannot give up any more
        # sentences (it is down to one). Dropping it whole is the only way
        # to shed the rest. A heading whose only paragraph was dropped is
        # removed further down, so no section is left dangling.
        if current - para_words < floor:
            # Cutting this paragraph would starve the post. Leave it and
            # move on - a slightly long post beats a short one.
            blocks[i] = " ".join(sentences)
            current -= shed
            over -= shed
            continue
        dropped_blocks.add(i)
        current -= para_words
        over -= para_words


    if not dropped_blocks:
        # Only sentences were cut; no paragraph was removed wholesale.
        return "\n\n".join(blocks).strip()

    kept = [b for i, b in enumerate(blocks) if i not in dropped_blocks]

    # A section heading whose only paragraph was dropped would dangle, so
    # drop the heading too — a heading with nothing under it reads as a
    # formatting error rather than a section.
    kept_kinds = classify_blocks(kept)
    final = []
    for i, (b, k) in enumerate(zip(kept, kept_kinds)):
        if k == "heading":
            following = kept_kinds[i + 1: i + 2]
            if not following or following[0] not in ("prose", "takeaway"):
                continue
        final.append(b)

    return "\n\n".join(final).strip()
