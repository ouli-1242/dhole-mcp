"""Query-focused content filtering for smart_fetch.

When the agent passes ``focus="..."``, the extracted markdown is filtered to
the blocks (paragraphs / headings / tables / lists) most relevant to the query,
so the agent loads less context on long pages. Inspired by Crawl4AI's
BM25ContentFilter, implemented locally (no extra dep).

Design choice: focus runs **post-cache**. The full extracted text is cached
once per URL; different focus queries are just different views over the same
cached content, so focusing never causes a re-fetch and two different focuses
on the same URL share one cache entry. Filtering happens inside
``_apply_chunking`` (the universal final wrapper), so it applies to live
fetches, cache hits, and bulk results alike.

BM25 (k1=1.5, b=0.75) with an always-positive IDF (the ``+1`` inside the log)
so a block with a single query-term occurrence gets a positive score and is
kept. Selection is ANCHORED TO THE BEST BLOCK (``relative_threshold``), not to
the absolute score alone: with only an absolute cut, a natural-language question
kept 55-76% of a docs page because its ubiquitous terms ("what", "is", "the")
score positively in nearly every block — i.e. the documented usage
(``focus='question'``) was the case that saved the least context. A heading
immediately preceding a kept block is preserved for context. If nothing clears
the threshold, the closest blocks are kept so the agent gets something to judge
instead of an empty page.

Tokenization is Unicode-aware and covers spaceless scripts (Han, kana, Thai) by
bigramming — see ``_tokens``. It is not a language-specific segmenter: there is
no stemming and no dictionary, so a CJK query matches on character bigrams.
"""

from __future__ import annotations

import math
import re

# A "word run": letters/digits of any script, split at underscores and
# punctuation. Unicode-aware (\w) rather than [a-z0-9], so Cyrillic / Greek /
# Arabic / Korean queries are tokenized at all instead of silently yielding an
# empty term set.
_WORD_RUN_RE = re.compile(r"[^\W_]+", re.UNICODE)

# Scripts that are written without spaces between words. A run in one of these
# is a phrase, not a word, so it gets bigrammed (see _tokens).
_SPACELESS_CHAR_RE = re.compile(
    r"[\u0e00-\u0e7f"          # Thai
    r"\u1000-\u109f"           # Myanmar
    r"\u1780-\u17ff"           # Khmer
    r"\u3040-\u30ff"           # Hiragana + Katakana
    r"\u3400-\u4dbf"           # CJK ext A
    r"\u4e00-\u9fff"           # CJK unified
    r"\uf900-\ufaff"           # CJK compatibility
    r"\uff66-\uff9f]"          # halfwidth Katakana
)


def _is_spaceless(ch: str) -> bool:
    return bool(_SPACELESS_CHAR_RE.match(ch))


def _tokens(text: str) -> list[str]:
    """Tokenize for BM25: Unicode word runs, spaceless scripts bigrammed.

    ``[a-z0-9]+`` only ever matched ASCII, so a Chinese query produced an EMPTY
    term set and ``focus_content`` returned the page unchanged — no error, no
    note, just the full text the caller was passing ``focus`` to avoid. Nothing
    downstream could tell that apart from "every block is relevant".

    Han / kana / Thai runs have no word boundaries to split on and no segmenter
    is bundled, so each run is expanded into overlapping character bigrams
    ("如何创建任务" -> 如何, 何创, 创建, 建任, 任务). One token per run would match
    almost nothing (a whole sentence is not a term), and single characters would
    match almost everything. Bigrams are the standard segmenter-free middle.

    Boundaries are respected per script, so a mixed run like "Python教程" yields
    the word "python" plus the CJK bigrams instead of one unusable token.
    ASCII behaviour is unchanged: runs shorter than 2 characters are dropped.
    """
    out: list[str] = []
    for run in _WORD_RUN_RE.findall((text or "").lower()):
        i, n = 0, len(run)
        while i < n:
            spaceless = _is_spaceless(run[i])
            j = i
            while j < n and _is_spaceless(run[j]) == spaceless:
                j += 1
            piece = run[i:j]
            if spaceless:
                if len(piece) == 1:
                    out.append(piece)
                else:
                    out.extend(piece[k:k + 2] for k in range(len(piece) - 1))
            elif len(piece) >= 2:
                out.append(piece)
            i = j
    return out


def _is_heading(block: str) -> bool:
    """True if the block's first non-blank line is a valid ATX markdown heading."""
    return _heading_level(block) <= 6


def _heading_level(block: str) -> int:
    """Return heading level (1-6) or 99 if not a heading.

    ATX headings require a space (or EOL) after the # characters per CommonMark:
    '# Title' is valid, '#Title' is NOT a heading.
    """
    for line in block.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            rest = stripped[level:]
            # Valid ATX: '# ' or '#' alone (empty heading); '#H' is NOT a heading
            if level <= 6 and (not rest or rest[0] == " "):
                return level
            return 99
        if stripped:
            return 99
    return 99


def _is_table(block: str) -> bool:
    """True if the block looks like a markdown table (has | and --- separators)."""
    lines = [line for line in block.splitlines() if line.strip()]
    if len(lines) < 2:
        return False
    return "|" in lines[0] and "---" in lines[1] if len(lines) > 1 else False


def _is_code(block: str) -> bool:
    """Detect code blocks: fenced (```) or consistently indented."""
    stripped = block.strip()
    if stripped.startswith("```"):
        return True
    lines = block.splitlines()
    non_blank = [line for line in lines if line.strip()]
    if not non_blank or len(non_blank) < 2:
        return False
    indented = sum(
        1 for line in non_blank if line.startswith("    ") or line.startswith("\t")
    )
    return indented >= len(non_blank) * 0.8


def _split_blocks(text: str) -> list[str]:
    """Split markdown into blocks separated by blank lines. A block is a
    heading, paragraph, table, or list — kept verbatim (order preserved)."""
    blocks: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.strip() == "":
            if current:
                blocks.append("\n".join(current))
                current = []
        else:
            current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def focus_content(
    text: str,
    query: str,
    threshold: float = 1.0,
    k1: float = 1.5,
    b: float = 0.75,
    fallback_top: int = 5,
    relative_threshold: float = 0.33,
) -> str:
    """Return the blocks of ``text`` most relevant to ``query`` (BM25).

    Two cuts apply, and a block must clear BOTH: an absolute ``threshold``
    score, and ``relative_threshold`` x the best block's score. The relative
    cut exists because the absolute one alone cannot tell "this block is about
    the query" from "this block contains the word 'the'": BM25+ keeps IDF
    positive for every term, so a question's stopwords accumulate a small
    positive score in nearly every block (measured: 55-76% of a docs page kept,
    against 9% for a keyword query). ``threshold`` therefore acts as a floor —
    a page whose best block is itself weak keeps the old, permissive behaviour.

    If ``query`` is empty, the text has <= 1 block, or the query yields no
    usable terms, the original text is returned unchanged (focus is a no-op).
    """
    if not query or not text or not text.strip():
        return text
    blocks = _split_blocks(text)
    if len(blocks) <= 1:
        return text
    qterms = set(_tokens(query))
    if not qterms:
        return text

    block_tokens = [_tokens(bl) for bl in blocks]
    n = len(blocks)
    avgdl = (sum(len(t) for t in block_tokens) / n) if n else 0.0 or 1.0
    if avgdl == 0:
        avgdl = 1.0

    # Document frequency per term (across blocks).
    df: dict[str, int] = {}
    for toks in block_tokens:
        for t in set(toks):
            df[t] = df.get(t, 0) + 1

    def idf(term: str) -> float:
        d = df.get(term, 0)
        # +1 inside the log keeps IDF positive (BM25+ flavor) so a single
        # occurrence always scores > 0.
        return math.log((n - d + 0.5) / (d + 0.5) + 1)

    def score(i: int) -> float:
        toks = block_tokens[i]
        if not toks:
            return 0.0
        tf: dict[str, int] = {}
        for t in toks:
            tf[t] = tf.get(t, 0) + 1
        dl = len(toks)
        s = 0.0
        denom_len = k1 * (1 - b + b * dl / avgdl)
        for term in qterms:
            f = tf.get(term)
            if f:
                s += idf(term) * (f * (k1 + 1)) / (f + denom_len)
        return s

    scores = [score(i) for i in range(n)]

    # ── Pass 2: heading-aware boosting ──────────────────────────────────
    # When a heading contains query terms, boost all blocks under it (1.5x)
    # until the next heading at the same or higher level.
    for i in range(n):
        if not _is_heading(blocks[i]):
            continue
        heading_tokens = set(_tokens(blocks[i]))
        if not heading_tokens & qterms:
            continue
        h_level = _heading_level(blocks[i])
        scores[i] *= 1.5
        for j in range(i + 1, n):
            if _is_heading(blocks[j]) and _heading_level(blocks[j]) <= h_level:
                break
            scores[j] *= 1.5

    # ── Pass 3: table/code preservation ─────────────────────────────────
    # Tables/code containing ANY query term are always kept (high-value).
    preserved: set = set()
    for i in range(n):
        if _is_table(blocks[i]) or _is_code(blocks[i]):
            if set(block_tokens[i]) & qterms:
                preserved.add(i)

    # ── Selection ───────────────────────────────────────────────────────
    # A block must clear BOTH cuts. The relative one is what makes a SENTENCE
    # query work: BM25+ keeps idf > 0 for every term, so each ubiquitous word
    # ("what", "is", "the", "with") adds a small positive score to nearly every
    # block, and the sum clears an absolute threshold almost everywhere.
    # Measured on docs.python.org/3/library/asyncio-task.html (88 blocks):
    #   'TaskGroup'                                                ->  9% kept
    #   "How do I run tasks concurrently with gather?"              -> 55% kept
    #   "What is the difference between asyncio.gather and TaskGroup?" -> 76% kept
    # i.e. the documented usage (a question) saved the least context. Anchoring
    # to the best block's score keeps what the query is ABOUT and drops the
    # blocks that merely share its stopwords, without touching the keyword case
    # (there the best block IS the one clearing the threshold).
    # The anchor is the best CONTENT block, not the best block overall: a
    # heading is boosted 1.5x (Pass 2) and matches the query by being its
    # title, so anchoring on it can put `best/3` above the actual answer block
    # and drop it - TestHeadingAwareBM25 caught exactly that (the "##
    # Training Details" heading outscored the "Adam optimizer" paragraph it
    # introduces, which is the one carrying the query's rare term).
    content_scores = [s for i, s in enumerate(scores) if not _is_heading(blocks[i])]
    best = max(content_scores) if content_scores else (max(scores) if scores else 0.0)
    cut = max(threshold, best * relative_threshold)
    keep = [i for i in range(n) if scores[i] >= cut]
    if not keep:
        # Nothing cleared the threshold — keep the closest blocks so the agent
        # has something to judge rather than an empty response.
        keep = sorted(range(n), key=lambda i: scores[i], reverse=True)[:fallback_top]

    keep_set = set(keep) | preserved
    # Preserve a heading immediately preceding a kept non-heading block.
    for i in keep:
        if i > 0 and _is_heading(blocks[i - 1]) and not _is_heading(blocks[i]):
            keep_set.add(i - 1)

    kept = "\n\n".join(blocks[i] for i in range(n) if i in keep_set)
    header = (
        f"[Focus: {query!r}; showing {len(keep_set)} of {n} blocks "
        f"by BM25 relevance. Pass focus='' for the full page.]"
    )
    return header + "\n\n" + kept
