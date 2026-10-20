r"""页边那一行重复了却被排版吸进段落时，不许静默：删不掉就要说它留下了。

现场（P02-06 邝野 S2 · R02-010 · P2 · 组 E；R01-037 那条判据没收口的另一半）：

    同一份两页通报：每页第一行刊头、最后一行下载水印
      整份读 → 头部：running **header** removed from the body: "Community Hospital …"（刊头成）
                正文最后一段仍以 `… Not a patient-facing document. Downloaded by ward-clerk on
                2023-11-02 (FOR INTERNAL USE ONLY) [page 1 of 2]` 收尾（页脚不成，且零申报）
      用户的话：「我以为它会删页脚，它没说。」

先说清这把刀**不做**什么，以及为什么（都是量出来的，不是怕）：

1. **不切尾巴**。F28 那一把已经把「从段落首/尾切出那一行」写成了可跑的补丁
   （review/.runtime/p4/f28.patch · 186 行），它在现场那一份上转绿，但同一趟它对
   `Chapter` 那种形状**误切真正文**（R02-052 那一族）。于是先量能不能把两堆分开 ——
   `review/.runtime/p4/f31_measure_edge_lines.py`（仓里现有全部 PDF：4 份 ≥2 页的文件 / 22 条边行）：

       repeating（同侧键在 ≥2 页上出现过）n=14  字号/正文字号 ratio 0.727 – 0.818 – 1.000
       unique   （只在这一页出现）        n=8   ratio 0.687 – 1.250 – 1.455
       离页边距离：repeating 26–680 · unique 46–649

   **两堆完全重叠，没有空带**：现场那份通报的刊头与页脚就是按**正文字号**印的（ratio=1.000），
   而 tests/wrapped_heading.pdf 里一句只出现一次的正文末行反而比正文还小（ratio=0.687）。
   字号与离页边距离都分不开这两堆 → 要切开就只剩"认词"（看到 Downloaded 就当页脚），
   那是 §12 明令不许的形状（换一份出版物就失效，还会删掉正文里合法的短句）。
2. **所以这一把只说出口，而且说的时候不越过硬的那一半**。那句话用的是**位置**与**重复**这两件
   这一支真知道的事（"每页首/末**印着**的这一行重复了、它没有被删、它为什么删不掉"），
   不是"这是页眉/页脚" —— 后者是功能判断，而这一刀的整个前提是分不开。量的另一半是噪声账
   （`review/.runtime/p4/f31_measure_declarations.py`，仓里 12 份 PDF）：真出版物那几份
   （pharmacy-bulletin / week1-handout / author-manuscript / furniture-3p）申报总共只响 **1 次**，
   响的正是现场那一条下载水印；同一句话在合成靶件上会响在模板正文句上，那是数字折叠本来
   就该认出的重复，而且它一个字节都不删。

契约（本文件钉住）：
  * 一条**印出来的**页边行（每页首/末行，渲染器自己交回的那份）在同侧跨 ≥2 页重复、
    而它落在一个比它长的段落里（= 被合并吞掉，这是能证明的那一格）时，`content` 头部交出
    一句申报：它重复了、它留下了、为什么删不掉（字号与页边距都分不开），以及读者能做什么；
  * **申报不删任何东西**：那一段正文（连同它自己的句子与数字）逐字留在交付里；
  * **申报不许冒充摘除的词汇**：句子里不出现 `running header` / `running footer`，
    不写 irrelevant / off-topic，不数"错"，不说"这一页有问题"；
  * **反面锁一**：独立成块的页边行照旧走"摘走 + 逐字引用"那条路，不许退化成这句申报；
  * **反面锁二**：没有重复就没有这句话（11/16 那一族不付费，与 R02-002/F30 同一条纪律）；
  * **反面锁三**：按字号认出来的标题（`#` 那一族）**从不**被这句话点名 —— 渲染器根本不把
    它记成页边行，这条保护与第四道闸同一个来源；整页只有这一行的那一格也不申报
    （那不是"被段落吸住"，是"摘完就空"那一族，别的句子已经在管它）；
  * 窗口照旧只缩窄交付：窗口外的页**只加证据**，那句话要说清证据来自哪几页、这里交付哪几页
    （与摘除那一支同一串尾巴，同一个作者 `_edge_note_suffix`）；
  * 作者唯一：跨页那一判还是 `_furniture_key`，页边那一判还是渲染器交回的首/末印现行，
    本刀不新建第三把尺。
"""

from __future__ import annotations

import inspect
import io

import pytest

from dhole_mcp.pdf_extractor import extract_pdf

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
