r"""句子里收尾的本机根也要遮：句号是句子的一部分，不是目录名的一部分。

台账 R02-049（R02-F25 修边界时如实留下的残留，当时记 P4 的理由写着
「这是读算式读出来的，不是量出来的」）。本文件的第一条断言就是把它量成现场。

现行算式（`src/dhole_mcp/paths.py:193`）：

    _root_boundary = r"(?![A-Za-z0-9_.\-])"

`_root_boundary` 的意图（写在同一处的注释里）是：**根后面跟的是"路径名字的字符"时，
那已经不是这一颗根**，于是 `C:\\Users\\me` 不许把 `C:\\Users\\mel` 折掉。这条守卫是对的，
但它把 `.` 也收进了字符集，于是库里最常见的形状整格漏：

    searched under D:\\tools\\dhole-mcp.        ← 句号收尾的一句「在这里没找到」
    cwd=D:\\tools\\dhole-mcp. Change it…        ← 逗号、冒号、引号都遮了，只有句号不遮

量过的形状（`review/.runtime/p4/f50_measure.py`，同一颗根 20 种后跟字符）：
串尾 / `\\src` / `/src` / `,` / `:` / `;` / `)` / `]` / `}` / `'` / `"` / `\\t` / `\\n` 都遮；
`.` 与 `. ` 与 `_x` / `-old` / `X` / `9` / `.mirror` 都不遮。前两种是本题（句子收尾），
后五种必须继续不遮（那是**另一颗目录的名字**，不是这一颗根）。

所以判据不许问"有没有点"，问的是"点后面还有没有名字字符"：
`.` 后面接字母/数字/`_`/`-` ⇒ 那是 `.mirror`、`.db` 这一类兄弟目录或扩展名，不遮；
`.` 后面是串尾、空白或别的标点 ⇒ 那是句子，遮。
"""
import os

from dhole_mcp.paths import mask_machine_paths

CWD = os.path.abspath(os.getcwd())
HOME = os.path.expanduser("~")
MASKED_CWD = "<server cwd>"


def _names_root(text: str) -> bool:
    """根还在不在字面上（两种拼法都查）。"""
    low = text.lower()
    return CWD.lower() in low or CWD.replace("\\", "/").lower() in low


class TestASentenceFinalPeriod:

    def test_a_period_closing_a_sentence_is_masked(self):
        msg = f"searched under {CWD}."
        out = mask_machine_paths(msg)
        assert not _names_root(out), f"句子收尾的根原样交回：{out}"
        assert MASKED_CWD in out, out
        assert out.endswith(f"{MASKED_CWD}."), f"标点要留在原位：{out}"

    def test_a_period_then_more_text_is_masked(self):
        out = mask_machine_paths(f"cwd={CWD}. Change it or pass an absolute path.")
        assert not _names_root(out), out
        assert out == f"cwd={MASKED_CWD}. Change it or pass an absolute path.", out

    def test_a_period_inside_parentheses_is_masked(self):
        out = mask_machine_paths(f"the working root ({CWD}.) is not yours")
        assert not _names_root(out), out

    def test_both_spellings_of_the_same_root(self):
        for spelling in (CWD, CWD.replace("\\", "/")):
            out = mask_machine_paths(f"nothing under {spelling}.")
            assert not _names_root(out) and MASKED_CWD in out, (spelling, out)


class TestASiblingNameIsStillNotFolded:
    """`.mirror` / `.db` 这一类是**另一颗目录的名字**：遮了就把没点名的东西折进标签。"""

    def test_a_dot_followed_by_a_name_character_is_left_alone(self):
        for tail in (".mirror", ".db", ".old", ".2", "._x", ".-x"):
            msg = f"sibling checkout {CWD}{tail} still holds the old rows"
            out = mask_machine_paths(msg)
            assert _names_root(out), f"这一格该原样交出（那是兄弟目录/扩展名）：{out}"

    def test_name_characters_still_block_the_mask(self):
        for tail in ("X", "9", "_x", "-old"):
            assert _names_root(mask_machine_paths(f"root {CWD}{tail} is elsewhere")), tail


class TestTheTerminatorsThatAlreadyWorked:
    """反面锁：R02-F25 那一次认下的所有收尾形状，一个字都不许退化。"""

    def test_every_punctuation_terminator_still_masks(self):
        for tail in ("", ",", ":", ";", ")", "]", "}", "'", '"', "\t", "\n",
                     " ", "\\", "/"):
            out = mask_machine_paths(f"searched under {CWD}{tail} rest")
            assert not _names_root(out), f"收尾 {tail!r} 从前就遮，现在必须还遮"

    def test_a_quoted_library_message_is_masked(self):
        out = mask_machine_paths(f"Package not found at '{CWD}'")
        assert out == f"Package not found at '{MASKED_CWD}'", out

    def test_a_child_path_keeps_its_own_tail(self):
        out = mask_machine_paths(f"no file under {CWD}\\tests was readable")
        assert out == f"no file under {MASKED_CWD}\\tests was readable", out

    def test_a_file_extension_inside_a_child_path_survives(self):
        out = mask_machine_paths(f"the store lives at {CWD}\\cache.db")
        assert out == f"the store lives at {MASKED_CWD}\\cache.db", out


class TestTheHomesRootGetsTheSameRule:
    """主目录那一颗走同一条算式：句号收尾同样要遮成 `~`。"""

    def test_home_root_before_a_period(self):
        if not HOME or HOME == os.path.dirname(HOME):
            import pytest
            pytest.skip("主目录形状不适合这一格")
        out = mask_machine_paths(f"the profile sits under {HOME}.")
        assert HOME not in out, out
        assert out.endswith("."), f"句号要留在原位：{out}"
