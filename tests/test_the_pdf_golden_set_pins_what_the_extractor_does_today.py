"""PDF 黄金集：把提取器的输出钉住，表格形状每一次改动都要看得见（P5 建，P1 翻）。

为什么建在改动之前：这批要动的是表格渲染、标题层级和 TOC 的页码上界，而
`tests/test_pdf_real.py` 里 6 条用例**没有一条**断言过表格、TOC、标题或图。改完之后
"看起来对了"是这句被点名的失败在本仓库的又一次出场 —— 唯一能让"我只修了合并单元格"
这句话成立的证人，是**没被修的那部分逐字节没动**。

两个证人各有各的活，缺一个都不算：

- **字符串**（`tests/pdf_golden/<fixture>.md`）：现场。它说"这台 pdfplumber 交回的就是
  这段字节"。一次全表输出的漂移不许靠"性质还成立"混过去。
- **性质**（下面 `test_every_*` / `test_the_degraded_form_*` 那几条）：方向。字符串钉死会
  把 pdfplumber 的版本号也钉进仓库，所以真正要长期守的是"不存在非空单元格数与表头不符的
  行""降级不许丢任何一格文本"这类不依赖版本的断言。

P1 落地时刷过一次黄金集（规整表那一段**逐字节未变**，变的是两张合并表），diff 读进提交
信息；`TestTheMergedGeometryIsRestored` 就是 P5 那三条"今天记录错在哪"翻过来的样子 —— 翻
不全就是没修完，所以翻转本身也钉在用例里。

刷新黄金集 = `python tests/pdf_golden_record.py`，而它是一次**有意识的**决定：把
markdown 的 diff 读完再刷，并把 delta 写进提交信息（与 `tests/mcp_snapshot.py` 的
census 同一规矩）。手抄、或为了让用例绿而改黄金集，都是把证人换成被告。

`tests/merged_cells.pdf` 的来路（CONTRIBUTING: never hand-write a fixture —— PDF 是
二进制，手写的 PDF 只会画出"我以为表格长成的样子"，所以它是一份**打印结果**，源文件是
入库的 `tests/merged_cells.html`）：

    "C:/Program Files/Google/Chrome/Application/chrome.exe" \
      --headless=new --disable-gpu --no-pdf-header-footer \
      --print-to-pdf="D:/tools/dhole-mcp/tests/merged_cells.pdf" \
      "file:///D:/tools/dhole-mcp/tests/merged_cells.html"

命令原样记在这里，也在 `tests/merged_cells.html` 的注释里；重跑它要产出同一个文件，
否则那份 HTML 就不是作者而是装饰品。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from dhole_mcp.pdf_extractor import extract_pdf

HERE = Path(__file__).resolve().parent
GOLDEN = HERE / "pdf_golden"
FIXTURES = ["merged_cells.pdf", "background_checks.pdf", "dummy.pdf"]

_TABLE_SEP = re.compile(r"^\|(?: :?---:? \|)+$", re.MULTILINE)


@pytest.fixture(scope="module")
def extracted():
    return {name: extract_pdf((HERE / name).read_bytes()) for name in FIXTURES}


def _markdown(res) -> str:
    return "\n".join(res.content)


def _tables(markdown: str) -> list[list[list[str]]]:
    """从 markdown 里读出每张表：表头行 + 跳过分隔行 + 后续数据行，直到非 `|` 行。

    解析写在测试里是刻意的：黄金集要能独立复核（读 JSON/文本就能重言同一件事），而
    "提取器自己数出的表"不能作为自己输出的证人。
    """
    out = []
    lines = markdown.splitlines()
    i = 0
    while i < len(lines):
        if (i + 1 < len(lines) and lines[i].startswith("|")
                and _TABLE_SEP.match(lines[i + 1].strip())):
            rows = [[c.strip() for c in lines[i].strip().strip("|").split("|")]]
            j = i + 2
            while j < len(lines) and lines[j].startswith("|"):
                rows.append([c.strip() for c in lines[j].strip().strip("|").split("|")])
                j += 1
            out.append(rows)
            i = j
        else:
            i += 1
    return out


class TestTheGoldenFilesAreThePoint:

    @pytest.mark.parametrize("name", FIXTURES)
    def test_the_markdown_is_recorded_byte_for_byte(self, extracted, name):
        stem = name.rsplit(".", 1)[0]
        want = (GOLDEN / f"{stem}.md").read_text(encoding="utf-8")
        got = _markdown(extracted[name])
        assert got == want, (
            f"{name} 的输出与黄金集不一致。先读 diff 再决定：是提取器变了（那就是这次"
            "要看的 delta，跑 python tests/pdf_golden_record.py 重录并把 delta 写进提交"
            "信息），还是这一改动本来就不该动它。")

    @pytest.mark.parametrize("name", FIXTURES)
    def test_the_recorded_facts_match(self, extracted, name):
        stem = name.rsplit(".", 1)[0]
        facts = json.loads((GOLDEN / f"{stem}.json").read_text(encoding="utf-8"))
        res = extracted[name]
        for key, value in facts.items():
            got = (list(res.table_of_contents) if key == "table_of_contents"
                   else list(res.pages_extracted) if key == "pages_extracted"
                   else getattr(res, key))
            assert got == value, f"{name}.{key}: 记录的是 {value!r}，现在量到 {got!r}"


class TestTheStructureProperties:
    """性质断言：不依赖 pdfplumber 版本，是这批修完之后长期要守的那一半。"""

    @pytest.mark.parametrize("name", FIXTURES)
    def test_every_row_has_as_many_cells_as_its_header(self, extracted, name):
        """一张 markdown 表里不许出现"格子数与表头不符"的行。

        P1 之后这条只管**规整表**（不规则的已经改发对齐文本，见
        `test_an_irregular_table_is_never_written_as_a_grid`），所以它守的是"规整表不许
        塌列"，而语义损失那一半由下面两条看着。
        """
        for idx, table in enumerate(_tables(_markdown(extracted[name])), 1):
            header = table[0]
            bad = [(n + 1, len(r)) for n, r in enumerate(table) if len(r) != len(header)]
            assert not bad, f"{name} 第 {idx} 张表行格数与表头({len(header)})不符: {bad}"

    @pytest.mark.parametrize("name", FIXTURES)
    def test_no_table_is_all_phantom_cells(self, extracted, name):
        """一张表至少要有一行是真有内容的，否则"提取到表格"这句话是空的。"""
        for idx, table in enumerate(_tables(_markdown(extracted[name])), 1):
            assert any(any(c for c in row) for row in table[1:] or table), \
                f"{name} 第 {idx} 张表除了表头全是空格"

    @pytest.mark.parametrize("name", FIXTURES)
    def test_an_irregular_table_is_never_written_as_a_grid(self, extracted, name):
        """降级不许半途：被判为不规则的表不能既发 `[merged-cell table:` 抬头又留一张网格。

        这条与上面那条合起来才封住"合并格被写成空数据"那一条路 —— 只要网格里还有
        `| --- |`，读者就会把第二行表头当第一行数据。
        """
        import pdfplumber

        from dhole_mcp.pdf_extractor import table_geometry, table_is_rectangular

        rect = []
        with pdfplumber.open(str(HERE / name)) as pdf:
            for page in pdf.pages:
                for t in page.find_tables(table_settings={"text_x_tolerance": 2,
                                                          "text_y_tolerance": 3}):
                    g = table_geometry(t)
                    if g:
                        rect.append(table_is_rectangular(g))
        md = _markdown(extracted[name])
        irregular = [i for i, ok in enumerate(rect) if not ok]
        if not irregular:
            assert "[merged-cell table:" not in md, md
            return
        assert md.count("[merged-cell table:") == len(irregular), (
            f"{len(irregular)} 张不规则表，抬头却有 "
            f"{md.count('[merged-cell table:')} 段")
        for block in md.split("[merged-cell table:")[1:]:
            body = block.split("\n\n")[0]
            assert "\n| --- |" not in body, f"降级块里混进了网格分隔行: {body[:200]}"
            assert "row 1:" in body, body[:200]

    @pytest.mark.parametrize("name", FIXTURES)
    def test_the_degraded_form_loses_no_cell_text(self, extracted, name):
        """降级只换形状，不换内容：几何层每一个非空格的文本都要在正文里出现。

        这条是 P1 的反面守卫 —— 把横向合并"收起来"（不逐个列出被盖住的位置）很容易
        顺手把数据也收掉，而那比一张错网格更糟。地面真值取 `table.extract()`
        （pdfplumber 自己数出来的每一格），不取渲染器的话。
        """
        import pdfplumber

        md = _markdown(extracted[name])
        seen = 0
        with pdfplumber.open(str(HERE / name)) as pdf:
            for page in pdf.pages:
                for t in page.find_tables(table_settings={"text_x_tolerance": 2,
                                                          "text_y_tolerance": 3}):
                    for row in t.extract():
                        for cell in row:
                            text = re.sub(r"\s+", " ", str(cell or "").strip())
                            if not text:
                                continue
                            seen += 1
                            assert text in md, f"{name}: 降级渲染丢了格子文本 {text[:60]!r}"
        if not seen:
            # dummy.pdf 是扫描件：它一张表都没有，这条守卫在那份夹具上是空转，
            # 所以明说它没跑，而不是让"没有文本"冒充"文本都没丢"。
            assert extracted[name].scanned is True, name
            pytest.skip(f"{name} 里没有表 —— 这条守卫没有可查的东西")

    def test_the_heading_tree_matches_the_document(self, extracted):
        """四行标题就是四条：1 个 level-1 + 3 个 level-2，且都在第 1 页。

        P2 要动的就是这一条的**反面**（标题被按换行腰斩、续行因结尾带 `.` 被降级），
        所以今天先把正确的形状录下来。
        """
        toc = extracted["merged_cells.pdf"].table_of_contents
        assert [e["level"] for e in toc] == [1, 2, 2, 2], toc
        assert all(e["page"] == 1 for e in toc)
        assert "Merged cell fixtures" not in json.dumps(toc), \
            "文档元数据的标题不该混进正文 TOC"

    @pytest.mark.parametrize("name", FIXTURES)
    def test_every_toc_range_stays_inside_the_document(self, extracted, name):
        """P3 的靶子：`end_page` 越过 `pages_total` 的区间会命中任何 `pages=` 请求。

        书签路径 `_extract_toc` 返回时不钳（标题路径钳），所以这一条今天要**允许**
        它成立 —— 单页夹具恰好钳得住，P3 用多页夹具来撞它。
        """
        res = extracted[name]
        for entry in (res.table_of_contents or []):
            assert 1 <= entry["page"] <= res.pages_total, entry
            assert entry["end_page"] <= res.pages_total, entry
            assert entry["end_page"] >= entry["page"], entry


class TestTheMergedGeometryIsRestored:
    """P5 录下这三条时它们是"今天的错"；P1 落地，三条一起翻 —— 翻不全就是没修完。"""

    def test_a_merged_subtotal_is_not_written_as_an_empty_cell(self, extracted):
        """跨列的 "North subtotal" 从前被补成 `| North subtotal |  | 545 |`。

        那时读的人无从区分"这一格属于左边那一格"与"这一格真的没数据"，而这句话出自
        一张自称成功的表格（`content_ok: true`）。现在跨度写在格子上，被盖住的位置
        不再冒充数据。
        """
        md = _markdown(extracted["merged_cells.pdf"])
        assert "| North subtotal |  | 545 |" not in md, md
        assert '"North subtotal" [spans 2 columns]' in md, md

    def test_a_two_row_header_no_longer_collapses_into_the_data_rows(self, extracted):
        """表二有两行表头（`Revenue by quarter` 跨两列），markdown 只容得下一行。

        前果是第二行表头（Third quarter / Fourth quarter / Year）作为**数据行**发出
        —— 体验测试里那句"Fourth quarter 掉进数据行"就是这么来的。现在这张表不出网格，
        两行都按行号列出，谁继承谁也说得出。
        """
        md = _markdown(extracted["merged_cells.pdf"])
        assert _tables(md), "规整表仍在（正对照不许被误伤）"
        for table in _tables(md):
            flat = " ".join(" ".join(r) for r in table)
            assert "Fourth quarter" not in flat, f"仍有网格把第二行表头当数据: {flat[:160]}"
        assert 'row 2: (as row 1, column 1) | "Third quarter" | "Fourth quarter"' in md, md

    def test_the_wide_real_world_table_no_longer_pays_a_phantom_column_each(self, extracted):
        """`background_checks.pdf`（仓库里既有的真 PDF）是同一件事的真实尺寸证人。

        从前那张 25 列网格的表头里有一半以上是补出来的空位。现在它按行列出，跨度自己
        说话；P1 若只把玩具表修好而让真文件更糟，这里的数字会动 —— 记着改前的实测：
        6,716 字符 / 一张 25 列网格，改后 7,565 字符（+12.6%，多出来的是跨度说明）。
        """
        md = _markdown(extracted["background_checks.pdf"])
        assert "[merged-cell table:" in md
        assert "| --- |" not in md, "这份真 PDF 已经不规则，不该再出网格"
        assert '"NICS Firearm Background Checks November - 2015" [spans 25 columns]' in md
        assert len(md) < 6_716 * 2, (
            f"降级把正文放大到 {len(md)} 字符 —— 那就不叫降级，叫放大（实测过逐位列出"
            "被盖住位置会把 6.7k 变成 33k）")


class TestTheGeometryItself:
    """几何层单独钉：跨度是从 bbox 推的，推错了整个渲染就建立在错数据上。

    这里的 `_Table` 是**纯函数的桩**（`rows[i].cells` 的 bbox 形状取自
    `tests/merged_cells.pdf` 的实测读数），不是内容夹具 —— 被钉的是"给一堆 bbox，
    跨度算得对不对"，与哪份 PDF 无关。
    """

    def test_spans_come_from_the_boxes_not_from_the_padded_list(self):
        from dhole_mcp.pdf_extractor import (
            _merged_table_to_text, table_geometry, table_is_rectangular)

        class _Row:
            def __init__(self, cells):
                self.cells = cells

        class _Table:
            # 两列网格，首行那一格横向盖住两列：`extract()` 只会给 ['North subtotal', None]，
            # 是 x 边缘序列才说出"那是 2 列"而不是"这里空着"。
            rows = [_Row([(0.0, 0.0, 100.0, 10.0), None]),
                    _Row([(0.0, 10.0, 50.0, 20.0), (50.0, 10.0, 100.0, 20.0)])]

            @staticmethod
            def extract():
                return [["North subtotal", None], ["First quarter", "120"]]

        grid = table_geometry(_Table)
        assert grid is not None
        assert grid[0][0]["cspan"] == 2 and grid[0][1]["covered"] is True
        assert table_is_rectangular(grid) is False
        # 横向被盖住的位置不再逐个列出，也不许冒充一列数据
        assert _merged_table_to_text(grid).splitlines()[1] == \
            'row 1: "North subtotal" [spans 2 columns]'

    def test_a_rowspan_is_reported_as_a_carried_value_not_a_blank(self):
        from dhole_mcp.pdf_extractor import _merged_table_to_text, table_geometry

        class _Row:
            def __init__(self, cells):
                self.cells = cells

        class _Table:
            # 两行的表，第一列那一格纵向盖住两行（y 边缘 0/15/30 与两个单行格对齐）。
            rows = [_Row([(0.0, 0.0, 50.0, 30.0), (50.0, 0.0, 100.0, 15.0)]),
                    _Row([None, (50.0, 15.0, 100.0, 30.0)])]

            @staticmethod
            def extract():
                return [["North", "First quarter"], [None, "Second quarter"]]

        grid = table_geometry(_Table)
        assert grid[0][0]["rspan"] == 2
        assert 'row 2: (as row 1, column 1) | "Second quarter"' in \
            _merged_table_to_text(grid)

    def test_geometry_backs_off_when_the_two_views_disagree(self):
        """`extract()` 与 `rows` 对不上形状时交回 None，让调用方回落到补齐的那份。

        宁可用旧的（会撒谎的）形状，也不许拿一份**猜出来的**几何去渲染。
        """
        from dhole_mcp.pdf_extractor import table_geometry

        class _Row:
            def __init__(self, cells):
                self.cells = cells

        class _Table:
            rows = [_Row([(0.0, 0.0, 50.0, 10.0), (50.0, 0.0, 100.0, 10.0)])]

            @staticmethod
            def extract():
                return [["A", "B", "C"]]

        assert table_geometry(_Table) is None

    def test_the_fallback_refuses_a_header_row_that_carries_a_span(self):
        """读不到几何时的兜底判据：首行里有 `None` 延续就不许提它当表头。"""
        from dhole_mcp.pdf_extractor import _table_to_markdown

        padded = [["Region", "Revenue by quarter", None, "Share"],
                  [None, "Third quarter", "Fourth quarter", "Year"]]
        out = _table_to_markdown(padded)
        assert "| --- |" not in out, out
        assert "Third quarter" in out
        clean = _table_to_markdown([["A", "B"], ["1", "2"]])
        assert clean.startswith("| A | B |\n| --- | --- |"), clean


class TestTheDegradationIsAnnounced:
    """降级要出口：只写在正文里的一段话，按 `summary` 读的人看不见（R3 的那条教训）。"""

    def test_summary_says_the_table_came_back_as_text_rows(self):
        import asyncio

        from dhole_mcp.context import _arg_notes_scope
        from dhole_mcp.server import MasterFetchServer

        async def run():
            with _arg_notes_scope():
                return await MasterFetchServer().doc_parse(str(HERE / "merged_cells.pdf"))

        out = asyncio.run(run())
        assert "merged cells" in (out.summary or ""), out.summary
        assert "not a markdown grid" in out.summary

    def test_a_page_with_no_merging_says_nothing_about_merging(self):
        """没发生降级就不许说这句话 —— 告警的默认值是沉默。"""
        import asyncio

        from dhole_mcp.context import _arg_notes_scope
        from dhole_mcp.pdf_extractor import _announce_merged_table

        async def run():
            with _arg_notes_scope() as notes:
                _announce_merged_table(0, 0)
                return list(notes)

        assert asyncio.run(run()) == []

    def test_the_manual_says_a_merged_table_is_not_a_grid(self):
        """形状的突然改变要写在调用方会读的那一卷里，而不是只写在正文的那一段抬头里。

        只读 `tools/list` 与说明书的人遇到 `row 1: …` 会以为是解析失败；这句话把"这是
        刻意的降级"说在前面。
        """
        from dhole_mcp.guide import _ROOT

        text = (_ROOT / "references" / "tools.md").read_text(encoding="utf-8")
        for needle in ("merged-cell table", "not a markdown grid", "(as row 1, column 1)",
                       "Rectangular tables are unaffected"):
            assert needle in text, f"tools.md 没写着「{needle}」"


class TestTheFixtureItselfIsReproducible:

    def test_the_html_source_is_checked_in_next_to_the_pdf(self):
        src = HERE / "merged_cells.html"
        assert src.exists(), "PDF 的源必须入库，否则这份夹具没人能重造"
        text = src.read_text(encoding="utf-8")
        for table_id in ("rectangular", "colspan", "rowspan"):
            assert f'id="{table_id}"' in text, f"少了 {table_id} 那张表"
        assert "--print-to-pdf" in text, "生成命令要写在源文件里，不只是写在这里"

    def test_the_scanned_fixture_still_says_it_has_no_text(self, extracted):
        """dummy.pdf 是"没有可提取文本"的正对照：黄金集不许把它录成一具空正文。"""
        res = extracted["dummy.pdf"]
        assert res.scanned is True and res.error.startswith("scanned_pdf")
        assert "no extractable text" in _markdown(res)
