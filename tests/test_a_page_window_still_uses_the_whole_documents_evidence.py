r"""页窗口只缩窄交付，不缩窄证据：整份文件在产品手里，跨页那一判就该用整份。

现场（P02-06 邝野 S5 的回访残留面 · R02-023 · P2 · 组 E；第 01 轮 R01-037 立的那条判据的另一半）：

    doc_parse(同一份两页 PDF)     → 头部：running footer removed from the body:
                                     "Cross-Cultural Comm 2023 internal handout - do not
                                     distribute - page 1" (last line of pages 1-2)
    doc_parse(同一份, pages="2")  → 同一行**原样留在正文最后一行**，头部一句申报都没有

判据的定义域是「同一颗行在 ≥2 页的同一侧出现」—— 单页窗口里天然没有第二页可比，这不是判据坏了。
**坏的是不申报**：那一封明明写着 `PDF · page 2 of 2`，产品知道窗口外还有页，也知道整份文件在手里
（`pages=` 只缩窄抽取范围，不需要任何没给过它的信息）。用户按页取正是因为她只想要那一页 ——
而 R01-037 立这条的动机（第 01 轮 B-19 记的也是同一族）是「抄正文的人每翻一页手删一次」：
按页取的那一路把那一行还给她，等于动机在最常用的那一支上没兑现。

契约（本文件钉住）：
  * **证据用整份**：窗口只有一页时，页边行的比较照旧跨全文件做（`pages=` 缩窄的是交付，不是判据的输入），
    摘除只作用在窗口内的页面上；交代那一行去向的那句话必须说得出**证据来自哪几页**，也要说得出
    本窗口是第几页；
  * **反面锁一**：那一行只在窗口这一页出现、整份里没有第二页与之同侧 → 一个字都不动，
    也不许多出任何一句申报（不 claim 没发生过的事）；
  * **反面锁二**：整份读取（不给 `pages=`）那一路的交代一句话都不改形 —— 本刀不动它，
    「窗口」那一维只在窗口真的存在时才出现；
  * **反面锁三**：既有的四道闸一条不许松（本页至少两段、单行、≤120 字符、`#` 标题不参与）——
    窗口页只剩一段时照旧不摘，宁留噪声也不交出一页空白；
  * **不重画整份**：窗口外的页只为「页边那一行」而读（取首末两行），不许把 300 页的文档重新渲染一遍
    来回答一个单页请求 —— 钉成性能锁；
  * 比较键的作者唯一：跨页那一判仍走既有的 `_furniture_key`，新读者不许自己造第二把规范化尺。
"""

from __future__ import annotations

import inspect
import io

import pytest

from dhole_mcp.pdf_extractor import extract_pdf

pytest.importorskip("pypdf")

HEADER = ("Journal of Test Remote Sensing | Vol. 9, No. 1 | (c) 2019 Test Publisher")
FOOTER_T = ("Downloaded by 10.0.0.{i} on 2024-0{i}-05 (NOT FOR DISTRIBUTION) "
            "[page {i} of 3]")


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


THREE_PAGES = _pdf([_page(1, footer=FOOTER_T.format(i=1)),
                    _page(2, footer=FOOTER_T.format(i=2)),
                    _page(3, footer=FOOTER_T.format(i=3))])


def _text(res) -> str:
    return "\n".join(res.content)


def _body(res) -> str:
    """窗口那一页的正文（头部那些 `>` 行不算 —— 它们正是本刀要检查的去向交代）。"""
    whole = _text(res)
    marker = whole.find("--- Page ")
    return whole[marker:] if marker >= 0 else whole


# ─── 1 · 窗口里的正文：证据来自整份 ───

def test_a_single_page_window_loses_its_running_furniture_too():
    res = extract_pdf(THREE_PAGES, pages="2")
    body = _body(res)
    assert "NOT FOR DISTRIBUTION" not in body, body
    assert "Journal of Test Remote Sensing" not in body, body
    assert "Body paragraph on page 2" in body, body


def test_the_note_names_the_evidence_pages_and_the_window():
    res = extract_pdf(THREE_PAGES, pages="2")
    notes = [ln for ln in _text(res).splitlines() if "running footer removed" in ln]
    assert len(notes) == 1, _text(res)[:600]
    n = notes[0]
    assert "pages 1-3" in n, n                        # 证据来自哪几页
    assert "window" in n.lower() and "page 2" in n, n  # 交付的只是这一页，要说得出


def test_the_header_of_the_same_window_is_accounted_for_too():
    res = extract_pdf(THREE_PAGES, pages="2")
    assert any("running header removed" in ln for ln in _text(res).splitlines()), \
        _text(res)[:600]


# ─── 2 · 反面锁 ───

def test_a_line_that_only_exists_on_the_window_page_is_left_alone():
    """整份里没有第二页与它同侧 → 那是一行正文，不是页边行，也不许多出一句申报。"""
    odd = _pdf([_page(1, header=None, footer=FOOTER_T.format(i=1)),
                _page(2, header=None, footer=FOOTER_T.format(i=2),
                      middle="A short line only on page two"),
                _page(3, header=None, footer=FOOTER_T.format(i=3))])
    res = extract_pdf(odd, pages="2")
    assert "A short line only on page two" in _body(res), _text(res)[:600]
    assert not any("only on page two" in ln for ln in _text(res).splitlines()
                   if ln.startswith(">")), _text(res)[:600]


def test_the_whole_document_note_keeps_its_old_shape():
    """整份读取那一路一字不改形：「窗口」那一维只在窗口真的存在时才出现。"""
    res = extract_pdf(THREE_PAGES)
    notes = [ln for ln in _text(res).splitlines() if "running footer removed" in ln]
    assert len(notes) == 1, _text(res)[:600]
    assert "pages 1-3" in notes[0], notes[0]
    assert "window" not in notes[0].lower(), notes[0]


def test_the_at_least_two_blocks_gate_still_holds_inside_a_window():
    """窗口页只剩两段时页边行照旧不摘 —— 宁留噪声，也不交出一页空白。"""
    thin = _pdf([_page(1, header=None, footer=FOOTER_T.format(i=1)),
                 [(800, 9, FOOTER_T.format(i=2)), (60, 9, "Last short line of the thin page")],
                 _page(3, header=None, footer=FOOTER_T.format(i=3))])
    res = extract_pdf(thin, pages="2")
    assert "Last short line of the thin page" in _body(res), _text(res)[:600]


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
