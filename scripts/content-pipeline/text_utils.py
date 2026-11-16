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


# ── Repeated-sentence detection ────────────────────────────────────────────
#
# WHY (2026-11-16)
# ---------------
# The 2026-11-16 Sol's Take shipped with the sentence "Synthetic data is not
# poison." three times in a row. Nothing caught it. The word budget was
# satisfied (196 words, inside the band), the post carried no contamination,
# the front matter was clean — and the reader saw a model that had looped.
#
# The fix has to be careful, because repeating a sentence is not always a
# defect. The weekly roundup's "Sol's Take" names each story by its actual
# title, and those titles are also printed as the headline list above them.
# That is deliberate quoting, and a naive duplicate detector flags it as a
# defect that it is not.
#
# So the two ideas are kept apart:
#
#   * `find_repeated_sentences()` reports every sentence that appears more
#     than once. It is a signal, not a verdict — too blunt to be a hard gate
#     on its own.
#   * `collapse_repeated_sentences()` removes a later copy, but only when it
#     is safe to do so: the sentence stands alone, the paragraph keeps at
#     least one sentence, and removing it does not unbalance a quotation.
#
# `consecutive_only=True` narrows both to the unambiguous case — a sentence
# immediately following an identical one. No one writes the same sentence
# twice in a row on purpose, so it has no false positives.

# A sentence counts as "the same" when its words and digits match, whatever
# the capitalisation, the quoting or the terminal punctuation. The match is
# deliberately loose: the defect being caught is a model repeating itself
# verbatim, and a loose match still catches that without needing to
# understand the prose.
def _sentence_key(sentence):
    return re.sub(r"[^a-z0-9]+", " ", sentence.lower()).strip()


# A model that loops does not always repeat a sentence byte for byte. The
# 2026-11-16 post read:
#
#     Now, synthetic data is not poison. Synthetic data is not poison.
#     Synthetic data is not poison.
#
# The first copy carries a discourse marker the other two do not, so an exact
# match saw two repeats instead of three. A short prefix in front of an
# otherwise identical sentence is the same sentence to a reader, so it counts
# as one — but only when the prefix is short, or two genuinely different
# sentences that happen to share a tail would be mistaken for one.
MAX_PREFIX_WORDS = 3


def _same_sentence(key_a, key_b):
    """True when two sentence keys describe the same sentence.

    Equal keys are the same sentence. So is a key that ends with the other
    and carries only a short prefix in front — that is the re-prefixed
    repeat described above.
    """
    if key_a == key_b:
        return True
    a = key_a.split()
    b = key_b.split()
    if not a or not b:
        return False
    longer, shorter = (a, b) if len(a) > len(b) else (b, a)
    if len(longer) - len(shorter) > MAX_PREFIX_WORDS:
        return False
    return longer[-len(shorter):] == shorter


# Short sentences ("It is not.", "Right.") repeat in real prose without
# being a defect, so a repeat only counts when the sentence carries enough
# words to be a statement rather than an interjection.
MIN_REPEAT_WORDS = 4


def _quote_signature(text):
    """The count of each DOUBLE-quote character in the text.

    Only double quotes are counted. A straight or curly apostrophe is
    usually part of a contraction ("Alpha's", "it's"), so its count says
    nothing about balance — but an unmatched double quote is a visible hole
    in the prose.
    """
    return (
        text.count('"'),
        text.count("“"),
        text.count("”"),
    )


def _iter_sentences(text):
    """Yield (paragraph_text, sentences) for each paragraph in the body."""
    for para in [p.strip() for p in text.split("\n\n") if p.strip()]:
        yield para, split_sentences(para)


def find_repeated_sentences(text, min_words=MIN_REPEAT_WORDS, consecutive_only=False):
    """Report the sentences that appear more than once in `text`.

    Returns a list of dicts, in document order: `{"sentence": str,
    "count": int}`. The sentence text is the FIRST occurrence, verbatim.

    With `consecutive_only`, only a sentence that immediately follows an
    identical one counts, and `count` is the size of the run (a sentence
    repeated three times in a row reports a count of 3).
    """
    results = []
    seen = []
    previous = None

    for _, sentences in _iter_sentences(text):
        for sentence in sentences:
            key = _sentence_key(sentence)
            if len(sentence.split()) < min_words or not key:
                previous = None
                continue

            match = None
            if consecutive_only:
                # Only the sentence immediately before can match.
                if previous is not None and _same_sentence(key, previous["key"]):
                    match = previous
            else:
                for entry in seen:
                    if _same_sentence(key, entry["key"]):
                        match = entry
                        break

            if match is None:
                entry = {"key": key, "sentence": sentence, "count": 1}
                seen.append(entry)
                results.append(entry)
                previous = entry
            else:
                match["count"] += 1
                previous = match

    return [entry for entry in results if entry["count"] > 1]


def collapse_repeated_sentences(text, min_words=MIN_REPEAT_WORDS, consecutive_only=False):
    """Remove later copies of a sentence that already appears earlier.

    Returns `(body, removed)` where `removed` is the number of sentences
    dropped. The body is returned BYTE-IDENTICAL when nothing was removed,
    so calling this on a clean body is a genuine no-op.

    A sentence is only dropped when the drop is safe:

      * it stands alone as a whole sentence — never a fragment of one;
      * the drop does not change the number of double quotes, so it cannot
        leave a dangling quotation mark behind.

    A paragraph whose every sentence was a repeat is dropped whole: its
    content is already elsewhere in the body, so removing it loses nothing.

    That quote guard is what keeps the weekly roundup's deliberate quoting
    intact: the story title appears both as a headline and inside the
    commentary, and the commentary's copy carries the opening quotation
    mark, so it is left alone.
    """
    if not text:
        return text, 0

    seen = []
    previous = None
    removed = 0
    paragraphs = []

    for para, sentences in _iter_sentences(text):
        kept = []
        dropped_here = 0
        for sentence in sentences:
            key = _sentence_key(sentence)
            too_short = len(sentence.split()) < min_words or not key

            if too_short:
                is_repeat = False
            elif consecutive_only:
                is_repeat = previous is not None and _same_sentence(key, previous["key"])
            else:
                is_repeat = any(_same_sentence(key, entry["key"]) for entry in seen)

            if is_repeat:
                with_sentence = " ".join(kept + [sentence])
                without_sentence = " ".join(kept)
                if _quote_signature(with_sentence) == _quote_signature(without_sentence):
                    removed += 1
                    dropped_here += 1
                    previous = {"key": key, "sentence": sentence}
                    continue

            kept.append(sentence)
            if not too_short:
                entry = {"key": key, "sentence": sentence}
                seen.append(entry)
                previous = entry
            else:
                previous = None

        if not kept:
            # Every sentence in this paragraph was a repeat of a sentence
            # that is still in the body, so the paragraph is dropped whole.
            continue
        if dropped_here:
            paragraphs.append(" ".join(kept))
        else:
            # Nothing was removed from this paragraph, so keep it exactly as
            # it was. A paragraph can carry its own internal line breaks
            # (a list, a heading) and rebuilding it from split sentences
            # would flatten them.
            paragraphs.append(para)

    if not removed:
        return text, 0
    return "\n\n".join(paragraphs).strip(), removed


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

    WHY THE FIRST SENTENCE NEEDS ITS OWN GUARD (2026-11-10)
    -------------------------------------------------------
    The loop used to read:

        if count + words > limit and kept:
            break

    The `and kept` made the limit conditional on the paragraph already
    holding a sentence. For a paragraph's FIRST sentence `kept` is empty,
    so the check never fired and the sentence was kept even when it
    pushed the body past the ceiling. The 2026-11-07 Sol's Take shipped
    at 206 words against a 200-word brief because of it.

    The first sentence is now subject to the same limit as every other
    one. When it does not fit, the trim stops at the paragraph boundary
    instead of skipping ahead — skipping would leave a hole in the
    middle of the piece, and the body would no longer read as written.
    """
    if len(text.split()) <= limit:
        return text

    kept_paragraphs = []
    count = 0
    stop = False

    for para in [p.strip() for p in text.split("\n\n") if p.strip()]:
        if count >= limit or stop:
            break
        kept = []
        for sentence in split_sentences(para):
            words = len(sentence.split())
            if count + words > limit:
                if kept:
                    # This paragraph already has a sentence, so stop here
                    # and leave the rest of it out.
                    break
                if count > 0:
                    # The paragraph's first sentence does not fit in what
                    # is left of the budget. Stop the trim at the paragraph
                    # boundary rather than overrun the ceiling.
                    stop = True
                    break
                # Nothing has been kept yet and even the opening sentence
                # is longer than the whole budget. Keep it: cutting a
                # sentence in half is not an option, and there is nothing
                # else to publish.
            kept.append(sentence)
            count += words
        # A trailing fragment (no terminal punctuation) means the model was
        # cut off mid-sentence — drop it rather than publish it.
        if kept and not ends_cleanly(kept[-1]):
            count -= len(kept.pop().split())
        if kept:
            kept_paragraphs.append(" ".join(kept))

    trimmed = "\n\n".join(kept_paragraphs).strip()
    if not trimmed:
        return text
    # If trimming gutted the piece, keep the original — a short comment is
    # worse than a slightly long one. But only when the original is merely
    # a little over the ceiling: a body that is wildly over the limit is
    # worse than a short one.
    if len(trimmed.split()) < 80 and len(text.split()) <= limit * 1.2:
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
