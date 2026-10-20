"""「就是那个文件夹」与一颗没作用到身上的参数：两种沉默都要改成交回一句话。

台账 R02-026（P02-06 高杉 · S3 路径 + S8 参数面），现场量于 2026-10-16（9202 · 本地文件）：

    doc_parse(file_path='review/.runtime/p06')      ← 那颗目录真实存在
      error = "File not found: <server cwd>\\review\\.runtime\\p06, ~\\review\\.runtime\\p06.
               Pass an absolute path, or pass cwd=<working directory> so relative paths
               resolve against it."
    doc_parse(file_path='review/.runtime/p06/nope.docx')   ← 真缺失
      error = 同一族、同一句、同一下一步

    doc_parse(file_path='…/week1-导读.docx', pages='1-2')
      content = 384 字符，与不带 pages 逐字节相同；error / summary / next_action 也一字未动
    doc_parse(file_path='…/week1-导读.docx', password='hunter2')
      同上：384 字符，整封回执里连 "password" 这个字都没出现过

判据不许问措辞，问形状：

1. **目录不是缺失**。`isfile()` 一颗都不许冒充成"没找到"：那两句话把调用方送到不同的
   地方去 —— 一句让她复查拼写（拼写没有错），一句让她列出里面的文件名（那是正确答案）。
   这一刀的正面形状是"你说的是目录 + 里面有这些文件"，反面锁是真缺失那一句**一字不改**
   （`test_a_refused_path_is_not_described_as_a_file_that_was_read.py` 里已经钉着它）。
2. **接受了却没作用的参数要自报**。`pages=` / `password=` 只在 .pdf 那条腿上被读；其余格式
   拿到它们时整封回执像什么都没发生过。这一刀只加一行申报，**正文一寸不改**：把那一行
   摘掉之后必须与不带参数时逐字节相同（这条是全部风险的对照面）。
   `password` 的值永不回显。
3. 不谎报、不占位：参数真起作用时（.pdf）不许出现这一行；没给参数时也不许出现。
"""
import asyncio
import os

import pytest

from dhole_mcp.parse import parse_file_detailed
from dhole_mcp.server import MasterFetchServer


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
