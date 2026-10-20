"""契约测试：PDF 交付里那句"文件的自述"要各说各的来源。

`_format_metadata` 从前把**两类不同的东西**挤在同一行里，而那一行的括注只描述其中一类：

    > Title: untitled · Author: anonymous · Created: 2026-10-14 · Modified: 2026-10-14 ·
      Subject: unspecified (the file's own stamps - when this PDF was written, not when the
      work was written)

括注是 R01-048 那一把为 `Created` / `Modified` 写的（两枚文件戳不是文献年份），它说得对；
但它挂在**整行**末尾，于是 `Author: anonymous` 也被读成"文件戳之一"，而那句话其实是一句
**关于作者的主张** —— ReportLab 在没人填字段时就写 `anonymous` / `unspecified`。
实测（`review/.runtime/p4/f47_measure.py`）：仓库里只有这份 handout 带 author/subject
（其余稿子这两格是空的、根本不出场），所以缺陷面就是"缺省串被当成关于作品的陈述"。

判据不问"这串像不像缺省值"（词表路线在 R02-059 那把已经否掉：别的库有别的缺省串，而一份
真署名 anonymous 的作品会被误说成工具）。判据问**这一格是谁写的**：

* Title / Author / Subject / Keywords 是**信息字典**里的记录 ⇒ 一行，括注说清是谁记的；
* Created / Modified 是**文件戳** ⇒ 另一行，括注沿用 R01-048 那句（一字不改）；
* 两类都不许多占一行；空的格子两种都不出。
"""

from __future__ import annotations

import pytest

from dhole_mcp.parse import parse_file

reportlab = pytest.importorskip("reportlab", reason="the [all] extra provides reportlab")

from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.units import mm  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

FIELDS_NOTE = "info dictionary"
STAMPS_NOTE = "the file's own stamps"


def _pdf(tmp_path, name: str, *, title=None, author=None, subject=None,
         keywords=None, heading=None) -> str:
    path = str(tmp_path / name)
    c = canvas.Canvas(path, pagesize=A4)
    if title is not None:
        c.setTitle(title)
    if author is not None:
        c.setAuthor(author)
    if subject is not None:
        c.setSubject(subject)
    if keywords is not None:
        c.setKeywords(keywords)
    c.setFont("Helvetica-Bold" if heading else "Helvetica", 22 if heading else 11)
    if heading:
        c.drawString(20 * mm, 275 * mm, heading)
    c.setFont("Helvetica", 11)
    c.drawString(20 * mm, 262 * mm, "Body text at eleven points, the ordinary size.")
    c.drawString(20 * mm, 252 * mm, "A second line so the body size is measured.")
    c.showPage()
    c.save()
    return path


def _header_rows(text: str) -> list[str]:
    return [ln for ln in text.split("--- Page 1 ---")[0].splitlines()
            if ln.startswith(">")]


class TestEachClaimNamesItsOwnSource:
    """同一行里挤着两类主张时，那句括注只覆盖得了一部分。"""

    def test_the_field_line_says_the_info_dictionary_wrote_it(self, tmp_path):
        """台账那一发的形状：`Author: anonymous` 是产出器写的，不是这份作品的署名。"""
        path = _pdf(tmp_path, "defaults.pdf", title="", heading="Week 1 reading guide")

        text, err = parse_file(path)

        assert err == ""
        # reportlab 没被填过的三格：untitled / anonymous / unspecified
        fields = [ln for ln in _header_rows(text) if "Author:" in ln]
        assert fields, text[:400]
        assert "anonymous" in fields[0]
        assert FIELDS_NOTE in fields[0], fields[0]
        assert STAMPS_NOTE not in fields[0], "文件戳那句话只描述 Created/Modified 那一行"

    def test_the_stamp_line_keeps_the_sentence_it_already_had(self, tmp_path):
        """R01-048 那句一字不改地留在戳那一行上 —— 本刀不许改写别人的判据。"""
        path = _pdf(tmp_path, "stamps.pdf", title="", author="")

        text, _ = parse_file(path)

        stamps = [ln for ln in _header_rows(text) if "Created:" in ln]
        assert stamps, text[:400]
        assert ("(the file's own stamps - when this PDF was written, "
                "not when the work was written)") in stamps[0]

    def test_the_two_kinds_never_share_a_line(self, tmp_path):
        """混合的那一行是本刀的缺陷本体：一行里不许同时有字段与戳。"""
        path = _pdf(tmp_path, "mixed.pdf", title="File Record", author="Ada Lovelace",
                    subject="Notes", keywords="a, b", heading="A Printed Heading")

        text, _ = parse_file(path)

        for ln in _header_rows(text):
            assert not ("Author:" in ln and "Created:" in ln), ln
        joined = "\n".join(_header_rows(text))
        assert "Title:" in joined and "Created:" in joined

    def test_a_real_author_gets_the_same_sentence_as_a_default_one(self, tmp_path):
        """归因是同一句：真署名的人不许被说成工具，缺省串也不许被说成作者。"""
        named = parse_file(_pdf(tmp_path, "named.pdf", title="", author="Ada Lovelace"))[0]
        blank = parse_file(_pdf(tmp_path, "blank.pdf", title="", author=""))[0]

        line = [ln for ln in _header_rows(named) if "Author: Ada Lovelace" in ln]
        assert line and FIELDS_NOTE in line[0], line
        assert not [ln for ln in _header_rows(blank) if "Author:" in ln]

    def test_no_bare_date_word_enters_the_header(self, tmp_path):
        """`Date:` 这个词在头部不出现是 R01-048 的锁，本刀不许把它带回来。"""
        text, _ = parse_file(_pdf(tmp_path, "word.pdf", title="", author="x"))

        assert "Date:" not in text.split("--- Page 1 ---")[0]


class TestNothingThatReadsRightTodayIsLost:
    """分开两行不许多占一行，也不许少交一格。"""

    def test_a_document_with_only_stamps_gets_one_line(self, tmp_path):
        """`tests/background_checks.pdf` 那一族：Title/Author 都空 ⇒ 只有戳那一行。"""
        path = _pdf(tmp_path, "only_stamps.pdf", title="", author="", subject="")

        text, _ = parse_file(path)

        rows = _header_rows(text)
        assert [ln for ln in rows if "Created:" in ln], rows
        assert not [ln for ln in rows if "Title:" in ln or "Author:" in ln], rows

    def test_keywords_stay_on_the_field_side(self, tmp_path):
        path = _pdf(tmp_path, "kw.pdf", title="", author="", keywords="alpha, beta",
                    heading="A Printed Heading")

        text, _ = parse_file(path)

        line = [ln for ln in _header_rows(text) if "Keywords:" in ln]
        assert line and FIELDS_NOTE in line[0], line
        assert "Created:" not in line[0]

    def test_every_field_still_arrives(self, tmp_path):
        """拆行不许丢字段：四格都在，`metadata` 里也仍然取得到。"""
        path = _pdf(tmp_path, "all.pdf", title="File Record", author="Ada", subject="Notes",
                    keywords="a, b", heading="A Printed Heading")

        text, err = parse_file(path)

        assert err == ""
        for mark in ("Title:", "Author: Ada", "Subject: Notes", "Keywords: a, b"):
            assert mark in text, mark
