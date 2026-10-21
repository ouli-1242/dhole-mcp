"""test_paths_1.py —— 由 2 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import os
from dhole_mcp.paths import mask_machine_paths


# ============================================================================
# 来源：test_a_machines_root_stays_masked_whichever_way_it_is_spelled.py
# ============================================================================
# 这台机器自己的根，换一个拼法、换一种收尾，都不能就漏出去。

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


# ============================================================================
# 来源：test_a_sentence_final_period_does_not_keep_the_root.py
# ============================================================================
# 句子里收尾的本机根也要遮：句号是句子的一部分，不是目录名的一部分。
# 重名消歧：CWD → CWD__a_sentence_final_period__2c2c、HOME → HOME__a_sentence_final_period__2c2c

CWD__a_sentence_final_period__2c2c = os.path.abspath(os.getcwd())
HOME__a_sentence_final_period__2c2c = os.path.expanduser("~")
MASKED_CWD = "<server cwd>"


def _names_root(text: str) -> bool:
    """根还在不在字面上（两种拼法都查）。"""
    low = text.lower()
    return CWD__a_sentence_final_period__2c2c.lower() in low or CWD__a_sentence_final_period__2c2c.replace("\\", "/").lower() in low


class TestASentenceFinalPeriod:

    def test_a_period_closing_a_sentence_is_masked(self):
        msg = f"searched under {CWD__a_sentence_final_period__2c2c}."
        out = mask_machine_paths(msg)
        assert not _names_root(out), f"句子收尾的根原样交回：{out}"
        assert MASKED_CWD in out, out
        assert out.endswith(f"{MASKED_CWD}."), f"标点要留在原位：{out}"

    def test_a_period_then_more_text_is_masked(self):
        out = mask_machine_paths(f"cwd={CWD__a_sentence_final_period__2c2c}. Change it or pass an absolute path.")
        assert not _names_root(out), out
        assert out == f"cwd={MASKED_CWD}. Change it or pass an absolute path.", out

    def test_a_period_inside_parentheses_is_masked(self):
        out = mask_machine_paths(f"the working root ({CWD__a_sentence_final_period__2c2c}.) is not yours")
        assert not _names_root(out), out

    def test_both_spellings_of_the_same_root(self):
        for spelling in (CWD__a_sentence_final_period__2c2c, CWD__a_sentence_final_period__2c2c.replace("\\", "/")):
            out = mask_machine_paths(f"nothing under {spelling}.")
            assert not _names_root(out) and MASKED_CWD in out, (spelling, out)


class TestASiblingNameIsStillNotFolded:
    """`.mirror` / `.db` 这一类是**另一颗目录的名字**：遮了就把没点名的东西折进标签。"""

    def test_a_dot_followed_by_a_name_character_is_left_alone(self):
        for tail in (".mirror", ".db", ".old", ".2", "._x", ".-x"):
            msg = f"sibling checkout {CWD__a_sentence_final_period__2c2c}{tail} still holds the old rows"
            out = mask_machine_paths(msg)
            assert _names_root(out), f"这一格该原样交出（那是兄弟目录/扩展名）：{out}"

    def test_name_characters_still_block_the_mask(self):
        for tail in ("X", "9", "_x", "-old"):
            assert _names_root(mask_machine_paths(f"root {CWD__a_sentence_final_period__2c2c}{tail} is elsewhere")), tail


class TestTheTerminatorsThatAlreadyWorked:
    """反面锁：R02-F25 那一次认下的所有收尾形状，一个字都不许退化。"""

    def test_every_punctuation_terminator_still_masks(self):
        for tail in ("", ",", ":", ";", ")", "]", "}", "'", '"', "\t", "\n",
                     " ", "\\", "/"):
            out = mask_machine_paths(f"searched under {CWD__a_sentence_final_period__2c2c}{tail} rest")
            assert not _names_root(out), f"收尾 {tail!r} 从前就遮，现在必须还遮"

    def test_a_quoted_library_message_is_masked(self):
        out = mask_machine_paths(f"Package not found at '{CWD__a_sentence_final_period__2c2c}'")
        assert out == f"Package not found at '{MASKED_CWD}'", out

    def test_a_child_path_keeps_its_own_tail(self):
        out = mask_machine_paths(f"no file under {CWD__a_sentence_final_period__2c2c}\\tests was readable")
        assert out == f"no file under {MASKED_CWD}\\tests was readable", out

    def test_a_file_extension_inside_a_child_path_survives(self):
        out = mask_machine_paths(f"the store lives at {CWD__a_sentence_final_period__2c2c}\\cache.db")
        assert out == f"the store lives at {MASKED_CWD}\\cache.db", out


class TestTheHomesRootGetsTheSameRule:
    """主目录那一颗走同一条算式：句号收尾同样要遮成 `~`。"""

    def test_home_root_before_a_period(self):
        if not HOME__a_sentence_final_period__2c2c or HOME__a_sentence_final_period__2c2c == os.path.dirname(HOME__a_sentence_final_period__2c2c):
            import pytest
            pytest.skip("主目录形状不适合这一格")
        out = mask_machine_paths(f"the profile sits under {HOME__a_sentence_final_period__2c2c}.")
        assert HOME__a_sentence_final_period__2c2c not in out, out
        assert out.endswith("."), f"句号要留在原位：{out}"
