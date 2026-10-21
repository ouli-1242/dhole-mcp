"""test_parse_4.py —— 由 4 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import pytest
from dhole_mcp.parse import parse_file
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from dhole_mcp.trafilatura_extractor import _restore_table_markdown, _table_to_markdown_tokens, _trafilatura_markdown
import os
import pathlib
from dhole_mcp import paths as paths_mod
from dhole_mcp.parse import parse_file_detailed
from dhole_mcp.parse import _cfb_refusal


# ============================================================================
# 来源：test_a_files_own_fields_say_which_record_they_came_from.py
# ============================================================================
# 契约测试：PDF 交付里那句"文件的自述"要各说各的来源。

reportlab = pytest.importorskip("reportlab", reason="the [all] extra provides reportlab")


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


# ============================================================================
# 来源：test_html_tables_g13.py
# ============================================================================
# G13 回归：HTML 表格要变成 markdown 表格，而不是九行散落的单元格。

DOC = """<html><head><title>Prices</title></head><body>
<h1>Quarterly prices</h1>
<p>Some prose before the table so extraction has context.</p>
<table><thead><tr><th>Region</th><th>Q1</th><th>Q2</th></tr></thead>
<tbody><tr><td>North</td><td>12</td><td>14</td></tr>
<tr><td>South</td><td>9</td><td>11</td></tr></tbody></table>
<p>Prose after the table.</p>
</body></html>"""

TWO = """<html><body><h1>Two tables</h1>
<p>intro prose that trafilatura will keep</p>
<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>
<p>middle prose that trafilatura will keep</p>
<table><tr><th>C</th><th>D</th></tr><tr><td>3</td><td>4</td></tr></table>
<p>outro prose that trafilatura will keep</p></body></html>"""

NO_TABLE = """<html><body><h1>Only prose</h1>
<p>The first paragraph carries the content of this page.</p>
<p>And a second paragraph after it, with nothing table-shaped anywhere.</p>
</body></html>"""


def _write(tmp_path, text, name="page.html"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return str(p)


class TestTheTableSurvivesAsATable:

    def test_parse_gives_a_real_markdown_table(self, tmp_path):
        text, err = parse_file(_write(tmp_path, DOC))

        assert err == ""
        assert "| Region | Q1 | Q2 |" in text, text
        assert "| --- | --- | --- |" in text, text
        assert "| North | 12 | 14 |" in text, text
        # The flattening this replaces looked like six loose lines of cells.
        assert "Region\nQ1" not in text, text

    def test_the_prose_around_it_is_not_collateral(self, tmp_path):
        text, _ = parse_file(_write(tmp_path, DOC))

        assert "Some prose before the table" in text
        assert "Prose after the table" in text
        assert text.index("before the table") < text.index("| Region |") < \
            text.index("after the table"), "表格要回到它原来在的位置"

    def test_two_tables_stay_in_their_own_order(self, tmp_path):
        text, _ = parse_file(_write(tmp_path, TWO))

        assert "| A | B |" in text and "| C | D |" in text, text
        assert text.index("| A | B |") < text.index("| C | D |")
        assert "middle prose" in text

    def test_a_ragged_headerless_table_still_comes_out_as_a_table(self, tmp_path):
        """真网页里的表经常没有 thead、行还不齐。这类表最需要的恰恰是别把形状弄丢。"""
        ragged = """<html><body><h1>Ragged</h1><p>prose that keeps the extraction alive</p>
        <table><tr><td>x</td><td>y</td></tr><tr><td>1</td></tr></table>
        <p>trailing prose</p></body></html>"""
        text, _ = parse_file(_write(tmp_path, ragged))

        assert "| x | y |" in text, text

    def test_a_prose_page_is_untouched_by_the_mechanism(self, tmp_path):
        """占位机制只在文档真有表格时才进场；没有表格的页面输出必须一字不差。"""
        html, tokens = _table_to_markdown_tokens(NO_TABLE)
        assert tokens == [] and html == NO_TABLE

        before = _trafilatura_markdown(NO_TABLE, "https://prose.example/")
        assert before and "first paragraph" in before

    def test_a_page_that_litters_the_token_prefix_is_not_mistaken(self, tmp_path):
        """占位词认的是「本次调用生成的那一个」，页面里恰好写着 DHOLE-TBL 也不该被
        当成表格回填。"""
        tricky = DOC.replace("Prose after the table.",
                             "Prose after the table. DHOLE-TBL-0-deadbeef")
        out = _trafilatura_markdown(tricky, "https://tricky.example/")

        assert "| Region | Q1 | Q2 |" in out, out
        assert "DHOLE-TBL-0-deadbeef" in out, "页面自己的文字不是我们的占位符"


class TestTheSwapItself:

    def test_the_placeholder_replaces_the_table_node(self):
        swapped, tokens = _table_to_markdown_tokens(DOC)

        assert len(tokens) == 1
        token, rendered = tokens[0]
        assert "<table" not in swapped and token in swapped
        assert rendered.startswith("| Region |")

    def test_no_tables_means_no_work(self):
        assert _table_to_markdown_tokens(NO_TABLE) == (NO_TABLE, [])
        assert _table_to_markdown_tokens("<html><body>no markup at all") == \
            ("<html><body>no markup at all", [])

    def test_a_missing_placeholder_is_a_fallback_not_a_loss(self):
        """占位词被 trafilatura 当噪声丢掉时，宁可退回「表格还在，只是扁的」，
        也不能交出一份本来有表却看不见表的文本。"""
        tokens = [("DHOLE-TBL-0-abc123", "| A | B |\n| --- | --- |")]
        assert _restore_table_markdown("prose only, token went missing", tokens) is None

    def test_the_placeholder_line_is_replaced_whole(self):
        tokens = [("DHOLE-TBL-0-abc123", "| A | B |")]
        out = _restore_table_markdown("before\nDHOLE-TBL-0-abc123\nafter", tokens)

        assert out == "before\n| A | B |\nafter", repr(out)


# ============================================================================
# 来源：test_a_parse_refusal_does_not_print_the_machines_own_root.py
# ============================================================================
# 本地文档的拒绝不许把这台的根印在回执里：判决说什么，路径不是它的话。

ROOTS = [p for p in (os.path.abspath(os.getcwd()), os.path.expanduser("~")) if p]


def _roots_leaked(text: str) -> list[str]:
    return [r for r in ROOTS if r and r.lower() in (text or "").lower()]


def test_a_package_error_does_not_print_the_machines_root(tmp_path):
    bogus = tmp_path / "sample.docx"
    bogus.write_bytes(b"PK\x03\x04not a real document at all")
    _content, err, _extras = parse_file_detailed(str(bogus))
    assert err, "拒绝本身没了"
    assert not _roots_leaked(err), f"本机根被印进回执：{err!r}"
    # 遮蔽之后这句话还得说得出这一发知道的东西
    assert "PackageNotFoundError" in err, err
    assert ".docx" in err, err


def test_an_import_error_does_not_print_the_machines_root(monkeypatch, tmp_path):
    from dhole_mcp import parse as parse_mod

    def _raise(_path):
        # 库的消息照例把**它翻过的那条目录**写全（根后面还带一段），
        # 这正是遮蔽能吃住的形状。"根出现在句尾、后面没有分隔符"那一格遮蔽今天吃不住，
        # 那是另一条账（R02-045），不在这一刀里悄悄改共享的遮蔽器。
        raise ModuleNotFoundError(
            f"No module named 'dhole_absent_pkg' (searched under "
            f"{os.path.join(os.getcwd(), 'site-packages', 'dhole_absent_pkg')})")

    monkeypatch.setattr(parse_mod, "_parse_xlsx", _raise)
    xlsx = tmp_path / "book.xlsx"
    xlsx.write_bytes(b"PK\x03\x04not a real workbook")
    _content, err, _extras = parse_file_detailed(str(xlsx))
    assert not _roots_leaked(err), f"缺依赖那一句里带着这台的路径：{err!r}"
    assert "dhole_absent_pkg" in err, err


def test_the_missing_file_branch_keeps_its_mask(tmp_path):
    """反面锁：D11 那一处已经修好的不许被这一刀弄回原形。"""
    gone = pathlib.Path(os.path.expanduser("~")) / "definitely-not-here-42.docx"
    _content, err, _extras = parse_file_detailed(str(gone))
    assert "File not found" in err, err
    assert not _roots_leaked(err), err


def test_the_unsupported_extension_sentence_is_untouched(tmp_path):
    rar = tmp_path / "archive.rar"
    rar.write_bytes(b"RAR6\x00not an archive dhole opens")
    _content, err, _extras = parse_file_detailed(str(rar))
    assert err.startswith("Unsupported file type '.rar'"), err
    assert "rename it to .txt" in err, err


def test_the_mask_has_one_author():
    """parse 不许自己删路径：它交出的句子必须与 `paths.mask_machine_paths` 逐字同。

    比对而不是数正则 —— 一份复制品能过"没有第二条正则"的字面锁，过不了同输入同输出。
    """
    import inspect

    from dhole_mcp import parse as parse_mod

    local = os.path.join(os.getcwd(), "review", "x.docx")
    for sample in (
        f"Parse error (.docx): PackageNotFoundError: Package not found at '{local}'",
        f"searched under {os.path.join(os.path.expanduser('~'), '.dhole', 'cache')}",
        "Unsupported file type '.rar'. Supported: .csv, .docx",   # 没有根的那一句照旧原样
    ):
        assert parse_mod._parse_refusal(sample) == paths_mod.mask_machine_paths(sample), sample

    src = inspect.getsource(parse_mod)
    assert src.count("_parse_refusal(") >= 6, "五支异常那一句都必经这位遮蔽作者"
    assert 're.compile(r"[A-Za-z]:' not in src, "parse 自己写了一条删路径的正则 = 第二个作者"


# ============================================================================
# 来源：test_an_unknown_container_is_not_told_to_save_it_as_a_document.py
# ============================================================================
# 非 Office 的 CFB 容器不是"没转成 docx 的文档"：出路不许指定一个文档转换目标。
# 重名消歧：_write → _write__an_unknown_container_is__71ed

#: OLE2 / CFB 魔数：`_sniff_cfb` 只读前 8 字节，后面填零即可。
CFB = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 512


def _write__an_unknown_container_is__71ed(tmp_path, name: str) -> str:
    path = pathlib.Path(tmp_path) / name
    path.write_bytes(CFB)
    return str(path)


# ─── 1 · 未知容器不许被指定一个文档目标 ───

@pytest.mark.parametrize("name", ["installer.msi", "mail.msg", "shell.bin"])
def test_an_unknown_container_is_not_told_to_save_it_as_a_document(tmp_path, name):
    err, way = _cfb_refusal(pathlib.Path(name).suffix)
    assert way, "拒绝不能没有出路"
    assert "docx" not in way and "xlsx" not in way and "pptx" not in way, way
    assert "soffice" not in way, way
    assert "cannot be opened" in way or "not a document" in way.lower(), way


def test_the_unknown_container_verdict_still_refuses_cleanly(tmp_path):
    path = _write__an_unknown_container_is__71ed(tmp_path, "installer.msi")
    content, err, _extras = parse_file_detailed(path)
    assert content == "" and err, (content[:40], err[:80])
    assert "another CFB container (msi, msg, ...)" in err, err
    assert "renaming it does not change the bytes" in err, err


# ─── 2 · 已知映射一位不动（反面锁） ───

@pytest.mark.parametrize("ext,target", [(".doc", "docx"), (".xls", "xlsx"), (".ppt", "pptx")])
def test_the_known_legacy_pairs_keep_their_conversion_target(ext, target):
    err, way = _cfb_refusal(ext)
    assert f"--convert-to {target}" in way, (ext, way)
    assert "Save As" in way, way
    assert "renaming it does not change the bytes" in err, err


def test_a_renamed_pair_names_the_format_it_actually_is():
    """改名成 .docx 的 CFB（最常见的自救尝试）仍然指回 docx，且说清真格式。"""
    err, way = _cfb_refusal(".docx")
    assert "named .docx but is a pre-2007 Word binary" in err, err
    assert "--convert-to docx" in way, way


# ─── 3 · 目标只有一位作者 ───

def test_the_way_forward_target_comes_only_from_the_known_map():
    """兜底目标从函数里消失：未命中映射 = 没有目标，而不是"默认 docx"。"""
    import inspect

    from dhole_mcp import parse as parse_mod
    src = inspect.getsource(parse_mod._cfb_refusal)
    assert 'target = "docx"' not in src, src
    assert "else" in src, "未命中映射那一支必须自己有一条出路，而不是共用文档那句"
