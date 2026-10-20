"""契约测试：PDF 的**信息字典**不许冒充这份文档的大纲顶行。

`pdf_extractor._format_metadata` 把信息字典里的 Title 直接写成 `# <title>`，放在整份
交付的第一行 —— 而那一行的形状（`#`）在读的人眼里就是"这份文档的标题"。它其实不是结构，
是文件身上的一个字段：ReportLab 没被作者设过标题时，会把 `untitled` / `anonymous` /
`unspecified` 这组缺省串原样写进信息字典（实测 `review/.runtime/p06/week1-handout.pdf`
的 metadata：`title='untitled', author='anonymous', subject='unspecified'`）。于是
交回的第一行是一串占位符，**真标题在它的下一行**（`## Week 1 reading guide`，页面自己的
字号长出来的）。同一格在 HTTP 与磁盘两条路上都印一次（`tests/test_v17_fix_plan.py`
里"标题只许出现一次"那条锁管着），字段格里也另有一份（`title=` / `metadata.title=`）。

判据落在**这一行是谁写的**上，用量得到的东西判，不猜词表：
* 这份交付**自己带得出大纲**（`table_of_contents` 非空，它本来就是从页面字号/书签量出来的）
  ⇒ 页面已经说出标题是谁了，`# <Title>` 就是文件字段在冒充结构 —— 撤掉那一行；
  字段仍然交回（`Title: …` 进那一行 `>` 的文件自述里，与 `Author:` / `Subject:` 同格同等待），
  除非页面印的标题**就是同一串**（那已经在大纲里看得见，不再印第二遍）；
* 这份交付带不出大纲（扫描页 / 只有一档字号的正文）⇒ `# <Title>` 是交回的唯一标题，**留着** ——
  修一个冒充不许换来一份没有标题的稿子；
* Title 是空的 ⇒ 两种行都不许多出来。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dhole_mcp.parse import parse_file, parse_file_detailed

reportlab = pytest.importorskip("reportlab", reason="the [all] extra provides reportlab")

from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

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
