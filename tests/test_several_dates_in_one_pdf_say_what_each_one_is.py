"""R01-048 · 一份文件里有好几个日期，就必须说清每个日期是什么的日期。

现场（P08 谢临 S2，`sessions/P08-s2-pdf-p23.json`；report-persona-08.md 改进清单第 4 条）：
他数了一遍这一份文件里的日期 —— **元数据 2026、页脚水印 2024、刊头 (c) 2023、正文自述的
数据窗 2021-03 与 2022-09** —— 而回执给他的只有 `> Date: 2026-10-14` 一行：

  「你四个都给我了，你为什么不告诉我哪个才是页面正文自述的日期？」

这一条最容易修坏的方式是"挑一个"：把一个 PDF 的 CreationDate 当文献年份印出去，就是
把"这台机器上什么时候导出的 PDF"说成"这篇论文哪年发表"。画像自己也确认现版本没撒这个谎
（"元数据也没假装知道文献年份"是加分项），所以本刀**不选权威日期**，做的是另外两件事：

① 文件自己那两枚戳要各说各的身份（CreationDate / ModDate 都上线并标明来自 info 字典，
   不再共用一个 `Date:`）；
② 交回的那几页里**印着的**日期按形状扫出来（ISO 全日期 > 年-月 > 四_digit 年），
   按精确度排序、点名出现在哪几页，并明说这些都不等于文献的发表年。

判据仍是形状，不认刊名也不认 "Copyright" 一类词。候选数的是**这一页印了什么**，不是
它最后住在信封的哪一格：被本刀前一把（R01-037）认成页眉页脚而摘到头部去逐字存档的那两行，
日期同样要数到 —— 否则"页面上明明有 2024"这件事会因为搬家而消失。
"""

from __future__ import annotations

import io
import re

import pytest

from dhole_mcp.pdf_extractor import extract_pdf

pytest.importorskip("pypdf")

CREATED = "D:20261014010101+08'00'"
MODIFIED = "D:20261014010202+08'00'"


def _pdf(pages: list[list[tuple[float, float, str]]], *, info: dict | None = None) -> bytes:
    """真 PDF 字节 + 可点名的 info 字典（每行给 y/size/text，y 越大越靠页顶）。"""
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
    if info:
        writer.add_metadata(info)
    bio = io.BytesIO()
    writer.write(bio)
    return bio.getvalue()


def _journal_page(i: int) -> list[tuple[float, float, str]]:
    return [
        (800, 9, "Journal of Test Remote Sensing | Vol. 9 | (c) 2023 Test Publisher"),
        (740, 11, f"{i + 1}. Section heading on page {i}"),
        (726, 11, "Two Sentinel-2 tiles were pulled between 2021-03 and 2022-09."),
        (60, 9, f"Downloaded by 10.0.0.{i} on 2024-01-0{i} (NOT FOR DISTRIBUTION)"),
    ]


@pytest.fixture(scope="module")
def journal() -> bytes:
    return _pdf([_journal_page(i) for i in (1, 2, 3)],
                info={"/CreationDate": CREATED, "/ModDate": MODIFIED,
                      "/Producer": "test builder"})


def _head(md: str) -> str:
    return md.split("--- Page 1 ---")[0]


def _date_line(md: str) -> str:
    """头部那一行候选日期（不存在时交回空串，断言就照着空串报）。"""
    for row in md.splitlines():
        if "dates readable in the returned text" in row:
            return row
    return ""


# ─── 1 · 文件自己那两枚戳 ───────────────────────────────────────

def test_the_two_file_stamps_say_which_stamp_each_is(journal):
    """CreationDate 与 ModDate 不再共用一个 `Date:` —— 那一个词承载不了两种意思。"""
    head = _head(extract_pdf(journal).content[0])
    assert "Created: 2026-10-14" in head, head
    assert "Modified: 2026-10-14" in head, head
    assert "Date:" not in head, head          # 裸的 `Date:` 这个词不再出现在头部


def test_the_file_stamp_is_labeled_as_a_stamp_not_as_the_work(journal):
    """P08 表扬的就是这一格没有撒谎：文件戳不是文献年份，这一句必须自己说出来。"""
    head = _head(extract_pdf(journal).content[0])
    assert "the file's own stamps" in head, head
    assert "not when the work was written" in head, head


# ─── 2 · 页面里印着的日期 ───────────────────────────────────────

def test_dates_printed_in_the_pages_are_listed_with_their_pages(journal):
    head = _head(extract_pdf(journal).content[0])
    assert "dates readable in the returned text" in head, head
    assert "2024-01-01" in head, head
    assert "2021-03" in head and "2022-09" in head, head
    assert "2023" in head, head


def test_the_more_specific_shape_is_ordered_first(journal):
    """排序按"这个形状能表达多精确"，不按数值大小：全日期在年月之前，年月在裸年在前面。"""
    head = _head(extract_pdf(journal).content[0])
    line = _date_line(head)
    order = [line.index(d) for d in ("2024-01-01", "2021-03", "2023")]
    assert order == sorted(order), line


def test_a_full_date_is_not_also_counted_as_its_own_year(journal):
    """`2024-01-01` 不该另外产出候选 `2024`：一个位置只归最长的那个形状。"""
    head = _head(extract_pdf(journal).content[0])
    line = _date_line(head)
    tokens = re.findall(r"(?<![\d-])(\d{4}(?:-\d{2}(?:-\d{2})?)?)(?![\d-])", line)
    assert "2024-01-01" in tokens, tokens
    assert "2024" not in tokens, tokens
    assert "2024-01" not in tokens, tokens


def test_none_of_them_is_claimed_as_the_publication_year(journal):
    head = _head(extract_pdf(journal).content[0])
    assert "no date here is a stated publication date" in head, head
    assert "Published:" not in head, head


def test_a_document_without_dates_pays_no_such_line():
    """没有候选就没有这句话：一句关于没发生过的事不占字节。"""
    pdf = _pdf([[(740, 11, "A page of prose with no year-shaped number at all.")]])
    md = extract_pdf(pdf).content[0]
    assert "dates readable in the returned text" not in md, md[:400]


def test_a_long_reference_list_is_counted_not_listed():
    """裸年超过一屏就报数不报清单：参考列表里那二十个年份不是"这份文件的日期"。"""
    years = " ".join(str(1990 + i) for i in range(12))
    pdf = _pdf([[(740, 11, f"References {years}")],
                [(740, 11, f"More references {years}")]])
    md = extract_pdf(pdf).content[0]
    line = _date_line(md)
    assert line, md[:400]
    assert "and " in line and "more year-shaped number(s)" in line, line


# ─── 3 · 手册 ───────────────────────────────────────────────────

def test_the_manual_names_both_date_lines_where_an_agent_will_look():
    from dhole_mcp import guide

    corpus = "\n".join(p.read_text(encoding="utf-8") for p in guide._ROOT.rglob("*.md"))
    assert "the file's own stamps" in corpus, corpus[:200]
    assert "dates readable in the returned text" in corpus
