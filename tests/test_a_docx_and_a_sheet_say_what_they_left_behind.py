"""契约测试：`.docx` 与 `.xlsx` 的输出。

这两个是最常用的格式，却是解析层里唯一**没有输出契约测试**的两个：g30 给
`.pptx`/`.odt`/`.json`/`.yaml` 写了 20 条断言钉住输出形状，而 `.docx`/`.xlsx`
全仓只有一句 `assert ".docx" in SUPPORTED_EXTENSIONS`——没有任何测试真的解析一份
Word 或 Excel 并断言它读出了什么。四条缺陷因此活了下来，全部是实测所得：

* **xlsx 的截断提示写反了**：一份 500 行的表回的是「99 more rows shown」，而那 99 行
  正是被丢掉的行。数字本身也是从上限推出来的（读满 200 行就停、只渲染 99 行、报
  200-101），与真实剩余行数无关。截断读起来像完整，是这个项目最防的那类失败。
* **docx 把所有表格搬到文末**：`doc.paragraphs` 全输出完再 append `doc.tables`，于是
  正文里「见下表」的引用指到了文末之后。
* **docx 丢了页眉/页脚/脚注/批注却一字不说**：同一个模块里 `.pptx`/`.odt` 都会在首行
  声明自己带不走什么，只有 `.docx` 不说——一份少了页脚的稿件看起来和完整的一模一样。
* **xlsx 的公式单元格静默变空白**：`data_only=True` 取的是缓存值，而库写出的工作簿
  没有缓存，`=A2*B2` 渲染成空格子，读起来像「这格没数据」而不是「这格是算出来的」。

补测试是第一步：不补网，修了也会退化。
"""

from __future__ import annotations

import base64

import pytest

from dhole_mcp.parse import parse_file

#: A real 1x1 PNG. python-docx validates the bytes, so a fake will not do.
_PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/"
    "q842iQAAAABJRU5ErkJggg==")


def _docx(tmp_path, name: str, build) -> str:
    """A real .docx built by python-docx (skips when the extra is absent)."""
    docx = pytest.importorskip("docx", reason="the [all] extra provides python-docx")
    doc = docx.Document()
    build(doc)
    path = tmp_path / name
    doc.save(str(path))
    return str(path)


def _xlsx(tmp_path, name: str, build) -> str:
    """A real .xlsx built by openpyxl (skips when the extra is absent)."""
    openpyxl = pytest.importorskip("openpyxl", reason="the [all] extra provides openpyxl")
    wb = openpyxl.Workbook()
    build(wb)
    path = tmp_path / name
    wb.save(str(path))
    return str(path)


class TestTheDocumentOrderSurvives:
    """表格留在正文放它的位置，而不是被搬到文末。"""

    def test_a_table_stays_between_the_prose_that_brackets_it(self, tmp_path):
        def build(doc):
            doc.add_heading("安装指南", level=1)
            doc.add_paragraph("先看下面这张表：")
            table = doc.add_table(rows=2, cols=2)
            table.rows[0].cells[0].text = "参数"
            table.rows[0].cells[1].text = "说明"
            table.rows[1].cells[0].text = "timeout"
            table.rows[1].cells[1].text = "第一行"
            doc.add_paragraph("表后还有一段结论。")

        text, err = parse_file(_docx(tmp_path, "order.docx", build))

        assert err == ""
        # 旧行为：表格排在「表后还有一段结论」之后
        assert (text.index("先看下面这张表")
                < text.index("| 参数 | 说明 |")
                < text.index("表后还有一段结论"))

    def test_two_tables_keep_their_own_places(self, tmp_path):
        def build(doc):
            doc.add_paragraph("第一段。")
            first = doc.add_table(rows=1, cols=1)
            first.rows[0].cells[0].text = "甲表"
            doc.add_paragraph("中间段。")
            second = doc.add_table(rows=1, cols=1)
            second.rows[0].cells[0].text = "乙表"
            doc.add_paragraph("最后一段。")

        text, err = parse_file(_docx(tmp_path, "two.docx", build))

        assert err == ""
        order = [text.index(mark) for mark in
                 ("第一段", "| 甲表 |", "中间段", "| 乙表 |", "最后一段")]
        assert order == sorted(order), f"顺序被打乱: {order}"

    def test_headings_still_become_markdown_headings(self, tmp_path):
        """顺序改了，层级映射不能跟着变。"""
        def build(doc):
            doc.add_heading("一级", level=1)
            doc.add_heading("二级", level=2)
            doc.add_paragraph("正文。")

        text, err = parse_file(_docx(tmp_path, "heads.docx", build))

        assert "# 一级" in text and "## 二级" in text


class TestTheFirstLineNamesWhatWasDropped:
    """带不走的东西写在首行——`.pptx`/`.odt` 的契约，`.docx` 以前不遵守。"""

    def test_a_footer_with_text_is_named_not_silently_dropped(self, tmp_path):
        def build(doc):
            doc.add_paragraph("正文一句。")
            doc.sections[0].footer.paragraphs[0].text = "机密 · 页脚里的重要声明"

        text, err = parse_file(_docx(tmp_path, "footer.docx", build))

        assert err == ""
        # 仍然不读页脚（那是边界不是 bug），但必须说出来
        assert "机密" not in text
        assert "1 header/footer(s)" in text
        assert text.splitlines()[0].startswith("[Word document;")

    def test_an_image_is_counted(self, tmp_path):
        image = tmp_path / "px.png"
        image.write_bytes(_PNG_1X1)

        def build(doc):
            doc.add_paragraph("正文。")
            doc.add_picture(str(image))

        text, err = parse_file(_docx(tmp_path, "pic.docx", build))

        assert err == ""
        assert "1 image(s)" in text

    def test_a_plain_document_states_the_boundary_without_a_count(self, tmp_path):
        def build(doc):
            doc.add_paragraph("只有正文。")

        text, err = parse_file(_docx(tmp_path, "plain.docx", build))

        assert err == ""
        assert "headers/footers, footnotes, comments and images not included" in text
        assert "dropped:" not in text, "没有东西被丢时不该报数字"

    def test_styles_and_numbering_are_not_mistaken_for_a_header(self, tmp_path):
        """`styles.xml` / `numbering.xml` 里也有 `<w:t>`。

        匹配的是**部件名**（`header*.xml` / `footer*.xml`），不是字节——按字节判会
        把每一份文档都报成「丢了页眉」。
        """
        def build(doc):
            doc.add_paragraph("列表项", style="List Bullet")

        text, err = parse_file(_docx(tmp_path, "list.docx", build))

        assert err == ""
        assert "dropped:" not in text


class TestASheetSaysWhatItLeftBehind:
    """行上限必须报出被丢掉的行，且数字来自工作表而不是上限本身。"""

    def test_the_note_counts_the_rows_dropped_not_the_rows_kept(self, tmp_path):
        def build(wb):
            ws = wb.active
            ws.title = "大表"
            ws.append(["序号", "值"])
            for i in range(1, 500):
                ws.append([i, i * 2])

        text, err = parse_file(_xlsx(tmp_path, "big.xlsx", build))

        assert err == ""
        # 500 行 - (1 表头 + 100 数据) = 399
        assert "399 more row(s) not shown, of 500 in the sheet" in text
        assert "more rows shown" not in text, "旧措辞说的正是被丢掉的那批行"
        assert "sheet may have more" not in text, "上限推出来的含糊数字"

    def test_the_cap_renders_the_header_and_a_hundred_data_rows(self, tmp_path):
        def build(wb):
            ws = wb.active
            ws.title = "大表"
            ws.append(["序号", "值"])
            for i in range(1, 500):
                ws.append([i, i * 2])

        text, err = parse_file(_xlsx(tmp_path, "big.xlsx", build))

        assert err == ""
        rows = [ln for ln in text.splitlines() if ln.startswith("| ")]
        assert len(rows) == 102, "表头 + 分隔行 + 100 数据行"
        assert "| 100 | 200 |" in text
        assert "| 101 |" not in text

    def test_a_sheet_inside_the_cap_carries_no_truncation_note(self, tmp_path):
        def build(wb):
            ws = wb.active
            ws.title = "小表"
            ws.append(["区域", "营收"])
            ws.append(["华东", 1200])
            ws.append(["华南", 980])

        text, err = parse_file(_xlsx(tmp_path, "small.xlsx", build))

        assert err == ""
        assert "more row(s) not shown" not in text
        assert "| 华东 | 1200 |" in text


class TestAComputedCellIsNotShownAsBlank:
    """公式没有缓存值时回公式文本，不回空格子。"""

    def test_a_formula_without_a_cached_value_shows_the_formula(self, tmp_path):
        def build(wb):
            ws = wb.active
            ws.title = "计算"
            ws.append(["数量", "单价", "合计"])
            ws.append([3, 100, "=A2*B2"])

        text, err = parse_file(_xlsx(tmp_path, "formula.xlsx", build))

        assert err == ""
        assert "=A2*B2" in text
        assert "1 formula cell(s) had no cached value" in text
        # 旧行为：这一格是空的，读起来像「没有数据」
        assert "| 3 | 100 |  |" not in text

    def test_a_literal_sheet_gets_no_formula_note(self, tmp_path):
        def build(wb):
            ws = wb.active
            ws.title = "字面量"
            ws.append(["区域", "营收"])
            ws.append(["华东", 1200])

        text, err = parse_file(_xlsx(tmp_path, "literal.xlsx", build))

        assert err == ""
        assert "formula" not in text
        assert "| 华东 | 1200 |" in text

    def test_a_formula_row_whose_cache_is_missing_is_not_dropped_as_empty(self, tmp_path):
        """整行都是公式且无缓存时，那一行不能因为「全是 None」被当成空行。"""
        def build(wb):
            ws = wb.active
            ws.title = "整行公式"
            ws.append(["甲", "乙"])
            ws.append(["=1+1", "=2+2"])

        text, err = parse_file(_xlsx(tmp_path, "rowformula.xlsx", build))

        assert err == ""
        assert "=1+1" in text and "=2+2" in text
