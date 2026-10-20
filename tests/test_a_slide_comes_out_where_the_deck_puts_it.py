"""契约测试：`.pptx` 的页序。

`slide1.xml` / `slide2.xml` 里的数字是**文件名**，不是**位置**。真正决定播放顺序的
是 `ppt/presentation.xml` 里那张 `p:sldIdLst` 清单（`<p:sldId r:id="rIdN"/>` 按先后
排列），再经 `ppt/_rels/presentation.xml.rels` 把每个 `r:id` 翻成部件名。在 PowerPoint
里拖动一页，改的是那张清单，**文件名一个都不动**。

旧实现按文件名里的数字排序，于是拖过页序的稿子读出来顺序是错的 —— 而错得很安静：
内容一行不少、第一行的「N slide(s)」也对，调用方**没有任何办法**从输出里看出顺序
错了。一份页序错乱的摘要是错的摘要，读起来和一份正常的摘要一模一样；这正是本项目
当作比报错更糟的那一类失败。

实测（手工造的稿子，清单里明写 slide2 在前）：

    旧：## slide1.xml / ## slide2.xml      ← 反了，零提示
    新：## slide2.xml / ## slide1.xml

（后来小节标题改成了「第几页」（R02-025 那一把：包内文件名是给人看的正文里的
机器面向噪声），顺序的证据因此改读每页自己的文字，见 `_pages` 那段。）

判据落在**两件事**上，缺一不可：① 有清单时按清单排；② 清单读不到（残缺包、第三方
导出器只写一半）时**退回文件名顺序而不是丢页** —— 修一个"顺序错"不能换来一个"少几页"。
"""

from __future__ import annotations

import re
import zipfile

from dhole_mcp.parse import parse_file

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
