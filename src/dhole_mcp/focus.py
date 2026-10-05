"""Query-focused content filtering for web_read.

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

import contextvars
import logging
import math
import re
from typing import Optional

logger = logging.getLogger("dhole-mcp.focus")

# ─── 本次调用要哪个 focus 打分器（按次参数 focus_engine，W15）───────────────
# 为什么这个状态住在 focus.py：读它的是两个消费者 —— server 的
# ``_apply_chunking``（web_read 与 bulk）和 crawl 的 ``_classify_and_extract``，
# 而 crawl 不能在模块级 import server（循环）。focus.py 只
# 依赖标准库，两边都能 import；contextvar 对 ``asyncio.to_thread`` 也是透传的，
# 所以爬取线程读到的是发起那次调用的值。
#
# 语义（17.0）：focus 打分引擎是**纯按次选择** —— DHOLE_NEURAL_FOCUS 许可门
# 已删除（写死判据：要不要每页 ~12.5s 的 CPU 推理是每一次调用的决定，不是一次
# 配置；没有全局默认可跟随）。
#   ""       走 BM25（~23ms/页的便宜默认；不传就是这样）
#   "bm25"   明确走便宜的路（与 "" 同义，写给读描述的人）
#   "neural" 明确要语义分（~12.5s/页）；模型缺失在工具入口显式报错，不静默下载
FOCUS_ENGINE: contextvars.ContextVar[str] = contextvars.ContextVar(
    "dhole.focus_engine", default="")
ENGINES = ("bm25", "neural")


def normalize_engine(value: object) -> str:
    """把客户端给的东西读成 'bm25' / 'neural' / ''。

    不可识别的值取 ''（= 不点名，走 BM25）。这条宽容现在只对**内部调用方**成立：
    17.0 摘掉线格式 enum 之后，调用方传来的拼错值在进这一层之前就被
    `params._coerce_enum_arg` 拒掉（并把四颗合法值一起报回去），所以走到这里的
    非 'bm25'/'neural' 字符串只可能是代码里传错的，不是用户手打的。
    """
    if isinstance(value, str):
        candidate = value.strip().lower()
        if candidate in ENGINES:
            return candidate
    return ""


def engine_wants_neural() -> bool:
    """本次调用**点名**语义打分了吗。只认按次的 ``focus_engine``：
    没点名（""）或点名 "bm25" 都是 BM25 —— 神经分是显式选择，从来不该默认发生。"""
    choice = FOCUS_ENGINE.get()
    if choice:
        return choice == "neural"
    return False

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


def _bm25_scores(
    blocks: list[str], block_tokens: list[list[str]], qterms: set, k1: float, b: float
) -> list[float]:
    """BM25+ block scores, including the heading-aware boost (pass 2).

    Extracted from ``focus_content`` so the neural scorer (M-1) can replace
    exactly the scoring step and inherit the rest of the pipeline — selection,
    table/code preservation, heading context — unchanged. The heading boost
    stays BM25-only: it is a band-aid for lexical matching, and a
    cross-encoder that reads the whole pair does not need heading terms
    inflating the blocks beneath them.
    """
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

    # Heading-aware boosting: when a heading contains query terms, boost all
    # blocks under it (1.5x) until the next heading at the same or higher level.
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

    return scores


# A (query, doc) pair is truncated at the cross-encoder's MAX_SEQ=512, so a
# block longer than the window gets scored on its opening and its tail is
# invisible to the model. The BM25 block size was tuned for lexical overlap;
# the encoder needs its own bound. ~300 chars is the safe ceiling: the default
# model's worst case is Han at ~1 token/char, which still leaves the query
# itself room inside 512.
_NEURAL_MAX_CHARS = 300

# Cost model (measured 2026-10-01 on README.md 94 blocks / CHANGELOG.md 140):
# wall time tracks the CHUNK count, not the block count -- 202 chunks 14.0s vs
# 214 chunks 14.8s, even though the block counts differ by 1.5x. Blocks of
# different sizes converge once split to <=300 chars, which is why the earlier
# "91 blocks vs 140 blocks cost about the same" reading looked like fixed
# overhead: what matched was the chunk counts, not a constant. ~69 ms/chunk for
# Han text (300 chars ~ 300 tokens, near MAX_SEQ).
#
# Do NOT "optimize" this by batching same-length chunks together. score() pads
# the whole batch to its longest member and the model is NOT padding invariant:
# the same chunk scores differently in a 300-char batch than in a 71-char one
# (measured max 2.2e-2), and a 4-way length split moved single scores by up to
# 0.34 and swapped 2-3 blocks in and out of the top 20. Length bucketing
# measured 37-66% faster and was REJECTED for changing the answer. Within one
# page the batch is fixed, so scores stay reproducible; they are simply not
# comparable ACROSS pages, and changing batch composition is a behaviour change.


def _split_for_encoder(block: str, cap: int = _NEURAL_MAX_CHARS) -> list[str]:
    """Split a block into pieces that fit the encoder window (long-line aware).

    Minified single-line documents must hard-wrap, or one piece still carries
    the whole line. Line-based accumulation keeps normal markdown blocks
    unsplit where they already fit.
    """
    if len(block) <= cap:
        return [block]
    pieces: list[str] = []
    cur = ""

    def _flush() -> None:
        nonlocal cur
        if cur.strip():
            pieces.append(cur)
        cur = ""

    for line in block.splitlines():
        while len(line) > cap:
            _flush()
            pieces.append(line[:cap])
            line = line[cap:]
        if cur and len(cur) + len(line) + 1 > cap:
            _flush()
        cur = f"{cur}\n{line}" if cur else line
    _flush()
    return pieces or [block[:cap]]


def _neural_block_scores(reranker, query: str, blocks: list[str]) -> Optional[list[float]]:
    """Cross-encoder score per block; None = the scorer itself failed.

    score() already answers an inference hiccup with all-zeros and a shape
    mismatch with a shorter list; both read as "the model is not answering"
    here, so they map to None and the caller falls back to BM25 instead of
    serving a page emptied by a broken scorer. Per-block score is the MAX over
    its chunks — a block is as relevant as its most relevant part.
    """
    try:
        chunk_map: list[tuple[int, str]] = []
        for i, bl in enumerate(blocks):
            for piece in _split_for_encoder(bl):
                chunk_map.append((i, piece))
        raw = reranker.score(query, [c for _, c in chunk_map])
        if len(raw) != len(chunk_map):
            return None
        out = [0.0] * len(blocks)
        for (i, _piece), s in zip(chunk_map, raw):
            if s > out[i]:
                out[i] = float(s)
        if all(s == 0.0 for s in out):
            return None
        return out
    except Exception as e:
        logger.warning("neural focus scoring failed: %s", e)
        return None


def _neural_scorer_note() -> str:
    try:
        from dhole_mcp.reranker import active_model
        return f"neural relevance ({active_model().name} cross-encoder)"
    except Exception:
        return "neural relevance (cross-encoder)"


def focus_content(
    text: str,
    query: str,
    threshold: float = 1.0,
    k1: float = 1.5,
    b: float = 0.75,
    fallback_top: int = 5,
    relative_threshold: float = 0.33,
    reranker=None,
    mode: str = "filter",
    stats: Optional[dict] = None,
) -> str:
    """Return the blocks of ``text`` most relevant to ``query``.

    Two cuts apply to the FILTER mode, and a block must clear BOTH: an absolute
    ``threshold`` score, and ``relative_threshold`` x the best block's score.
    The relative cut exists because the absolute one alone cannot tell "this
    block is about the query" from "this block contains the word 'the'": BM25+
    keeps IDF positive for every term, so a question's stopwords accumulate a
    small positive score in nearly every block (measured: 55-76% of a docs page
    kept, against 9% for a keyword query). ``threshold`` therefore acts as a
    floor — a page whose best block is itself weak keeps the old, permissive
    behaviour.

    If ``query`` is empty, the text has <= 1 block, or the query yields no
    usable terms, the original text is returned unchanged (focus is a no-op).

    ``reranker`` (M-1, neural focus): when a warm ``_Reranker`` is passed, every
    block is scored by the local cross-encoder instead of BM25. The scorer
    never loads a model and never downloads — callers (``_apply_chunking``) peek
    ``get_reranker()`` only when this call named ``focus_engine='neural'`` (17.0:
    there is no permission flag; the load is arranged at the tool entry, and a
    named-but-missing model is refused there rather than downgraded); a
    None/failed scorer here falls back to BM25 with the header saying so. The
    cross-encoder does not need the heading
    boost (it reads the pair, not term overlap), and the absolute ``threshold``
    does not apply — the sigmoid scale has its own geometry (on-topic ~0.9+,
    off-topic ~0.001), so only the best-anchored relative cut is used.

    ``mode="best"`` (M-2): return only the single highest-scoring block plus
    its immediately preceding heading — "which part of this page answers the
    question" — instead of every block past the cut.

    ``stats`` (out-param, M-3): ``{"scorer": "neural"|"bm25", "best_score": ...}``
    lets the caller report an intent-miss hint without reparsing the output.
    ``best_score`` is filled only for the neural scorer — BM25's unbounded
    scale has no calibrated reading.
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

    stats_out = stats if isinstance(stats, dict) else None

    scorer_note = "BM25 relevance"
    neural_used = False
    scores: Optional[list[float]] = None
    if reranker is not None:
        neural = _neural_block_scores(reranker, query, blocks)
        if neural is not None:
            scores = neural
            neural_used = True
            scorer_note = _neural_scorer_note()
    if scores is None:
        scores = _bm25_scores(blocks, block_tokens, qterms, k1, b)

    # Tables/code containing ANY query term are always kept (high-value) —
    # this deterministic safety net covers both scorers.
    preserved: set = set()
    for i in range(n):
        if _is_table(blocks[i]) or _is_code(blocks[i]):
            if set(block_tokens[i]) & qterms:
                preserved.add(i)

    # Selection: anchored to the best CONTENT block, not the best block overall
    # (a heading is boosted 1.5x under BM25 and matches the query by being its
    # title — anchoring on it can drop the answer block it introduces). The
    # neural path skips the absolute threshold: the sigmoid scale has no
    # BM25-style floor, and the relative cut already drops the 0.0001 tail.
    content_scores = [s for i, s in enumerate(scores) if not _is_heading(blocks[i])]
    best = max(content_scores) if content_scores else (max(scores) if scores else 0.0)
    if stats_out is not None:
        stats_out["scorer"] = "neural" if neural_used else "bm25"
        stats_out["best_score"] = round(best, 4) if neural_used else None
    cut = best * relative_threshold
    if not neural_used:
        cut = max(threshold, cut)
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

    if mode == "best":
        top = max(keep, key=lambda i: scores[i])
        top_set = {top}
        if top > 0 and _is_heading(blocks[top - 1]) and not _is_heading(blocks[top]):
            top_set.add(top - 1)
        header = (
            f"[Focus best block: {query!r}; top of {n} by {scorer_note}. "
            f"Pass focus='' for the full page, or keep mode unset for the filtered page.]"
        )
        if len(blocks[top].strip()) < 200:
            # The BM25 arm has no calibrated score to name a weak hit (unlike
            # the neural intent floor), but a tiny best block is checkable
            # without one: a 30-char "best answer" is usually just a heading.
            header += (
                f" The best block is only {len(blocks[top].strip())} chars - "
                "possibly just a heading with no answer; keep mode unset "
                "(filter) or reword the focus query.")
        return header + "\n\n" + "\n\n".join(blocks[i] for i in range(n) if i in top_set)

    kept = "\n\n".join(blocks[i] for i in range(n) if i in keep_set)
    header = (
        f"[Focus: {query!r}; showing {len(keep_set)} of {n} blocks "
        f"by {scorer_note}. Pass focus='' for the full page.]"
    )
    return header + "\n\n" + kept
