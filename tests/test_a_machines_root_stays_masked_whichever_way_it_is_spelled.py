r"""这台机器自己的根，换一个拼法、换一种收尾，都不能就漏出去。

现场（2026-10-15 · P4 写 R02-F22 时量到 · R02-045 · P2 · 单条）：
`paths.mask_machine_paths` 的正则是 `re.escape(root) + r"(?=[\\\\/]|$)"`，两格因此吃不住
（`review/.runtime/p4/f22_mask_probe.py` 当场输出）：

    root_then_separator      masked=True    → not found at '<server cwd>\review\x.docx'
    root_at_end_of_token     masked=False   → not found at 'D:\tools\dhole-mcp'
    home_then_separator      masked=True    → ~\.dhole\cache
    home_at_end_of_token     masked=False   → cache lives in 'C:\Users\…'
    root_then_forward_slash  masked=False   → D:/tools/dhole-mcp/review/x.docx   ← 一字不改

两个因：lookahead 只认「分隔符或串尾」，而库交出的消息常把路径打在**引号里**；
`re.escape(root)` 在 Windows 上只生成反斜杠拼法，于是**同一个根的斜杠写法**永远匹配不上 ——
而响应里到处是归一化过的 POSIX 路径（pathlib 的 `.as_posix()`、zip/fsspec 的内部路径、
`file:///` 那一格的形状）。函数自己的注释写着「under-masking is the failure that leaks」。

契约（本文件钉住）：
  * 同一个根的**两种拼法**（反斜杠与正斜杠）都要遮，且跟在后面的**不是路径名字符**时就算边界
    （分隔符、引号、括号、空格、串尾）；
  * **反面锁一**：调用方自己编的、不属于这两根的绝对路径必须**原样交回**（这不是"整体打码"）；
  * **反面锁二**：一条更长的目录名以这根开头时不许被折掉（`<cwd>-old\x`、`<cwd>_2\x` 都得原样）——
    这正是原实现用分隔符前瞻想守住的那一格，放宽边界不能把它丢掉；
  * 判决一字不少：遮蔽只改路径的写法，句子其余部分照旧。
"""

from __future__ import annotations

import os

from dhole_mcp.paths import mask_machine_paths

CWD = os.path.abspath(os.getcwd())
HOME = os.path.expanduser("~")
CWD_FWD = CWD.replace("\\", "/")
HOME_FWD = HOME.replace("\\", "/")


def _leaked(text: str) -> bool:
    """两个拼法都查：漏一种就是漏。"""
    low = (text or "").lower()
    return any(r.lower() in low for r in (CWD, CWD_FWD, HOME, HOME_FWD) if r)


# ─── 1 · 边界不再只认分隔符 ───

def test_a_root_inside_quotes_at_the_end_is_still_masked():
    out = mask_machine_paths("Package not found at '" + CWD + "'")
    assert not _leaked(out), out
    assert "Package not found at" in out, out


def test_a_home_root_at_the_end_of_a_token_is_masked():
    out = mask_machine_paths("cache lives in '" + HOME + "'")
    assert not _leaked(out), out
    assert "cache lives in" in out, out


def test_a_root_before_a_bracket_is_masked():
    out = mask_machine_paths("searched under [" + CWD + "] for the module")
    assert not _leaked(out), out


# ─── 2 · 换一个拼法不是换一个根 ───

def test_the_forward_slash_spelling_of_the_same_root_is_masked():
    out = mask_machine_paths("not found at " + CWD_FWD + "/review/x.docx")
    assert not _leaked(out), out
    assert "/review/x.docx" in out, out


def test_the_forward_slash_home_is_masked():
    out = mask_machine_paths("model sits in " + HOME_FWD + "/.dhole/models")
    assert not _leaked(out), out


# ─── 3 · 反面锁：原来守得住的那一格一格都不许丢 ───

def test_the_separator_case_keeps_working():
    out = mask_machine_paths("not found at " + CWD + os.sep + "review" + os.sep + "x.docx")
    assert not _leaked(out), out


def test_a_longer_directory_that_starts_with_the_root_is_not_folded():
    r"""`C:\Users\me` 不能把 `C:\Users\mel` 吃掉 —— 边界必须是「不是路径名字符」。"""
    for longer in (CWD + "-old" + os.sep + "x.txt", CWD + "_archive" + os.sep + "y.txt",
                   CWD + "x" + os.sep + "z.txt"):
        out = mask_machine_paths("read " + longer)
        assert longer in out, (longer, out)


def test_a_path_the_caller_invented_is_echoed_back_verbatim():
    """不属于这两根的绝对路径是调用方自己的，原样交回才是可诊断的。"""
    theirs = "E:" + os.sep + "somewhere" + os.sep + "else" + os.sep + "report.csv"
    assert mask_machine_paths("File not found: " + theirs) == "File not found: " + theirs
    assert mask_machine_paths("File not found: E:/somewhere/else/report.csv") == \
        "File not found: E:/somewhere/else/report.csv"


def test_no_text_is_invented_and_empty_stays_empty():
    assert mask_machine_paths("") == ""
    assert mask_machine_paths("nothing about paths here") == "nothing about paths here"


# ─── 4 · 用她的地方：回执的 `url` 那一格也必经同一位作者 ───

def test_a_not_found_receipt_does_not_echo_the_root_in_its_url_field():
    """两侧复验量到的第二格：`File not found:` 那句遮过，而**同一封回执的 `url`** 把
    根原样交回（两种拼法都试）—— 遮蔽挂在散文上、没挂在字段上，等于只遮了一半。"""
    import asyncio

    from dhole_mcp import server as server_mod

    for probe in (CWD_FWD + "/review/.runtime/p4/nope-45.docx",
                  CWD + os.sep + "review" + os.sep + ".runtime" + os.sep + "p4"
                  + os.sep + "nope-45.docx",
                  CWD):
        out = asyncio.run(server_mod.MasterFetchServer().doc_parse(probe))
        text = out.model_dump_json() if hasattr(out, "model_dump_json") else str(out)
        assert not _leaked(text), (probe, text[:400])
        # 这一格守的是**遮蔽**，不是 "File not found" 那三个词。第三个探针 `CWD` 本来就是一颗
        # 目录：改前它也收到那句 "File not found"，而那正是贴错的标签（R02-026 —— 一颗真实的
        # 文件夹被说成不存在，把人支去查拼写）。R02-F48 把目录那一支改口成 "Path is a directory"
        # 之后，这里的断言跟着改问"这一发拒绝了吗、且说了它是什么"：`_leaked` 与遮蔽调用一寸未松。
        assert out.error.startswith(("File not found", "Path is a directory")), (probe, text[:200])
        assert not out.content, (probe, text[:200])
