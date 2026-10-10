"""画出来的一页要有账：图不内联、不冒充正文，但要说它在第几页（P4）。

`_render_page` 只看 `page.chars` 与 `find_tables`，从不碰 `page.images` / `page.rects`
/ `page.curves`。于是一页里**画**出来的东西不会出现在正文里，也没有任何地方说它存在过 ——
"这份文档没有图"与"第 1 页那幅图没进来"在响应里是同一句话，而这两件事的下一步完全不同
（前者换来源，后者上 vision / screenshot）。

形状按测出来的两条边界定：

- **不进正文**（`metadata.figures` + `summary`，不内联 `[Figure on page N]`）。一个方括号
  字符串是字符数，而薄抽取/质量那套算术读的就是字符数 —— 内联会让"我们承认这页有图"这一
  件纯粹的事，翻转 `content_ok`。这条是这批自己的教训（R1/R2 的 measure-then-flip 读的
  就是同一批字符数）。
- **矢量按"围不围得出面积"计数**。两条排除都是量出来的，不是猜的：
  1. 表格区域里的不算 —— 表的边框也是 `page.rects`，不过滤的话 `tests/merged_cells.pdf`
     会报"141 个本提取器没渲染的对象"，而那张表的文字**整整齐齐回来了**
     （实测 `background_checks.pdf` 是 446）。
  2. 短边不足 3pt 的细线不算 —— `<hr>`、分隔线、引文的左边框都是这种 mark。
     `tests/deco_rules.pdf`（三条 `<hr>` + 一段带左边框的引文 + 一张有边框的表 + 正文）
     实测表框外 13 个对象，短边全在 0.75–1.5pt，于是这一页**什么都不报**；加这条之前它
     报的是 13。一句会误报的告示与一句静默是同一种罪的两面 —— 它教读者忽略这条消息。

     排除的判据必须是**范围**而不是笔画粗细，否则真图会跟着一起没：`tests/line_chart.pdf`
     里坐标轴与网格是四条 0pt 短边的线，数据线也只有 2pt 粗，但它横跨 285×90pt —— 按粗细
     判就把一张图判成了装饰，P4 白修。位图两条过滤都不走：表格单元里的一张贴图与正文里的
     一张一样读不到。

夹具 `tests/figure_page.pdf` 由内联 SVG 的 `tests/figure_page.html` 经真 Chrome 打印而来
（命令写在 HTML 注释与本文件抬头）：SVG 是人能读能改的文本，而 Chrome 把它作为矢量画进
PDF —— 正好是"文本路径拿不回来"那一类内容。`tests/deco_rules.html` / `tests/line_chart.html`
是同一规矩打出来的**两侧对照**（一边不许报，一边必须报）。位图那一半由仓库既有的
`tests/dummy.pdf`（扫描件，整页就是一张图）作证。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from dhole_mcp.pdf_extractor import (
    _encloses_area,
    _figures_metadata,
    _figures_on_page,
    extract_pdf,
)

HERE = Path(__file__).resolve().parent
FIGURE_PDF = HERE / "figure_page.pdf"
SCANNED_PDF = HERE / "dummy.pdf"
TABLE_PDF = HERE / "merged_cells.pdf"
DECO_PDF = HERE / "deco_rules.pdf"
LINE_PDF = HERE / "line_chart.pdf"


@pytest.fixture(scope="module")
def figure_res():
    return extract_pdf(FIGURE_PDF.read_bytes())


@pytest.fixture(scope="module")
def table_res():
    return extract_pdf(TABLE_PDF.read_bytes())


def _summary_notes(path: str):
    from dhole_mcp.context import _arg_notes_scope
    from dhole_mcp.server import MasterFetchServer

    async def run():
        with _arg_notes_scope() as notes:
            out = await MasterFetchServer().doc_parse(path)
            return out, list(notes)

    return asyncio.run(run())


class TestAPageWithADrawingSaysSo:

    def test_the_drawing_is_counted_not_silently_gone(self, figure_res):
        figures = figure_res.metadata.get("figures")
        assert figures, "一页画着东西，metadata 里却没有账"
        assert figures[0]["page"] == 1
        assert figures[0]["vector_objects"] > 0, figures

    def test_the_body_carries_only_what_is_text(self, figure_res):
        """正文不许被塞进内联占位符：它是那张图的账，也是它的字符数。"""
        body = "\n".join(figure_res.content)
        assert "31415-figure-probe" in body, "图注是文本，必须在"
        for marker in ("[Figure", "figure omitted", "[Page 1 has a drawing]"):
            assert marker not in body, f"内联标记混进正文了：{marker}"

    def test_the_announcement_names_pages_counts_and_the_way_to_read_them(
            self, figure_res):
        out, notes = _summary_notes(str(FIGURE_PDF))
        breath = " ".join(notes)
        assert "1 of 1 page(s) carries" in breath, breath
        assert "vector object(s)" in breath
        assert "metadata.figures" in breath, "读者要能找到那份账"
        assert "vision tool or web_act screenshot" in breath, "要给出下一步动作"
        assert "metadata.figures" not in (out.content[0] if out.content else "")

    def test_a_scanned_page_reports_its_raster_image(self):
        """位图那一半：整页是一张图的扫描件，账要记在 raster 上而不是矢量上。"""
        res = extract_pdf(SCANNED_PDF.read_bytes())
        figures = res.metadata.get("figures")
        assert figures and figures[0]["raster"] >= 1, figures
        assert "largest_px" in figures[0], figures
        assert res.scanned is True, "这条夹具的既有身份不许被这次改动带跑"
        assert res.error.startswith("scanned_pdf")

    def test_the_scanned_dead_end_still_carries_the_figure_account(self):
        """扫描件那条 return 也要结这笔账：P4 只修一半就是还剩一半静默。"""
        _out, notes = _summary_notes(str(SCANNED_PDF))
        assert any("embedded image(s)" in n for n in notes), notes


class TestTheCensusDoesNotCryWolf:

    def test_table_borders_are_not_reported_as_unread_content(self, table_res):
        """`merged_cells.pdf` 全是表格边框：文字回来了，就不该有"图没进来"这句话。

        这条是这次改动自己的反面守卫。不过滤时实测报的是 141 个对象（而
        `background_checks.pdf` 是 446）—— 一张表完整回来的页面被说成"有一堆内容没渲染"，
        读者学到的就是别信这条消息。
        """
        assert "figures" not in table_res.metadata, table_res.metadata.get("figures")
        out, notes = _summary_notes(str(TABLE_PDF))
        assert not any("non-text content" in n for n in notes), notes
        assert any("merged cells" in n for n in notes), "表格那条降级照旧要说"

    def test_a_page_of_prose_with_rules_is_not_called_a_figure_page(self):
        """细线不是内容：一页正文加三条 `<hr>` 与一段带边框的引文，什么都不许报。

        加这条过滤之前，这一页报的是"13 个本提取器没渲染的矢量对象"（实测），而那 13 个
        短边全在 0.75–1.5pt。文字整页回来了，却说"有你读不到的东西"，读者学到的就是
        不看这条消息。
        """
        res = extract_pdf(DECO_PDF.read_bytes())
        assert res.error == ""
        assert "7653-deco-rules-anchor" in "\n".join(res.content), "正文本来就该全回来"
        assert "figures" not in (res.metadata or {}), (res.metadata or {}).get("figures")
        _out, notes = _summary_notes(str(DECO_PDF))
        assert not any("non-text content" in n for n in notes), notes

    def test_a_chart_made_of_thin_strokes_is_still_counted(self):
        """反方向的守卫：判据若是笔画粗细，这张真图会被判成装饰，P4 白修。

        `tests/line_chart.pdf` 的轴与网格是四条 0pt 短边的线，数据线也只有 2pt 粗，但它
        横跨 285×90pt —— 围得出面积就算内容。这一条钉住"范围不是粗细"。
        """
        res = extract_pdf(LINE_PDF.read_bytes())
        figures = (res.metadata or {}).get("figures")
        assert figures and figures[0]["vector_objects"] >= 1, figures
        assert "4421-line-chart-anchor" in "\n".join(res.content)
        _out, notes = _summary_notes(str(LINE_PDF))
        assert any("non-text content" in n for n in notes), notes

    def test_the_area_test_looks_at_the_box_not_at_the_stroke(self):
        """单元一层：同一根 2pt 的线，横着是装饰，斜着围出高度就是内容。"""
        rule = {"x0": 10.0, "x1": 400.0, "top": 20.0, "bottom": 20.75}
        data_path = {"x0": 30.0, "x1": 315.0, "top": 20.0, "bottom": 110.0}
        assert _encloses_area(rule) is False, "横线的短边是线宽，不是内容"
        assert _encloses_area(data_path) is True

    def test_a_vector_object_inside_a_table_bbox_is_excluded_but_a_picture_is_not(self):
        """过滤只作用在矢量上：贴图在表格单元里也一样读不到。"""
        class _Page:
            images = [{"width": 200, "height": 120}]
            rects = [{"object_type": "rect", "x0": 10, "x1": 30, "top": 10, "bottom": 30},
                     {"object_type": "rect", "x0": 300, "x1": 340, "top": 200,
                      "bottom": 240}]
            lines: list = []
            curves: list = []

        bboxes = [(0, 0, 100, 100)]
        facts = _figures_on_page(_Page(), bboxes)
        assert facts["raster"] == 1, "位图不受表格框影响"
        assert facts["vector_objects"] == 1, "两个矩形里只有一个在表格外"

    def test_a_page_with_nothing_drawn_adds_no_entry(self):
        """没发生降级就不许说这句话 —— 告警的默认值是沉默。"""
        class _Blank:
            images: list = []
            rects: list = []
            lines: list = []
            curves: list = []

        assert _figures_on_page(_Blank(), []) == {}
        assert _figures_metadata({1: {}}, [1], 1) == []

    def test_pages_without_figures_are_not_listed(self):
        figures = {1: {"raster": 2}, 2: {}, 3: {"vector_objects": 4}}
        listed = _figures_metadata(figures, [1, 2, 3], 3)
        assert [e["page"] for e in listed] == [1, 3], listed


class TestTheFixtureIsReproducible:

    def test_the_html_source_names_the_print_command_and_an_anchor(self):
        src = (HERE / "figure_page.html").read_text(encoding="utf-8")
        assert "--print-to-pdf" in src and "figure_page.pdf" in src
        assert "31415-figure-probe" in src, "正文里要有一个可断言的锚点"
        assert "<svg" in src, "这一页的内容必须是**画**出来的"

    def test_the_manual_says_a_drawing_is_accounted_not_inlined(self):
        """读者要靠手册预见"这一页有图但正文里没有"，不然他只会看到一段图注。

        手册与代码各写各的，就是下一次漂移的开始 —— 这批给每条结论位都配了一根针。
        """
        from dhole_mcp.guide import _ROOT

        tools = (_ROOT / "references" / "tools.md").read_text(encoding="utf-8")
        for needle in ("metadata.figures", "vector_objects", "largest_px",
                       "non-text content this extraction does not render",
                       "extent, not ink"):
            assert needle in tools, f"tools.md 没写着「{needle}」"
        fields = (_ROOT / "references" / "fields.md").read_text(encoding="utf-8")
        assert "Table borders and hairline rules" in fields, \
            "fields.md 要写明边框与细线不计数，否则读者以为这行账会把装饰线也报出来"

    def test_both_new_fixtures_carry_their_print_command(self):
        """两份对照夹具必须是**打印出来**的，不是手抄的：源入库、命令写在源里。"""
        deco = DECO_PDF.with_suffix(".html").read_text(encoding="utf-8")
        line = LINE_PDF.with_suffix(".html").read_text(encoding="utf-8")
        for src, pdf in ((deco, "deco_rules.pdf"), (line, "line_chart.pdf")):
            assert "--print-to-pdf" in src and pdf in src, f"{pdf} 没写打印命令"
        assert "<hr>" in deco and "border-left" in deco, "这份对照要的就是细线装饰"
        assert 'polyline' in line and 'stroke-width="2"' in line, \
            "真图那一侧的数据线必须是细的 —— 否则这条对照测不到'范围 vs 粗细'"

    def test_the_golden_set_covers_the_figure_fixture(self):
        """新夹具进了黄金集，就得和其余几份一样被逐字节钉住。"""
        golden = HERE / "pdf_golden" / "figure_page.md"
        assert golden.exists(), "跑 python tests/pdf_golden_record.py 把它录进来"
        assert golden.read_text(encoding="utf-8") == "\n".join(
            extract_pdf(FIGURE_PDF.read_bytes()).content)
