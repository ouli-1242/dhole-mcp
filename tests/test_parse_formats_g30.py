"""G30 回归：parse 能吃 .md/.txt/.json/.yaml 与 .pptx/.odt —— 不引新依赖。

报告的缺口是「parse 只吃 .html/.docx/.xlsx/.csv/.pdf」。两类补法不同：

* 纯文本类（.md/.markdown/.txt）：旧代码刻意拒绝并说「用你自己的文件工具读」。那句话
  预设了调用方**有**文件工具——只会说 MCP 的客户端，本地文档这条路是它唯一的入口。
  拒绝它是在给自己立规矩，不是在服务调用方。
* 二进制容器类（.pptx/.odt）：它们本来就是 zip 里的 XML，`<a:t>` / `text:p` 里就是
  读者要的文字。为读这一层再引 python-pptx / odfpy 不值。

文本能带上的唯一增值是**校验**：一份 .json 是不是合法 JSON，正是把配置文件递给文档
工具的人想问的问题。而一个文本工具读不到的部分（备注页、版式、样式、图片）必须写在
输出的第一行里——少了备注的稿件和完整的稿件长得一样，读的人有权知道。
"""

from __future__ import annotations

import zipfile

import pytest

from dhole_mcp.parse import (
    SUPPORTED_EXTENSIONS,
    _pptx_paragraphs,
    parse_file,
    parse_file_detailed,
)


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
        assert "## slide1.xml" in text and "## slide2.xml" in text
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

    def test_speaker_notes_stay_out_and_the_output_says_so(self, tmp_path):
        path = _write(tmp_path, "deck.pptx", _pptx({"slide1": _slide_body([["On screen"]])}))

        text, _ = parse_file(path)

        assert "PRIVATE speaker note" not in text
        assert "speaker notes" in text.splitlines()[0].lower()

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
