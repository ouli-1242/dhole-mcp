"""test_parse_3.py —— 由 5 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import base64
import pytest
from dhole_mcp.parse import parse_file
import os
import tempfile
from dhole_mcp.parse import SUPPORTED_EXTENSIONS, MAX_PARSE_FILE_SIZE
from pathlib import Path
from dhole_mcp.parse import parse_file_detailed
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
import re
import zipfile
import importlib
import pathlib
from dhole_mcp import charset as charset_mod
from dhole_mcp import parse as parse_mod


# ============================================================================
# 来源：test_a_docx_and_a_sheet_say_what_they_left_behind.py
# ============================================================================
# 契约测试：`.docx` 与 `.xlsx` 的输出。

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


# ============================================================================
# 来源：test_parse.py
# ============================================================================
# Tests for local file parsing (parse.py).

class TestParseFileBasic:
    """Basic parse_file behavior."""

    def test_empty_path(self):
        content, error = parse_file("")
        assert content == ""
        assert "required" in error

    def test_nonexistent_file(self):
        content, error = parse_file("/nonexistent/path/file.html")
        assert content == ""
        assert "not found" in error.lower() or "not found" in error

    def test_unsupported_extension(self):
        with tempfile.NamedTemporaryFile(suffix=".xyz", delete=False) as f:
            f.write(b"test")
            path = f.name
        try:
            content, error = parse_file(path)
            assert content == ""
            assert "Unsupported" in error
        finally:
            os.unlink(path)

    def test_an_unlisted_extension_points_at_the_next_step(self):
        """G30 之前 .txt/.md 是被**刻意**拒绝的，理由是「用你自己的文件工具直读」——
        那句话预设了调用方有文件工具，而只会说 MCP 的客户端没有：本地文档只有这一个入口。

        现在它们进了支持集。仍然要守的是这条的另一半：**每一种**拒绝都得给出下一步
        （点名支持集 + 文本文件改名的办法），否则 agent 读到的是「这个文件读不了」。
        """
        for suffix in (".log", ".py", ".rst"):
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
                f.write(b"hello")
                path = f.name
            try:
                content, error = parse_file(path)
            finally:
                os.unlink(path)
            assert content == ""
            assert "Unsupported" in error and suffix in error
            assert "Supported:" in error, f"{suffix} 的报错没有列出支持集"
            assert "rename it to .txt" in error, f"{suffix} 的报错缺少下一步"

    def test_text_formats_now_come_in_rather_than_being_turned_away(self):
        for suffix in (".txt", ".md"):
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
                f.write(b"hello there")
                path = f.name
            try:
                content, error = parse_file(path)
            finally:
                os.unlink(path)
            assert error == "", f"{suffix}: {error}"
            assert "hello there" in content

    def test_pdf_with_text_is_parsed(self):
        """本地 PDF 走 web_read 用的同一个提取器，不再是「给个提示」。"""
        path = os.path.join(os.path.dirname(__file__), "background_checks.pdf")
        content, error = parse_file(path)
        assert error == ""
        assert "--- Page 1 ---" in content
        assert "NICS Firearm Background Checks" in content

    def test_image_only_pdf_is_ocrd_not_just_explained(self):
        """装了 [all] 时，扫描件不该只回一句「没有文字层」。

        缺陷 2 之前：同一份字节走 URL（web_read）会自动 OCR，走本地（parse）
        却答「去装 [all]」——而 [all] 早就装好了。本文件下面那条
        `test_pdf_with_text_is_parsed` 说的「本地 PDF 走同一个提取器」对扫描件
        分支当时并不成立，这条钉住它成立。
        """
        path = os.path.join(os.path.dirname(__file__), "dummy.pdf")
        content, error = parse_file(path)
        assert error == ""
        assert "OCR-extracted from scanned PDF" in content
        assert content.count("# dummy") == 1, (
            "标题被 extractor 的头部与 _parse_pdf 各写一次 —— 同一份字节走 URL 只写一次")

    def test_image_only_pdf_still_reports_why_without_ocr(self, monkeypatch):
        """没有 OCR 附加包时，「必须说明原因、不许静默返回空内容」仍然成立。"""
        monkeypatch.setattr("dhole_mcp.ocr.ocr_available", lambda: False)
        path = os.path.join(os.path.dirname(__file__), "dummy.pdf")
        content, error = parse_file(path)
        assert content == ""
        assert "no extractable text" in error.lower() or "scanned" in error.lower()

    def test_malformed_pdf_does_not_raise(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-1.4")
            path = f.name
        try:
            content, error = parse_file(path)
            assert content == ""
            assert error
        finally:
            os.unlink(path)

    def test_pdf_never_hints_at_the_file_scheme(self, monkeypatch):
        """回归：这里曾返回「用 web_read(url='file://...')」，而 file:// 被
        SSRF 守卫硬拦 —— agent 照做必然失败，且失败前已经白花两次调用。

        缺陷 2 之后本机（装了 [all]）走 OCR 成功分支、`error` 是空串，只跑这一
        支的话断言**恒真**。那句提示真正出现的地方是没有 OCR 时的那条错误，所以
        两支都跑。
        """
        path = os.path.join(os.path.dirname(__file__), "dummy.pdf")
        _, error = parse_file(path)
        assert "file://" not in error
        monkeypatch.setattr("dhole_mcp.ocr.ocr_available", lambda: False)
        content, error = parse_file(path)
        assert content == "" and error, "没有 OCR 时必须给出原因，而不是空内容"
        assert "file://" not in error


class TestParseHtml:
    """HTML parsing."""

    def test_simple_html(self):
        html = "<html><body><h1>Title</h1><p>Hello world</p></body></html>"
        with tempfile.NamedTemporaryFile(suffix=".html", mode="w", delete=False, encoding="utf-8") as f:
            f.write(html)
            path = f.name
        try:
            content, error = parse_file(path)
            assert error == ""
            assert "Hello" in content or "Title" in content
        finally:
            os.unlink(path)

    def test_empty_html(self):
        with tempfile.NamedTemporaryFile(suffix=".html", mode="w", delete=False, encoding="utf-8") as f:
            f.write("")
            path = f.name
        try:
            content, error = parse_file(path)
            # Should not crash
            assert error == "" or content == ""
        finally:
            os.unlink(path)


class TestParseCsv:
    """CSV parsing."""

    def test_simple_csv(self):
        csv_content = "name,age\nAlice,30\nBob,25\n"
        with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", delete=False, encoding="utf-8") as f:
            f.write(csv_content)
            path = f.name
        try:
            content, error = parse_file(path)
            assert error == ""
            assert "Alice" in content
            assert "Bob" in content
            assert "|" in content  # markdown table
        finally:
            os.unlink(path)

    def test_empty_csv(self):
        with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", delete=False, encoding="utf-8") as f:
            f.write("")
            path = f.name
        try:
            content, error = parse_file(path)
            assert error == ""
            assert "empty" in content.lower()
        finally:
            os.unlink(path)


class TestParseFileSizeLimit:
    """File size limit enforcement."""

    def test_oversized_file_rejected(self):
        # Create a file that exceeds the limit (mock by checking the constant)
        assert MAX_PARSE_FILE_SIZE == 50 * 1024 * 1024

    def test_normal_file_accepted(self):
        csv_content = "a,b\n1,2\n"
        with tempfile.NamedTemporaryFile(suffix=".csv", mode="w", delete=False, encoding="utf-8") as f:
            f.write(csv_content)
            path = f.name
        try:
            content, error = parse_file(path)
            assert error == ""
        finally:
            os.unlink(path)


class TestParsePathSecurity:
    """Path traversal protection (tested at server level, but verify parse_file handles paths)."""

    def test_tilde_expansion(self):
        # parse_file should expand ~ without crashing
        content, error = parse_file("~/nonexistent_file_xyz.html")
        assert "not found" in error.lower() or "not found" in error

    def test_relative_path_resolution(self):
        # Relative paths should be resolved to absolute
        content, error = parse_file("relative/nonexistent.csv")
        assert "not found" in error.lower() or "not found" in error


class TestSupportedExtensions:
    """Verify supported extension set."""

    def test_expected_extensions(self):
        assert ".html" in SUPPORTED_EXTENSIONS
        assert ".htm" in SUPPORTED_EXTENSIONS
        assert ".xhtml" in SUPPORTED_EXTENSIONS
        assert ".docx" in SUPPORTED_EXTENSIONS
        assert ".xlsx" in SUPPORTED_EXTENSIONS
        assert ".csv" in SUPPORTED_EXTENSIONS
        assert ".pdf" in SUPPORTED_EXTENSIONS

    def test_unexpected_extensions(self):
        assert ".exe" not in SUPPORTED_EXTENSIONS
        assert ".py" not in SUPPORTED_EXTENSIONS
        # `.txt` used to be asserted absent: refusing plain text was a deliberate
        # stance until G30 (a text-only MCP client has no other way to a local
        # file). What must stay absent is the executable/source category.
        assert ".txt" in SUPPORTED_EXTENSIONS
        assert ".dll" not in SUPPORTED_EXTENSIONS


# ============================================================================
# 来源：test_a_pdfs_own_title_is_not_the_heading_of_its_outline.py
# ============================================================================
# 契约测试：PDF 的**信息字典**不许冒充这份文档的大纲顶行。

reportlab = pytest.importorskip("reportlab", reason="the [all] extra provides reportlab")


#: (text, font size, bold) laid out top-down; the body size is what the heading
#: detector measures against, so keep every "flat" fixture at one size.
def _pdf(tmp_path, name: str, lines, *, title: str | None = None,
         author: str | None = None) -> str:
    """Print a one-page PDF. `title=None` leaves reportlab's own default alone.

    That default is part of the shape being pinned: a document nobody set a title
    on still arrives with `Title: untitled` / `Author: anonymous` / `Subject:
    unspecified` in its info dictionary, so an empty field needs `setTitle('')`.
    """
    path = str(tmp_path / name)
    c = canvas.Canvas(path, pagesize=A4)
    if title is not None:
        c.setTitle(title)
    if author is not None:
        c.setAuthor(author)
    y = 275.0
    for text, size, bold in lines:
        c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
        c.drawString(20 * mm, y * mm, text)
        y -= size * 0.9
    c.showPage()
    c.save()
    return path


def _hash_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if ln.startswith("#")]


class TestTheFieldDoesNotHeadTheOutline:
    """页面自己长得出标题时，`# <信息字典的 Title>` 那一行是文件字段在冒充结构。"""

    def test_a_placeholder_title_is_not_printed_as_the_documents_heading(self, tmp_path):
        """台账那一发的形状：文件写着 untitled，页面印的是另一串。"""
        path = _pdf(tmp_path, "handout.pdf", [
            ("Week 1 reading guide", 22, True),
            ("Body text at the ordinary size of this page.", 11, False),
            ("A second body line so the body size is measured.", 11, False),
        ], title="untitled", author="anonymous")

        text, err = parse_file(path)

        assert err == ""
        assert "# untitled" not in text, "占位串不该占住大纲顶行"
        assert any(ln.endswith("Week 1 reading guide") for ln in _hash_lines(text)), \
            "页面自己印的那一行才是这份稿子的标题"
        assert "Title: untitled" in text, "撤掉那一行不等于把这个字段藏起来"

    def test_the_delivered_pages_still_carry_every_line_of_their_own(self, tmp_path):
        """撤的只是那一行 `#`，页面正文一行不少。"""
        path = _pdf(tmp_path, "prose.pdf", [
            ("Divisional Results", 18, True),
            ("First sentence of the body.", 11, False),
            ("Second sentence of the body.", 11, False),
        ], title="untitled")

        text, _ = parse_file(path)

        assert "First sentence of the body." in text
        assert "Second sentence of the body." in text
        assert text.count("# untitled") == 0

    def test_the_title_is_still_a_field_after_the_line_goes(self, tmp_path):
        """字段的账照旧：`title=` / `metadata.title=` 都还在原地。"""
        path = _pdf(tmp_path, "field.pdf", [
            ("A printed heading on the page", 20, True),
            ("Body line one.", 11, False),
            ("Body line two.", 11, False),
        ], title="untitled")

        content, err, extras = parse_file_detailed(path)

        assert err == ""
        assert extras["metadata"]["title"] == "untitled"
        assert "# untitled" not in content

    def test_a_title_the_pages_print_too_is_not_showed_twice(self, tmp_path):
        """页面印的标题与信息字典同一串：大纲里已经看得见，不再另印一遍字段行。"""
        path = _pdf(tmp_path, "matching.pdf", [
            ("Quarterly Revenue Review", 22, True),
            ("Body text at eleven points, the ordinary size.", 11, False),
            ("A second paragraph line so the body size is measured.", 11, False),
        ], title="Quarterly Revenue Review")

        text, _ = parse_file(path)

        assert text.count("Quarterly Revenue Review") == 1, (
            "同一串只在它该在的地方出现一次")
        assert "Title: Quarterly Revenue Review" not in text

    def test_the_real_fixture_shape_comes_back_without_the_placeholder_heading(self, tmp_path):
        """仓库里那一发真稿子（ReportLab 造的），钉的是它交回的**第一行不再是占位串**。"""
        path = Path(__file__).resolve().parents[1] / "review/.runtime/p06/week1-handout.pdf"
        if not path.exists():
            pytest.skip("persona fixture not on this machine")

        text, err = parse_file(str(path))

        assert err == ""
        assert text.splitlines()[0] != "# untitled"
        assert "Title: untitled" in text
        assert "## Week 1 reading guide" in text, "页面自己长出来的那一行原样在"


class TestTheOnlyTitleStaysWhereItIsNeeded:
    """带不出大纲的稿子：`# <Title>` 是交回的唯一标题，撤掉它就是少给一份标题。"""

    def test_a_document_with_no_heading_of_its_own_keeps_the_field_as_its_title(self, tmp_path):
        path = _pdf(tmp_path, "flat.pdf", [
            ("Only body text on this page, one size throughout.", 11, False),
            ("A second line at the very same size.", 11, False),
        ], title="A claim from the file")

        text, _ = parse_file(path)

        assert text.splitlines()[0] == "# A claim from the file"
        assert "Title: A claim from the file" not in text, "同一串不许印两遍"

    def test_the_single_title_line_is_one_line_not_two(self, tmp_path):
        """`tests/test_v17_fix_plan.py` 那条「标题只出现一次」的锁在新判据下照旧成立。"""
        path = _pdf(tmp_path, "once.pdf", [
            ("Nothing here is bigger than the body.", 11, False),
            ("So the outline cannot be measured from these pages.", 11, False),
        ], title="Quarterly Report 2026")

        text, _ = parse_file(path)

        assert text.count("# Quarterly Report 2026") == 1

    def test_an_empty_title_prints_neither_line(self, tmp_path):
        path = _pdf(tmp_path, "notitle.pdf", [
            ("Body text only, and no Title field in the file.", 11, False),
            ("Another body line.", 11, False),
        ], title="", author="")

        text, _ = parse_file(path)

        assert "Title:" not in text
        assert not text.startswith("#")


class TestTheWindowIsJudgedOnWhatTheWindowCarries:
    """判据问的是「这一发交回的稿子有没有自己的大纲」，不是整份文件有没有。"""

    def test_a_window_with_no_heading_in_it_still_shows_the_files_title(self, tmp_path):
        path = str(tmp_path / "two_page.pdf")
        c = canvas.Canvas(path, pagesize=A4)
        c.setTitle("Back Matter Note")
        c.setFont("Helvetica", 11)
        c.drawString(20 * mm, 270 * mm, "Page one is plain body text at one size.")
        c.drawString(20 * mm, 260 * mm, "Nothing here can be measured as a heading.")
        c.showPage()
        c.setFont("Helvetica-Bold", 22)
        c.drawString(20 * mm, 270 * mm, "A Real Heading On Page Two")
        c.setFont("Helvetica", 11)
        c.drawString(20 * mm, 260 * mm, "Body text under it.")
        c.showPage()
        c.save()

        one, err = parse_file(path, pages="1")
        whole, _ = parse_file(path)

        assert err == ""
        assert one.splitlines()[0] == "# Back Matter Note", \
            "这一发交回的部分没有自己的大纲，标题得留着"
        assert any(ln.endswith("A Real Heading On Page Two") for ln in _hash_lines(whole))
        assert "# Back Matter Note" not in whole, \
            "整份交回时页面自己有标题，字段那一行就只是文件的自述"


# ============================================================================
# 来源：test_a_slide_is_numbered_where_the_deck_plays_it.py
# ============================================================================
# 契约测试：`.pptx` 交回的小节标题要说**这是第几页**，不是说包里那个文件叫什么。

_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_RELS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _slide_xml(text: str, notes: str = "") -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<p:sld xmlns:a="{_A}" xmlns:p="{_P}"><p:cSld><p:spTree><p:sp><p:txBody>'
        f'<a:p><a:r><a:t>{text}</a:t></a:r></a:p>'
        '</p:txBody></p:sp></p:spTree></p:cSld></p:sld>')


def _deck(tmp_path, name: str, play_order: list[str], texts: dict[str, str], *,
          write_presentation: bool = True, write_rels: bool = True,
          notes: dict[str, str] | None = None) -> str:
    """手工造一份最小 .pptx（与既有那把顺序锁同一构型，不引 python-pptx）。"""
    parts = sorted(texts, key=lambda n: int(re.search(r"(\d+)", n).group(1)))
    rid_of = {part: f"rId{i + 1}" for i, part in enumerate(parts)}
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as zf:
        if write_presentation:
            listed = "".join(
                f'<p:sldId id="{256 + i}" r:id="{rid_of[part]}"/>'
                for i, part in enumerate(play_order) if part in rid_of)
            zf.writestr(
                "ppt/presentation.xml",
                f'<?xml version="1.0"?><p:presentation xmlns:p="{_P}" xmlns:r="{_R}">'
                f"<p:sldIdLst>{listed}</p:sldIdLst></p:presentation>")
        if write_rels:
            rels = "".join(
                f'<Relationship Id="{rid}" Type="{_R}/slide" '
                f'Target="{part[len("ppt/"):]}"/>'
                for part, rid in rid_of.items())
            zf.writestr("ppt/_rels/presentation.xml.rels",
                        f'<Relationships xmlns="{_PKG_RELS}">{rels}</Relationships>')
        for part, text in texts.items():
            zf.writestr(part, _slide_xml(text))
        for part, note in (notes or {}).items():
            num = re.search(r"(\d+)", part).group(1)
            zf.writestr(f"ppt/notesSlides/notesSlide{num}.xml", _slide_xml(note))
            zf.writestr(f"ppt/slides/_rels/slide{num}.xml.rels",
                        f'<Relationships xmlns="{_PKG_RELS}">'
                        f'<Relationship Id="rIdN" Type="{_R}/notesSlide" '
                        f'Target="../notesSlides/notesSlide{num}.xml"/></Relationships>')
            zf.writestr(f"ppt/notesSlides/_rels/notesSlide{num}.xml.rels",
                        f'<Relationships xmlns="{_PKG_RELS}"/>')
    return str(path)


def _sections(text: str) -> list[tuple[str, str]]:
    """[(标题, 该标题下第一行正文), …] —— 位置与内容必须一起读才叫证据。"""
    out: list[tuple[str, str]] = []
    for ln in text.splitlines():
        if ln.startswith("## "):
            out.append((ln[3:], ""))
        elif out and not out[-1][1] and ln.strip():
            out[-1] = (out[-1][0], ln.strip())
    return out


SWAPPED = dict(
    play_order=["ppt/slides/slide2.xml", "ppt/slides/slide1.xml"],
    texts={"ppt/slides/slide1.xml": "甲页", "ppt/slides/slide2.xml": "乙页"})
THREE = dict(
    play_order=["ppt/slides/slide3.xml", "ppt/slides/slide1.xml",
                "ppt/slides/slide2.xml"],
    texts={"ppt/slides/slide1.xml": "甲页", "ppt/slides/slide2.xml": "乙页",
           "ppt/slides/slide3.xml": "丙页"})


class TestTheLabelIsAPosition:

    def test_a_slide_is_labelled_by_position_not_by_its_part_name(self, tmp_path):
        path = _deck(tmp_path, "three.pptx", **THREE)
        text, err = parse_file(path)
        assert err == ""
        heads = [h for h, _ in _sections(text)]
        assert heads == ["Slide 1 of 3", "Slide 2 of 3", "Slide 3 of 3"], heads

    def test_no_package_filename_rides_along_anywhere(self, tmp_path):
        """题面那一格的全部危害就在这儿：`.xml` 不该出现在给人粘的正文里。"""
        path = _deck(tmp_path, "three.pptx", **THREE)
        text, _ = parse_file(path)
        assert ".xml" not in text, [ln for ln in text.splitlines() if ".xml" in ln]
        assert not re.search(r"slide\d+\.xml", text)

    def test_the_position_and_that_pages_text_travel_together(self, tmp_path):
        """顺序的证据不许因为换标签而变弱：位置与正文逐页对得上。"""
        path = _deck(tmp_path, "three.pptx", **THREE)
        text, _ = parse_file(path)
        assert [body for _, body in _sections(text)] == ["丙页", "甲页", "乙页"]

    def test_a_dragged_deck_is_numbered_from_one_with_no_gaps(self, tmp_path):
        path = _deck(tmp_path, "swapped.pptx", **SWAPPED)
        text, _ = parse_file(path)
        assert [h for h, _ in _sections(text)] == ["Slide 1 of 2", "Slide 2 of 2"]

    def test_the_notes_stay_under_the_slide_they_belong_to(self, tmp_path):
        """`> Notes:` 那一半本来就是对的，本刀不许把它挤到别的位置下。"""
        path = _deck(tmp_path, "noted.pptx",
                     play_order=["ppt/slides/slide2.xml", "ppt/slides/slide1.xml"],
                     texts={"ppt/slides/slide1.xml": "甲页",
                            "ppt/slides/slide2.xml": "乙页"},
                     notes={"ppt/slides/slide2.xml": "这一页的讲稿"})
        text, err = parse_file(path)
        assert err == ""
        secs = _sections(text)
        assert secs[0][0] == "Slide 1 of 2" and secs[0][1] == "乙页", secs
        assert "> Notes: 这一页的讲稿" in text
        assert text.index("> Notes: 这一页的讲稿") < text.index("甲页"), text


class TestThePositionNamesItsBasis:
    """标题声称"第几页"，那"这一串顺序从哪儿来"就不能是读者要猜的事。"""

    def test_a_deck_without_a_readable_list_says_the_order_is_by_file_name(self, tmp_path):
        path = _deck(tmp_path, "no-pres.pptx",
                     play_order=["ppt/slides/slide2.xml", "ppt/slides/slide1.xml"],
                     texts={"ppt/slides/slide1.xml": "甲页",
                            "ppt/slides/slide2.xml": "乙页"},
                     write_presentation=False)
        text, err = parse_file(path)
        assert err == ""
        first = text.splitlines()[0]
        assert "file name" in first, first
        assert [h for h, _ in _sections(text)] == ["Slide 1 of 2", "Slide 2 of 2"]
        assert [body for _, body in _sections(text)] == ["甲页", "乙页"]

    def test_a_partial_list_says_how_much_it_named(self, tmp_path):
        """清单只点名了一部分：位置仍是我们的输出序，而那段是从文件名补齐的 —— 要说。"""
        path = _deck(tmp_path, "partial.pptx",
                     play_order=["ppt/slides/slide2.xml"],
                     texts={"ppt/slides/slide1.xml": "甲页",
                            "ppt/slides/slide2.xml": "乙页"})
        text, err = parse_file(path)
        assert err == ""
        first = text.splitlines()[0]
        assert "named 1 of 2" in first, first
        assert [body for _, body in _sections(text)] == ["乙页", "甲页"]

    def test_a_readable_list_claims_nothing_about_fallback(self, tmp_path):
        """有清单时不许带上那句退路话（否则读者永远不知道这次到底是谁说了算）。"""
        path = _deck(tmp_path, "plain.pptx", **THREE)
        text, _ = parse_file(path)
        assert "file name" not in text.splitlines()[0]


class TestOneAuthorAndTheShape:

    def test_the_heading_is_minted_in_one_place(self):
        import inspect

        import dhole_mcp.parse as mod
        assert inspect.getsource(mod).count('f"\\n## Slide') == 1

    def test_the_first_line_still_declares_the_count_and_the_boundary(self, tmp_path):
        """既有那一句「N slide(s); text only - …」是本刀之外的一位作者，逐字不许动。"""
        path = _deck(tmp_path, "three.pptx", **THREE)
        text, _ = parse_file(path)
        first = text.splitlines()[0]
        assert first.startswith("[3 slide(s);")
        assert "layout and diagram text are not included" in first

    def test_the_manual_says_slide_sections_are_labelled_by_position(self):
        from dhole_mcp import guide
        corpus = "\n".join(p.read_text(encoding="utf-8")
                           for p in guide._ROOT.rglob("*.md"))
        # 手册要说得出两件事：标题是位置，以及位置说不清时第一行会交代根据。
        assert "headed by **where it sits**" in corpus
        assert "no readable slide list" in corpus


# ============================================================================
# 来源：test_a_missing_dependency_claim_must_name_the_package_that_is_missing.py
# ============================================================================
# 「你缺依赖」这句话只能用在真的缺依赖上。

@pytest.fixture
def txt_file():
    d = pathlib.Path(tempfile.mkdtemp(prefix="dhole-r02-040-"))
    f = d / "note.txt"
    f.write_text("hello 正文 here\nsecond line\n", encoding="utf-8")
    return str(f)


@pytest.fixture
def half_reloaded_charset():
    """半重载的确定形状：模块在 sys.modules 里，但那颗符号没了。

    （新进程里 `from .charset import X` 撞上的是同一句 ImportError —— 区别只在谁把
    邻居留下了旧版本，本文件不演那段历史，只钉这一句报告。）
    """
    saved = getattr(charset_mod, "contested_rival")
    del charset_mod.contested_rival
    try:
        yield
    finally:
        charset_mod.contested_rival = saved
        importlib.reload(charset_mod)


def test_an_internal_import_failure_is_not_reported_as_a_missing_dependency(
        txt_file, half_reloaded_charset):
    _content, err, _extras = parse_file_detailed(txt_file)
    assert err, "内部 import 失败被吞掉了"
    assert "Missing dependency" not in err, err
    assert "dhole_mcp.charset" in err, f"报告没点名是哪颗模块失败：{err!r}"
    assert "contested_rival" in err, f"报告没点名是哪颗符号：{err!r}"
    assert "pip install" not in err and "install" not in err.lower(), (
        f"没有东西缺装，却把人支去装包：{err!r}")


def test_the_two_sentences_never_disagree(txt_file, half_reloaded_charset):
    """家族守卫：因果词不许超出这一发知道的。"""
    _content, err, _extras = parse_file_detailed(txt_file)
    lowered = err.lower()
    assert not ("missing dependency" in lowered and "not a missing dependency" in lowered), err


def test_the_verdict_positions_do_not_move(txt_file, half_reloaded_charset):
    content, err, _extras = parse_file_detailed(txt_file)
    assert err and content == "", (content[:40], err[:80])
    assert content == "", "内部失败被改记成成功就是弱化门禁"


def test_a_genuinely_missing_package_is_named_and_priced(monkeypatch, txt_file):
    """反面锁：真缺依赖那一支不许被本刀删哑 —— 点名缺的那颗，并给装它的命令。"""
    def _raise(_path):
        raise ModuleNotFoundError("No module named 'dhole_absent_pkg_xyz'",
                                  name="dhole_absent_pkg_xyz")

    xlsx = pathlib.Path(tempfile.mkdtemp(prefix="dhole-r02-040-b-")) / "book.xlsx"
    xlsx.write_bytes(b"PK\x03\x04not really a workbook")
    monkeypatch.setattr(parse_mod, "_parse_xlsx", _raise)
    _content, err, _extras = parse_file_detailed(str(xlsx))
    assert "dhole_absent_pkg_xyz" in err, f"缺的那颗包没被点名：{err!r}"
    assert "cannot be imported" in err, err
    assert "pip install" in err, f"真缺包时不给装法：{err!r}"
    assert "docx" not in err, f"一句 .xlsx 的建议里冒出了 python-docx：{err!r}"


def test_the_package_name_is_read_out_of_the_message_when_name_is_empty(
        monkeypatch, tmp_path):
    """`e.name` 缺席、只有消息的那一发（`tests/test_install_hints.py` 的 import
    hook 就是这个形状）—— 包名仍要读得出来，装它的命令仍要在。"""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.split(".")[0] == "docx":
            raise ModuleNotFoundError("No module named 'docx'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    target = tmp_path / "sample.docx"
    target.write_bytes(b"not a real docx")
    _content, err, _extras = parse_file_detailed(str(target))
    assert "docx" in err, err
    assert "pip install dhole-mcp[all]" in err, err
    assert "not a missing dependency" not in err, err


def test_an_import_inside_an_installed_package_is_not_called_a_missing_dependency(
        monkeypatch, txt_file):
    """第三种形状：包在盘上，可它里面的 import 解不开（版本不匹配）。

    旧代码在这里交出的正是那两句自话 —— 「Missing dependency」+「python-docx is
    installed - the parse failure was not a missing dependency」。
    """
    def _raise(_path):
        raise ImportError("cannot import name 'Document' from 'docx' (D:\\x\\docx\\__init__.py)",
                          name="docx")

    docx_file = pathlib.Path(tempfile.mkdtemp(prefix="dhole-r02-040-c-")) / "a.docx"
    docx_file.write_bytes(b"PK\x03\x04not really a document")
    monkeypatch.setattr(parse_mod, "_parse_docx", _raise)
    _content, err, _extras = parse_file_detailed(str(docx_file))
    assert "docx" in err, err
    assert "Missing dependency" not in err, err
    assert "not a missing dependency" not in err, err
    assert "pip install" not in err, f"盘上就有的东西不许叫人去装：{err!r}"


def test_an_import_failure_that_names_no_module_claims_no_package(monkeypatch, txt_file):
    """`e.name` 是空的时候，这句话就没有资格点任何一颗包的名。"""
    def _raise(_path):
        raise ImportError("a bare import failure with no name at all")

    xlsx = pathlib.Path(tempfile.mkdtemp(prefix="dhole-r02-040-d-")) / "book.xlsx"
    xlsx.write_bytes(b"PK\x03\x04not really a workbook")
    monkeypatch.setattr(parse_mod, "_parse_xlsx", _raise)
    _content, err, _extras = parse_file_detailed(str(xlsx))
    assert "Missing dependency" not in err, err
    assert "pip install" not in err, err
    assert "names no module" in err, err


def test_the_name_is_read_from_the_exception_itself():
    """`_failed_import_name` 只搬异常自己给出的名字：`e.name` 优先，其次消息里的
    "No module named 'x'"，两头都没有就是 ""。"""
    from dhole_mcp.parse import _failed_import_name

    assert _failed_import_name(
        ModuleNotFoundError("No module named 'a.b'", name="a.b")) == "a.b"
    assert _failed_import_name(
        ModuleNotFoundError("No module named 'docx'")) == "docx"
    assert _failed_import_name(ImportError(
        "cannot import name 'Document' from 'docx' (D:\\x\\docx\\__init__.py)",
        name="docx")) == "docx"
    # 只有消息、没有 e.name 的那一发（真实 Python 会带上 name，量具造的 hook 不会）：
    # 两句消息里都写着模块名，读得出来就不许退回"点不出包名"那一支。
    assert _failed_import_name(ImportError(
        "cannot import name 'Document' from 'docx' (D:\\x\\docx\\__init__.py)")) == "docx"
    assert _failed_import_name(ImportError(
        "cannot import name 'X' from 'dhole_mcp.charset' (p)")) == "dhole_mcp.charset"
    assert _failed_import_name(ImportError("nothing identifiable here")) == ""
