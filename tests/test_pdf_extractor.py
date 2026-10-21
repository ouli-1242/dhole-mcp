"""test_pdf_extractor.py —— 由 5 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import inspect
import io
import pytest
from dhole_mcp.pdf_extractor import extract_pdf
import pymupdf
from dhole_mcp import context
from dhole_mcp.models import ResponseModel
from dhole_mcp.server import _apply_chunking
from pathlib import Path


# ============================================================================
# 来源：test_a_repeated_page_edge_line_that_stays_says_that_it_stays.py
# ============================================================================
# 页边那一行重复了却被排版吸进段落时，不许静默：删不掉就要说它留下了。

pytest.importorskip("pypdf")

MASTHEAD = "Journal of Test Remote Sensing | Vol. 9, No. 1 | (c) 2019 Test Publisher"
WORD = ["one", "two", "three"]
BODY_T = "Body paragraph {w} closes the section with a real sentence."
WATERMARK = "Downloaded by ward-clerk on 2024-0{i}-05 (FOR INTERNAL USE ONLY) [page {i} of 3]"
STAYS = "but stays in the body"
REMOVED = "removed from the body"


def _pdf(pages: list[list[tuple[float, float, str]]]) -> bytes:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    def esc(s: str) -> str:
        return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica")}))
    for items in pages:
        page = writer.add_blank_page(width=595, height=842)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        ops = ["BT"] + [f"/F1 {size} Tf 1 0 0 1 60 {y} Tm ({esc(text)}) Tj"
                        for y, size, text in items] + ["ET"]
        stream = DecodedStreamObject()
        stream.set_data("\n".join(ops).encode("latin-1"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    bio = io.BytesIO()
    writer.write(bio)
    return bio.getvalue()


def _merged(i: int) -> list[tuple[float, float, str]]:
    """现场形状：页脚**紧贴**最后一行正文（行距 14），字号与正文同级（分不开的那一堆）。"""
    return [(760, 11, f"{i}. Section heading number {i}"),
            (700, 11, BODY_T.format(w=WORD[i - 1])),
            (686, 11, WATERMARK.format(i=i))]


MERGED = _pdf([_merged(i) for i in (1, 2, 3)])


def _spaced(i: int) -> list[tuple[float, float, str]]:
    """对照组：页边行独立成块（行距拉开），那一族照旧该被摘走。"""
    return [(800, 9, MASTHEAD),
            (740, 11, f"{i}. Section heading number {i}"),
            (680, 11, BODY_T.format(w=WORD[i - 1])),
            (60, 9, WATERMARK.format(i=i))]


SPACED = _pdf([_spaced(i) for i in (1, 2, 3)])


def _text(res) -> str:
    return "\n".join(res.content)


def _body(md: str) -> str:
    return "\n".join(ln for ln in md.splitlines() if not ln.startswith(">"))


def _stays(md: str) -> list[str]:
    return [ln for ln in md.splitlines() if STAYS in ln]


def _removals(md: str) -> list[str]:
    return [ln for ln in md.splitlines() if REMOVED in ln]


# ─── 1 · 说出口这一半 ───

def test_a_repeated_edge_line_that_is_merged_says_that_it_stays():
    md = _text(extract_pdf(MERGED))
    notes = _stays(md)
    assert len(notes) == 1, md[:700]
    assert "last printed line" in notes[0], notes[0]


def test_the_staying_sentence_says_why_and_names_the_pages():
    n = _stays(_text(extract_pdf(MERGED)))[0]
    assert "pages 1-3" in n, n
    assert "longer paragraph" in n, n            # 为什么：它没独立成行
    assert "nothing in size or margin" in n, n   # 为什么不是"我们懒得切"
    assert "Delete that line by hand" in n, n    # 读者能做什么


def test_a_declaration_deletes_nothing():
    """申报不删任何东西：那句页脚与它旁边那句正文都逐字在。"""
    md = _body(_text(extract_pdf(MERGED)))
    assert "FOR INTERNAL USE ONLY" in md, md[:700]
    for w in WORD:
        assert BODY_T.format(w=w) in md, md[:700]


def test_the_declaration_quotes_the_line_once():
    md = _text(extract_pdf(MERGED))
    n = _stays(md)[0]
    assert WATERMARK.format(i=1)[:30] in n, n
    assert "numbers vary page to page" in n, n   # 引的是第一页那一条，其余换了号


def test_the_declaration_is_not_the_removal_sentence():
    """两句话不许共用词汇：这一刀没删任何东西，也不许读起来像删了。"""
    md = _text(extract_pdf(MERGED))
    assert not _removals(md), md[:700]
    for n in _stays(md):
        assert "running header" not in n and "running footer" not in n, n


# ─── 2 · 反面锁 ───

def test_an_edge_line_that_is_its_own_block_is_still_lifted_not_declared():
    """已经成的那一半不许退化：独立成块的页边行照旧摘走 + 逐字交代，不该出现申报句。"""
    md = _text(extract_pdf(SPACED))
    assert "running footer removed from the body" in md, md[:700]
    assert "FOR INTERNAL USE ONLY" not in _body(md), md[:700]
    assert not _stays(md), md[:700]


def test_a_document_without_repetition_says_nothing():
    """每页换**字**（不是换数字）时两句话都不许出现 —— 没有重复就没有陈述，一个字节都不付。"""
    once = _pdf([[(760, 11, f"{WORD[i - 1].title()} section heading for this page"),
                  (700, 11, BODY_T.format(w=WORD[i - 1])),
                  (686, 11, f"A closing line only the {WORD[i - 1]} page prints")]
                 for i in (1, 2, 3)])
    md = _text(extract_pdf(once))
    assert "running header" not in md and "running footer" not in md, md[:700]
    assert not _stays(md), md[:700]
    for w in WORD:
        assert BODY_T.format(w=w) in md, md[:700]


def test_a_size_recognised_heading_is_never_named_by_the_declaration():
    """17pt 的题头被渲染器认成 `#`，它压根不记为页边行 —— 摘除与申报两条路都不许碰它。

    `Chapter 1 / Chapter 2 / Chapter 3` 折成同一颗键（数字折叠），所以这一格是"每一页首行
    重复"最像的形状；挡住它的是同一个来源（渲染器不交那一条），不是第二把尺。
    正文每页**换字**，于是这一份里连一句申报都不该有。
    """
    pdf = _pdf([[(800, 17, f"Chapter {i} of the annual review"),
                 (740, 11, BODY_T.format(w=WORD[i - 1])),
                 (680, 11, f"A body sentence only the {WORD[i - 1]} chapter prints here.")]
                for i in (1, 2, 3)])
    md = _text(extract_pdf(pdf))
    assert not _stays(md), md[:500]
    assert not _removals(md), md[:500]
    for i in (1, 2, 3):
        assert f"Chapter {i} of the annual review" in md, md[:500]


def test_a_page_whose_only_line_is_the_repeated_one_pays_no_staying_sentence():
    """整页只有那一行时不许编理由：那不是"被段落吸住"，是"摘完就空"那一族（别的句子在管）。"""
    solo = _pdf([[(700, 11, f"Standing order number {i} ships from the depot")]
                 for i in (1, 2, 3)])
    md = _text(extract_pdf(solo))
    assert not _stays(md), md[:700]
    assert not _removals(md), md[:700]
    assert "Standing order number 1" in md, md[:500]


def test_the_staying_sentence_is_not_a_verdict():
    low = _text(extract_pdf(MERGED)).lower()
    assert "irrelevant" not in low and "off-topic" not in low, low[:700]


def test_a_one_page_window_declares_with_evidence_from_the_pages_it_kept_back():
    """`pages=` 缩窄交付不缩窄判据（R02-023 那一半对两条陈述同一口径）：尾巴要说清证据来源。"""
    md = _text(extract_pdf(MERGED, pages="2"))
    notes = _stays(md)
    assert len(notes) == 1, md[:700]
    assert "outside this window" in notes[0], notes[0]
    assert "delivered here: page 2" in notes[0], notes[0]
    assert "FOR INTERNAL USE ONLY" in _body(md), md[:700]


# ─── 3 · 作者唯一 ───

def test_no_third_scale_is_introduced():
    from dhole_mcp import pdf_extractor as m

    src = inspect.getsource(m)
    assert src.count("def _furniture_key") == 1, "跨页那一判的第二个作者"
    assert src.count("def _edge_note_suffix") == 1, "两条陈述共用同一串尾巴"
    assert "edges_out" in src, "页边那一行要有渲染器那一个来源"


# ============================================================================
# 来源：test_a_page_window_still_uses_the_whole_documents_evidence.py
# ============================================================================
# 页窗口只缩窄交付，不缩窄证据：整份文件在产品手里，跨页那一判就该用整份。
# 重名消歧：_body → _body__a_page_window_still_uses_c459、_pdf → _pdf__a_page_window_still_uses_c459、_text → _text__a_page_window_still_uses_c459

pytest.importorskip("pypdf")

HEADER = ("Journal of Test Remote Sensing | Vol. 9, No. 1 | (c) 2019 Test Publisher")
FOOTER_T = ("Downloaded by 10.0.0.{i} on 2024-0{i}-05 (NOT FOR DISTRIBUTION) "
            "[page {i} of 3]")


def _pdf__a_page_window_still_uses_c459(pages: list[list[tuple[float, float, str]]]) -> bytes:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    def esc(s: str) -> str:
        return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica")}))
    for items in pages:
        page = writer.add_blank_page(width=595, height=842)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        ops = ["BT"] + [f"/F1 {size} Tf 1 0 0 1 60 {y} Tm ({esc(text)}) Tj"
                        for y, size, text in items] + ["ET"]
        stream = DecodedStreamObject()
        stream.set_data("\n".join(ops).encode("latin-1"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    bio = io.BytesIO()
    writer.write(bio)
    return bio.getvalue()


def _page(i: int, *, header: str | None = HEADER, footer: str | None = None,
          middle: str | None = None) -> list[tuple[float, float, str]]:
    items = []
    if header is not None:
        items.append((800, 9, header))
    items += [(740, 11, f"{i}. Section heading number {i}"),
              (720, 11, f"Body paragraph on page {i} with real content inside it.")]
    if middle is not None:
        items.append((700, 11, middle))
    if footer is not None:
        items.append((60, 9, footer))
    return items


THREE_PAGES = _pdf__a_page_window_still_uses_c459([_page(1, footer=FOOTER_T.format(i=1)),
                    _page(2, footer=FOOTER_T.format(i=2)),
                    _page(3, footer=FOOTER_T.format(i=3))])


def _text__a_page_window_still_uses_c459(res) -> str:
    return "\n".join(res.content)


def _body__a_page_window_still_uses_c459(res) -> str:
    """窗口那一页的正文（头部那些 `>` 行不算 —— 它们正是本刀要检查的去向交代）。"""
    whole = _text__a_page_window_still_uses_c459(res)
    marker = whole.find("--- Page ")
    return whole[marker:] if marker >= 0 else whole


# ─── 1 · 窗口里的正文：证据来自整份 ───

def test_a_single_page_window_loses_its_running_furniture_too():
    res = extract_pdf(THREE_PAGES, pages="2")
    body = _body__a_page_window_still_uses_c459(res)
    assert "NOT FOR DISTRIBUTION" not in body, body
    assert "Journal of Test Remote Sensing" not in body, body
    assert "Body paragraph on page 2" in body, body


def test_the_note_names_the_evidence_pages_and_the_window():
    res = extract_pdf(THREE_PAGES, pages="2")
    notes = [ln for ln in _text__a_page_window_still_uses_c459(res).splitlines() if "running footer removed" in ln]
    assert len(notes) == 1, _text__a_page_window_still_uses_c459(res)[:600]
    n = notes[0]
    assert "pages 1-3" in n, n                        # 证据来自哪几页
    assert "window" in n.lower() and "page 2" in n, n  # 交付的只是这一页，要说得出


def test_the_header_of_the_same_window_is_accounted_for_too():
    res = extract_pdf(THREE_PAGES, pages="2")
    assert any("running header removed" in ln for ln in _text__a_page_window_still_uses_c459(res).splitlines()), \
        _text__a_page_window_still_uses_c459(res)[:600]


# ─── 2 · 反面锁 ───

def test_a_line_that_only_exists_on_the_window_page_is_left_alone():
    """整份里没有第二页与它同侧 → 那是一行正文，不是页边行，也不许多出一句申报。"""
    odd = _pdf__a_page_window_still_uses_c459([_page(1, header=None, footer=FOOTER_T.format(i=1)),
                _page(2, header=None, footer=FOOTER_T.format(i=2),
                      middle="A short line only on page two"),
                _page(3, header=None, footer=FOOTER_T.format(i=3))])
    res = extract_pdf(odd, pages="2")
    assert "A short line only on page two" in _body__a_page_window_still_uses_c459(res), _text__a_page_window_still_uses_c459(res)[:600]
    assert not any("only on page two" in ln for ln in _text__a_page_window_still_uses_c459(res).splitlines()
                   if ln.startswith(">")), _text__a_page_window_still_uses_c459(res)[:600]


def test_the_whole_document_note_keeps_its_old_shape():
    """整份读取那一路一字不改形：「窗口」那一维只在窗口真的存在时才出现。"""
    res = extract_pdf(THREE_PAGES)
    notes = [ln for ln in _text__a_page_window_still_uses_c459(res).splitlines() if "running footer removed" in ln]
    assert len(notes) == 1, _text__a_page_window_still_uses_c459(res)[:600]
    assert "pages 1-3" in notes[0], notes[0]
    assert "window" not in notes[0].lower(), notes[0]


def test_the_at_least_two_blocks_gate_still_holds_inside_a_window():
    """窗口页只剩两段时页边行照旧不摘 —— 宁留噪声，也不交出一页空白。"""
    thin = _pdf__a_page_window_still_uses_c459([_page(1, header=None, footer=FOOTER_T.format(i=1)),
                 [(800, 9, FOOTER_T.format(i=2)), (60, 9, "Last short line of the thin page")],
                 _page(3, header=None, footer=FOOTER_T.format(i=3))])
    res = extract_pdf(thin, pages="2")
    assert "Last short line of the thin page" in _body__a_page_window_still_uses_c459(res), _text__a_page_window_still_uses_c459(res)[:600]


# ─── 3 · 作者与成本 ───

def test_the_comparison_key_has_one_author():
    from dhole_mcp import pdf_extractor as m

    src = inspect.getsource(m)
    assert src.count("def _furniture_key") == 1, "规范化尺出现第二个作者 = 两条路会分叉"
    assert src.count("def _strip_running_furniture") == 1


def test_pages_outside_the_window_are_read_for_two_lines_not_rendered():
    """窗口外的页只为准「页边那一行」而读：不许把整份重新渲染一遍来回答一个单页请求。"""
    from dhole_mcp import pdf_extractor as m

    real = m._render_page
    seen: list = []

    def spy(*args, **kw):
        seen.append(kw.get("page_num", args[3] if len(args) > 3 else None))
        return real(*args, **kw)

    m._render_page = spy
    try:
        extract_pdf(THREE_PAGES, pages="2")
    finally:
        m._render_page = real
    assert [n for n in seen if n is not None] == [2], seen


# ============================================================================
# 来源：test_a_merged_paragraph_is_not_a_line_the_page_prints.py
# ============================================================================
# 「它是一行」必须量在**印刷行**上：段落合并拼出来的那一条不是页眉页脚，是真正文的一段话。
# 重名消歧：MASTHEAD → MASTHEAD__a_merged_paragraph_is_no_889b、SPACED → SPACED__a_merged_paragraph_is_no_889b、WATERMARK → WATERMARK__a_merged_paragraph_is_no_889b、_body → _body__a_merged_paragraph_is_no_889b、_pdf → _pdf__a_merged_paragraph_is_no_889b、_spaced → _spaced__a_merged_paragraph_is_no_889b、_text → _text__a_merged_paragraph_is_no_889b

pytest.importorskip("pypdf")

BODY_ONE = "Body sentence number {i} explaining the chapter plainly."
BODY_TWO = "More body text on page {i} so this page is a real page."
MASTHEAD__a_merged_paragraph_is_no_889b = "Journal of Test Remote Sensing | Vol. 9, No. 1 | (c) 2019 Test Publisher"
WATERMARK__a_merged_paragraph_is_no_889b = "Downloaded by 10.0.0.{i} on 2024-0{i}-05 (NOT FOR DISTRIBUTION) [page {i} of 3]"


def _pdf__a_merged_paragraph_is_no_889b(pages: list[list[tuple[float, float, str]]]) -> bytes:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    def esc(s: str) -> str:
        return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica")}))
    for items in pages:
        page = writer.add_blank_page(width=595, height=842)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        ops = ["BT"] + [f"/F1 {size} Tf 1 0 0 1 60 {y} Tm ({esc(text)}) Tj"
                        for y, size, text in items] + ["ET"]
        stream = DecodedStreamObject()
        stream.set_data("\n".join(ops).encode("latin-1"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    bio = io.BytesIO()
    writer.write(bio)
    return bio.getvalue()


def _chapter(i: int) -> list[tuple[float, float, str]]:
    """现场那一页：17pt 题头 + 两行 11pt 正文，行距 14 —— 后两行会被并成一个块。"""
    return [(800, 17, f"Chapter {i} of the annual review"),
            (740, 11, BODY_ONE.format(i=i)),
            (726, 11, BODY_TWO.format(i=i))]


CHAPTERS = _pdf__a_merged_paragraph_is_no_889b([_chapter(i) for i in (1, 2, 3)])


def _spaced__a_merged_paragraph_is_no_889b(i: int) -> list[tuple[float, float, str]]:
    """同一份内容，但页边行**独立成块**（行距拉开到 gap_threshold 之上）—— R01-037 那一族。"""
    return [(800, 9, MASTHEAD__a_merged_paragraph_is_no_889b),
            (740, 11, f"{i}. Section heading number {i}"),
            (700, 11, BODY_ONE.format(i=i)),
            (660, 11, BODY_TWO.format(i=i)),
            (60, 9, WATERMARK__a_merged_paragraph_is_no_889b.format(i=i))]


SPACED__a_merged_paragraph_is_no_889b = _pdf__a_merged_paragraph_is_no_889b([_spaced__a_merged_paragraph_is_no_889b(i) for i in (1, 2, 3)])


def _text__a_merged_paragraph_is_no_889b(res) -> str:
    return "\n".join(res.content)


def _body__a_merged_paragraph_is_no_889b(md: str) -> str:
    return "\n".join(ln for ln in md.splitlines() if not ln.startswith(">"))


# ─── 1 · 现场那一格：正文不许被当成页脚摘走 ───

def test_a_merged_paragraph_is_not_page_furniture():
    """R02-052 的锁一条不减：F31 的申报句用的是**位置**词（`first/last printed line …`），
    所以这里连 `running header` / `running footer` 两个词都不许出现 —— 两种陈述的词汇
    分开来锁，才是「没删就不许说成删过」与「说了就不许不改口径」同时成立。
    """
    md = _text__a_merged_paragraph_is_no_889b(extract_pdf(CHAPTERS))
    assert not any(ln.startswith("> running footer") for ln in md.splitlines()), md[:700]
    assert not any(ln.startswith("> running header") for ln in md.splitlines()), md[:700]


def test_the_two_body_sentences_stay_in_the_body_where_they_are():
    md = _body__a_merged_paragraph_is_no_889b(_text__a_merged_paragraph_is_no_889b(extract_pdf(CHAPTERS)))
    for i in (1, 2, 3):
        assert BODY_ONE.format(i=i) in md, md[:700]
        assert BODY_TWO.format(i=i) in md, md[:700]


# ─── 2 · 反面锁：独立成块的页边行照旧（R01-037 那一族一条不退化） ───

def test_a_masthead_that_is_its_own_line_is_still_lifted():
    md = _text__a_merged_paragraph_is_no_889b(extract_pdf(SPACED__a_merged_paragraph_is_no_889b))
    assert "running header removed from the body" in md, md[:700]
    assert MASTHEAD__a_merged_paragraph_is_no_889b.split("|")[0] not in _body__a_merged_paragraph_is_no_889b(md), md[:700]


def test_a_watermark_that_is_its_own_line_is_still_lifted():
    md = _text__a_merged_paragraph_is_no_889b(extract_pdf(SPACED__a_merged_paragraph_is_no_889b))
    assert "running footer removed from the body" in md, md[:700]
    assert "NOT FOR DISTRIBUTION" not in _body__a_merged_paragraph_is_no_889b(md), md[:700]


def test_the_body_of_that_document_is_untouched():
    md = _body__a_merged_paragraph_is_no_889b(_text__a_merged_paragraph_is_no_889b(extract_pdf(SPACED__a_merged_paragraph_is_no_889b)))
    for i in (1, 2, 3):
        assert f"{i}. Section heading number {i}" in md, md[:700]
        assert BODY_ONE.format(i=i) in md, md[:700]
        assert BODY_TWO.format(i=i) in md, md[:700]


# ─── 3 · 单一作者：印刷行由渲染器交回，判据不自己再切一次 ───

def test_the_printed_line_has_one_author():
    from dhole_mcp import pdf_extractor as m

    src = inspect.getsource(m)
    assert "edges_out" in src, "印刷行没有单一来源 = 判据要自己再切一遍行"
    assert src.count("def _furniture_key") == 1, "比较键的第二个作者 = 两条路会分叉"


# ============================================================================
# 来源：test_a_continuation_page_does_not_repay_the_whole_map.py
# ============================================================================
# 续页与切片不许重付整份章节地图（2026-10-08 真实使用模拟 · 刀7）

def _booklet(pages: int = 6, toc=None) -> bytes:
    doc = pymupdf.open()
    for i in range(1, pages + 1):
        page = doc.new_page()
        page.insert_text((72, 90), f"Section {i} body. " * 30)
    doc.set_toc(toc if toc is not None else [
        [1, "Chapter one", 1], [1, "Chapter two", 3], [1, "Chapter three", 5]])
    out = doc.tobytes()
    doc.close()
    return out


def _titles(result):
    return [e["title"] for e in result.table_of_contents]


class TestASlicedPdfCarriesOnlyItsOwnMap:

    def test_the_whole_document_keeps_the_whole_map(self):
        """反向锁：不带 `pages=` 时一寸不许少。"""
        assert _titles(extract_pdf(_booklet())) == ["Chapter one", "Chapter two",
                                                    "Chapter three"]

    def test_pages_narrows_the_outline_to_the_pages_returned(self):
        got = _titles(extract_pdf(_booklet(), pages="1-2"))
        assert got == ["Chapter one"], got
        assert "Chapter three" not in got, "第 5 章不在交回的页里"

    def test_a_range_spanning_two_chapters_keeps_both(self):
        assert _titles(extract_pdf(_booklet(), pages="2-4")) == [
            "Chapter one", "Chapter two"]

    def test_the_narrowing_is_announced(self):
        with context._arg_notes_scope() as notes:
            extract_pdf(_booklet(), pages="1-2")
        assert any("table_of_contents" in n for n in notes), (
            f"裁了大纲却没说：{notes}")

    def test_an_entry_that_cannot_be_placed_is_kept_not_dropped(self):
        """放不进任何页的条目宁可多留一条，不许悄悄丢一章。"""
        odd = [[1, "No page", 0], [1, "Chapter two", 3]]
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("dhole_mcp.pdf_extractor._extract_toc",
                       lambda body: [{"title": t, "page": p, "end_page": p}
                                     for _lvl, t, p in odd])
            got = _titles(extract_pdf(_booklet(), pages="1-2"))
        assert "No page" in got, got


class TestAContinuationPageDoesNotRepayIt:

    def _result(self, toc):
        body = "x" * 2000
        return ResponseModel(url="https://docs.test/p.pdf", status=200,
                             content=[body], fetcher_used="http",
                             extracted_type="markdown", content_ok=True,
                             total_extracted_chars=2000,
                             table_of_contents=toc)

    def test_offset_zero_still_carries_the_map(self):
        out = _apply_chunking(self._result([{"title": "A", "page": 1}]),
                              max_chars=500, offset=0)
        assert len(out.table_of_contents) == 1

    def test_a_continuation_slice_says_why_the_map_is_absent(self):
        toc = [{"title": "A", "page": 1}, {"title": "B", "page": 9}]
        with context._arg_notes_scope() as notes:
            out = _apply_chunking(self._result(toc), max_chars=500, offset=500)
        assert out.table_of_contents == [], "续页又付了一遍整份地图"
        assert any("table_of_contents" in n for n in notes), (
            f"省略了要说明，不许静默缺席：{notes}")

    def test_a_pdf_with_no_map_at_all_stays_silent(self):
        """没有地图就没什么可宣布的。"""
        with context._arg_notes_scope() as notes:
            _apply_chunking(self._result([]), max_chars=500, offset=500)
        assert not any("table_of_contents" in n for n in notes), notes


# ============================================================================
# 来源：test_pdf_real.py
# ============================================================================
# 真 PDF 字节上的提取契约（含口令链的最后一环）。

PDF_DIR = Path(__file__).parent
TEXT_PDF = PDF_DIR / "background_checks.pdf"
SCANNED_PDF = PDF_DIR / "dummy.pdf"
MARKER = "Firearm Background Checks"   # 实测出现在正文里（见下 test_text_pdf）
PASSWORD = "correct horse battery staple"


def _encrypted(pdf_bytes: bytes, password: str = PASSWORD) -> bytes:
    pytest.importorskip("pypdf")
    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(io.BytesIO(pdf_bytes))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt(password, algorithm="AES-256")
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


class TestPlaintextPdfBytes:
    """先证明 fixture 本身是可解析的真 PDF —— 否则下面每条断言都可能是空转。"""

    def test_text_pdf_is_extracted(self):
        res = extract_pdf(TEXT_PDF.read_bytes())
        assert res.error == ""
        assert res.encrypted is False
        assert res.pages_total >= 1
        assert MARKER in res.content[0]

    def test_image_only_pdf_is_reported_as_scanned(self):
        """扫描件要如实说"没有可提取文本"，而不是给一具空正文。"""
        res = extract_pdf(SCANNED_PDF.read_bytes())
        assert res.error.startswith("scanned_pdf")
        assert res.content and "no extractable text" in res.content[0]


class TestEncryptedPdf:

    def test_missing_password_is_diagnosed_not_reported_as_broken(self):
        res = extract_pdf(_encrypted(TEXT_PDF.read_bytes()), password=None)
        assert res.encrypted is True, "加密标志要立起来（调用方据此提示口令）"
        assert res.error.startswith("encrypted_pdf")
        assert "no password was supplied" in res.error
        assert "the supplied password was rejected" not in res.error

    def test_wrong_password_is_distinguishable_from_missing(self):
        """两者此前都是同一条空消息 —— agent 没法据此改行为。"""
        res = extract_pdf(_encrypted(TEXT_PDF.read_bytes()), password="wrong")
        assert res.encrypted is True
        assert "the supplied password was rejected" in res.error

    def test_correct_password_returns_the_real_text(self):
        res = extract_pdf(_encrypted(TEXT_PDF.read_bytes()), password=PASSWORD)
        assert res.error == ""
        assert MARKER in res.content[0], "解出来的必须是原正文，不是占位符"

    def test_corrupt_bytes_are_not_blamed_on_a_password(self):
        """反例守卫：真正坏掉的文件不许被说成"需要口令"。"""
        res = extract_pdf(b"%PDF-1.4\nnot a real pdf")
        assert res.encrypted is False
        assert res.error.startswith("pdf_open_failed")
        assert res.error.strip() != "pdf_open_failed:", "错误消息不许是空的"
