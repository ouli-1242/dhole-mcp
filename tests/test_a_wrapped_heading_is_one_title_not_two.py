"""一行被页面折断的标题是一个标题，不是两条（P2）。

夹具与来路（CONTRIBUTING: never hand-write a fixture —— 这里要钉的恰恰是"换行真的发生
在哪里"，手写的 PDF 只会画出我以为的换行）：`tests/wrapped_heading.html` 是入库的源，
`tests/wrapped_heading.pdf` 是它经真 Chrome 打印的结果，命令两处都写着：

    "C:/Program Files/Google/Chrome/Application/chrome.exe" \
      --headless=new --disable-gpu --no-pdf-header-footer \
      --print-to-pdf="D:/tools/dhole-mcp/tests/wrapped_heading.pdf" \
      "file:///D:/tools/dhole-mcp/tests/wrapped_heading.html"

改前在同一份 PDF 上实测的三种坏法（都记在这里，因为它们是判据的来源，不是故事）：

1. 封面大字两行、两行都过字号闸 → 交回**两条 level-1**：`Quarterly Revenue Review`
   与 `for the Fiscal Year 2026`，后者还替自己算出一个覆盖到末页的 `end_page`，也就是一份
   不存在的章节。体验测试里那个 45 页文件"只有 2 条 TOC、两条都是标题"就是这个形状。
2. 第二行以 `.` 收尾 → `_SENTENCE_END` 那道闸把它降级成正文，标题只剩半截
   （`## Divisional Results Discussed`），尾串 `In This Review.` 变成下一段的开头。
3. 窄栏里一个标题被浏览器折成四行 → 交回**四条 level-2**
   （`Revenue Review` / `By Division For` / `The Fiscal Year` / `2026`），一条 TOC 就此
   多了三个入口。

闸本身是对的：一句完整的句子不是标题。坏的是它被**逐行独立**地应用 —— 一行是不是标题，
取决于它前面那行是什么。所以修在几何层：同一众数字号、同一粗细、垂直间距不超过一行、
中间没插进表格的行，折回同一行印刷标题再说它是几行（`_fold_heading_lines`）。
标题一旦收句（尾字符落在 `_SENTENCE_END`）就停止折叠，长度帽 `_HEADING_MAX_LEN` 兜住失控。

这里同时钉住**反面**：正文行不许被折进标题（尺寸判据一旦被摘掉，标题就会吞掉紧跟的那段
话），以及被一段正文隔开的同级标题不许并成一条。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from dhole_mcp.pdf_extractor import (
    _fold_heading_lines,
    extract_pdf,
)

HERE = Path(__file__).resolve().parent
PDF = HERE / "wrapped_heading.pdf"


@pytest.fixture(scope="module")
def res():
    return extract_pdf(PDF.read_bytes())


@pytest.fixture(scope="module")
def markdown(res):
    return "\n".join(res.content)


def _heading_lines(md: str) -> list[str]:
    return [ln for ln in md.splitlines() if re.match(r"^#{1,6} ", ln)]


class TestOnePrintedTitleIsOneHeading:

    def test_the_cover_title_comes_back_as_one_line(self, markdown, res):
        assert "# Quarterly Revenue Review for the Fiscal Year 2026" in markdown
        # 半截的那两条都不许再以标题身份出现
        assert not any(ln.startswith("# for the Fiscal Year")
                       for ln in _heading_lines(markdown)), markdown
        assert sum(1 for e in res.table_of_contents
                   if e["title"].startswith("Quarterly Revenue Review")) == 1

    def test_a_title_whose_tail_ends_with_a_period_is_not_cut_in_half(self, markdown):
        """尾行带 `.` 的那一种：从前半截是标题，尾串被当成下一段的开头。"""
        assert "## Divisional Results Discussed In This Review." in markdown
        assert "## Divisional Results Discussed\n" not in markdown
        assert not re.search(r"^In This Review\.", markdown, re.MULTILINE), markdown

    def test_a_narrow_wrap_is_one_section_not_four(self, markdown):
        assert "## Revenue Review By Division For The Fiscal Year 2026" in markdown
        for fragment in ("## By Division For", "## The Fiscal Year", "## 2026"):
            assert fragment not in markdown, f"{fragment} 仍被当成一条标题"

    def test_the_outline_has_the_sections_the_document_actually_has(self, res):
        """6 条：1 条封面大标题 + 2 条页 1 的 16pt 标题 + 3 个小节。

        改前是 10 条 —— 多的 4 条全是同一行标题被折断的碎片。数量在这里可数，是因为
        "多出来的条目"正是这份文档最贵的坏法：调用方照 `table_of_contents` 挑 `pages=`。
        """
        assert len(res.table_of_contents) == 6, res.table_of_contents
        assert [e["level"] for e in res.table_of_contents] == [1, 2, 2, 2, 2, 2]

    def test_the_level_still_comes_from_the_first_line_of_the_title(self, res):
        cover = res.table_of_contents[0]
        assert cover["level"] == 1 and cover["page"] == 1
        assert all(e["level"] == 2 for e in res.table_of_contents[1:])


class TestTheFoldDoesNotSwallowTheDocument:
    """折叠的负面边界：判据一旦被摘掉，坏法不是"少一条标题"，是把正文吃进标题。"""

    def test_the_paragraph_after_a_heading_is_not_folded_into_it(self, markdown):
        assert "A fixture document for the PDF heading outline." in markdown
        for ln in _heading_lines(markdown):
            assert "ordinary body text" not in ln, f"正文被折进标题: {ln}"

    def test_sections_divided_by_body_text_stay_separate(self, markdown, res):
        """三个小节被正文隔开，也隔着页 —— 同级不等于同一行印刷。"""
        titles = [e["title"] for e in res.table_of_contents]
        assert [t for t in titles if t.startswith("Section")] == [
            "Section one: the northern division",
            "Section two: the southern division",
            "Section three: outlook"]

    def test_a_heading_that_already_ends_a_sentence_stops_the_fold(self):
        """`_SENTENCE_END` 之后的同级行不再吸收 —— 那已经是另一个标题的开头。"""
        blocks = [
            (0.0, "text", ("First Part Of A Title", _chars(20.0), 0.0, 12.0)),
            (0.0, "text", ("Second Part", _chars(20.0), 13.0, 25.0)),
            (0.0, "text", ("A Different Heading", _chars(20.0), 26.0, 38.0)),
            (0.0, "text", ("More", _chars(20.0), 39.0, 51.0)),
        ]
        # 第二行带句点：折叠在它之后必须收住
        blocks[1] = (0.0, "text", ("Second Part.", _chars(20.0), 13.0, 25.0))
        text, consumed = _fold_heading_lines(blocks, 0, gap_threshold=12.0)
        assert text == "First Part Of A Title Second Part." and consumed == 1

    def test_a_line_at_a_different_size_is_not_a_continuation(self):
        blocks = [
            (0.0, "text", ("The Title Line", _chars(20.0), 0.0, 12.0)),
            (0.0, "text", ("An ordinary sentence.", _chars(11.0), 13.0, 25.0)),
        ]
        text, consumed = _fold_heading_lines(blocks, 0, gap_threshold=12.0)
        assert text == "The Title Line" and consumed == 0, (text, consumed)

    def test_a_line_below_the_other_is_not_a_continuation(self):
        """行距超出一页正文的"下一行"就不是折行 —— 它离这一行太远。"""
        blocks = [
            (0.0, "text", ("The Title Line", _chars(20.0), 0.0, 12.0)),
            (0.0, "text", ("Another Heading", _chars(20.0), 40.0, 52.0)),
        ]
        text, consumed = _fold_heading_lines(blocks, 0, gap_threshold=12.0)
        assert text == "The Title Line" and consumed == 0

    def test_a_table_between_two_big_lines_breaks_the_fold(self):
        blocks = [
            (0.0, "text", ("The Title Line", _chars(20.0), 0.0, 12.0)),
            (0.0, "table", ([["a"]], None)),
            (0.0, "text", ("Another Heading", _chars(20.0), 13.0, 25.0)),
        ]
        text, consumed = _fold_heading_lines(blocks, 0, gap_threshold=12.0)
        assert text == "The Title Line" and consumed == 0

    def test_the_fold_stops_at_the_heading_length_cap(self):
        """长度帽是这条规则唯一的"失控刹车"，所以它自己要有用例。

        一份整页都是同一大字号的文档（目录页、诗句排版的书脊页）不该被折成一条 5,000
        字的标题 —— 折到 `_HEADING_MAX_LEN` 就停，剩下的行回到它们原来的命运（正文或
        另一个标题），而不是一条吞掉整页的假标题。
        """
        from dhole_mcp.pdf_extractor import _HEADING_MAX_LEN

        words = [f"Segment{i:02d}filler" for i in range(30)]
        blocks = [(0.0, "text", (w, _chars(20.0), 13.0 * (i + 1), 13.0 * (i + 2)))
                  for i, w in enumerate(words)]
        text, consumed = _fold_heading_lines(blocks, 0, gap_threshold=12.0)
        assert len(text) <= _HEADING_MAX_LEN, len(text)
        assert 0 < consumed < len(blocks) - 1, consumed
        assert text.split() == words[:consumed + 1], text


def _chars(size: float):
    return [{"text": "x", "size": size, "fontname": "Arial-BoldMT",
             "upright": True, "x0": 0, "x1": 10, "top": 0, "bottom": 10}]


class TestTheFixtureIsReproducible:

    def test_the_html_source_names_the_print_command(self):
        src = (HERE / "wrapped_heading.html").read_text(encoding="utf-8")
        assert "--print-to-pdf" in src and "wrapped_heading.pdf" in src
        for anchor in ("split-both", "split-tail-period", "narrow-wrap"):
            assert f'id="{anchor}"' in src, f"少了 {anchor} 那一段"

    def test_the_manual_says_a_wrapped_title_is_one_entry(self):
        """`pages=` 是照 `table_of_contents` 挑的，所以"一行标题还是两条"是调用方要预见的事。"""
        from dhole_mcp.guide import _ROOT

        text = (_ROOT / "references" / "tools.md").read_text(encoding="utf-8")
        for needle in ("wrapped onto several lines", "**one** entry",
                       "no table in between"):
            assert needle in text, f"tools.md 没写着「{needle}」"

    def test_the_other_fixtures_headings_did_not_move(self):
        """折叠规则只该动"被折断的行"：另一份夹具的标题树逐条不许变。

        这是这次改动的对照 —— `merged_cells.pdf` 的 4 条标题（1+3）在 P2 前后同形。
        """
        other = extract_pdf((HERE / "merged_cells.pdf").read_bytes())
        assert [(e["level"], e["title"], e["page"])
                for e in other.table_of_contents] == [
            (1, "Quarterly revenue fixtures", 1),
            (2, "Table one: rectangular", 1),
            (2, "Table two: a header that spans two columns", 1),
            (2, "Table three: rowspan plus a merged subtotal", 1)]
