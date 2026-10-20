"""R01-049 · 小节号不许和它领的那句话粘成一句（P08 谢临 火气 #6）

现场（`sessions/P08-s2-pdf-p23.json`）：交回的正文里每一小节都是这个样子 ——

```
2. Data Two Sentinel-2 tiles T49SBU and T49SCU were downloaded from the …
3. Methods We evaluate k-means, DBSCAN and a conditional random field. …
```

那两行在 PDF 里是**两行**：`2. Data` 是标题，后面才是句子。渲染器把它们并成了一段，
于是编号与句子粘在一起，markdown 里既没有 `#` 也没有换行，用户"要重排"。

根因在段落合并只看行距（`gap_threshold = max(8, median_lh*1.5)`）：这份材料每行行距
都是 14pt，标题行与正文行之间没有任何几何差别，而标题也没有字号差（11pt 对 11pt），
所以 `_heading_level` 认它不出来 —— 它既不是大字也不是粗体，只是**一行短的编号**。

修法只加一条形状判据，不加词汇表：一行同时满足
①以一个编号开头（`1.` / `2. Data` / `1.2.4 Methods` 这一类 1-2 位的编号，四位的年份不算）、
②整行很短（≤60 字符，句子不会这么短）、
③行末没有句子标点（`..;:!` 都不算标题的收尾）
就让它单独成段 —— 前后都断开。不升成 `#`：我们只知道"这是一行独立的编号标题"，
不知道它是几级；把它写进章节地图（`table_of_contents`）同样是过度声称，测试里钉住不许。

反面都比判据本身重要，所以三条闸各自配一个用例：四位年份起头不算编号（形状闸）、
行末带句号不算编号（标点闸）、超过 60 字符不算编号（长度闸）——
每个用例只关掉一维，另两维仍满足，这样红起来才知道是哪一维没守住。
"""

from __future__ import annotations

import io

import pytest

from dhole_mcp.pdf_extractor import extract_pdf

pytest.importorskip("pypdf")


def _pdf(pages: list[list[str]], size: float = 11) -> bytes:
    """真 PDF 字节：每页给一组行，逐行 14pt 行距、一律同一个字号。

    刻意不给标题任何几何优待（同字号、同样距、无空行）：如果靠行距或字号就能分开，
    这一刀要修的东西就没有被测到。
    """
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    def esc(s: str) -> str:
        return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica")}))
    for lines in pages:
        page = writer.add_blank_page(width=595, height=842)
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        y = 780
        ops = ["BT"]
        for text in lines:
            ops.append(f"/F1 {size} Tf 1 0 0 1 60 {y} Tm ({esc(text)}) Tj")
            y -= 14
        ops.append("ET")
        stream = DecodedStreamObject()
        stream.set_data("\n".join(ops).encode("latin-1"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    bio = io.BytesIO()
    writer.write(bio)
    return bio.getvalue()


HEADINGS = ("1. Introduction", "2. Data", "3. Methods", "1.2.4 Sub-heading depth")


@pytest.fixture(scope="module")
def manuscript() -> bytes:
    return _pdf([
        ["1. Introduction",
         "Parcel boundary extraction remains one of the least solved problems",
         "in agricultural remote sensing today.",
         "2. Data",
         "Two Sentinel-2 tiles T49SBU and T49SCU were downloaded from the Copernicus",
         "data space between 2021-03 and 2022-09. Cloud masking used s2cloudless."],
        ["3. Methods",
         "We evaluate k-means, DBSCAN and a conditional random field on the",
         "same NDVI stack; the parameters are in Table 1.",
         "1.2.4 Sub-heading depth",
         "Ground truth was drawn by two technicians and the agreement was",
         "only 0.78 (Cohen kappa)."],
    ])


def _blocks(md: str) -> list[str]:
    """页 markdown 按空行切块（跳过 `---` 与头部那几行 `>`）。"""
    return [b.strip() for b in md.split("\n\n")
            if b.strip() and not b.strip().startswith(("---", ">"))]


# ─── 1 · 粘住的那一格 ───────────────────────────────────────────

def test_a_short_numbered_line_is_not_glued_to_the_sentence_it_heads(manuscript):
    blocks = _blocks(extract_pdf(manuscript).content[0])
    for heading in HEADINGS:
        assert heading in blocks, (heading, blocks)
        # 粘住的形状是"标题 + 一个空格 + 句子"同处一块；单独成块才算拆开
        assert not [b for b in blocks
                    if b.startswith(heading + " ") and len(b) > len(heading)], blocks


def test_the_prose_under_a_heading_stays_one_paragraph(manuscript):
    """拆开的是标题，不是正文：两行句子仍然并成一段。"""
    blocks = _blocks(extract_pdf(manuscript).content[0])
    prose = [b for b in blocks if b.startswith("Two Sentinel-2 tiles")]
    assert len(prose) == 1, blocks
    assert "Copernicus data space" in prose[0], prose


def test_the_split_does_not_claim_a_heading_level(manuscript):
    """我们知道它是一行独立编号，不知道它是几级 —— 不许顺手写成 `#`，也不许进章节地图。"""
    res = extract_pdf(manuscript)
    md = res.content[0]
    for heading in ("Introduction", "Data", "Methods", "Sub-heading depth"):
        assert f"# {heading}" not in md, md[:400]
    assert [t.get("title") for t in res.table_of_contents] == [], res.table_of_contents


# ─── 2 · 反面：三条闸各自挡一格 ─────────────────────────────────

def test_a_four_digit_year_opening_a_wrapped_line_is_not_a_number():
    """形状闸单独守：续行以 `1999 …` 起头（四位、无点号）不算编号，照常并段。"""
    blocks = _blocks(extract_pdf(_pdf([
        ["The survey was designed in",
         "1999 and the fields were revisited every season after that",
         "until the cooperative dissolved."]])).content[0])
    assert len(blocks) == 1, blocks
    assert "designed in 1999" in blocks[0], blocks


def test_a_short_numbered_line_that_ends_with_a_period_stays_a_sentence():
    """标点闸单独守：这一行既短又带编号，只有行末的句号挡着它 —— 不许切成标题。"""
    blocks = _blocks(extract_pdf(_pdf([
        ["3. See Table 1.",
         "The committee then approved the budget without amendment."]])).content[0])
    assert len(blocks) == 1, blocks
    assert "See Table 1." in blocks[0] and "The committee" in blocks[0], blocks


def test_a_numbered_line_longer_than_any_heading_would_be_stays_in_the_paragraph():
    """长度闸单独守：编号对、行末无标点，唯独长度超限 —— 不许切。"""
    long_title = ("7. Because this line is deliberately far longer than any heading "
                  "would ever be")
    blocks = _blocks(extract_pdf(_pdf([
        [long_title,
         "and the next line continues the same thought in prose form."]])).content[0])
    assert not any(b.strip() == long_title for b in blocks), blocks
    assert any("prose form" in b for b in blocks), blocks
