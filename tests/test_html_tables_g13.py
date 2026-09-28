"""G13 回归：HTML 表格要变成 markdown 表格，而不是九行散落的单元格。

复现是 `parse` 读一个含 `<table>` 的 HTML：输出 `Region / Q1 / Q2 / North / 12 / 14`
——表格的**形状**（哪个值属于哪一行哪一列）在输出里消失了。同一工具对 DOCX / XLSX 的
表格却给出真正的 markdown 表格。修法在共享抽取器里（`_trafilatura_markdown`），所以
`parse`、`smart_fetch`、`smart_crawl` 三条路同时被修，也因此要小心别把没表格的页面
抽坏了 —— 那条「行为逐字节不变」的断言就是为这个放的。

机制是把 `<table>` 换成一个占位词，让 trafilatura 只处理散文，最后把渲染好的
markdown 表格放回原位。占位词没活着回来时必须退回原路径，宁可留着扁平的单元格，也不
能「本来有表、输出里没了」。
"""

from __future__ import annotations

from dhole_mcp.parse import parse_file
from dhole_mcp.trafilatura_extractor import (
    _restore_table_markdown,
    _table_to_markdown_tokens,
    _trafilatura_markdown,
)

DOC = """<html><head><title>Prices</title></head><body>
<h1>Quarterly prices</h1>
<p>Some prose before the table so extraction has context.</p>
<table><thead><tr><th>Region</th><th>Q1</th><th>Q2</th></tr></thead>
<tbody><tr><td>North</td><td>12</td><td>14</td></tr>
<tr><td>South</td><td>9</td><td>11</td></tr></tbody></table>
<p>Prose after the table.</p>
</body></html>"""

TWO = """<html><body><h1>Two tables</h1>
<p>intro prose that trafilatura will keep</p>
<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>
<p>middle prose that trafilatura will keep</p>
<table><tr><th>C</th><th>D</th></tr><tr><td>3</td><td>4</td></tr></table>
<p>outro prose that trafilatura will keep</p></body></html>"""

NO_TABLE = """<html><body><h1>Only prose</h1>
<p>The first paragraph carries the content of this page.</p>
<p>And a second paragraph after it, with nothing table-shaped anywhere.</p>
</body></html>"""


def _write(tmp_path, text, name="page.html"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


class TestTheTableSurvivesAsATable:

    def test_parse_gives_a_real_markdown_table(self, tmp_path):
        text, err = parse_file(_write(tmp_path, DOC))

        assert err == ""
        assert "| Region | Q1 | Q2 |" in text, text
        assert "| --- | --- | --- |" in text, text
        assert "| North | 12 | 14 |" in text, text
        # The flattening this replaces looked like six loose lines of cells.
        assert "Region\nQ1" not in text, text

    def test_the_prose_around_it_is_not_collateral(self, tmp_path):
        text, _ = parse_file(_write(tmp_path, DOC))

        assert "Some prose before the table" in text
        assert "Prose after the table" in text
        assert text.index("before the table") < text.index("| Region |") < \
            text.index("after the table"), "表格要回到它原来在的位置"

    def test_two_tables_stay_in_their_own_order(self, tmp_path):
        text, _ = parse_file(_write(tmp_path, TWO))

        assert "| A | B |" in text and "| C | D |" in text, text
        assert text.index("| A | B |") < text.index("| C | D |")
        assert "middle prose" in text

    def test_a_ragged_headerless_table_still_comes_out_as_a_table(self, tmp_path):
        """真网页里的表经常没有 thead、行还不齐。这类表最需要的恰恰是别把形状弄丢。"""
        ragged = """<html><body><h1>Ragged</h1><p>prose that keeps the extraction alive</p>
        <table><tr><td>x</td><td>y</td></tr><tr><td>1</td></tr></table>
        <p>trailing prose</p></body></html>"""
        text, _ = parse_file(_write(tmp_path, ragged))

        assert "| x | y |" in text, text

    def test_a_prose_page_is_untouched_by_the_mechanism(self, tmp_path):
        """占位机制只在文档真有表格时才进场；没有表格的页面输出必须一字不差。"""
        html, tokens = _table_to_markdown_tokens(NO_TABLE)
        assert tokens == [] and html == NO_TABLE

        before = _trafilatura_markdown(NO_TABLE, "https://prose.example/")
        assert before and "first paragraph" in before

    def test_a_page_that_litters_the_token_prefix_is_not_mistaken(self, tmp_path):
        """占位词认的是「本次调用生成的那一个」，页面里恰好写着 DHOLE-TBL 也不该被
        当成表格回填。"""
        tricky = DOC.replace("Prose after the table.",
                             "Prose after the table. DHOLE-TBL-0-deadbeef")
        out = _trafilatura_markdown(tricky, "https://tricky.example/")

        assert "| Region | Q1 | Q2 |" in out, out
        assert "DHOLE-TBL-0-deadbeef" in out, "页面自己的文字不是我们的占位符"


class TestTheSwapItself:

    def test_the_placeholder_replaces_the_table_node(self):
        swapped, tokens = _table_to_markdown_tokens(DOC)

        assert len(tokens) == 1
        token, rendered = tokens[0]
        assert "<table" not in swapped and token in swapped
        assert rendered.startswith("| Region |")

    def test_no_tables_means_no_work(self):
        assert _table_to_markdown_tokens(NO_TABLE) == (NO_TABLE, [])
        assert _table_to_markdown_tokens("<html><body>no markup at all") == \
            ("<html><body>no markup at all", [])

    def test_a_missing_placeholder_is_a_fallback_not_a_loss(self):
        """占位词被 trafilatura 当噪声丢掉时，宁可退回「表格还在，只是扁的」，
        也不能交出一份本来有表却看不见表的文本。"""
        tokens = [("DHOLE-TBL-0-abc123", "| A | B |\n| --- | --- |")]
        assert _restore_table_markdown("prose only, token went missing", tokens) is None

    def test_the_placeholder_line_is_replaced_whole(self):
        tokens = [("DHOLE-TBL-0-abc123", "| A | B |")]
        out = _restore_table_markdown("before\nDHOLE-TBL-0-abc123\nafter", tokens)

        assert out == "before\n| A | B |\nafter", repr(out)
