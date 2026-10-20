# -*- coding: utf-8 -*-
"""契约测试：`.pptx` 交回的小节标题要说**这是第几页**，不是说包里那个文件叫什么。

题面（台账 R02-025 的第一张脸，P02-06 高杉·S4）：`doc_parse('第一周课件.pptx')` 交回

    ## slide1.xml
    ## slide2.xml
    ## slide3.xml

她是拿这份输出往讲义里粘的 —— 粘进去的是一串包内文件名。这与同一条里 PDF 那格
`# untitled` 是同一件事的两个方向：**给用户看的正文里住着给工具看的名字**。
而这一发比 `# untitled` 更绕：`slide2.xml` 里的数字连"第几页"都不是（播放顺序住在
`ppt/presentation.xml` 的清单里，拖过页序的稿子里文件名与位置是两回事 —— 那是第 01 轮
那一刀修的洞，本文件不重开它，只把它的观察窗口换掉）。

判据：
1. 标题是位置（`## Slide 2 of 3`），整份输出里**一个 `.xml` 都没有**。
2. 位置这个词要说出它的根据：清单读不到而退回文件名时、清单只点名一部分时，
   第一行要说明这一段顺序是从哪儿来的 —— 标题声称"第几页"，根据就不能不说。
3. 顺序的证据不许变弱：每一页的正文跟着它自己的标题走（甲乙丙逐页对得上）。
4. 备注仍然挂在正确那一页下面（`> Notes:` 那一半本来就是好的，本刀不许弄坏它）。
5. 装配标题的作者只有一位（`## Slide` 在 parse.py 里只出现一次）。
"""

from __future__ import annotations

import re
import zipfile

from dhole_mcp.parse import parse_file

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
