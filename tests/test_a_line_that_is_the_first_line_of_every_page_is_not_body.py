"""R01-037 · 每页都重复的那一行不是正文（P08 谢临 S2 的头号火气）。

现场（`sessions/P08-s2-pdf-p23.json`，`doc_parse(pages="2-3")`，4 页学术期刊样本）：
抽出来的正文里，**每页第一行是刊头** `Journal of Applied Remote Sensing | Vol. 14,
No. 2 | (c) 2023 Publisher`，**每页最后一行是下载水印** `Downloaded by 94.21.7.6 on
2024-01-05 (NOT FOR DISTRIBUTION) [page 2 of 4]` —— 与正文同权重、无标注。画像卡片明写
他最怕的就是这一条：「看起来抽到了，其实抽的是页眉」。火气 #1。

代价不是"难看"：这一族用户把正文复制出去（翻译、引用、排进稿子），页眉页脚每页都要
手删一次；实测这一发交出的 1,044 个正文字符里有 **290 个**是出版社的版权行与 IP 水印
（两页各 71 + 74），占正文的 27.8%。

修法只认**形状**，不认内容（§12：不许为某刊名、某水印词开特例）：
① 它是本页第一段或最后一段，② 它是一行、且短（≤120 字符），③ 同一个规范化后的行
（数字折成 `#`，于是 `[page 2 of 4]` 与 `[page 3 of 4]` 是同一颗）在 ≥2 页的**同一侧**
出现。三条同时成立才叫"页边重复行"，从正文里摘掉，并在文档头部**逐字**交代它是什么、
在哪一侧、摘自哪几页 —— 摘走的是位置，一个字都不许静默消失。

不做的四件事也都各有理由：整份只有一页时不做（没有第二页可比就不许猜 —— 注意这一条在
R02-023 之后**不再等于「按页取时不做」**：`pages=` 缩窄的是交付，整份文件在产品手里，
窗口外那些页的首末行照样当证据，见 `test_a_page_window_still_uses_the_whole_documents_evidence.py`）；
页面中间的重复行
不算（那是结构，不是页边）；长行不算（一段话在每页开头出现是极小的概率，误伤代价大于
收益）；页边行是本页唯一内容时不摘（宁可留噪声，也不交出一页空白）。第四条管的是数字
折叠自己的副作用 —— 折掉数字之后「Chapter 1」与「Chapter 2」也成了同一颗，那种每页换
一个号的编号标题是正文：渲染器按字号认出来、写成 `#` 标题的那些行不参与。
"""

from __future__ import annotations

import io
import re

import pytest

from dhole_mcp.pdf_extractor import extract_pdf

pytest.importorskip("pypdf")

HEADER = ("Journal of Test Remote Sensing | Vol. 9, No. 1 | (c) 2019 Test Publisher")
FOOTER_T = ("Downloaded by 10.0.0.{i} on 2024-0{i}-05 (NOT FOR DISTRIBUTION) "
            "[page {i} of 3]")


def _pdf(pages: list[list[tuple[float, float, str]]]) -> bytes:
    """真 PDF 字节：每页给 (y, size, text) 三件事，y 越大越靠页面顶部。

    页边行放在 y=800 / y=60，正文落在 740-680 —— 与真期刊的版面节奏一致，
    这样"页边"这一维是几何给的，不是我拿文本猜的。
    """
    from pypdf import PdfWriter
    from pypdf.generic import (DecodedStreamObject, DictionaryObject, NameObject)

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


def _page(i: int, *, header: str = HEADER, footer: str | None = None,
          middle: str | None = None) -> list[tuple[float, float, str]]:
    items = []
    if header is not None:
        items.append((800, 9, header))
    items += [(740, 11, f"{i}. Section heading number {i}"),
              (726, 11, f"Body line one for page {i} explaining the method plainly."),
              (712, 11, f"Body line two for page {i} with enough letters to matter.")]
    if middle is not None:
        items.append((700, 11, middle))
    items.append((680, 11, f"Second paragraph on page {i}: the numbers are in Table {i}."))
    items.append((60, 9, footer if footer is not None else FOOTER_T.format(i=i)))
    return items


@pytest.fixture(scope="module")
def journal() -> bytes:
    return _pdf([_page(i) for i in (1, 2, 3)])


def _pages_of(md: str) -> dict[int, str]:
    """把 `--- Page N ---` 那一层拆回来，断言才说得出"哪一页"。"""
    out: dict[int, str] = {}
    for part in re.split(r"(?=^--- Page \d+ ---$)", md, flags=re.M):
        m = re.match(r"^--- Page (\d+) ---$", part.strip(), flags=re.M)
        if m:
            out[int(m.group(1))] = part
    return out


# ─── 1 · P08 撞的那一格 ─────────────────────────────────────────

def test_a_line_that_is_the_first_line_of_every_page_is_not_body(journal):
    res = extract_pdf(journal)
    assert res.error == "", res.error
    for n, block in _pages_of(res.content[0]).items():
        assert HEADER not in block, f"第 {n} 页仍把刊头当正文交付"
        assert "Downloaded by" not in block, f"第 {n} 页仍把下载水印当正文交付"


def test_the_removed_line_is_quoted_verbatim_once_for_the_document(journal):
    """摘走位置不摘走信息：它必须逐字活着，并说清是从哪一侧、哪几页摘的。"""
    head = extract_pdf(journal).content[0]
    assert f'> running header removed from the body: "{HEADER}"' in head, head[:400]
    assert "first line of pages 1-3" in head, head[:400]
    assert '> running footer removed from the body: "Downloaded by 10.0.0.1' in head, head[:400]
    assert "last line of pages 1-3" in head, head[:400]


def test_a_footer_whose_only_difference_is_a_page_number_is_one_line(journal):
    """`[page 1 of 3]` 与 `[page 2 of 3]` 是同一颗页边行：数字折成 #，其余逐字比。"""
    head = extract_pdf(journal).content[0]
    assert "numbers vary page to page" in head, head[:400]
    # 摘走的必须是那一条：留下的代表是第一页那一条，逐字可查
    assert "[page 1 of 3]" in head, head[:400]


def test_the_body_that_is_unique_stays_whole(journal):
    """只摘页边重复行：正文的每一段一个字都不许跟着少。"""
    res = extract_pdf(journal)
    blocks = _pages_of(res.content[0])
    for n in (1, 2, 3):
        assert f"{n}. Section heading number {n}" in blocks[n], blocks[n]
        assert f"Body line one for page {n}" in blocks[n], blocks[n]
        assert f"Body line two for page {n}" in blocks[n], blocks[n]
        assert f"Second paragraph on page {n}" in blocks[n], blocks[n]


def test_the_page_markers_still_carry_what_the_user_said_was_useful(journal):
    """P08 明写的加分项不许被这一刀弄坏：页界与页数照旧在。"""
    md = extract_pdf(journal).content[0]
    assert "--- Page 1 ---" in md and "--- Page 3 ---" in md
    assert "> PDF · 3 pages" in md, md[:300]


# ─── 2 · 判据的反面：形状不够就不许claim ────────────────────────

def test_a_one_page_window_uses_the_pages_it_did_not_deliver_as_evidence(journal):
    """R02-023 改的就是这一格：旧契约「只抽一页时无从比较」的前提是「产品手里只有这一页」。

    它不是 —— `pages=` 只缩窄交付，整份文件在手里。守住的那一半照守：**不许猜**，
    证据必须是另一页上真印着的同一颗行（下面那条单页文档的锁就是这一半）。
    """
    res = extract_pdf(journal, pages="1")
    block = _pages_of(res.content[0])[1]
    assert HEADER not in block, "窗口外的页是真证据：这一行在每一页都印着"
    notes = [ln for ln in res.content[0].splitlines() if "running header removed" in ln]
    assert len(notes) == 1, res.content[0][:400]
    assert "outside this window" in notes[0], notes[0]
    assert "pages 1-3" in notes[0], notes[0]      # 证据来自哪几页，说得出
    assert "Body line one for page 1" in block, block  # 正文一个字没跟着少


def test_a_one_page_document_claims_no_furniture():
    """整份只有一页时仍然一个字都不摘、一句都不 claim —— 没有第二页可比就不许猜。"""
    solo = _pdf([_page(1)])
    res = extract_pdf(solo)
    assert HEADER in res.content[0], res.content[0][:400]
    assert "running header" not in res.content[0], res.content[0][:400]


def test_a_repeated_line_in_the_middle_of_the_page_stays():
    """页面中间的重复行是结构不是页边：位置这一维必须参与判据。"""
    mid = "Confidential - reading copy only"
    pdf = _pdf([_page(i, middle=mid) for i in (1, 2, 3)])
    md = extract_pdf(pdf).content[0]
    assert md.count(mid) == 3, md                       # 三页都还留着它
    assert f'"{mid}"' not in md, md[:400]               # 也没有哪句旁注把它当成页边行
    assert "first line of pages 1-3" in md, md[:400]    # 真页边行照摘


def test_a_long_repeated_first_line_is_left_alone():
    """短这一维也要参与：超过 120 字符的重复行留在正文里，不摘。"""
    long_hdr = "Abstract - " + ("this sentence is deliberately padded so the guard on "
                  "line length is what decides here and nothing else ") * 2
    pdf = _pdf([_page(i, header=long_hdr) for i in (1, 2, 3)])
    md = extract_pdf(pdf).content[0]
    assert "running header" not in md, md[:400]
    assert md.count("Abstract -") == 3, md[:400]


def test_a_page_whose_only_text_is_the_repeated_line_is_not_gutted():
    """宁可留噪声：摘完就空的那一页不参与，其余页照摘，页码那一段也就跟着点名。"""
    pdf = _pdf([[(800, 9, HEADER), (740, 11, f"Unique body sentence number {i}." * 4),
                 (726, 11, f"Another body sentence on page {i} to keep this page real.")]
                if i != 2 else [(800, 9, HEADER)] for i in (1, 2, 3)])
    md = extract_pdf(pdf).content[0]
    blocks = _pages_of(md)
    assert HEADER in blocks[2], "第 2 页唯一的文字就是那一行：摘走交出去的是空白"
    assert HEADER not in blocks[1] and HEADER not in blocks[3], md[:400]
    assert "first line of pages 1, 3" in md, md[:400]


def test_a_numbered_heading_at_the_top_of_every_page_stays():
    """数字折叠会把「Chapter 1 / Chapter 2」折成同一颗 —— 每页换一个号的编号标题是正文。

    挡它的是第四道形状闸：渲染器按字号认出来的标题在 markdown 里带 `#`，带 `#` 的行不是
    页边行。这里 17pt 的题头 vs 11pt 的正文，正是那一道闸负责的那一格。
    """
    pdf = _pdf([[(800, 17, f"Chapter {i} of the annual review"),
                 (740, 11, f"Body sentence number {i} explaining the chapter plainly."),
                 (726, 11, f"More body text on page {i} so this page is a real page.")]
                for i in (1, 2, 3)])
    md = extract_pdf(pdf).content[0]
    assert "running header" not in md, md[:400]
    for i in (1, 2, 3):
        assert f"Chapter {i} of the annual review" in md, md[:400]


def test_a_document_without_repetition_pays_no_extra_line():
    """没有重复就没有这句话：一句关于没发生过的事不占字节。

    这里刻意让每页的页边行**换字**而不是换数字 —— 换数字的那一种是被折叠认成同一颗的
    （见 `test_a_footer_whose_only_difference_is_a_page_number_is_one_line`），
    那一格不是"没重复"，是"重复但带页码"。
    """
    pdf = _pdf([_page(i, header=f"Section {chr(96 + i)} notes for the annual review",
                      footer=f"Copied by {['alice', 'bob', 'carol'][i - 1]} for the desk")
                 for i in (1, 2, 3)])
    md = extract_pdf(pdf).content[0]
    assert "running header" not in md and "running footer" not in md, md[:400]
    for i in (1, 2, 3):
        assert f"Section {chr(96 + i)} notes" in md, md[:400]
        assert ["Copied by alice", "Copied by bob", "Copied by carol"][i - 1] in md


# ─── 3 · 连带：这一刀不许动别的东西 ─────────────────────────────

def test_quality_and_page_count_are_scored_on_what_the_page_actually_said(journal):
    """账要算在摘除之前：质量分与页数不许因为少了两行就改口。"""
    before = extract_pdf(journal)
    assert before.pages_total == 3 and before.pages_extracted == [1, 2, 3]
    assert before.quality_score == pytest.approx(1.0), before.quality_score
    assert before.content_ok is True


def test_the_manual_names_the_stripping_where_an_agent_will_look():
    from dhole_mcp import guide

    corpus = "\n".join(p.read_text(encoding="utf-8") for p in guide._ROOT.rglob("*.md"))
    assert "running header" in corpus, (
        "正文被摘走两行而手册没写：只读手册的 agent 会以为 PDF 抽出来少了一段")
    # R02-010 的第二句也要在册：手册不许只写「会摘」而漏掉「有一种摘不掉、改为申报」——
    # 只读手册的人看见正文里留着一行水印时，得知道那是产品明说过的、不是漏网。
    assert "but stays in the body" in corpus, (
        "申报那一句上了线却没进手册：读者会以为那行是没被认出来的漏网噪声")
