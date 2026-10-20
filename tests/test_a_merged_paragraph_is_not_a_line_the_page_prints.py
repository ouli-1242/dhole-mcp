r"""「它是一行」必须量在**印刷行**上：段落合并拼出来的那一条不是页眉页脚，是真正文的一段话。

现场（P4 写 R02-F28 的一趟撞出来的 · R02-052 · P2 · 组 E；**已提交的树 9f36ab6 上可重跑**）：

    三页靶件，每页三行印刷文字：17pt 的 `Chapter {i} of the annual review`、
    11pt 的 `Body sentence number {i} explaining the chapter plainly.`、
    11pt 的 `More body text on page {i} so this page is a real page.`（后两行的行距 14）

    extract_pdf(...) 的头部交出：
      > running footer removed from the body: "Body sentence number 1 explaining the chapter
        plainly. More body text on page 1 so this page is a real page."
        (last line of pages 1-3); numbers vary page to page

**被摘走的是一段真正文**。`_strip_running_furniture` 的第二道闸写着「它是一行且短」，
但它读的是 `_blocks_with_offsets` 交回的**块**，而块是段落合并之后的东西 —— 合并只看行距
（`gap_threshold = max(8.0, median_lh * 1.5)`，这份靶件里 ≈21，而行距是 4）：
同一段里的两行印刷行被拼成一条 ≤120 字符的「一行」，再叠上数字折叠（`page 1` 与 `page 2`
折成同一颗）与「每页同一侧 ≥2 页」，三道闸全过 —— **过的是一把错尺**：
量的不是「这一行印刷着」，是「这次合并正好把它拼成了一块」。

既有测试为什么没看见：`test_a_numbered_heading_at_the_top_of_every_page_stays` 只断了
`running header` 不在（它防的是第四道闸那一格），页眉那一侧确实没事；页脚这一侧被摘走的是正文，
那句话里就引着它，谁都没去读。这一格是 R02-010（页脚被合并吞掉、躲过摘除）的**镜像**：
同一把错尺，一头把正文当页脚摘走，一头把页脚当正文留下。两半共用同一份新信息 ——
渲染器本来就一行一行地走，把每页的首/末**印刷行**交回来就行。

契约（本文件钉住）：
  * 页边那一判的「一行」改成按**印刷行**判：一个块只有当它逐字等于这一页的第一行（页眉侧）
    或最后一行（页脚侧）时，才是页边行的候选；块比印刷行长（说明它是拼出来的）就不是；
  * 反面锁（一条都不许退化）：独立成块的刊头/水印照旧被摘走并逐字交代（R01-037 那一族的全部形状）；
  * 反面锁：正文被摘走时那一段一个字不许少，头部也不许多出任何一句关于它的交代；
  * 单一作者：印刷行这一维由 `_render_page` 自己交回（`edges_out`），
    判据这边不许再切一次行，也不许再写第二个分行器。
"""

from __future__ import annotations

import inspect
import io

import pytest

from dhole_mcp.pdf_extractor import extract_pdf

pytest.importorskip("pypdf")

BODY_ONE = "Body sentence number {i} explaining the chapter plainly."
BODY_TWO = "More body text on page {i} so this page is a real page."
MASTHEAD = "Journal of Test Remote Sensing | Vol. 9, No. 1 | (c) 2019 Test Publisher"
WATERMARK = "Downloaded by 10.0.0.{i} on 2024-0{i}-05 (NOT FOR DISTRIBUTION) [page {i} of 3]"


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


def _chapter(i: int) -> list[tuple[float, float, str]]:
    """现场那一页：17pt 题头 + 两行 11pt 正文，行距 14 —— 后两行会被并成一个块。"""
    return [(800, 17, f"Chapter {i} of the annual review"),
            (740, 11, BODY_ONE.format(i=i)),
            (726, 11, BODY_TWO.format(i=i))]


CHAPTERS = _pdf([_chapter(i) for i in (1, 2, 3)])


def _spaced(i: int) -> list[tuple[float, float, str]]:
    """同一份内容，但页边行**独立成块**（行距拉开到 gap_threshold 之上）—— R01-037 那一族。"""
    return [(800, 9, MASTHEAD),
            (740, 11, f"{i}. Section heading number {i}"),
            (700, 11, BODY_ONE.format(i=i)),
            (660, 11, BODY_TWO.format(i=i)),
            (60, 9, WATERMARK.format(i=i))]


SPACED = _pdf([_spaced(i) for i in (1, 2, 3)])


def _text(res) -> str:
    return "\n".join(res.content)


def _body(md: str) -> str:
    return "\n".join(ln for ln in md.splitlines() if not ln.startswith(">"))


# ─── 1 · 现场那一格：正文不许被当成页脚摘走 ───

def test_a_merged_paragraph_is_not_page_furniture():
    """R02-052 的锁一条不减：F31 的申报句用的是**位置**词（`first/last printed line …`），
    所以这里连 `running header` / `running footer` 两个词都不许出现 —— 两种陈述的词汇
    分开来锁，才是「没删就不许说成删过」与「说了就不许不改口径」同时成立。
    """
    md = _text(extract_pdf(CHAPTERS))
    assert not any(ln.startswith("> running footer") for ln in md.splitlines()), md[:700]
    assert not any(ln.startswith("> running header") for ln in md.splitlines()), md[:700]


def test_the_two_body_sentences_stay_in_the_body_where_they_are():
    md = _body(_text(extract_pdf(CHAPTERS)))
    for i in (1, 2, 3):
        assert BODY_ONE.format(i=i) in md, md[:700]
        assert BODY_TWO.format(i=i) in md, md[:700]


# ─── 2 · 反面锁：独立成块的页边行照旧（R01-037 那一族一条不退化） ───

def test_a_masthead_that_is_its_own_line_is_still_lifted():
    md = _text(extract_pdf(SPACED))
    assert "running header removed from the body" in md, md[:700]
    assert MASTHEAD.split("|")[0] not in _body(md), md[:700]


def test_a_watermark_that_is_its_own_line_is_still_lifted():
    md = _text(extract_pdf(SPACED))
    assert "running footer removed from the body" in md, md[:700]
    assert "NOT FOR DISTRIBUTION" not in _body(md), md[:700]


def test_the_body_of_that_document_is_untouched():
    md = _body(_text(extract_pdf(SPACED)))
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
