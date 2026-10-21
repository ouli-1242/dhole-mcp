"""test_parse_2.py —— 由 4 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import zipfile
import pytest
from dhole_mcp.parse import SUPPORTED_EXTENSIONS, _pptx_paragraphs, parse_file, parse_file_detailed
import asyncio
import os
from dhole_mcp.server import MasterFetchServer
import inspect
import pathlib
from dhole_mcp import params
from dhole_mcp import server as server_mod
import re


# ============================================================================
# 来源：test_parse_formats_g30.py
# ============================================================================
# G30 回归：parse 能吃 .md/.txt/.json/.yaml 与 .pptx/.odt —— 不引新依赖。

def _write(tmp_path, name: str, data) -> str:
    path = tmp_path / name
    payload = data.encode("utf-8") if isinstance(data, str) else data
    path.write_bytes(payload)
    return str(path)


def _pptx(slides: dict[str, str]) -> bytes:
    """A minimal-but-real OOXML package: the parts this reader uses, named as
    PowerPoint names them (slide1.xml, slide2.xml, ...)."""
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("_rels/.rels", "<Relationships/>")
        for part, body in slides.items():
            zf.writestr(f"ppt/slides/{part}.xml",
                        '<?xml version="1.0"?><p:sld xmlns:a="http://schemas.'
                        'openxmlformats.org/drawingml/2006/main">'
                        f'<p:cSld><p:spTree>{body}</p:spTree></p:cSld></p:sld>')
        # A notes slide exists in real decks and must NOT pass for content.
        zf.writestr("ppt/notesSlides/notesSlide1.xml",
                    '<p:notes xmlns:a="..."><a:t>PRIVATE speaker note</a:t></p:notes>')
    return buf.getvalue()


def _slide_body(paragraphs: list[list[str]]) -> str:
    out = []
    for runs in paragraphs:
        out.append("<a:p>" + "".join(f"<a:r><a:t>{t}</a:t></a:r>" for t in runs) + "</a:p>")
    return "".join(out)


def _odt(body: str) -> bytes:
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", "application/vnd.oasis.opendocument.text")
        zf.writestr("content.xml",
                    '<?xml version="1.0"?>'
                    '<office:document-content '
                    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
                    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
                    f"<office:body><office:text>{body}</office:text></office:body>"
                    "</office:document-content>")
    return buf.getvalue()


class TestTextFormatsAreNotRefusedAnymore:

    def test_markdown_comes_back_as_itself(self, tmp_path):
        text, err = parse_file(_write(tmp_path, "notes.md",
                                       "# Title\n\n- item one\n- item two\n"))

        assert err == ""
        assert "# Title" in text and "- item one" in text

    def test_plain_txt_is_served_too(self, tmp_path):
        """旧 stance 拒绝它，理由是对自己有文件工具的调用方说的。"""
        text, err = parse_file(_write(tmp_path, "log.txt", "line one\nline two\n"))

        assert err == "" and "line two" in text

    def test_a_non_utf8_text_file_is_decoded_not_replaced(self, tmp_path):
        """和 .csv/.html 同一套字节级解码：这是把它们接进来的全部理由。"""
        path = _write(tmp_path, "gbk.md", b"# \xd6\xd0\xce\xc4\xbb\xf9\xd3\xda")

        text, err = parse_file(path)

        assert err == ""
        assert "\ufffd" not in text, "替换字符说明猜错了字符集，而输出看着像成功"
        assert "中文" in text

    def test_the_extension_list_is_the_one_the_code_uses(self):
        assert {".md", ".txt", ".json", ".yaml", ".pptx", ".odt"} <= SUPPORTED_EXTENSIONS


class TestStructuredTextIsValidatedNotJustEchoed:

    def test_valid_json_is_returned(self, tmp_path):
        text, err = parse_file(_write(tmp_path, "data.json", '{"a": [1, 2], "b": "c"}'))
        assert err == "" and '"a"' in text

    def test_invalid_json_is_an_error_with_the_complaint(self, tmp_path):
        text, err = parse_file(_write(tmp_path, "data.json", '{"a": [1, 2,,}'))

        assert text == ""
        assert "not valid JSON" in err, err

    def test_valid_yaml_is_returned(self, tmp_path):
        yaml = pytest.importorskip("yaml")  # noqa: F841
        text, err = parse_file(_write(tmp_path, "conf.yaml", "key: value\nlist:\n  - a\n"))
        assert err == "" and "key: value" in text

    def test_a_multi_document_yaml_is_not_judged_on_its_first_page(self, tmp_path):
        """safe_load 只看第一个文档；分隔符后面的坏文档会假装不存在。"""
        pytest.importorskip("yaml")
        bad = "a: 1\n---\nb: [unclosed\n"

        text, err = parse_file(_write(tmp_path, "multi.yaml", bad))

        assert text == "" and "not valid YAML" in err, err


class TestSlidesComeThroughWithoutAPowerPoint:

    def test_each_slide_becomes_its_own_section_in_part_order(self, tmp_path):
        path = _write(tmp_path, "deck.pptx", _pptx({
            "slide1": _slide_body([["First", " slide"], ["bullet A"]]),
            "slide2": _slide_body([["Second slide"]]),
        }))

        text, err = parse_file(path)

        assert err == ""
        # 小节标题是**位置**，不是包里的部件名（R02-025 那一把）：这一发的判据是
        # 「每页各成一节、按页序排」，用位置标题来读同样成立，还多锁住一件事 ——
        # 给人粘的正文里不许住着 `.xml`。
        assert "## Slide 1 of 2" in text and "## Slide 2 of 2" in text
        assert ".xml" not in text
        assert text.index("First slide") < text.index("Second slide")
        assert "bullet A" in text
        assert "2 slide(s)" in text.splitlines()[0]

    def test_slide_ten_does_not_sort_before_slide_two(self, tmp_path):
        """部件名按字典序排会得到 slide1, slide10, slide2 —— 页序会乱。"""
        path = _write(tmp_path, "deck.pptx", _pptx({
            f"slide{n}": _slide_body([[f"page {n}"]]) for n in (2, 10)
        }))

        text, _ = parse_file(path)

        assert text.index("page 2") < text.index("page 10")

    def test_a_notes_part_nothing_links_to_is_not_content(self, tmp_path):
        """备注只有被某一页的 rels 挂上，才属于那一页。

        这份夹具里躺着一个 `ppt/notesSlides/notesSlide1.xml`，但没有任何
        `ppt/slides/_rels/slideN.xml.rels` 指向它。真实的稿子里备注总是被挂上的，
        而一个孤儿部件不该被当成某一页的内容 —— 配对是唯一判据。
        """
        path = _write(tmp_path, "deck.pptx", _pptx({"slide1": _slide_body([["On screen"]])}))

        text, _ = parse_file(path)

        assert "PRIVATE speaker note" not in text
        assert "> Notes:" not in text

    def test_the_first_line_names_what_is_really_left_out(self, tmp_path):
        """第一行的职责是声明没带什么。

        备注现在**带上了**（挂在它自己那一页下面），所以从那张单子上下来；但不能顺手
        把版式也划掉 —— 那才是真的还在丢的东西。
        """
        path = _write(tmp_path, "deck.pptx", _pptx({"slide1": _slide_body([["On screen"]])}))

        text, _ = parse_file(path)

        first = text.splitlines()[0].lower()
        assert "layout" in first
        assert "speaker notes" not in first

    def test_xml_entities_are_decoded_back_to_text(self):
        runs = _pptx_paragraphs("<a:p><a:r><a:t>5 &lt; 7 &amp; 8 &gt; 2</a:t></a:r></a:p>")
        assert runs == ["5 < 7 & 8 > 2"]

    def test_a_package_without_slides_says_that_instead_of_failing(self, tmp_path):
        path = _write(tmp_path, "empty.pptx", _pptx({}))

        text, err = parse_file(path)

        assert "no slides" in text.lower(), text

    def test_a_slide_with_no_text_still_gets_its_heading(self, tmp_path):
        """图-only 的一页要在输出里看得见「这页没字」，而不是整页消失。"""
        path = _write(tmp_path, "d.pptx", _pptx({"slide1": "<a:graphicFrame/>"}))

        text, _ = parse_file(path)

        assert "(no text on this slide)" in text


class TestOpenDocumentText:

    def test_headings_and_paragraphs_become_markdown(self, tmp_path):
        body = ('<text:h text:outline-level="1">Chapter</text:h>'
                '<text:p>Body paragraph.</text:p>'
                '<text:p><text:span>Nested span text</text:span></text:p>')
        path = _write(tmp_path, "doc.odt", _odt(body))

        text, err = parse_file(path)

        assert err == ""
        assert "# Chapter" in text, text
        assert "Body paragraph." in text and "Nested span text" in text
        assert "styles, images and comments are not included" in text

    def test_a_deeper_outline_level_does_not_become_a_seven_hash_heading(self, tmp_path):
        body = '<text:h text:outline-level="9">Deep</text:h>'
        text, _ = parse_file(_write(tmp_path, "d.odt", _odt(body)))

        assert text.count("#") <= 7, text
        assert "Deep" in text

    def test_a_zip_that_is_not_an_odt_is_an_error_not_a_crash(self, tmp_path):
        import io

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("mimetype", "application/zip")
        path = _write(tmp_path, "fake.odt", buf.getvalue())

        text, err = parse_file(path)

        assert "no content.xml" in text.lower() or err

    def test_bogus_garbage_in_a_known_extension_is_reported(self, tmp_path):
        """不是 zip 的 .odt 要成为一句可读的错，而不是一路抛到工具外面。"""
        text, err = parse_file(_write(tmp_path, "broken.odt", b"not a zip at all"))

        assert text == "" and err
        assert "Parse error" in err or "zip" in err.lower()


class TestTheRefusalStillNamesWhatExists:

    def test_an_unknown_extension_lists_the_supported_set(self, tmp_path):
        _text, err = parse_file(_write(tmp_path, "archive.rar", b"x"))

        assert "Unsupported file type '.rar'" in err
        for ext in (".pptx", ".odt", ".md", ".json"):
            assert ext in err, err

    def test_detailed_results_keep_the_charset_metadata(self, tmp_path):
        content, err, extras = parse_file_detailed(
            _write(tmp_path, "note.md", "# t\n"))

        assert err == "" and content.startswith("# t")
        assert isinstance(extras, dict)


# ============================================================================
# 来源：test_a_directory_and_an_inert_parameter_say_what_they_are.py
# ============================================================================
# 「就是那个文件夹」与一颗没作用到身上的参数：两种沉默都要改成交回一句话。

def _parse(**kw):
    return asyncio.run(MasterFetchServer().doc_parse(**kw))


def _body(r) -> str:
    return "\n".join(r.content or [])


# ── 脸 1：目录被说成"没找到"──────────────────────────────────────────────

class TestADirectoryIsNamedAsADirectory:

    def test_a_folder_does_not_answer_file_not_found(self, tmp_path):
        d = tmp_path / "materials"
        d.mkdir()
        (d / "handout.pdf").write_bytes(b"%PDF-1.4\n")
        (d / "notes.docx").write_text("x", encoding="utf-8")

        r = _parse(file_path=str(d))

        assert r.error, "目录必须被拒绝，不许交回空正文"
        assert not r.error.startswith("File not found"), (
            f"目录明明在场，却被报成找不到：{r.error[:200]}")
        assert "directory" in r.error, r.error

    def test_the_answer_names_the_files_inside(self, tmp_path):
        d = tmp_path / "materials"
        d.mkdir()
        for name in ("a.docx", "b.pdf", "c.txt"):
            (d / name).write_text("x", encoding="utf-8")

        r = _parse(file_path=str(d))

        for name in ("a.docx", "b.pdf", "c.txt"):
            assert name in r.error, f"名字列不出来，调用方下一发还是只能猜：{r.error[:240]}"

    def test_the_count_is_the_directorys_own_arithmetic(self, tmp_path):
        """列 3 颗文件、另有 2 颗目录没列 —— 两类数都要说，且加起来对得上。"""
        d = tmp_path / "materials"
        d.mkdir()
        for name in ("a.docx", "b.pdf", "c.txt"):
            (d / name).write_text("x", encoding="utf-8")
        (d / "sub1").mkdir()
        (d / "sub2").mkdir()

        error = _parse(file_path=str(d)).error

        assert "3 file(s)" in error, error
        assert "2 subdirector" in error, f"两颗目录被摘走却没说：{error[:240]}"

    def test_the_listing_is_bounded_and_says_so(self, tmp_path):
        d = tmp_path / "materials"
        d.mkdir()
        for i in range(9):
            (d / f"file{i}.docx").write_text("x", encoding="utf-8")

        error = _parse(file_path=str(d)).error

        shown = [f"file{i}.docx" for i in range(9) if f"file{i}.docx" in error]
        assert 0 < len(shown) < 9, f"要么全列（大目录会把回执挤爆），要么一个没列：{error[:240]}"
        assert "9 file(s)" in error, f"截断了却没说总数：{error[:240]}"
        assert f"{len(shown)} listed" in error, error

    def test_a_folder_with_nothing_readable_says_that(self, tmp_path):
        d = tmp_path / "materials"
        d.mkdir()
        (d / "sub").mkdir()

        error = _parse(file_path=str(d)).error

        assert "no regular file" in error, error
        assert ".docx" not in error.split("no regular file")[-1], (
            "里面没有文件，就不许凭空点出文件名")

    def test_the_path_advice_is_not_repeated_for_a_resolved_path(self, tmp_path):
        """目录这一发不需要"换个绝对路径"：它已经解析成功了。"""
        d = tmp_path / "materials"
        d.mkdir()
        (d / "a.docx").write_text("x", encoding="utf-8")

        r = _parse(file_path=str(d))

        assert "Pass an absolute path" not in r.error, r.error
        assert "Pass an absolute path" not in r.next_action, r.next_action
        assert "directory" in r.next_action.lower(), r.next_action

    def test_the_missing_file_answer_is_untouched(self, tmp_path):
        """反面锁：相对那一发的两句出路一字不改（旧判据也钉着它）。

        原本这一格传的是 `str(tmp_path / "nope.pdf")` —— 那是**绝对**写法，于是它钉住的
        正是 R02-062 要修的那句错话（对已经绝对的路径建议「换成绝对形式」）。形状换回这一句
        本来面向的那一发（相对），绝对那一侧由
        `tests/test_an_absolute_path_is_not_advised_to_become_absolute.py` 接管。
        """
        r = _parse(file_path="r02-062-relative-lock.txt")

        assert r.error.startswith("File not found"), r.error
        assert "Pass an absolute path" in r.error
        assert "Path is a directory" not in r.error

    def test_the_machines_root_stays_masked_in_the_listing(self, tmp_path):
        home = os.path.expanduser("~")
        if not str(tmp_path).startswith(home):
            pytest.skip("tmp_path 不在主目录下，这一格量不到")
        d = tmp_path / "materials"
        d.mkdir()
        (d / "a.docx").write_text("x", encoding="utf-8")

        r = _parse(file_path=str(d))

        assert home not in r.error, r.error
        assert home not in (_body(r) or ""), r.error


# ── 脸 2：接受了却没作用的参数───────────────────────────────────────────

def _docx(tmp_path) -> str:
    docx = pytest.importorskip("docx")
    p = tmp_path / "guide.docx"
    d = docx.Document()
    d.add_heading("Week 1", level=1)
    d.add_paragraph("正文一句。")
    d.save(str(p))
    return str(p)


class TestAnInertParameterSaysItDidNothing:

    def test_pages_on_a_docx_is_declared_not_swallowed(self, tmp_path):
        p = _docx(tmp_path)
        plain, _, _ = parse_file_detailed(p)
        asked, _, _ = parse_file_detailed(p, pages="1-2")

        line = [ln for ln in asked.splitlines() if ln.startswith("> pages=")]
        assert line, f"pages 被收下却一字不报：{asked[:200]}"
        assert "did nothing" in line[0] and "no page concept" in line[0], line[0]
        assert "1-2" in line[0], f"要说清是哪一串被没用上：{line[0]}"
        assert plain != asked

    def test_declaring_the_parameter_changes_not_one_byte_of_content(self, tmp_path):
        """本刀的全部风险的对照面：那一行摘掉之后必须与不带参数时逐字节相同。"""
        p = _docx(tmp_path)
        plain, _, _ = parse_file_detailed(p)
        asked, _, _ = parse_file_detailed(p, pages="1-2")

        kept = "\n".join(ln for ln in asked.splitlines()
                         if not ln.startswith("> pages="))
        assert kept.strip() == plain.strip(), (
            "申报一行，正文不许动一行")

    def test_password_is_declared_and_never_echoed(self, tmp_path):
        p = _docx(tmp_path)
        asked, _, _ = parse_file_detailed(p, password="hunter2")

        line = [ln for ln in asked.splitlines() if ln.startswith("> password=")]
        assert line and "did nothing" in line[0], asked[:200]
        assert "hunter2" not in asked, "凭据不回显，报参数名就够了"

    def test_two_inert_parameters_get_two_sentences(self, tmp_path):
        """各说各的理由：一个是「没有页可切」，一个是「不需要密码」。"""
        p = _docx(tmp_path)
        asked, _, _ = parse_file_detailed(p, pages="1-2", password="x")

        assert len([ln for ln in asked.splitlines() if ln.startswith("> ")]) == 2
        pages_line = [ln for ln in asked.splitlines() if ln.startswith("> pages=")][0]
        pwd_line = [ln for ln in asked.splitlines() if ln.startswith("> password=")][0]
        assert "page" in pages_line and "password" not in pages_line
        assert "password" in pwd_line and "page concept" not in pwd_line

    def test_a_pdf_that_actually_used_pages_says_nothing_extra(self):
        """反面锁：参数真起作用时不许出现申报行。"""
        real = os.path.join(os.path.dirname(__file__), "dummy.pdf")
        if not os.path.exists(real):
            pytest.skip("需要仓库里的真 PDF")
        _, err, _ = parse_file_detailed(real)
        if err:
            pytest.skip(f"这台机器读不动 dummy.pdf：{err[:80]}")
        whole, _, _ = parse_file_detailed(real)

        assert not any(ln.startswith("> pages=") for ln in whole.splitlines())

    def test_no_parameter_no_line(self, tmp_path):
        p = _docx(tmp_path)
        plain, _, _ = parse_file_detailed(p)

        assert "> pages=" not in plain and "> password=" not in plain

    def test_the_formats_own_first_line_still_leads(self, tmp_path):
        """位置锁：docx 那颗「带不走的东西」首行不许被这一行挤下去。"""
        p = _docx(tmp_path)
        asked, _, _ = parse_file_detailed(p, pages="1-2")

        assert asked.splitlines()[0].startswith("[Word document;"), asked.splitlines()[:2]
        assert asked.splitlines()[1].startswith("> pages="), asked.splitlines()[:2]

    @pytest.mark.parametrize("name,body", (
        ("notes.txt", "just text\n"),
        ("rows.csv", "a,b\n1,2\n"),
        ("data.json", '{"a": 1}\n'),
    ))
    def test_any_format_without_pages_declares_it(self, tmp_path, name, body):
        p = tmp_path / name
        p.write_text(body, encoding="utf-8")
        plain, _, _ = parse_file_detailed(str(p))
        content, err, _ = parse_file_detailed(str(p), pages="1-2")

        assert err == ""
        assert any(ln.startswith("> pages=") for ln in content.splitlines()), content[:200]
        # 这类正文没有 `[格式自述]` 首行，申报行落在最前面，与正文之间隔一个空行（没有那行
        # 空行，markdown 会把整篇正文读成这一句引用的续行）。**按字节比**：申报行 + 空行之外，
        # 正文必须与不带参数时逐字节相同（连换行符都不许变）。
        line = [ln for ln in content.splitlines() if ln.startswith("> pages=")]
        assert len(line) == 1
        assert content == line[0] + "\n\n" + plain, (repr(content), repr(plain))

    def test_an_unreadable_page_spec_still_counts_as_inert(self, tmp_path):
        """pages="banana" 在 docx 上同样没作用 —— 不许静默收下第二种形状。"""
        p = _docx(tmp_path)
        content, _, _ = parse_file_detailed(p, pages="banana")

        line = [ln for ln in content.splitlines() if ln.startswith("> pages=")]
        assert line and "banana" in line[0], content[:200]


# ============================================================================
# 来源：test_a_legacy_office_file_and_a_locked_pdf_say_what_they_are.py
# ============================================================================
# 契约测试：旧版 Office 的 CFB 诊断、`.xlsm`/`.tsv`、本地 PDF 的 `pages=`/`password=`。

#: The OLE2 / Compound File Binary signature, then filler. `_sniff_cfb` reads
#: exactly the first eight bytes, so nothing after them has to be a valid CFB.
_CFB_HEADER = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512

#: A real, text-bearing PDF that ships with the suite. Re-paging or encrypting
#: it is cheaper — and more honest — than fabricating one with a PDF writer.
_BASE_PDF = pathlib.Path(__file__).resolve().parent / "background_checks.pdf"


def _cfb_file(tmp_path, name: str) -> str:
    path = tmp_path / name
    path.write_bytes(_CFB_HEADER)
    return str(path)


def _repage_pdf(tmp_path, name: str, times: int) -> str:
    """A real PDF whose page 1 is repeated `times` times — text on every page."""
    pypdf = pytest.importorskip("pypdf", reason="the [all] extra provides pypdf")
    reader = pypdf.PdfReader(str(_BASE_PDF))
    writer = pypdf.PdfWriter()
    for _ in range(times):
        writer.add_page(reader.pages[0])
    path = tmp_path / name
    with open(path, "wb") as fh:
        writer.write(fh)
    return str(path)


def _encrypted_pdf(tmp_path, name: str, password: str) -> str:
    pypdf = pytest.importorskip("pypdf", reason="the [all] extra provides pypdf")
    reader = pypdf.PdfReader(str(_BASE_PDF))
    writer = pypdf.PdfWriter()
    writer.add_page(reader.pages[0])
    writer.encrypt(password)
    path = tmp_path / name
    with open(path, "wb") as fh:
        writer.write(fh)
    return str(path)


def _call(name: str, args: dict) -> dict:
    """The dispatcher's own two-step: validate, then coerce types."""
    return params._coerce_arg_types(name, params._validate_tool_args(name, args))


class TestAnOle2FileIsNamedForWhatItIs:
    """CFB 容器按魔数命名，并给一条真能走通的路。"""

    @pytest.mark.parametrize("name,phrase,target", [
        ("old.doc", "Word 97-2003 (.doc)", "docx"),
        ("old.xls", "Excel 97-2003 (.xls)", "xlsx"),
        ("old.ppt", "PowerPoint 97-2003 (.ppt)", "pptx"),
    ])
    def test_each_legacy_family_names_itself_and_a_converter(
            self, tmp_path, name, phrase, target):
        text, err = parse_file(_cfb_file(tmp_path, name))

        assert text == "", "没有文本层，就不该伪造正文"
        assert phrase in err, err
        assert "OLE2/CFB" in err, err
        assert f"soffice --headless --convert-to {target}" in err, err
        # 旧行为：一句"改名成 .txt"。改扩展名不改字节，那条建议走不通。
        assert "rename it to .txt" not in err

    def test_a_cfb_file_renamed_to_docx_is_not_reported_as_a_missing_package(
            self, tmp_path):
        """最常见的自救尝试：把 .doc 改名成 .docx。

        旧行为是把它交给 python-docx，回一句 `PackageNotFoundError: Package not
        found at '...'` —— 不说真格式，也不说下一步。
        """
        text, err = parse_file(_cfb_file(tmp_path, "renamed.docx"))

        assert text == ""
        assert "PackageNotFoundError" not in err, "内建异常名不该是给调用方的答案"
        assert "named .docx but is a pre-2007 Word binary" in err, err

    def test_a_non_office_cfb_container_gets_the_generic_line(self, tmp_path):
        """`.msi` 也是 CFB，但它不是 Office 文档，不能被教"另存为 .docx"。"""
        _, err = parse_file(_cfb_file(tmp_path, "installer.msi"))

        assert "another CFB container (msi, msg, ...)" in err, err

    def test_a_real_zip_docx_is_not_caught_by_the_sniffer(self, tmp_path):
        """反向守卫：魔数检查只认 CFB，不许误伤一份正常的 zip 型 .docx。"""
        docx = pytest.importorskip("docx", reason="the [all] extra provides python-docx")
        doc = docx.Document()
        doc.add_paragraph("正文一句。")
        path = tmp_path / "real.docx"
        doc.save(str(path))

        text, err = parse_file(str(path))

        assert err == "", err
        assert "正文一句" in text


class TestTheNewExtensionsActuallyParse:
    """`.xlsm` / `.tsv` 不是被放行到一半再炸，而是真的读得出表。"""

    def test_a_macro_enabled_workbook_parses_like_a_plain_one(self, tmp_path):
        openpyxl = pytest.importorskip("openpyxl", reason="the [all] extra provides openpyxl")
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "宏表"
        ws.append(["产品", "数量"])
        ws.append(["甲", 3])
        path = tmp_path / "macro.xlsm"
        wb.save(str(path))

        text, err = parse_file(str(path))

        assert err == "", err
        assert "| 产品 | 数量 |" in text
        assert "| 甲 | 3 |" in text

    def test_a_tab_separated_file_parses_into_a_table(self, tmp_path):
        path = tmp_path / "data.tsv"
        path.write_text("区域\t营收\n华东\t1200\n华南\t980\n", encoding="utf-8")

        text, err = parse_file(str(path))

        assert err == "", err
        assert "| 区域 | 营收 |" in text
        assert "| 华东 | 1200 |" in text
        assert "\t" not in text, "分隔符应当被吃成表格，而不是留在正文里"

    def test_the_two_new_extensions_are_advertised_everywhere(self):
        """白名单、wire 属性描述、扩展名表三处同颗 —— 少一处就是"够不着"。"""
        assert ".xlsm" in SUPPORTED_EXTENSIONS
        assert ".tsv" in SUPPORTED_EXTENSIONS

        defs = {d["name"]: d for d in server_mod.MasterFetchServer._TOOL_DEFS}
        file_path = defs["doc_parse"]["inputSchema"]["properties"]["file_path"]
        desc = file_path.get("description", "")
        assert ".xlsm" in desc and ".tsv" in desc, desc


class TestALockedPdfCanBeOpened:
    """加密 PDF：正确的口令读得出，缺/错口令各自说清是哪一种。"""

    def test_the_right_password_returns_the_text(self, tmp_path):
        path = _encrypted_pdf(tmp_path, "locked.pdf", "s3cret")

        text, err = parse_file(str(path), password="s3cret")

        assert err == "", err
        assert len(text) > 1000, f"只读到 {len(text)} 字，口令路径没真的解出正文"

    def test_without_a_password_the_error_says_none_was_supplied(self, tmp_path):
        path = _encrypted_pdf(tmp_path, "locked.pdf", "s3cret")

        text, err = parse_file(str(path))

        assert text == ""
        assert "encrypted_pdf" in err
        assert "no password was supplied" in err, err

    def test_a_wrong_password_is_reported_as_rejected_not_as_corrupt(self, tmp_path):
        """错口令与"没给口令"是两件事：前者要说口令被拒，不能含糊成打不开。"""
        path = _encrypted_pdf(tmp_path, "locked.pdf", "s3cret")

        _, err = parse_file(str(path), password="not-it")

        assert "the supplied password was rejected" in err, err

    def test_the_password_the_error_asks_for_is_one_the_dispatcher_accepts(self):
        """这一条就是事故本身：报错教人传 `password`，而那颗参数原先不存在。

        四处必须同颗 —— 白名单、wire 属性、类型归一表、分发读法。少一处，
        「照报错说的做」就会变成 `TypeError: unexpected keyword argument`。
        """
        assert "password" in server_mod._TOP_LEVEL_ARGS["doc_parse"], "白名单没放行"
        assert "pages" in server_mod._TOP_LEVEL_ARGS["doc_parse"], "白名单没放行"

        defs = {d["name"]: d for d in server_mod.MasterFetchServer._TOOL_DEFS}
        props = defs["doc_parse"]["inputSchema"]["properties"]
        assert "password" in props and "pages" in props, "wire 上看不见这两颗参数"

        assert params._ARG_TYPES["doc_parse"]["password"] == "strnum"
        assert params._ARG_TYPES["doc_parse"]["pages"] == "strnum"

        source = inspect.getsource(server_mod.MasterFetchServer._dispatch)
        assert 'args.get("password"' in source, "分发器没有真的把 password 读出来"
        assert 'args.get("pages"' in source, "分发器没有真的把 pages 读出来"

        # 端到端：字符串客户端发来的两颗参数必须被读过去，而不是抛异常。
        coerced = _call("doc_parse",
                        {"file_path": "a.pdf", "pages": "1-2", "password": "x"})
        assert coerced["pages"] == "1-2" and coerced["password"] == "x"


class TestAPageRangeNarrowsALocalPdf:
    """本地 PDF 的 `pages=` 与 URL 路径同义：只收窄抽取量，不改变文件读取。"""

    def test_a_page_range_extracts_less_than_the_whole_file(self, tmp_path):
        path = _repage_pdf(tmp_path, "two.pdf", times=2)

        whole, err_all = parse_file(str(path))
        first, err_one = parse_file(str(path), pages="1")

        assert err_all == "" and err_one == "", (err_all, err_one)
        assert 0 < len(first) < len(whole), (
            f"pages='1' 收了 {len(first)} 字，整份 {len(whole)} 字 —— 范围没起作用")

    def test_page_one_is_about_half_of_a_two_page_document(self, tmp_path):
        """页 1 重复两次造出 2 页，所以"只取第 1 页"应当接近整份的一半。"""
        path = _repage_pdf(tmp_path, "two.pdf", times=2)

        whole, _ = parse_file(str(path))
        first, _ = parse_file(str(path), pages="1")

        ratio = len(first) / len(whole)
        assert 0.4 < ratio < 0.6, f"比例 {ratio:.3f} 偏离半份，范围可能被忽略或重复计"

    def test_the_range_reaches_the_server_layer_too(self, tmp_path):
        """走一遍 server 的 `doc_parse`，证明那颗参数端到端可达。"""
        path = _repage_pdf(tmp_path, "two.pdf", times=2)

        whole = asyncio.run(server_mod.MasterFetchServer().doc_parse(str(path)))
        first = asyncio.run(
            server_mod.MasterFetchServer().doc_parse(str(path), pages="1"))

        whole_chars = sum(len(c) for c in (whole.content or []))
        first_chars = sum(len(c) for c in (first.content or []))
        assert 0 < first_chars < whole_chars, (first_chars, whole_chars)


# ============================================================================
# 来源：test_a_slide_comes_out_where_the_deck_puts_it.py
# ============================================================================
# 契约测试：`.pptx` 的页序。

_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_RELS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _slide_xml(text: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<p:sld xmlns:a="{_A}" xmlns:p="{_P}"><p:cSld><p:spTree><p:sp><p:txBody>'
        f'<a:p><a:r><a:t>{text}</a:t></a:r></a:p>'
        '</p:txBody></p:sp></p:spTree></p:cSld></p:sld>')


def _deck(tmp_path, name: str, play_order: list[str], texts: dict[str, str], *,
          ids: list[int] | None = None, write_presentation: bool = True,
          write_rels: bool = True, dangling_ids: tuple[str, ...] = ()) -> str:
    """A minimal .pptx, built by hand: real enough for the reader, no python-pptx.

    `play_order` is the deck's order as part names. Parts are always numbered
    1..N inside the zip, so a shuffled `play_order` is exactly the shape
    PowerPoint writes when someone drags slides around.
    """
    parts = sorted(texts, key=lambda n: int(re.search(r"(\d+)", n).group(1)))
    rid_of = {part: f"rId{i + 1}" for i, part in enumerate(parts)}
    id_nums = ids or [256 + i for i in range(len(play_order))]

    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as zf:
        if write_presentation:
            listed = "".join(
                f'<p:sldId id="{n}" r:id="{rid_of[part]}"/>'
                for n, part in zip(id_nums, play_order) if part in rid_of)
            dangling = "".join(
                f'<p:sldId id="{900 + i}" r:id="{rid}"/>'
                for i, rid in enumerate(dangling_ids))
            zf.writestr(
                "ppt/presentation.xml",
                f'<?xml version="1.0"?><p:presentation xmlns:p="{_P}" xmlns:r="{_R}">'
                f"<p:sldIdLst>{listed}{dangling}</p:sldIdLst></p:presentation>")
        if write_rels:
            rels = "".join(
                f'<Relationship Id="{rid}" Type="{_R}/slide" '
                f'Target="{part[len("ppt/"):]}"/>'
                for part, rid in rid_of.items())
            zf.writestr("ppt/_rels/presentation.xml.rels",
                        f'<Relationships xmlns="{_PKG_RELS}">{rels}</Relationships>')
        for part, text in texts.items():
            zf.writestr(part, _slide_xml(text))
    return str(path)


def _pages(text: str) -> list[str]:
    """每一节的第一行正文，按它们出来的先后排 —— 这才是顺序的证据。

    （窗口换过一次：小节标题从前是包内文件名，那串名字正好能当顺序的读数；
    标题改成「第几页」之后（R02-025 那一把），文件名不再出现在输出里，
    顺序的证据改由每页自己的文字来作 —— 它同样指得出**是哪一页**排在了哪儿，
    不比旧窗口弱。判据没放宽，只是换了可观察量。）
    """
    out: list[str] = []
    seen = False
    for ln in text.splitlines():
        if ln.startswith("## "):
            seen = True
        elif seen and ln.strip() and not ln.startswith("["):
            out.append(ln.strip())
            seen = False
    return out


class TestTheDeckOrderWins:
    """有清单时，顺序由清单说了算 —— 文件名里的数字不是位置。"""

    def test_a_dragged_slide_comes_out_where_the_deck_puts_it(self, tmp_path):
        path = _deck(tmp_path, "swapped.pptx",
                     ["ppt/slides/slide2.xml", "ppt/slides/slide1.xml"],
                     {"ppt/slides/slide1.xml": "甲页",
                      "ppt/slides/slide2.xml": "乙页"})

        text, err = parse_file(path)

        assert err == ""
        assert _pages(text) == ["乙页", "甲页"]
        # 旧行为：按文件名数字，甲页（slide1）排在前
        assert text.index("乙页") < text.index("甲页")

    def test_a_shuffled_three_slide_deck_comes_out_in_play_order(self, tmp_path):
        path = _deck(tmp_path, "shuffled.pptx",
                     ["ppt/slides/slide3.xml", "ppt/slides/slide1.xml",
                      "ppt/slides/slide2.xml"],
                     {"ppt/slides/slide1.xml": "甲页",
                      "ppt/slides/slide2.xml": "乙页",
                      "ppt/slides/slide3.xml": "丙页"})

        text, err = parse_file(path)

        assert err == ""
        assert _pages(text) == ["丙页", "甲页", "乙页"]
        positions = [text.index(m) for m in ("丙页", "甲页", "乙页")]
        assert positions == sorted(positions)

    def test_the_order_is_the_list_order_not_the_id_numbers(self, tmp_path):
        """`id="257"` 是创建序，不是播放序。

        判据必须是 `<p:sldId>` 元素出现的先后。取 `id` 属性排会在这一份上给出相反的
        答案（id 递减而清单顺序是 2、1），所以这条不是同义反复。
        """
        path = _deck(tmp_path, "descending.pptx",
                     ["ppt/slides/slide2.xml", "ppt/slides/slide1.xml"],
                     {"ppt/slides/slide1.xml": "甲页",
                      "ppt/slides/slide2.xml": "乙页"},
                     ids=[257, 256])

        text, err = parse_file(path)

        assert err == ""
        assert _pages(text) == ["乙页", "甲页"], "取的是清单先后，不是 id 数字"

    def test_an_unreordered_deck_is_unchanged(self, tmp_path):
        """没拖过页序的稿子（绝大多数）输出与从前逐字相同。"""
        path = _deck(tmp_path, "plain.pptx",
                     ["ppt/slides/slide1.xml", "ppt/slides/slide2.xml"],
                     {"ppt/slides/slide1.xml": "甲页",
                      "ppt/slides/slide2.xml": "乙页"})

        text, err = parse_file(path)

        assert err == ""
        assert _pages(text) == ["甲页", "乙页"]


class TestTheFallbackNeverLosesASlide:
    """清单读不到时退回文件名顺序 —— 修顺序不能换来丢页。"""

    def test_no_presentation_part_falls_back_to_filename_order(self, tmp_path):
        path = _deck(tmp_path, "no-pres.pptx",
                     ["ppt/slides/slide2.xml", "ppt/slides/slide1.xml"],
                     {"ppt/slides/slide1.xml": "甲页",
                      "ppt/slides/slide2.xml": "乙页"},
                     write_presentation=False)

        text, err = parse_file(path)

        assert err == ""
        assert _pages(text) == ["甲页", "乙页"], "退回文件名数字"

    def test_missing_rels_falls_back_instead_of_dropping(self, tmp_path):
        """清单在、rels 不在：`r:id` 翻不成部件名，不许因此一页都不出。"""
        path = _deck(tmp_path, "no-rels.pptx",
                     ["ppt/slides/slide2.xml", "ppt/slides/slide1.xml"],
                     {"ppt/slides/slide1.xml": "甲页",
                      "ppt/slides/slide2.xml": "乙页"},
                     write_rels=False)

        text, err = parse_file(path)

        assert err == ""
        assert _pages(text) == ["甲页", "乙页"]

    def test_a_partial_id_list_keeps_every_slide(self, tmp_path):
        """清单只点名了一部分：点名的按它的顺序在前，其余按文件名补齐。"""
        path = _deck(tmp_path, "partial.pptx",
                     ["ppt/slides/slide2.xml"],
                     {"ppt/slides/slide1.xml": "甲页",
                      "ppt/slides/slide2.xml": "乙页"})

        text, err = parse_file(path)

        assert err == ""
        assert _pages(text) == ["乙页", "甲页"], "不许丢页"
        assert "甲页" in text and "乙页" in text

    def test_an_id_pointing_at_nothing_is_skipped_not_fatal(self, tmp_path):
        """`r:id` 指向一个不存在的部件（残缺包）：跳过它，别让整份文件读不出来。"""
        path = _deck(tmp_path, "dangling.pptx",
                     ["ppt/slides/slide1.xml"],
                     {"ppt/slides/slide1.xml": "甲页"},
                     dangling_ids=("rId99",))

        text, err = parse_file(path)

        assert err == ""
        assert _pages(text) == ["甲页"]


class TestTheOutputContractIsUnchanged:
    """顺序修了，输出的形状一个字没动。"""

    def test_the_first_line_still_declares_the_count_and_the_boundary(self, tmp_path):
        path = _deck(tmp_path, "three.pptx",
                     ["ppt/slides/slide3.xml", "ppt/slides/slide1.xml",
                      "ppt/slides/slide2.xml"],
                     {"ppt/slides/slide1.xml": "甲页",
                      "ppt/slides/slide2.xml": "乙页",
                      "ppt/slides/slide3.xml": "丙页"})

        text, _ = parse_file(path)

        first = text.splitlines()[0]
        assert first.startswith("[3 slide(s);")
        # 这句随讲者备注的落地改过口：备注不再属于「带不走」的那一类，从清单上划掉。
        assert "layout and diagram text are not included" in first
        assert "speaker notes" not in first

    def test_an_empty_slide_still_says_so(self, tmp_path):
        path = _deck(tmp_path, "empty-slide.pptx",
                     ["ppt/slides/slide1.xml"],
                     {"ppt/slides/slide1.xml": ""})

        text, err = parse_file(path)

        assert err == ""
        assert "(no text on this slide)" in text

    def test_a_pptx_with_no_slide_parts_says_so(self, tmp_path):
        path = tmp_path / "nothing.pptx"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("ppt/presentation.xml", "<p:presentation/>")

        text, err = parse_file(str(path))

        assert err == ""
        assert "no slides found" in text
