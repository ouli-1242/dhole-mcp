"""Flagship PDF extraction for Dhole — optimized for AI agents, v6.

Turns a PDF's bytes into clean, structured markdown that an agent can reason
over, with HONEST quality signals so the agent can trust the output and a
per-page auto-OCR fallback that recovers text from broken font mappings (the
CID-corruption problem no lightweight extractor handles).

Built on ``pdfplumber`` (MIT, on pdfminer.six — both MIT, no AGPL) for text +
tables + layout + font data, and ``pypdfium2`` (BSD-3) for the PDF outline
(bookmarks/ToC) and for rendering CID-corrupted pages to images so ``rapidocr``
can read the VISIBLE text. All pure-pip, no system binaries, MIT-compatible.

The flagship trick — auto-OCR for CID-corrupted pages:
  Some PDFs embed font subsets without a ToUnicode CMap, so pdfplumber emits
  ``(cid:71)(cid:302)...`` garbage for those fonts (architecture diagrams,
  figures, math). But the glyphs RENDER correctly visually — only the
  text-to-unicode map is broken. So when a page's CID-garbage ratio is high,
  dhole renders that page to an image via pypdfium2 and OCRs it with rapidocr,
  recovering the real text. This reuses the OCR deps dhole already ships and
  turns the #1 PDF-extraction failure mode (CID garbage) into readable content.
  If OCR extras aren't installed, the page keeps a low quality_score + an honest
  marker so the agent knows to use a vision tool.

Agent design:
  * Markdown structure (headings / lists / tables) — agents reason best over it.
  * A metadata header up top so the agent can decide relevance before reading.
  * ``--- Page N ---`` markers (PDF page labels when available) for citation.
  * ``table_of_contents`` field from the PDF outline tree (books / reports).
  * ``quality_score`` (0-1, readable-char ratio) + honest ``content_ok`` so the
    agent can distinguish a clean extraction from a garbled one (the old
    content_ok=true-despite-corruption bug).
  * ``metadata`` populated with title/author/subject/keywords/creator/producer/
    dates — programmatically available, not just in the markdown header.
  * Page-range param (``pages="1-5"``) — extract only what you need.
  * ``include_media`` -> per-page embedded-image metadata (count + dimensions)
    so multimodal agents know which pages have figures to screenshot.
  * ``metadata.figures`` (always on, never inlined) — which pages draw something
    the text path cannot carry. A text reader that returns only a caption has to
    say so, or "no figures" and "the figure did not come back" are one sentence.
  * Honest ``scanned`` / ``encrypted`` detection with actionable errors.
"""

from __future__ import annotations

import io
import logging
import re
import statistics
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

logger = logging.getLogger("dhole-mcp.pdf")

# A page is considered scanned/image-only if it yields fewer than this many
# characters of extractable text on average.
_SCANNED_CHARS_PER_PAGE = 20

# Word-grouping x tolerance. pdfplumber's default (3) jams words together on
# PDFs that position words with tight inter-word gaps. 1.5 splits them correctly.
_X_TOL = 1.5

# Heading detection thresholds (ratio of a line's dominant font size to the
# document body size). Conservative: only short, non-sentence lines qualify.
_H1_RATIO = 2.0
_H2_RATIO = 1.55
_H3_RATIO = 1.25
_HEADING_MAX_LEN = 200
_SENTENCE_END = ". , ; : ? ! ) ]".split()

# CID font garbage: pdfplumber emits "(cid:NN)" for glyphs whose embedded font
# lacks a ToUnicode CMap. When a page's CID-garbage ratio exceeds this, dhole
# renders + OCRs that page to recover the real visible text.
_CID_RE = re.compile(r"\(cid:\d+\)")
_CID_RATIO_THRESHOLD = 0.30
# Below this readable-char ratio, content_ok is False (the doc is too garbled to
# trust, even after OCR). Tuned so a doc with one bad page out of many stays ok.
_QUALITY_OK_THRESHOLD = 0.70


@dataclass
class PdfResult:
    """Result of a PDF extraction. The caller maps this into a ResponseModel."""
    content: list[str] = field(default_factory=list)
    title: str = ""
    author: str = ""
    subject: str = ""
    keywords: str = ""
    pages_total: int = 0
    pages_extracted: list[int] = field(default_factory=list)
    scanned: bool = False
    encrypted: bool = False
    error: str = ""
    # v6 additions:
    metadata: dict[str, Any] = field(default_factory=dict)
    table_of_contents: list[dict] = field(default_factory=list)
    quality_score: float = 0.0
    content_ok: bool = False
    media: list[str] = field(default_factory=list)
    ocr_fallback_used: bool = False
    cid_pages_ocr: list[int] = field(default_factory=list)


def ocr_advice() -> str:
    """How to get text OCR did not produce, based on whether OCR is installed.

    "Install dhole-mcp[all]" is a factual claim about the machine, and on a
    machine that already has the extra it is a false one: it reports "this path
    produced no OCR text" as "you have not installed OCR". A live run lost its
    first hour to exactly that — the conclusion recorded was "this machine has
    no [all]", and the interpreter said otherwise. Real users would reinstall a
    package they already have.

    One source of truth for every OCR hint in the codebase (the all-scanned
    dead-end, the two per-page markers, and web_read's next_action), so the
    four cannot drift into disagreeing about the same machine.
    """
    try:
        from dhole_mcp.ocr import ocr_available
        ready = bool(ocr_available())
    except Exception:
        ready = False
    if ready:
        return ("OCR is installed but recovered no text here - use a vision tool / "
                "a web_act screenshot action, or another source.")
    from dhole_mcp import install as _install
    return ("Install OCR support with `" + _install.full_install_command() + "` and "
            "dhole will auto-OCR it; or use a vision-capable tool / screenshot.")


def recover_scanned_pdf(
    result: PdfResult,
    body: bytes,
    *,
    pages: Optional[str] = None,
    password: Optional[str] = None,
) -> PdfResult:
    """OCR-recover an all-scanned PDF (the ``scanned=True`` dead-end above).

    Shared by the URL path (server.py's PDF branch, i.e. web_read) and the
    local-file path (parse._parse_pdf) so the two behave identically — which is
    what ``parse._parse_pdf``'s docstring promises, and what the URL path alone
    violated: a scanned PDF read from disk answered "install [all] to auto-OCR"
    while the SAME BYTES over HTTP were OCR'd successfully.

    A no-op unless the extractor already decided this is an all-scanned,
    unencrypted document, so both call sites can invoke it unconditionally.

    Returns the result to use:
      * OCR worked -> the same object, content/quality swapped in;
      * the OCR path surfaced encryption -> the OCR path's own result;
      * OCR attempted and failed -> the same object carrying ``ocr_failed``;
      * OCR extras missing, or the document is not an all-scanned one -> unchanged.
    """
    if not (result.scanned and not result.encrypted):
        return result
    try:
        from dhole_mcp.ocr import ocr_available, ocr_pdf
        if not ocr_available():
            return result
        ocr_result = ocr_pdf(body, pages=pages, password=password)
        if ocr_result.content and not ocr_result.error:
            # Swap content but keep the text pass's metadata/toc.
            result.content = ocr_result.content
            result.error = ""
            result.scanned = False
            result.ocr_fallback_used = True
            result.quality_score = max(result.quality_score or 0.0, 0.9)
            result.content_ok = True
            return result
        if ocr_result.error and ocr_result.encrypted:
            return ocr_result  # encrypted surfaced by OCR path too
        if ocr_result.error:
            result.content = [
                f"[Scanned PDF - OCR attempted but failed: {ocr_result.error[:160]}]"]
            result.error = f"ocr_failed: {ocr_result.error[:160]}"
        return result
    except ImportError:
        # OCR extras not installed -> keep the scanned dead-end the caller has.
        return result
    except Exception as e:
        logger.debug("OCR fallback failed: %s", e)
        return result


def _get_pdfplumber():
    """Lazy import pdfplumber (optional [all] dependency)."""
    try:
        import pdfplumber  # type: ignore
        return pdfplumber
    except ImportError as e:
        from dhole_mcp import install as _install
        raise ImportError(
            f"PDF extraction requires pdfplumber. Run: {_install.full_install_command()}"
        ) from e


def _encryption_problem(exc: BaseException) -> str | None:
    """把"打不开"分成 口令问题 / 加密方案不支持 / 其他，返回 'password'/'unsupported'/None。

    判据必须落在**类型**上：pdfplumber 把 pdfminer 的 PDFPasswordIncorrect 包成
    `PdfminerException` 且 str() 是空串（实测），所以原来那句按消息文字匹配的
    `"password" in msg or "encrypt" in msg` 在真机上永远命不中 —— 结果是一条
    `pdf_open_failed: `（连原因都没有）加 `encrypted=False`：口令保护看起来像文件损坏。
    异常链要一起看：原始类型藏在 args/__context__ 里。
    """
    chain: list[BaseException] = [exc]
    for e in chain:
        for cand in list(getattr(e, "args", ()) or ()) + [getattr(e, "__context__", None)]:
            if isinstance(cand, BaseException) and cand not in chain:
                chain.append(cand)
        if len(chain) > 8:
            break
    for e in chain:
        name = type(e).__name__.lower()
        if "password" in name:
            return "password"
        if "encrypt" in name:
            return "unsupported"
        msg = str(e).lower()
        if "password" in msg:
            return "password"
        if "encrypt" in msg or "not supported" in msg:
            return "unsupported"
    return None


def _parse_pages(spec: str | None, total: int) -> list[int]:
    """Parse a page spec like '1-5', '1,3,5-7', '1, 2' into a sorted unique
    list of 1-indexed page numbers clamped to [1, total]. None -> all pages.

    The clamp is the DOCUMENT's size, not a configured range, so it is said in
    those words ("of 12 page(s)") — a caller who asked for pages 5-999 of a
    12-page file needs to know the file ended, not that some limit exists.
    A range entirely outside the file is a different outcome and stays an error
    (`no_pages_in_range` in the caller); this note covers the partial case, where
    some pages came back and the rest silently did not.
    """
    if not spec or not spec.strip():
        return list(range(1, total + 1))
    out: set[int] = set()
    trimmed = False
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            try:
                lo_i, hi_i = int(lo), int(hi)
            except ValueError:
                continue
            if lo_i > hi_i:
                lo_i, hi_i = hi_i, lo_i
            if lo_i < 1 or hi_i > total:
                trimmed = True
            out.update(range(max(1, lo_i), min(total, hi_i) + 1))
        else:
            try:
                n = int(part)
            except ValueError:
                continue
            if 1 <= n <= total:
                out.add(n)
            else:
                trimmed = True
    if out and trimmed:
        _announce_page_clamp(spec, sorted(out), total)
    return sorted(out)


def _announce_page_clamp(spec: str, applied: list[int], total: int) -> None:
    """Say so when the page list came back shorter than the spec asked for."""
    from dhole_mcp.context import _note_text
    got = (f"{applied[0]}-{applied[-1]}" if len(applied) > 1 else str(applied[0]))
    _note_text(f"pages clamped '{spec}' -> '{got}' of {total} page(s) "
               f"(supported range 1-{total})")


def _clean_text(s: str) -> str:
    return re.sub(r"[ \t]+", " ", s).strip()


# ── 表格几何：合并格在 `extract()` 之前就得读出来 ───────────────────────────
#
# `Table.extract()` 交回的是已经补过齐的 `list[list[str | None]]`：跨列、跨行与
# "这一格真的没数据"在它的输出里是同一个 `None`，几何那时就没了。所以形状要从
# `table.rows[i].cells` 那一层拿 —— 那里 `None` 说的是"这个格位被别的格子盖住了"，
# 而不是"这里是空的"，一句之差就是 P1 全部的内容。
#
# 一条对计划的更正：`explicit_physical_rows` / `explicit_vertical_edges` 在
# pdfplumber 0.11.10 上不存在（实测 AttributeError），所以边界是从 `row.cells` 的
# bbox 集合自己推的 —— 同一张表里所有格子的 x/y 边缘，就是这张表的列边界与行边界。


def _edge_positions(values: Iterable[float], tol: float = 0.75) -> list[float]:
    """把一堆边缘坐标收成去重后的边界序列（浮点边按 tol 合并）。"""
    out: list[float] = []
    for v in sorted(values):
        if not out or v - out[-1] > tol:
            out.append(v)
    return out


def _nearest(edges: list[float], value: float) -> int:
    return min(range(len(edges)), key=lambda i: abs(edges[i] - value))


def table_geometry(table: Any) -> list[list[dict]] | None:
    """One row per table row, one dict per grid position: text + what it spans.

    Returns None when the geometry cannot be read (no rows, or `extract()`
    disagrees with `rows` about the shape) — the caller then falls back to the
    padded `extract()` output, which is what this module did before.

    Keys per cell: ``text``, ``covered`` (this position is owned by another
    cell's span), ``rspan``/``cspan`` (1 when the cell is a plain grid square).
    """
    try:
        rows = list(table.rows or [])
        texts = table.extract()
    except Exception:
        return None
    if not rows:
        return None
    bboxes = [c for r in rows for c in (r.cells or []) if c]
    if not bboxes:
        return None
    xs = _edge_positions([b[0] for b in bboxes] + [b[2] for b in bboxes])
    ys = _edge_positions([b[1] for b in bboxes] + [b[3] for b in bboxes])
    grid: list[list[dict]] = []
    for ri, r in enumerate(rows):
        cells = list(r.cells or [])
        line_texts = list(texts[ri]) if ri < len(texts) and texts[ri] else []
        if len(cells) != len(line_texts):
            return None
        line: list[dict] = []
        for ci, box in enumerate(cells):
            raw = line_texts[ci]
            text = "" if raw is None else re.sub(r"\s+", " ", str(raw).strip())
            if box is None:
                line.append({"text": "", "covered": True, "rspan": 1, "cspan": 1})
                continue
            c0, c1 = _nearest(xs, box[0]), _nearest(xs, box[2])
            r0, r1 = _nearest(ys, box[1]), _nearest(ys, box[3])
            line.append({"text": text, "covered": False,
                         "rspan": max(1, r1 - r0), "cspan": max(1, c1 - c0)})
        grid.append(line)
    return grid


def table_is_rectangular(grid: list[list[dict]] | None) -> bool:
    """规整表 = 没有一个位置被盖住，也没有一个格子跨行跨列。"""
    if not grid:
        return False
    return all(not c["covered"] and c["rspan"] == 1 and c["cspan"] == 1
               for row in grid for c in row)


def _covering_cell(grid: list[list[dict]], ri: int, ci: int) -> tuple[int, int] | None:
    """这一空格位是被哪个真格子盖住的 —— 按跨度算，不按"往上/往左第一眼"猜。

    写这层时先试的是"往上扫到第一个非空格"，实测草稿立刻把横向合并说成了纵向继承
    （`North subtotal` 那一行的中间格报成"承自上一行"）：一句看起来是解释、其实是错的
    解释。既然有 bbox 跨度可算，就没有理由去猜。
    """
    for r0, row in enumerate(grid):
        for c0, cell in enumerate(row):
            if cell["covered"]:
                continue
            if r0 <= ri < r0 + cell["rspan"] and c0 <= ci < c0 + cell["cspan"]:
                return r0, c0
    return None


def _span_note(cell: dict) -> str:
    if cell["rspan"] > 1 and cell["cspan"] > 1:
        return f" [spans {cell['rspan']} rows x {cell['cspan']} columns]"
    if cell["cspan"] > 1:
        return f" [spans {cell['cspan']} columns]"
    if cell["rspan"] > 1:
        return f" [spans {cell['rspan']} rows]"
    return ""


def _merged_table_to_text(grid: list[list[dict]]) -> str:
    """一张有合并格的表 → 按行的对齐文本，而不是一张 markdown 网格。

    为什么不用网格：`| --- |` 那一行只在"首行是表头"时合法，而一张有合并格的表恰恰
    经常在首行就塌 —— 表二的首行是 `Region(跨2行) | Revenue by quarter(跨2列) | Share`，
    它的**第二行才是列名**。把首行提为表头，第二行就作为数据行发出（体验测试里那句
    "Fourth quarter 掉进数据行"就是这么来的），而读者看不出那张网格里有两行是表头。
    一个诚实的降级渲染胜过一张自信的错误网格 —— 这就是整批的论点。

    横向被盖住的位置**不逐个列出**（实测教训）：跨列那一格自己已经说了 "spans 25
    columns"，再把 24 个 "(covered by …)" 跟在后面，`background_checks.pdf` 从 6,716
    字涨到 33,307 字 —— 一次降级把正文放大近 5 倍，那是用一个新问题换掉旧问题，而这批
    修的正是体积。纵向继承留着，因为它在这一行里读不出来；给的是行号与列号而不是原文
    （原文跟着行号可查，重复它同样是在放大）。
    """
    spans = sum(1 for row in grid for c in row
                if not c["covered"] and (c["rspan"] > 1 or c["cspan"] > 1))
    out = [f"[merged-cell table: {spans} merged cell(s) - a markdown grid cannot "
           "carry spans, so this is one line per row; the columns a wide cell sits "
           "over are counted in that cell's own note]"]
    for ri, row in enumerate(grid):
        parts = []
        for ci, cell in enumerate(row):
            if cell["covered"]:
                src = _covering_cell(grid, ri, ci)
                if src is None:
                    parts.append("(covered)")
                    continue
                r0, c0 = src
                if r0 != ri:
                    parts.append(f"(as row {r0 + 1}, column {c0 + 1})")
                continue
            parts.append((f'"{cell["text"]}"' if cell["text"] else "(blank)")
                         + _span_note(cell))
        out.append(f"row {ri + 1}: " + " | ".join(parts))
    return "\n".join(out)


def _announce_merged_table(spans_on_page: int, tables_on_page: int) -> None:
    """降级要出口：只活在 markdown 里的一段话，调用方按 `summary` 读的时候看不见。"""
    from dhole_mcp.context import _note_text
    if not spans_on_page:
        return
    _note_text(f"table(s) with merged cells rendered as text rows, not a markdown "
               f"grid ({spans_on_page} merged cell(s) across "
               f"{tables_on_page} irregular table(s) on this page)")


def _dehyphenate_join(prev: str, nxt: str) -> tuple[str, bool]:
    """Join two consecutive lines, de-hyphenating a soft hyphen break."""
    prev = prev.rstrip()
    nxt = nxt.lstrip()
    if prev.endswith("-") and len(prev) >= 2 and prev[-2] != " ":
        if nxt and nxt[0].islower():
            return prev[:-1] + nxt, True
    return prev + " " + nxt, False


def _table_to_markdown(table: list[list[str | None]], text_mode: bool = False) -> str:
    if not table:
        return ""
    rows = [[("" if cell is None else re.sub(r"\s+", " ", str(cell).strip()))
             for cell in row] for row in table]
    raw_first = list(table[0]) if table else []
    rows = [r for r in rows if any(c for c in r)]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    # 首行里有 `None` 延续 = 这一行本身带合并（几何层读不到它时的兜底判据），那它就
    # 不是表头，而是一张会误导人的网格里第一行。宁可不提表头，也不把"看着像列名的一行"
    # 和"其实是列名的第二行"一起当数据发出去。
    header_ok = (len(raw_first) == width
                 and not any(c is None for c in raw_first))
    if text_mode or not header_ok:
        return "\n".join(" | ".join(r) for r in rows)
    header = rows[0]
    sep = ["---"] * width
    body = rows[1:] if len(rows) > 1 else []
    lines = ["| " + " | ".join(header) + " |",
             "| " + " | ".join(sep) + " |"]
    for r in body:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)


def _in_bbox(obj: dict, bbox: tuple[float, float, float, float], tol: float = 1.0) -> bool:
    x0, top, x1, bottom = bbox
    cx = (obj.get("x0", 0) + obj.get("x1", 0)) / 2
    cy = (obj.get("top", 0) + obj.get("bottom", 0)) / 2
    return (x0 - tol) <= cx <= (x1 + tol) and (top - tol) <= cy <= (bottom + tol)


#: A mark thinner than this on its SHORT side encloses no area: a rule, a divider,
#: a cell border. `_figures_on_page` measures the number against two fixtures that
#: disagree about nothing else (13 hairline objects on a prose page, one 2pt data
#: stroke on a chart page).
_FIGURE_MIN_SIDE_PT = 3.0


def _encloses_area(obj: dict) -> bool:
    """围得出一块面积才算"画了个东西"，判据是 bbox 的短边而不是笔画粗细。"""
    width = abs(obj.get("x1", 0) - obj.get("x0", 0))
    height = abs(obj.get("bottom", 0) - obj.get("top", 0))
    return min(width, height) >= _FIGURE_MIN_SIDE_PT


def _dominant_size(chars: Iterable[dict]) -> float:
    sizes = [c.get("size", 0) for c in chars if c.get("text", "").strip()]
    if not sizes:
        return 0.0
    return statistics.median(sizes)


def _is_bold(chars: Iterable[dict]) -> bool:
    for c in chars:
        fn = (c.get("fontname") or "").lower()
        if "bold" in fn or "black" in fn or "heavy" in fn:
            return True
    return False


def _heading_level(line_size: float, body_size: float, bold: bool, text: str) -> int:
    if body_size <= 0 or line_size <= 0:
        return 0
    t = text.strip()
    if not t or len(t) > _HEADING_MAX_LEN:
        return 0
    if t.rstrip()[-1:] in _SENTENCE_END:
        return 0
    ratio = line_size / body_size
    level = 0
    if ratio >= _H1_RATIO:
        level = 1
    elif ratio >= _H2_RATIO:
        level = 2
    elif ratio >= _H3_RATIO:
        level = 3
    if bold and level:
        level = max(1, level - 1)
    return level


# R01-049：有的材料把小节号排成正文的样子 —— 同字号、同样距、不加粗，几何层一点优待
# 都没有，于是 `_heading_level` 认不出它，段落合并（只看行距）就把 `2. Data` 和它领的
# 那句粘成一句交回。形状是这份材料里唯一的记号，所以判据只写形状、不写词汇表：
# ①以 1-2 位编号起头（可带级次 `1.2.4`；四位年份与 `1.2984` 这类小数都进不来），
# ②整行不超过一行标题该有的长度，③行末不收句（沿用 `_SENTENCE_END` 那道既有闸）。
# 只断段，不升 `#`：我们知道"这是一行独立编号"，不知道它是几级；写进章节地图同样是
# 过度声称。
_NUMBERED_TITLE = re.compile(r"^\d{1,2}(?:\.\d{1,2})*\.?\s+\S")
_NUMBERED_TITLE_MAX_CHARS = 60


def _is_numbered_title_line(text: str) -> bool:
    t = text.strip()
    if not t or len(t) > _NUMBERED_TITLE_MAX_CHARS:
        return False
    if t.rstrip()[-1:] in _SENTENCE_END:
        return False
    return bool(_NUMBERED_TITLE.match(t))


# 同一行标题折出来的两行，字号差通常来自一个数字或大写字母（实测 `…2026` 那一行里
# 只有数字，众数字号仍与首行一致，但别的字体会差 0.1-0.5pt）。0.75 是"同一行"的容差，
# 不是"同一级"的容差 —— 两级标题的字号差远大于此。
_HEAD_SIZE_TOL = 0.75


def _fold_heading_lines(blocks: list, start: int, gap_threshold: float
                        ) -> tuple[str, int]:
    """把**同一行印刷标题**被页面框折断的后续行折回第 `start` 行。

    判据全在几何层，与标签无关：同一众数字号、同一粗细、垂直间距不超过一行、且中间
    没有插进表格。长度帽兜住失控合并。

    为什么必须折（实测 `tests/wrapped_heading.pdf`，三条各有各的坏法）：
      - 封面式两行大字，两行都过闸 → 今天交回**两条同级标题**，第二条还替自己算出
        一个 `end_page` 覆盖到文档末页（一条不存在的章节）。
      - 第二行以 `.` 收尾 → `_SENTENCE_END` 那道闸把它降级成正文，标题只剩半截，
        尾串变成一段话跟着下一个标题跑。
      - 窄栏里一个标题折成四行 → 今天交回**四条** level-2 标题，正文一行不剩。
    `_SENTENCE_END` 那道闸本身是对的（一句完整的句子不是标题），坏的是它被逐行独立
    地应用 —— 一行是不是标题，取决于它前面那行是什么。
    """
    top, kind, payload = blocks[start]
    text, chars, _, bottom = payload
    size = _dominant_size(chars)
    bold = _is_bold(chars)
    last = start
    j = start
    while j + 1 < len(blocks):
        n_top, n_kind, n_payload = blocks[j + 1]
        if n_kind != "text":
            break
        n_text, n_chars, n_line_top, n_line_bottom = n_payload
        if not n_chars or _is_bold(n_chars) != bold:
            break
        if abs(_dominant_size(n_chars) - size) > _HEAD_SIZE_TOL:
            break
        if n_line_top - bottom > gap_threshold:
            break
        if text.rstrip()[-1:] in _SENTENCE_END:
            break  # 标题已经收句，下一行是另一个标题，不是它的尾巴
        joined = f"{text.rstrip()} {n_text.strip()}".strip()
        if len(joined) > _HEADING_MAX_LEN:
            break
        text, last, j, bottom = joined, j + 1, j + 1, n_line_bottom
    return text, last


def _clean_date(d: str) -> str:
    if not d:
        return ""
    if d.startswith("D:"):
        d = d[2:]
    m = re.match(r"^(\d{4})(\d{2})(\d{2})", d)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else d


def _format_metadata(meta: dict, own_outline: bool = False,
                     outline_titles: tuple[str, ...] = ()) -> list[str]:
    """Build the markdown metadata header lines from pdfplumber's .metadata.

    The Title field is **file furniture**, the same kind as Author and Subject,
    and it says what the producing app wrote, not what the document prints:
    ReportLab hands out `untitled` / `anonymous` / `unspecified` to every file
    nobody titled. So the outline top line belongs to the pages - when this
    delivery carries a section map of its own (measured from bookmarks or font
    sizes, the same thing `table_of_contents` ships), the field is delivered in
    the labeled line instead of heading the outline as if it were structure. When
    the pages offer no outline at all the field is the only title there is, and it
    stays on the top line - fixing a false claim must not hand back a titleless
    document. A field whose string the pages print anyway appears once: in the
    outline.
    """
    def g(*keys):
        for k in keys:
            v = meta.get(k)
            if v:
                return str(v)
        return ""
    title = g("Title", "title")
    author = g("Author", "author")
    subject = g("Subject", "subject")
    keywords = g("Keywords", "keywords")
    created = _clean_date(g("CreationDate", "creationdate"))
    modified = _clean_date(g("ModDate", "moddate"))
    out = []
    fields = []
    if title:
        if own_outline:
            if title.strip().casefold() not in outline_titles:
                fields.append(f"Title: {title}")
        else:
            out.append(f"# {title}")
    if author:
        fields.append(f"Author: {author}")
    if subject:
        fields.append(f"Subject: {subject}")
    if keywords:
        fields.append(f"Keywords: {keywords}")
    if fields:
        # 这四格是**信息字典**里的记录：写文件那台应用留下的，不是页面印出来的。
        # ReportLab 在没人填字段时会交 anonymous / unspecified —— 一句关于作者的话
        # 与一句文件戳的话不是一回事，各说各自的来源，读的人才不会把缺省串当署名。
        out.append("> " + " · ".join(fields)
                   + (" (this file's own info dictionary - recorded by the app "
                      "that wrote the file, not text printed on its pages)"))
    stamps = []
    if created:
        stamps.append(f"Created: {created}")
    if modified:
        stamps.append(f"Modified: {modified}")
    if stamps:
        # R01-048：一枚 `Date:` 承载不了两种意思。这两戳各自是"这台机器什么时候写下这个
        # 文件"，与"这篇东西什么时候写的"是两件事 —— 所以各自点名，并一起把身份说出口。
        out.append("> " + " · ".join(stamps)
                   + (" (the file's own stamps - when this PDF was written, "
                      "not when the work was written)"))
    return out


def _metadata_dict(meta: dict) -> dict[str, Any]:
    """Full structured metadata for the ResponseModel.metadata field (P7)."""
    def g(*keys):
        for k in keys:
            v = meta.get(k)
            if v:
                return str(v)
        return ""
    return {
        "title": g("Title", "title"),
        "author": g("Author", "author"),
        "subject": g("Subject", "subject"),
        "keywords": g("Keywords", "keywords"),
        "creator": g("Creator", "creator"),
        "producer": g("Producer", "producer"),
        "creation_date": _clean_date(g("CreationDate", "creationdate")),
        "mod_date": _clean_date(g("ModDate", "moddate")),
    }


def _toc_within_pages(toc: list[dict], pages: list[int]) -> list[dict]:
    """The outline entries whose page range touches one of `pages`.

    An entry with no usable page is KEPT: a section the map cannot place is not a
    section that is out of range, and dropping it would trade a token saving for a
    chapter nobody can find again.
    """
    want = set(pages)
    kept: list[dict] = []
    for entry in toc:
        start = entry.get("page")
        if not isinstance(start, int) or start <= 0:
            kept.append(entry)
            continue
        end = entry.get("end_page")
        end = end if isinstance(end, int) and end >= start else start
        if any(p in want for p in range(start, end + 1)):
            kept.append(entry)
    return kept


def _add_end_pages(toc: list[dict], total_pages: int) -> list[dict]:
    """Add `end_page` to each ToC entry from the outline structure.

    For entry i at level L, end_page = (page of the next entry j>i with
    level[j] <= L) - 1, or total_pages if no such entry. This gives the agent a
    page RANGE per section (e.g. 'Methodology: 23-31') so it can call
    pages='23-31' to grab exactly that section. Entries with no start page are
    left as-is (end_page not set).

    Both ends of the range are pinned to pages this file actually has. The
    outline is the document's own claim and it can be stale — measured on a
    3-page PDF whose bookmarks pointed at a longer edition: a section got
    `end_page: 98` (95 pages that do not exist) and the entry after it got
    `end_page: 3` while starting at page 99, an inverted range. `pages=` is
    picked from these ranges, so an out-of-bounds range is not extra
    information, it is a dispatch to a page that is not there.
    """
    n = len(toc)
    for i, entry in enumerate(toc):
        lvl = entry.get("level", 1)
        start = entry.get("page")
        if start is None:
            continue
        # 页数读不出来（`len(pdf)` 失败的兜底路径给的是 0）时不猜上界：一节只报到
        # 它自己那一页，比报到一个算不出来的区间诚实。
        end = total_pages if total_pages > 0 else int(start)
        for j in range(i + 1, n):
            if toc[j].get("level", 1) <= lvl and toc[j].get("page") is not None:
                end = int(toc[j]["page"]) - 1
                break
        if total_pages > 0:
            end = min(end, total_pages)
        entry["end_page"] = max(int(start), end)
    return toc


def _outline_inside_the_file(toc: list[dict], total_pages: int
                             ) -> tuple[list[dict], list[tuple[str, int]]]:
    """Split an outline into entries this file can honour and entries it cannot.

    A bookmark to page 99 of a 3-page document cannot be clamped into truth:
    there is no page to point it at. Dropping it silently would be the usual
    sin of this batch, so the caller says the count and the titles out loud.
    """
    if total_pages <= 0:
        return list(toc), []
    kept, dropped = [], []
    for entry in toc:
        start = entry.get("page")
        if isinstance(start, int) and not (1 <= start <= total_pages):
            dropped.append((str(entry.get("title") or "(untitled)"), start))
            continue
        kept.append(entry)
    return kept, dropped


def _extract_toc(body: bytes) -> list[dict]:
    """Extract the PDF outline tree (bookmarks) via pypdfium2 -> list of
    {level, title, page, end_page}. Empty for PDFs without an outline (most
    arxiv papers) - the caller falls back to a heading-based section-map built
    during extraction in that case."""
    try:
        import pypdfium2 as pdfium  # type: ignore
    except ImportError:
        return []
    try:
        pdf = pdfium.PdfDocument(body)
        try:
            total_pages = len(pdf)
        except Exception:
            # Fallback: derive from the outline's max page (real pypdfium2 supports
            # len; this guards exotic wrappers / test fakes without __len__).
            total_pages = 0
        toc = []
        for bookmark in pdf.get_toc():
            level = (getattr(bookmark, "level", 0) or 0) + 1  # 0-based -> 1-based
            try:
                title = bookmark.get_title() or ""
            except Exception:
                title = ""
            page = None
            try:
                dest = bookmark.get_dest()
                if dest is not None:
                    idx = dest.get_index()  # 0-based page index
                    if idx is not None:
                        page = int(idx) + 1
            except Exception:
                page = None
            toc.append({"level": int(level), "title": str(title).strip(), "page": page})
        pdf.close()
        # 先算区间（区间本身要钳），再挑掉起点就不在这份文件里的条目 —— 顺序反了
        # 就会先丢条目、后算区间，让"下一节"的缺席被当成"这一节到末页"。
        toc, dropped = _outline_inside_the_file(_add_end_pages(toc, total_pages),
                                                total_pages)
        if dropped:
            from dhole_mcp.context import _note_text
            _note_text(f"the PDF's own outline points "
                       f"{len(dropped)} section(s) at pages this file does not have "
                       f"({'; '.join(f'{t} -> page {p}' for t, p in dropped[:3])}"
                       f"{', …' if len(dropped) > 3 else ''}) - those entries are left "
                       f"out of the section map rather than clamped onto a page they "
                       f"never named; the file has {total_pages} page(s)")
        return toc
    except Exception as e:
        logger.debug("ToC extraction failed: %s", e)
        return []


def _cid_ratio(text: str) -> tuple[float, int]:
    """Return (ratio_of_cid_garbage_chars, cid_token_count) for a page's text."""
    if not text:
        return (0.0, 0)
    garbage = sum(len(m) for m in _CID_RE.findall(text))
    return (garbage / len(text), len(_CID_RE.findall(text)))


def _quality_score(text: str) -> float:
    """Doc/page readable-char ratio in [0,1]: printable chars minus CID garbage,
    over total chars. ~1.0 = clean; low = garbled (CID garbage not OCR-recovered)."""
    if not text:
        return 0.0
    clean = _CID_RE.sub("", text)
    printable = sum(1 for ch in clean if ch.isprintable() or ch in "\n\t ")
    # printable is over the cid-stripped text; score it against the ORIGINAL len
    # so cid garbage counts against quality.
    return max(0.0, min(1.0, (printable - 0) / max(len(text), 1)))


def _ocr_pages(body: bytes, page_nums: list[int], password: Optional[str]) -> dict[int, str]:
    """OCR a set of pages, returning {page_num: ocr_markdown_body}.

    Uses the existing OCR module (pypdfium2 render + rapidocr). One call for the
    whole set (one pypdfium2 open). Pages that fail to OCR are omitted from the
    returned dict (the caller keeps the original cid-garbage text for them)."""
    if not page_nums:
        return {}
    try:
        from dhole_mcp.ocr import ocr_pdf, ocr_available
    except Exception:
        return {}
    if not ocr_available():
        return {}
    spec = ",".join(str(n) for n in page_nums)
    try:
        res = ocr_pdf(body, pages=spec, password=password)
    except Exception as e:
        logger.debug("CID-page OCR batch failed: %s", e)
        return {}
    if res.error or not res.content:
        return {}
    # ocr_pdf returns one markdown string with "--- Page N ---" markers per page.
    full = res.content[0]
    out: dict[int, str] = {}
    # Split into per-page blocks by the "--- Page N ---" marker.
    parts = re.split(r"(?=^--- Page \d+ ---$)", full, flags=re.MULTILINE)
    for part in parts:
        m = re.match(r"^--- Page (\d+) ---$", part, re.MULTILINE)
        if m:
            n = int(m.group(1))
            if n in page_nums:
                # Strip the marker line; keep the body.
                body_md = re.sub(r"^--- Page \d+ ---\s*", "", part, count=1).strip()
                if body_md:
                    out[n] = body_md
    return out


def _figures_on_page(page: Any, table_bboxes: list = ()) -> dict[str, Any]:
    """One page's non-text payload, counted from what the page actually draws.

    `_render_page` walks `page.chars` and `find_tables` — it never looks at
    `page.images` or `page.rects`. So a page that is mostly a chart returns its
    caption and nothing else, and the reader cannot tell "this document has no
    figures" from "the figure on page 7 did not come back". Counts are cheap and
    are the honest half: the pixels and the vector paths are not extractable into
    text by anything in this module.

    Two exclusions, both measured rather than assumed:

    **Table regions are not counted.** The borders of a rendered table are
    `page.rects` too — counting them would announce "141 object(s) this
    extraction did not render" about a table whose text came back whole
    (`tests/merged_cells.pdf`: 141; `tests/background_checks.pdf`: 446).

    **Marks that enclose no area are not content.** A rule, a divider, a
    quote's left border draws across the page but carries nothing a reader could
    retrieve by looking at it, so the tell is the SHORT side of the bounding box,
    not the stroke width. Measured on `tests/deco_rules.pdf` (three `<hr>`, a
    bordered blockquote, prose): 13 objects outside the table, all of them
    0.75-1.5pt on their short side, so the count is 0 and nothing is announced —
    while the same page before this rule announced 13.

    The second exclusion is deliberately bounded, because a real line chart is
    built almost entirely out of thin strokes. On `tests/line_chart.pdf` the axes
    and grid lines are four 0pt-short-side lines and the data path is a 2pt stroke
    spanning 285x90pt: a stroke-width test would have called that page decoration
    and put P4 straight back, so the test is on extent — and it keeps the data path
    while dropping the axes and grid. Raster images get neither filter — a picture
    inside a table cell is exactly as unread as one in the body.
    """
    figures: dict[str, Any] = {}
    try:
        imgs = list(page.images or [])
    except Exception:
        imgs = []
    if imgs:
        figures["raster"] = len(imgs)
        largest = max(imgs, key=lambda im: (im.get("width", 0) * im.get("height", 0)))
        figures["largest_px"] = (f"{int(largest.get('width', 0))}"
                                 f"x{int(largest.get('height', 0))}")
    outside = 0
    for key in ("rects", "lines", "curves"):
        try:
            objects = list(getattr(page, key, None) or [])
        except Exception:
            continue
        for obj in objects:
            if table_bboxes and any(_in_bbox(obj, b) for b in table_bboxes):
                continue
            if not _encloses_area(obj):
                continue
            outside += 1
    if outside:
        figures["vector_objects"] = outside
    return figures


def _figures_metadata(figures: dict[int, dict], page_nums: list[int],
                      total_pages: int) -> list[dict]:
    """`metadata.figures`: per-page non-text payload, only for pages that have any.

    Deliberately NOT inlined into the extracted body. A `[Figure on page N]`
    string is a character count, and the thin-extraction / quality maths reads
    characters — an inlined marker can therefore flip `content_ok` on a page
    whose only change is that we admitted it has a figure. The list rides beside
    the text instead, where it can be read without touching that arithmetic.
    """
    out = []
    for n in page_nums:
        facts = figures.get(n) or {}
        if not facts:
            continue
        out.append({"page": n, **facts})
    return out


def _announce_figures(figures: list[dict], total_pages: int) -> None:
    """降级要出口：图没进来这件事得在 `summary` 说一次，不是只躺在 metadata 里。"""
    from dhole_mcp.context import _note_text
    if not figures:
        return
    pages = len(figures)
    raster = sum(int(f.get("raster", 0)) for f in figures)
    vector = sum(int(f.get("vector_objects", 0)) for f in figures)
    kinds = ", ".join(p for p in (
        f"{raster} embedded image(s)" if raster else "",
        f"{vector} vector object(s)" if vector else "") if p)
    _note_text(f"{pages} of {total_pages} page(s) "
               f"{'carries' if pages == 1 else 'carry'} non-text content this "
               f"extraction does not render ({kinds}) - see metadata.figures for "
               "which pages; a vision tool or web_act screenshot is how to read them")


def _extract_images_metadata(pdf, page_nums: list[int]) -> list[str]:
    """Per-page embedded raster-image metadata as agent-readable strings (P4).
    Vector graphics are NOT extractable; we report counts + dimensions so a
    multimodal agent knows which pages have figures to screenshot."""
    out: list[str] = []
    for n in page_nums:
        try:
            imgs = pdf.pages[n - 1].images
        except Exception:
            continue
        if not imgs:
            continue
        largest = max(imgs, key=lambda im: (im.get("width", 0) * im.get("height", 0)))
        out.append(
            f"page {n}: {len(imgs)} embedded image(s); largest "
            f"{int(largest.get('width', 0))}x{int(largest.get('height', 0))}"
        )
    return out


# --- 页边重复行（running header / footer）------------------------------------
# R01-037（P08 谢临 S2 的火气 #1："看起来抽到了，其实抽的是页眉"）：学术期刊的刊头与
# 下载水印每页都印一遍，抽取按 y 顺序输出文本行，于是它们与正文同权重混在交付里 ——
# 把正文复制出去（翻译、引用、排稿）的人每翻一页手删一次。
#
# 判据只有形状，一条都不认内容：不许写"看到 Journal of 就当页眉"这种为一个刊名开的特例
# （§12）—— 那种实现换一份别的出版物就失效，还会把正文里合法的短句删掉。三条同时成立
# 才算页边行：①它是本页第一段或最后一段，②它是一行且短，③规范化后同一颗行在 ≥2 页的
# 同一侧出现。摘走的是位置不是信息：每颗摘掉的行都在文档头部逐字引用，并点名它的位置、
# 摘自哪几页、数字是否每页不同。
_FURNITURE_MAX_CHARS = 120   # 页边行是短句。长行按正文处理：误删一段话比留一行噪声贵
_FURNITURE_MIN_PAGES = 2     # 一页无从比较：只抽一页时不claim，两页同侧才算"每页都有"


def _furniture_key(line: str) -> str:
    """比较键：空白折叠 + 大小写折叠 + 数字折成 `#`。

    数字必须折起来 —— 页脚本来就每页换一个页码（`[page 2 of 4]` / `[page 3 of 4]`），
    逐字比较会因为一颗数字放过整页噪声。折叠只作用于比较，摘出去的那句话仍然逐字引用。
    """
    return re.sub(r"\d+", "#", " ".join(line.split())).casefold()


def _page_span(pages: list[int]) -> str:
    """[1,2,3] -> "1-3"；[1,3] -> "1, 3"。说得出摘了哪几页，读者才敢信少掉的是版面。"""
    out: list[str] = []
    run: list[int] = []
    for n in [*pages, None]:
        if run and n is not None and n == run[-1] + 1:
            run.append(n)
            continue
        if run:
            out.append(str(run[0]) if len(run) == 1 else f"{run[0]}-{run[-1]}")
        run = [n] if n is not None else []
    return ", ".join(out)


def _edge_note_suffix(texts: list[str], pages_hit: list[int],
                      pages_in_window: list[int]) -> str:
    """两条路共用的那一串尾巴：数字是否每页换号、证据来自哪几页、本窗口交付哪几页。

    单一作者（R02-023 与 R01-037 的两半在同一颗候选上说话）：摘除那一支与申报那一支
    必须用同一句话说清「这几页是从哪来的」，否则读者只能从两条不同的旁注里猜哪条覆盖了
    窗口外的那些页。
    """
    vary = "; numbers vary page to page" if len(set(texts)) > 1 else ""
    beyond = sorted(set(pages_hit) - set(pages_in_window))
    window_note = (f"; evidence from pages {_page_span(beyond)} outside this window, "
                   f"delivered here: page {_page_span(pages_in_window)}" if beyond else "")
    return vary + window_note


def _blocks_with_offsets(md: str) -> tuple[list[str], list[tuple[int, str]]]:
    """把一页 markdown 按空行切成块，并留着重组用的分隔符。

    返回 `(parts, blocks)`：`parts` 是 `re.split` 的原样切片（奇数位是空行），
    `blocks` 是 `(parts 下标, 去空白后的文本)`。带着分隔符重组，摘掉一块之后其余字节
    一个字都不动 —— 表格、代码块这些多行块不会被这次切分改形。
    """
    parts = re.split(r"(\n[ \t]*\n)", md)
    blocks = [(i, parts[i].strip()) for i in range(0, len(parts), 2) if parts[i].strip()]
    return parts, blocks


def _edge_lines_outside_window(body: bytes, password: str | None,
                               window: list[int], total_pages: int) -> dict[int, tuple[str, str]]:
    """窗口外每一页的「首行 / 末行」—— 只为准页边行这一判而读，交回的是证据，不是交付。

    R02-023：`pages=` 缩窄的是**交付**，不是判据的输入。整份文件本来就在产品手里，而单页窗口
    里天然没有第二页可比 —— 于是「每页都印的那一行」在最常用的那一支上不再兑现（用户按页取，
    正因为她只想要那一页；R01-037 的动机是抄正文的人不必每翻一页手删一次）。

    读的是 pypdfium2 的文本层（实测 200 页 0.06s，pdfplumber 做同一件活要 0.66s）：这一页印着
    什么，不需要把整份重排一遍。读不到（依赖缺失、损坏的 PDF、口令不对）就交回空 dict ——
    那一情形下这一支的形状与改之前**逐字相同**：宁可不摘，也不交出一句关于没发生过的事的谎。
    """
    out: dict[int, tuple[str, str]] = {}
    if len(window) >= total_pages:
        return out
    in_window = set(window)
    try:
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(body, password=password or None)
    except Exception:
        return out
    for n in range(1, total_pages + 1):
        if n in in_window:
            continue
        try:
            text = doc[n - 1].get_textpage().get_text_bounded()
        except Exception:
            continue
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if len(lines) < 2:
            # 只印了一行的页说不出哪一侧是页边：它不当证据（也不当正文 —— 它本来就不在窗口里）
            continue
        out[n] = (lines[0], lines[-1])
    return out


def _strip_running_furniture(rendered: dict[int, str], page_nums: list[int],
                             outside: dict[int, tuple[str, str]] | None = None,
                             edges: dict[int, tuple[str, str]] | None = None) -> list[str]:
    """摘掉"每页同一侧的同一个短句"，返回交代它去了哪的头部行；就地改 `rendered`。

    先摘页眉再摘页脚：页脚那一轮看到的已经是摘过页眉的页面，于是"摘完就空"这一条
    （本页至少还剩一段正文才允许摘）自然在两轮里都成立。候选不足 `_FURNITURE_MIN_PAGES`
    页时一个字都不动 —— 宁可不摘，也不交出一页空白或一句关于没发生过的事。

    第四道闸管的是数字折叠的副作用：把数字折成 `#` 之后，"Chapter 1 …" 与 "Chapter 2 …"
    这类**每页换一个号的编号标题**也成了同一颗候选，而那种行正是不能碰的正文。挡它的不
    是新判据，是渲染器自己已经做过的判断 —— 按字号认出来的标题在 markdown 里带 `#`，
    带 `#` 的行就不是页边行。仍是形状，不认内容。

    `outside` 是窗口外各页的首末行（R02-023）：它们**只加证据，不决定摘除** —— 摘不摘仍由
    窗口内那一块过不过四道闸决定（包括 `#` 那一条：窗口内的编号标题照旧受保护），
    所以一位窗口外的假证据不会摘掉窗口内的一段正文。交代那一行去向的那句话会把
    「证据来自窗口外的哪几页、本窗口交付的是哪几页」一并说出口。

    两条陈述共用同一把尺（`_furniture_key` 与「同侧 ≥2 页」与 120 字符那道闸），只是**动作**
    不同：独立成块的那一行摘走并逐字引用（`running … removed from the body`）；同一颗行落在
    一个比它长的段落里时**一个字都不删**，改为交出一句位置陈述（`… printed line repeats on
    pages … but stays in the body`，R02-010）。第二句不许写成第一句的词 —— 「这是页眉/页脚」
    是一个功能判断，而这一刀的整个前提是手上没有一把尺能把页边行与段末的正文行分开
    （量过：字号比与离边距离两堆完全重叠）。所以它说的是"每页首/末**印着**的这一行重复了、
    它没有被删、它为什么删不掉"，三件都是这一支真知道的事。
    """
    outside = outside or {}
    #: 每页**印着**的首/末行（R02-052）。缺这份信息时（外部直接调用）退回按块判 ——
    #: 不猜，也不因为拿不到就多摘什么。
    edges = edges or {}
    notes: list[str] = []
    for side, ordinal, pick in (("header", "first", 0), ("footer", "last", 1)):
        groups: dict[str, dict[int, tuple[int, str]]] = {}
        for n in page_nums:
            _parts, blocks = _blocks_with_offsets(rendered[n])
            if len(blocks) < 2:
                continue
            idx, text = blocks[0] if side == "header" else blocks[-1]
            if "\n" in text or len(text) > _FURNITURE_MAX_CHARS:
                continue
            if text.startswith("#"):
                continue
            #: 「它是一行」量的必须是**印刷**的那一行：块比这一页的首/末印行长，
            #: 说明那是段落合并拼出来的（两行印刷行、行距不够开新块）—— 正文不是页脚。
            pair = edges.get(n)
            if pair and text != pair[0 if side == "header" else 1]:
                continue
            groups.setdefault(_furniture_key(text), {})[n] = (idx, text)
        evidence: dict[str, set[int]] = {}
        for n, pair in outside.items():
            line = pair[pick]
            if "\n" in line or len(line) > _FURNITURE_MAX_CHARS:
                continue
            evidence.setdefault(_furniture_key(line), set()).add(n)
        # 页边那一行**印着**也存在，只是被段落合并吸住了（它落在一个比自己长的段落里）——
        # 这一族只申报、不动手（R02-010）：切它就会开始删真正文（R02-052）。
        merged_away: dict[str, dict[int, str]] = {}
        for n in page_nums:
            pair = edges.get(n)
            if not pair:
                continue
            line = pair[pick]
            if not line or "\n" in line or len(line) > _FURNITURE_MAX_CHARS:
                continue
            key = _furniture_key(line)
            if key in groups:
                continue                     # 独立成块那一支已经在管它了
            _parts, blocks = _blocks_with_offsets(rendered[n])
            # 只在**能证明它被吞了**的时候才说这句话：它的块比这一行长。整页只有这一行
            # （块 == 行）不是"被段落吸住"，是"摘完就空"那一格 —— 那一格早就有别的说法，
            # 这里不许给它编一个新的理由。
            if not any(len(text) > len(line) and line in text for _i, text in blocks):
                continue
            merged_away.setdefault(key, {})[n] = line
        for key, hits in groups.items():
            pages_hit = sorted(set(hits) | evidence.get(key, set()))
            if len(pages_hit) < _FURNITURE_MIN_PAGES:
                continue
            pages_hit_in_window = sorted(hits)
            texts = [hits[n][1] for n in pages_hit_in_window]
            for n in pages_hit_in_window:
                idx = hits[n][0]
                parts, _b = _blocks_with_offsets(rendered[n])
                parts[idx] = ""
                # 连着它后面那个空行一起摘；本页最后一块则摘前面那个
                sep = idx + 1 if idx + 1 < len(parts) else idx - 1
                parts[sep] = ""
                rendered[n] = "".join(parts)
            notes.append(f'> running {side} removed from the body: "{texts[0]}" '
                         f'({ordinal} line of pages {_page_span(pages_hit)})'
                         f'{_edge_note_suffix(texts, pages_hit, pages_hit_in_window)}')

        # 申报这一半（R02-010）：删不掉不代表可以不说。字号与离页边距离都分不开
        # 「页边行」与「段末的正文行」—— 量过仓里 4 份 ≥2 页的 PDF / 22 条边行：
        # 重复那一堆的 字号/正文字号 ratio 一直到 1.000（现场那份通报的刊头与页脚就是按
        # 正文字号印的），只出现那一堆低到 0.687，两堆重叠没有空带。
        # 所以这里**不切**（切它就会删真正文），只把"重复了、留下了、为什么"交出去。
        # 用词也不越过硬的那一半：这一句说的是**位置**（这一页首/末印着的那一行）与
        # **重复**（同一颗折叠键跨 ≥2 页），不说"它是页眉/页脚" —— 这一刀的整个论点就是
        # 手上没有一把尺能把两者分开，所以句子不许同时声称分过了。
        for key, lines in merged_away.items():
            pages_hit = sorted(set(lines) | evidence.get(key, set()))
            if len(pages_hit) < _FURNITURE_MIN_PAGES:
                continue
            pages_hit_in_window = sorted(lines)
            texts = [lines[n] for n in pages_hit_in_window]
            notes.append(f'> {ordinal} printed line repeats on pages '
                         f'{_page_span(pages_hit)} but stays in the body: '
                         f'"{texts[0]}"{_edge_note_suffix(texts, pages_hit, pages_hit_in_window)}'
                         ' - on those pages it sits inside a longer paragraph instead of standing '
                         'as its own line, and nothing in size or margin tells that line apart '
                         'from the prose around it on this file, so dhole deletes nothing here. '
                         'Delete that line by hand if you want it gone.')
    return notes


# --- 页面上印着的日期形状（R01-048）------------------------------------------
# P08 数过一遍：一份文件里同时躺着四个日期 —— info 字典的 2026、水印的 2024、刊头的
# (c) 2023、正文自述的数据窗 2021-03 / 2022-09 —— 而回执只给了一行 `Date:`。他问的不是
# "哪个值最大"，是"哪个才是这篇东西的年份"。这一格最容易修坏的方式正是替他挑一个：
# 把 CreationDate 当文献年份印出去，等于把"这台机器什么时候导出 PDF"说成"论文哪年发表"。
# 所以这里只做形状能做的事：把页面上**印着的**日期形状数出来、按精确度排、点名在哪几页，
# 并明说这些都不是发表声明。不认 "Copyright" 一类的词，判据全在数字形状上。
_DATE_SHAPE = re.compile(
    r"(?<![\d.\-])(20\d{2}|1[89]\d{2})(?:-(\d{2})(?:-(\d{2}))?)?(?!\d)")
_DATE_LIST_CAP = 8


def _date_candidate_line(rendered: dict[int, str], page_nums: list[int]) -> str:
    """把返回的那几页里日期形状数成一行；一个都没有就交回空串（不占一行）。

    最长形状优先：`2024-01-01` 的一个位置不再另外产出 `2024-01` 与 `2024`。
    裸年太多时**报数不报清单** —— 参考列表里那二十个年份不是"这份文件的日期"。
    """
    seen: dict[str, tuple[int, set[int]]] = {}
    for n in page_nums:
        for m in _DATE_SHAPE.finditer(rendered[n]):
            token = m.group(0)
            parts = sum(1 for i in (1, 2, 3) if m.group(i))
            spec, pages = seen.get(token, (parts, set()))
            pages.add(n)
            seen[token] = (max(spec, parts), pages)
    if not seen:
        return ""
    ordered = sorted(seen.items(), key=lambda kv: (-kv[1][0], kv[0]))
    shown, rest = ordered[:_DATE_LIST_CAP], ordered[_DATE_LIST_CAP:]

    def where(pages: set[int]) -> str:
        rows = sorted(pages)
        return f"page {rows[0]}" if len(rows) == 1 else f"pages {_page_span(rows)}"

    bits = ", ".join(f"{token} ({where(pages)})" for token, (_spec, pages) in shown)
    if rest:
        bits += f", and {len(rest)} more year-shaped number(s) not listed"
    return (f"> dates readable in the returned text, most specific first: {bits} - "
            "these are printed on the pages; no date here is a stated publication "
            "date for the work")


def extract_pdf(
    body: bytes,
    extraction_type: str = "markdown",
    pages: str | None = None,
    password: str | None = None,
    include_media: bool = False,
) -> PdfResult:
    """Extract a PDF's bytes into agent-optimized markdown with honest quality
    signals + per-page CID-garbage auto-OCR fallback. See module docstring."""
    if not body or not isinstance(body, (bytes, bytearray)):
        return PdfResult(error="empty or non-bytes PDF body", content=["[Empty PDF body.]"])
    if not body[:5].startswith(b"%PDF"):
        return PdfResult(error="not_a_pdf: body does not start with %PDF",
                         content=["[Body is not a PDF despite content-type.]"])

    pdfplumber = _get_pdfplumber()
    text_mode = extraction_type == "text"

    try:
        pdf = pdfplumber.open(io.BytesIO(body), password=password or "")
    except Exception as e:
        kind = _encryption_problem(e)
        if kind == "password":
            detail = ("the supplied password was rejected" if password
                      else "no password was supplied")
            return PdfResult(encrypted=True,
                             error=f"encrypted_pdf: this PDF is password-protected; {detail} "
                                   "- pass the correct one via the 'password' option",
                             content=["[Encrypted PDF - pass a password to extract.]"])
        if kind == "unsupported":
            return PdfResult(encrypted=True,
                             error="encrypted_pdf: this PDF's encryption scheme is not "
                                   "supported by the installed PDF stack",
                             content=["[Encrypted PDF - unsupported encryption scheme.]"])
        # 消息可能是空串（pdfminer 不少异常 str() 为空）—— 那就至少把类型名报出来，
        # 留一条 "pdf_open_failed: " 等于什么也没说。
        msg = str(e)[:200] or type(e).__name__
        return PdfResult(error=f"pdf_open_failed: {msg}",
                         content=[f"[Could not open PDF: {msg}]"])

    try:
        total_pages = len(pdf.pages)
        if total_pages == 0:
            return PdfResult(error="empty_pdf: no pages", content=["[PDF has no pages.]"])

        page_nums = _parse_pages(pages, total_pages)
        if not page_nums:
            return PdfResult(pages_total=total_pages,
                             error="no_pages_in_range: the requested page range is out of bounds",
                             content=[f"[No pages in range '{pages}' (PDF has {total_pages} pages).]"])

        meta = pdf.metadata or {}
        meta_dict = _metadata_dict(meta)
        toc = _extract_toc(body)
        media = _extract_images_metadata(pdf, page_nums) if include_media else []

        # --- Pass 1: document body font size ---
        body_sizes: list[float] = []
        for n in page_nums:
            p = pdf.pages[n - 1]
            body_sizes.extend(c.get("size", 0) for c in p.chars if c.get("text", "").strip())
        body_size = statistics.median(body_sizes) if body_sizes else 0.0

        # --- Pass 2: render each page; detect CID corruption + scanned pages ---
        rendered: dict[int, str] = {}
        edges: dict[int, tuple[str, str]] = {}
        cid_pages: list[int] = []
        scan_pages: list[int] = []  # image-only pages in a MIXED PDF (per-page OCR)
        figures: dict[int, dict] = {}  # P4: 每页的非文本载荷（图 / 矢量），逐页数
        total_chars = 0
        heading_outline: list[dict] = []  # v8: heading-based section-map fallback
        for n in page_nums:
            p = pdf.pages[n - 1]
            try:
                page_md = _render_page(p, body_size, text_mode,
                                       page_num=n, headings=heading_outline,
                                       figures_out=figures, edges_out=edges)
            except Exception as e:
                logger.debug("PDF page %d render failed: %s", n, e)
                page_md = f"[Failed to render this page: {str(e)[:120]}]"
            rendered[n] = page_md
            total_chars += len(page_md)
            ratio, _n = _cid_ratio(page_md)
            if ratio >= _CID_RATIO_THRESHOLD:
                cid_pages.append(n)
            elif len(page_md.strip()) < _SCANNED_CHARS_PER_PAGE:
                # Low-text page: a scanned image page (has images) vs a blank
                # divider page (no images). Only flag the image-bearing ones.
                try:
                    has_images = len(p.images) > 0
                except Exception:
                    has_images = False
                if has_images:
                    scan_pages.append(n)

        # v8: section-map fallback. PDFs without a bookmark outline (most arxiv
        # papers, many reports) get a heading-based ToC built from the font-size
        # heading detection already run during render. The agent then navigates
        # with pages='X-Y' just like a bookmarked PDF. Clamped to the extracted
        # page range so the map matches what was actually returned.
        if not toc and heading_outline:
            toc = _heading_outline(heading_outline, page_nums, total_pages)

        # 有书签的那一路读的是整本文档，`pages=` 只裁了正文没裁地图：实测 19 页论文的
        # `pages='1-2'` 连 17-19 页的章节一起带回（29 条，约 1,000 token），而读者手上
        # 没有那些页可对照。无书签那一路早就按提取范围构造（上面那行），两条路口径
        # 不一致 —— 现在书签这一路也裁。
        if toc and len(page_nums) < total_pages:
            from dhole_mcp.context import _note_text
            _kept = _toc_within_pages(toc, page_nums)
            if len(_kept) != len(toc):
                _note_text(f"table_of_contents narrowed to the {len(page_nums)} of "
                           f"{total_pages} page(s) returned "
                           f"({len(toc)} sections -> {len(_kept)})")
                toc = _kept

        # --- All-scanned doc -> honest dead-end (caller runs the scanned-OCR) ---
        # 图的账要在这里就结好：扫描件那一路也带图（它就是由图组成的），那条返回
        # 同样得说得出"哪几页有什么在文本之外"，否则 P4 只修了一半的静默。
        figure_list = _figures_metadata(figures, page_nums, total_pages)
        if figure_list:
            meta_dict["figures"] = figure_list
        scanned = total_chars < _SCANNED_CHARS_PER_PAGE * len(page_nums)
        if scanned:
            # Capability-aware: reaching this branch with OCR installed means the
            # recovery path produced nothing, NOT that the user is missing the
            # extra. Saying "install [all]" there is a false instruction.
            advice = ocr_advice()
            _announce_figures(figure_list, total_pages)
            return PdfResult(
                title=meta_dict.get("title", ""), author=meta_dict.get("author", ""),
                subject=meta_dict.get("subject", ""), keywords=meta_dict.get("keywords", ""),
                pages_total=total_pages, pages_extracted=page_nums, scanned=True,
                metadata=meta_dict, table_of_contents=toc,
                error=f"scanned_pdf: this PDF is image-only (no extractable text). {advice}",
                content=[f"[Scanned/image-only PDF - no extractable text. {advice}]"],
            )

        # --- Per-page auto-OCR fallback: CID garbage + scanned pages (mixed PDFs) ---
        ocr_map = _ocr_pages(body, cid_pages + scan_pages, password) if (cid_pages or scan_pages) else {}
        ocr_used = bool(ocr_map)
        cid_pages_ocr = sorted(n for n in cid_pages if n in ocr_map)
        # Quality score from RAW page text + OCR recovery, so the honest markers
        # (clean English scaffolding) don't inflate the score and mask garbage.
        # OCR-recovered pages count as clean; unrecovered CID pages count as low.
        total_w = 0.0
        total_len = 0
        for n in page_nums:
            raw = rendered[n]
            if n in ocr_map:
                q = 1.0  # OCR recovered the visible text -> clean by construction
                plen = len(ocr_map[n])
            else:
                q = _quality_score(raw)
                plen = len(raw)
            total_w += q * plen
            total_len += plen
        quality = (total_w / total_len) if total_len else 0.0
        # R01-048 的账要数在摘除**之前**：它数的是"这一页印着什么"，不是"那句话最后住在
        # 信封的哪一格"—— 被 R01-037 当成页眉页脚存档的那些行，日期仍是页面上印着的。
        date_line = _date_candidate_line(rendered, page_nums)
        # R01-037：页边重复行。账算在摘除**之前** —— 上面那段 quality 用的还是原始页文本，
        # 这一刀只动 `rendered`，页数、质量分、`--- Page N ---` 页界一概不动。
        furniture_notes = _strip_running_furniture(
            rendered, page_nums,
            outside=_edge_lines_outside_window(body, password, page_nums, total_pages),
            edges=edges)
        # Now replace CID pages with markers (display only; quality already scored).
        for n in cid_pages:
            if n in ocr_map:
                rendered[n] = (
                    f"[Page {n}: text recovered by OCR from a broken font mapping — "
                    f"figures/equations OCR'd as visible symbols; use a vision tool "
                    f"for precise layout/LaTeX.]\n\n" + ocr_map[n]
                )
            else:
                rendered[n] = (
                    f"[Page {n}: {int(_cid_ratio(rendered[n])[0]*100)}% of this page "
                    f"is CID font garbage (embedded font without a Unicode map). "
                    f"{ocr_advice()}]\n\n"
                    + rendered[n]
                )

        for n in scan_pages:
            if n in ocr_map:
                rendered[n] = (
                    f"[Page {n}: scanned image page, text recovered by OCR.]\n\n"
                    + ocr_map[n]
                )
            else:
                rendered[n] = (
                    f"[Page {n}: scanned image page with no extractable text layer. "
                    f"{ocr_advice()}]\n\n" + rendered[n]
                )

        # --- Assemble: metadata header + pages ---
        # The outline this delivery ships, in the reader's own terms: bookmarks if
        # the file has them, otherwise the font-size measurement (see `toc` above).
        # Its titles are what the pages print, so a Title field equal to one of
        # them is already on the page and needs no second printing.
        outline_titles = tuple(
            {(h.get("title") or "").strip().casefold() for h in heading_outline}
            | {(t.get("title") or "").strip().casefold() for t in toc})
        header = _format_metadata(meta, own_outline=bool(toc),
                                  outline_titles=outline_titles)
        if len(page_nums) == total_pages:
            scope = f"{total_pages} pages"
        else:
            scope = (f"pages {page_nums[0]}-{page_nums[-1]} of {total_pages}"
                     if len(page_nums) > 1 else f"page {page_nums[0]} of {total_pages}")
        header.append(f"> PDF · {scope}")
        if ocr_used:
            header.append(f"> OCR fallback used on page(s): {', '.join(str(n) for n in sorted(ocr_map.keys()))}")
        # 摘走的每一颗都在这里逐字留一次底：正文干净了，但那两行仍可查（见
        # `_strip_running_furniture`）。没有页边行时这一串是空的，不多占一行。
        header.extend(furniture_notes)
        if date_line:
            header.append(date_line)
        body_md = "\n\n".join(f"--- Page {n} ---\n\n{rendered[n]}" for n in page_nums).strip()
        full = ("\n".join(header).strip() + "\n\n" + body_md).strip()

        # --- Quality score + honest content_ok (P3) ---
        # `quality` was computed above from raw page text + OCR recovery.
        content_ok = quality >= _QUALITY_OK_THRESHOLD and not scanned
        _announce_figures(figure_list, total_pages)

        return PdfResult(
            content=[full],
            title=meta_dict.get("title", ""), author=meta_dict.get("author", ""),
            subject=meta_dict.get("subject", ""), keywords=meta_dict.get("keywords", ""),
            pages_total=total_pages, pages_extracted=page_nums,
            metadata=meta_dict, table_of_contents=toc, quality_score=round(quality, 3),
            content_ok=content_ok, media=media, ocr_fallback_used=ocr_used,
            cid_pages_ocr=cid_pages_ocr,
        )
    finally:
        try:
            pdf.close()
        except Exception:
            pass


def _heading_outline(headings: list[dict], page_nums: list[int], total_pages: int
                     ) -> list[dict]:
    """Build a synthetic section-map from font-size-detected headings.

    Used when the PDF has no bookmark outline. Dedupes consecutive same-title
    headings, caps at 60 entries, keeps only headings on extracted pages, and
    computes end_page clamped to the extracted range so the map matches what was
    actually returned. Returns [{level, title, page, end_page}]."""
    if not headings:
        return []
    extracted = set(page_nums)
    max_extracted = max(page_nums) if page_nums else total_pages
    seen: set[tuple[int, str]] = set()
    toc: list[dict] = []
    for h in headings:
        page = h.get("page")
        if page is None or page not in extracted:
            continue
        title = (h.get("title") or "").strip()
        if not title or len(title) > 120:
            continue
        key = (page, title.lower())
        if key in seen:
            continue
        seen.add(key)
        toc.append({"level": int(h.get("level", 1)), "title": title, "page": int(page)})
        if len(toc) >= 60:
            break
    if not toc:
        return []
    toc = _add_end_pages(toc, total_pages)
    # Clamp end_page to the extracted range (we never return pages past max_extracted).
    for entry in toc:
        if "end_page" in entry:
            entry["end_page"] = min(int(entry["end_page"]), max_extracted)
    return toc


def _rotated_is_artifact(page: Any) -> bool:
    """Should non-upright characters be dropped from this page's text?

    Measured on arXiv 2609.25021 (the PDF behind the report's P2-8): page 1 carries
    39 non-upright chars out of ~3.1k, and they are exactly the noise the report
    quoted — the sidebar "arXiv:2609.25021v1  [cs.LG]  9 Aug 2026" is drawn rotated
    90°, so the extractor walks it in reading order and emits
    "1v12052.9062:viXra", "]GL.sc[", "guA".

    The flip side is a page where rotation IS the content — vertical CJK typesetting,
    or a whole page scanned sideways. There, dropping non-upright chars deletes the
    document, so the rule is a ratio, not a blanket: past half the characters on the
    page, the rotation is kept.
    """
    try:
        chars = page.chars
        tilted = sum(1 for c in chars if not c.get("upright", True))
    except Exception:
        return False
    if not chars:
        return False
    return tilted <= len(chars) / 2


def _render_page(page: Any, body_size: float, text_mode: bool,
                 page_num: int | None = None, headings: list[dict] | None = None,
                 figures_out: dict[int, dict] | None = None,
                 edges_out: dict[int, tuple[str, str]] | None = None) -> str:
    """Render one page to markdown: layout-aware text + tables merged by y-position.

    The figure census happens here rather than in `extract_pdf` because "what this
    page draws that did not come back" is answerable only next to the table
    bboxes this function already computed — see `_figures_on_page`.
    """
    try:
        tables = page.find_tables(table_settings={"text_x_tolerance": _X_TOL, "text_y_tolerance": 3})
    except Exception:
        tables = []
    table_bboxes = [t.bbox for t in tables] if tables else []
    if figures_out is not None and page_num is not None:
        facts = _figures_on_page(page, table_bboxes)
        if facts:
            figures_out[page_num] = facts

    drop_rotated = _rotated_is_artifact(page)
    if table_bboxes or drop_rotated:
        def _keep(obj: Any) -> bool:
            if obj.get("object_type") != "char":
                return True
            if drop_rotated and not obj.get("upright", True):
                return False
            return not (table_bboxes and any(_in_bbox(obj, b) for b in table_bboxes))

        filtered = page.filter(_keep)
    else:
        filtered = page

    try:
        text_lines = filtered.extract_text_lines(layout=False, x_tolerance=_X_TOL, return_chars=True)
    except Exception:
        text_lines = []
    if not text_lines and filtered.chars:
        txt = filtered.extract_text(layout=False, x_tolerance=_X_TOL) or ""
        return _dehyphenate_block(txt, text_mode)

    blocks: list[tuple[float, str, Any]] = []
    for tl in text_lines:
        txt = _clean_text(tl.get("text", ""))
        if txt:
            blocks.append((float(tl.get("top", 0)), "text",
                           (txt, tl.get("chars", []),
                            float(tl.get("top", 0)), float(tl.get("bottom", 0)))))
    for t in tables:
        try:
            data = t.extract()
        except Exception:
            data = []
        if data and any(any(cell for cell in row) for row in data):
            blocks.append((float(t.bbox[1]), "table",
                           (data, table_geometry(t))))

    blocks.sort(key=lambda b: b[0])

    line_heights = [float(tl.get("bottom", 0)) - float(tl.get("top", 0))
                    for tl in text_lines if tl.get("text", "").strip()]
    median_lh = statistics.median(line_heights) if line_heights else 12.0
    gap_threshold = max(8.0, median_lh * 1.5)

    out: list[str] = []
    para: list[str] = []
    merged_tables = 0
    merged_cells_total = 0

    def flush_paragraph():
        if not para:
            return
        joined = para[0]
        for nxt in para[1:]:
            joined, _ = _dehyphenate_join(joined, nxt)
        joined = _clean_text(joined)
        if joined:
            out.append(joined)
        para.clear()

    prev_bottom: float | None = None
    #: 这一页**印着**的第一行与最后一行（合并之前）。渲染器本来就一行一行地走，
    #: 页边行那一判要的就是这个 —— 块是合并之后的东西，「它是一行」量在块上会把自己
    #: 拼出来的段落当成页边行（R02-052）。单一作者：下游不许再切一次行。
    edge_first: str | None = None
    edge_last: str | None = None
    bi = 0
    while bi < len(blocks):
        top, kind, payload = blocks[bi]
        bi += 1
        if kind == "table":
            flush_paragraph()
            prev_bottom = None
            # 这一页以表格收尾时，最后一行正文不是它的底边 —— 那一格不当页边候选
            edge_last = None
            data, geometry = payload
            if geometry and not table_is_rectangular(geometry):
                spans = sum(1 for row in geometry for c in row
                            if not c["covered"] and (c["rspan"] > 1 or c["cspan"] > 1))
                merged_tables += 1
                merged_cells_total += spans
                md = _merged_table_to_text(geometry)
            else:
                md = _table_to_markdown(data, text_mode=text_mode)
            if md:
                out.append(md)
            continue
        txt, chars, line_top, line_bottom = payload
        level = _heading_level(_dominant_size(chars), body_size, _is_bold(chars), txt) if chars else 0
        if level and not text_mode:
            flush_paragraph()
            # 被页面框折断的那几行折回来再说它是几行：标题是"这一行印刷"的单位，
            # 不是"这一行文本"的单位（判据全在几何，见 _fold_heading_lines）。
            title, consumed = _fold_heading_lines(blocks, bi - 1, gap_threshold)
            bi = consumed + 1
            out.append(f"{'#' * level} {title}")
            if headings is not None and page_num is not None:
                headings.append({"level": level, "title": title.strip(),
                                 "page": page_num})
            prev_bottom = None
            continue
        if not text_mode and _is_numbered_title_line(txt):
            # 前后都断开：先把手上的段落交出去，自己单独一块，并让下一行从新段落起。
            flush_paragraph()
            out.append(txt)
            prev_bottom = None
            continue
        if prev_bottom is not None and (line_top - prev_bottom) > gap_threshold:
            flush_paragraph()
        if edge_first is None:
            edge_first = _clean_text(txt)
        edge_last = _clean_text(txt)
        para.append(txt)
        prev_bottom = line_bottom
    flush_paragraph()
    if edges_out is not None and page_num is not None and edge_first and edge_last:
        edges_out[page_num] = (edge_first, edge_last)
    _announce_merged_table(merged_cells_total, merged_tables)

    return "\n\n".join(out).strip()


def _dehyphenate_block(text: str, text_mode: bool) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return ""
    joined = lines[0]
    for nxt in lines[1:]:
        joined, _ = _dehyphenate_join(joined, nxt)
    return _clean_text(joined)
