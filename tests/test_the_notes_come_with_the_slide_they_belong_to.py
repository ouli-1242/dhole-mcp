"""契约测试：`.pptx` 的讲者备注，以及 `<a:fld>` 布局字段不是正文。

两件事同一批，因为它们改的是同一个函数（`_pptx_paragraphs`）。

**备注**：幻灯片正文常常只有标题级短语，讲稿、数据来源与要点住在 `ppt/notesSlides/`
里 —— 而「总结这份 PPT」正是这颗工具最常见的用法。旧行为是**明确声明**「speaker
notes … not included」（不是静默丢弃，这点它一直做对了），但对这个任务来说，信息
密度更高的那一半被留在了门外。

判据有四条，每条都有理由：

* **挂在它自己那一页下面**。备注的关联是**按页**的（`ppt/slides/_rels/slideN.xml.rels`
  → `../notesSlides/notesSlideM.xml`），搬到文末单独一节就丢了「这条属于哪一页」，
  而那信息找不回来。
* **标成 `> Notes:`**。不标就和正文糊在一起，读的人会把讲者备注当幻灯片内容引用 ——
  那正是「看着对、其实错」。
* **没有备注的页不写占位符**（同 `.docx` 那条规矩：只在文件真有的时候才说）。
* **页序变了备注跟着走** —— 备注跟的是**配对**，不是位置。

**`<a:fld>`**：幻灯片编号/日期占位符里包着一个真的 `<a:t>`，所以它会被当成正文抽出来。
实测一份正文是「第三季度营收 / 机密」的幻灯片回的是「第三季度营收 / 3 / 机密」——
一个凭空多出来的数字，读起来像作者写的。这条与备注**无关**（幻灯片路径上本来就有），
但备注页每一页都带编号字段，所以过滤它是读备注的前提；分两次做等于把同一个函数改两遍。
"""

from __future__ import annotations

import re
import zipfile

from dhole_mcp.parse import parse_file

_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_RELS = "http://schemas.openxmlformats.org/package/2006/relationships"


def _fld(value: str, kind: str = "slidenum") -> str:
    """A layout placeholder — the shape PowerPoint writes for page numbers."""
    return (f'<a:p><a:fld id="{{{kind}-0000-0000-0000-000000000001}}" type="{kind}">'
            f"<a:t>{value}</a:t></a:fld></a:p>")


def _slide_xml(body: str, *, pagenum: str | None = None) -> str:
    extra = ""
    if pagenum is not None:
        extra = (f'<p:sp><p:nvSpPr><p:nvPr><p:ph type="sldNum"/></p:nvPr></p:nvSpPr>'
                 f"<p:txBody>{_fld(pagenum)}</p:txBody></p:sp>")
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<p:sld xmlns:a="{_A}" xmlns:p="{_P}"><p:cSld><p:spTree>'
            f'<p:sp><p:txBody><a:p><a:r><a:t>{body}</a:t></a:r></a:p></p:txBody></p:sp>'
            f"{extra}</p:spTree></p:cSld></p:sld>")


def _notes_xml(paragraphs: list[str], *, pagenum: str | None = None) -> str:
    body = "".join(f"<a:p><a:r><a:t>{p}</a:t></a:r></a:p>" for p in paragraphs)
    num = (f'<p:sp><p:nvSpPr><p:nvPr><p:ph type="sldNum"/></p:nvPr></p:nvSpPr>'
           f"<p:txBody>{_fld(pagenum)}</p:txBody></p:sp>") if pagenum else ""
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<p:notes xmlns:a="{_A}" xmlns:p="{_P}"><p:cSld><p:spTree>'
            f'<p:sp><p:nvSpPr><p:nvPr><p:ph type="body"/></p:nvPr></p:nvSpPr>'
            f"<p:txBody>{body}</p:txBody></p:sp>{num}</p:spTree></p:cSld></p:notes>")


def _deck(tmp_path, name: str, slides: dict[str, str], *,
          notes: dict[str, list[str]] | None = None,
          page_numbers: dict[str, str] | None = None,
          play_order: list[str] | None = None,
          notes_pagenum: bool = True) -> str:
    """A minimal .pptx with optional per-slide notes, built by hand."""
    notes = notes or {}
    page_numbers = page_numbers or {}
    parts = sorted(slides, key=lambda n: int(re.search(r"(\d+)", n).group(1)))
    play_order = play_order or parts
    rid_of = {part: f"rId{i + 1}" for i, part in enumerate(parts)}

    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as zf:
        listed = "".join(
            f'<p:sldId id="{256 + i}" r:id="{rid_of[part]}"/>'
            for i, part in enumerate(play_order))
        zf.writestr(
            "ppt/presentation.xml",
            f'<?xml version="1.0"?><p:presentation xmlns:p="{_P}" xmlns:r="{_R}">'
            f"<p:sldIdLst>{listed}</p:sldIdLst></p:presentation>")
        zf.writestr(
            "ppt/_rels/presentation.xml.rels",
            f'<Relationships xmlns="{_PKG_RELS}">'
            + "".join(f'<Relationship Id="{rid}" Type="{_R}/slide" '
                      f'Target="{part[len("ppt/"):]}"/>'
                      for part, rid in rid_of.items())
            + "</Relationships>")

        for i, part in enumerate(parts, 1):
            zf.writestr(part, _slide_xml(slides[part],
                                         pagenum=page_numbers.get(part)))
            if part not in notes:
                continue
            note_part = f"ppt/notesSlides/notesSlide{i}.xml"
            zf.writestr(note_part, _notes_xml(
                notes[part], pagenum=str(i) if notes_pagenum else None))
            zf.writestr(
                f"{part.rsplit('/', 1)[0]}/_rels/{part.rsplit('/', 1)[-1]}.rels",
                f'<Relationships xmlns="{_PKG_RELS}"><Relationship Id="rId9" '
                f'Type="{_R}/notesSlide" Target="../notesSlides/'
                f'{note_part.rsplit("/", 1)[-1]}"/></Relationships>')
    return str(path)


def _blocks(text: str) -> list[list[str]]:
    """每一节从自己的**正文第一行**起算，装着它自己的正文与备注。

    窗口从前是部件名（`## slide1.xml`）—— 那既能当节的地址也能当顺序的读数。
    小节标题改成「第几页」之后（R02-025 那一把），部件名不再出场，这里改按
    **每页自己的文字**取节：它同样指得出是哪一页，备注挂在哪儿这条判据一点没变松。
    """
    out: list[list[str]] = []
    for ln in text.splitlines():
        if ln.startswith("## "):
            out.append([])
        elif out and ln.strip():
            out[-1].append(ln.strip())
    return out


def _by_body(text: str) -> dict:
    return {b[0]: b for b in _blocks(text) if b}


class TestNotesComeWithTheirSlide:
    """备注读进来，挂在它自己那一页下面，并且标清楚它是备注。"""

    def test_a_notes_part_is_emitted_under_its_own_slide(self, tmp_path):
        path = _deck(tmp_path, "notes.pptx",
                     {"ppt/slides/slide1.xml": "第三季度营收",
                      "ppt/slides/slide2.xml": "成本结构"},
                     notes={"ppt/slides/slide1.xml": ["这里要强调华东区的增长"]})

        text, err = parse_file(path)

        assert err == ""
        blocks = _by_body(text)
        assert blocks["第三季度营收"] == [
            "第三季度营收", "> Notes: 这里要强调华东区的增长"]
        # 另一页没有备注，它的块里就只有正文
        assert blocks["成本结构"] == ["成本结构"]

    def test_notes_are_labeled_so_they_are_not_mistaken_for_slide_text(self, tmp_path):
        """不标就和正文糊在一起 —— 读的人会把讲者备注当幻灯片内容引用。"""
        path = _deck(tmp_path, "labeled.pptx",
                     {"ppt/slides/slide1.xml": "第三季度营收"},
                     notes={"ppt/slides/slide1.xml": ["讲者备注一句"]})

        text, _ = parse_file(path)

        assert "> Notes: 讲者备注一句" in text

    def test_multi_paragraph_notes_keep_their_line_breaks(self, tmp_path):
        path = _deck(tmp_path, "multi.pptx",
                     {"ppt/slides/slide1.xml": "标题"},
                     notes={"ppt/slides/slide1.xml":
                            ["要点一", "数据来源：Q3 财报第 12 页"]})

        text, _ = parse_file(path)

        assert "> Notes: 要点一" in text
        assert "> 数据来源：Q3 财报第 12 页" in text

    def test_a_slide_without_notes_gets_no_placeholder(self, tmp_path):
        """同 `.docx` 的规矩：只在文件真有的时候才说。"""
        path = _deck(tmp_path, "mixed.pptx",
                     {"ppt/slides/slide1.xml": "有备注",
                      "ppt/slides/slide2.xml": "没备注"},
                     notes={"ppt/slides/slide1.xml": ["一句"]})

        text, _ = parse_file(path)

        assert "> Notes:" in text
        assert text.count("> Notes:") == 1, "没备注的页不该写占位符"
        assert "(no notes)" not in text

    def test_notes_follow_their_slide_when_the_deck_is_reordered(self, tmp_path):
        """备注跟的是**配对**，不是位置 —— 页序变了它得跟着自己那一页走。"""
        path = _deck(tmp_path, "reordered.pptx",
                     {"ppt/slides/slide1.xml": "甲页",
                      "ppt/slides/slide2.xml": "乙页"},
                     notes={"ppt/slides/slide2.xml": ["乙页的备注"]},
                     play_order=["ppt/slides/slide2.xml", "ppt/slides/slide1.xml"])

        text, _ = parse_file(path)

        assert [b[0] for b in _blocks(text)] == ["乙页", "甲页"], "页序没生效"
        blocks = _by_body(text)
        assert blocks["乙页"] == ["乙页", "> Notes: 乙页的备注"]
        assert blocks["甲页"] == ["甲页"]

    def test_a_notes_part_with_no_authored_text_gets_no_block(self, tmp_path):
        """备注页存在但里面只有编号字段：那不是备注，不该凭空长出一个 `> Notes:`。"""
        path = _deck(tmp_path, "empty-notes.pptx",
                     {"ppt/slides/slide1.xml": "正文"},
                     notes={"ppt/slides/slide1.xml": []})

        text, err = parse_file(path)

        assert err == ""
        assert "> Notes:" not in text

    def test_a_dangling_notes_relationship_is_skipped(self, tmp_path):
        """rels 指向一个不存在的备注部件：跳过它，别让整份文件读不出来。"""
        path = tmp_path / "dangling-notes.pptx"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("ppt/slides/slide1.xml", _slide_xml("正文"))
            zf.writestr(
                "ppt/slides/_rels/slide1.xml.rels",
                f'<Relationships xmlns="{_PKG_RELS}"><Relationship Id="rId9" '
                f'Type="{_R}/notesSlide" Target="../notesSlides/gone.xml"/>'
                "</Relationships>")

        text, err = parse_file(str(path))

        assert err == ""
        assert "正文" in text
        assert "> Notes:" not in text


class TestALayoutFieldIsNotText:
    """`<a:fld>` 是版式占位，不是作者写的字。"""

    def test_a_slide_number_placeholder_is_not_emitted(self, tmp_path):
        path = _deck(tmp_path, "pagenum.pptx",
                     {"ppt/slides/slide1.xml": "第三季度营收"},
                     page_numbers={"ppt/slides/slide1.xml": "3"})

        text, err = parse_file(path)

        assert err == ""
        # 旧行为：正文是 "第三季度营收" + 一个凭空多出来的 "3"
        assert "3" not in text, f"页码占位符漏进了正文：{text!r}"
        assert "第三季度营收" in text

    def test_a_notes_slide_number_placeholder_is_not_emitted(self, tmp_path):
        """备注页每一页都带编号字段 —— 不过滤它，读备注就变成读一堆数字。"""
        path = _deck(tmp_path, "notes-pagenum.pptx",
                     {"ppt/slides/slide1.xml": "正文"},
                     notes={"ppt/slides/slide1.xml": ["备注一句"]})

        text, err = parse_file(path)

        assert err == ""
        assert text.count("> Notes: 备注一句") == 1
        assert "\n> 1\n" not in text, "备注页的编号字段漏了出来"

    def test_a_date_field_is_dropped_too(self, tmp_path):
        """不止编号：日期占位符是同一类东西。"""
        path = tmp_path / "datefield.pptx"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("ppt/slides/slide1.xml",
                        '<?xml version="1.0"?>'
                        f'<p:sld xmlns:a="{_A}" xmlns:p="{_P}"><p:cSld><p:spTree>'
                        f'<p:sp><p:txBody><a:p><a:r><a:t>季度回顾</a:t></a:r></a:p>'
                        f"<a:p>{_fld('2026-10-06', 'datetime')}</a:p>"
                        f"</p:txBody></p:sp></p:spTree></p:cSld></p:sld>")

        text, err = parse_file(str(path))

        assert err == ""
        assert "季度回顾" in text
        assert "2026-10-06" not in text

    def test_text_beside_a_field_survives(self, tmp_path):
        """过滤的是字段本身，不是它所在的段落里的其它文字。"""
        path = tmp_path / "beside.pptx"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("ppt/slides/slide1.xml",
                        '<?xml version="1.0"?>'
                        f'<p:sld xmlns:a="{_A}" xmlns:p="{_P}"><p:cSld><p:spTree>'
                        f'<p:sp><p:txBody><a:p><a:r><a:t>第 </a:t></a:r>'
                        f'<a:fld id="{{X}}" type="slidenum"><a:t>7</a:t></a:fld>'
                        f'<a:r><a:t> 页结束</a:t></a:r></a:p>'
                        f"</p:txBody></p:sp></p:spTree></p:cSld></p:sld>")

        text, err = parse_file(str(path))

        assert err == ""
        assert "第" in text and "页结束" in text
        assert "7" not in text


class TestTheFirstLineSaysWhatIsLeft:
    """第一行的职责是声明没带什么 —— 备注带上了就从那张单子上下来。"""

    def test_the_first_line_no_longer_lists_speaker_notes(self, tmp_path):
        path = _deck(tmp_path, "first.pptx", {"ppt/slides/slide1.xml": "正文"})

        text, _ = parse_file(path)

        first = text.splitlines()[0]
        assert first.startswith("[1 slide(s);")
        assert "speaker notes" not in first

    def test_the_first_line_still_lists_what_really_is_dropped(self, tmp_path):
        """把备注从清单上划掉，不能顺手把版式也划掉 —— 那才是真的还在丢。"""
        path = _deck(tmp_path, "still.pptx", {"ppt/slides/slide1.xml": "正文"})

        text, _ = parse_file(path)

        first = text.splitlines()[0]
        assert "layout and diagram text are not included" in first
