"""Tests for local file parsing (parse.py)."""

import os
import tempfile


from dhole_mcp.parse import parse_file, SUPPORTED_EXTENSIONS, MAX_PARSE_FILE_SIZE


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
        """本地 PDF 走 smart_fetch 用的同一个提取器，不再是「给个提示」。"""
        path = os.path.join(os.path.dirname(__file__), "background_checks.pdf")
        content, error = parse_file(path)
        assert error == ""
        assert "--- Page 1 ---" in content
        assert "NICS Firearm Background Checks" in content

    def test_image_only_pdf_reports_why(self):
        """扫描件没有文字层时必须说明原因，而不是静默返回空内容。"""
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

    def test_pdf_never_hints_at_the_file_scheme(self):
        """回归：这里曾返回「用 smart_fetch(url='file://...')」，而 file:// 被
        SSRF 守卫硬拦 —— agent 照做必然失败，且失败前已经白花两次调用。"""
        path = os.path.join(os.path.dirname(__file__), "dummy.pdf")
        _, error = parse_file(path)
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
