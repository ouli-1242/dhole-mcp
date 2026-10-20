# -*- coding: utf-8 -*-
"""机器身份住在根外面，也得遮（评审 R02-090 · P2）。

遮蔽器 `paths.mask_machine_paths` 的判据是**前缀**：认得两个根（home → `~`、进程 cwd →
`<server cwd>`），别的绝对路径原样交回 —— 而它自己写下的理由是「用户名会跟着路径出门」。
两句不是一回事。真机量到的形状（`review/.runtime/p1/r98b_user_segment.json` · 2 发）：

    doc_parse(file_path=<TMP>/probe-of-<user>/report.txt)
      url   = file:///D:/Cache/temp/probe-of-<user>/report.txt     ← 逐字回显
    doc_parse(file_path=<TMP>/probe-of-<user>/gone.txt)
      error = File not found: D:\\Cache\\temp\\probe-of-<user>\\gone.txt. …

`<TMP>` 不是 home 也不是 cwd，所以一个字的遮法都没有；`pytest-of-<user>` 正是 pytest 自己
的默认拼法（套件那三格红就是被它带出来的）。所以这一族判据要从「以哪个根开头」补一条
「这台机器的身份出现在任何一段里」。

同时不能过头：调用方自己写的、不含身份的路径要**原样读得回来**（这条承诺写在遮蔽器的
docstring 里，也是下面反向守卫的对象）。
"""
import asyncio
import os

import pytest

from dhole_mcp import paths
from dhole_mcp.server import MasterFetchServer

MASKED_USER = "<user>"
USER = os.path.basename(os.path.expanduser("~"))


def _mask(text: str) -> str:
    return paths.mask_machine_paths(text)


@pytest.fixture
def a_file_under_a_foreign_root(tmp_path):
    """一份放在「既不是 home 也不是 cwd」的根下面、且目录名里带着用户名的文件。

    名字照 pytest 自己的拼法来（`pytest-of-<user>` 就是这个形状），这样这一格量的
    是真实世界里会出现的目录，不是一个为了测试编的字符串。
    """
    d = tmp_path / f"probe-of-{USER}"
    d.mkdir()
    good = d / "report.txt"
    good.write_text("第一行\n第二行\n", encoding="utf-8")
    return good


def _parse(**kw):
    return asyncio.run(MasterFetchServer().doc_parse(**kw))


class TestTheIdentityIsMaskedWhicheverRootItLivesOn:

    def test_a_username_shaped_segment_is_masked_in_a_plain_string(self):
        out = _mask(f"/var/tmp/probe-of-{USER}/report.txt")
        assert USER not in out, out
        assert MASKED_USER in out, out

    def test_both_spellings_of_the_same_root_are_masked(self):
        win = _mask(f"D:\\Cache\\temp\\probe-of-{USER}\\gone.txt")
        posix = _mask(f"D:/Cache/temp/probe-of-{USER}/gone.txt")
        assert USER not in win + posix, (win, posix)
        assert win.replace("\\", "/").replace(MASKED_USER, "X") == \
               posix.replace(MASKED_USER, "X"), (win, posix)

    def test_the_mask_is_case_insensitive_because_windows_respells_it(self):
        out = _mask(f"C:/temp/probe-OF-{USER.upper()}/x.txt")
        assert USER.lower() not in out.lower(), out

    def test_the_receipt_from_the_parse_door_carries_no_identity(self, a_file_under_a_foreign_root):
        """成功那一格（`url`）—— D11 当年修的就是这一格，而它只遮了两个根。"""
        r = _parse(file_path=str(a_file_under_a_foreign_root))
        assert r.status == 200, r.error
        assert USER not in r.url, r.url
        assert r.url.endswith(f"{MASKED_USER}/report.txt"), r.url

    def test_the_failure_sentence_carries_no_identity(self, tmp_path):
        d = tmp_path / f"probe-of-{USER}"
        d.mkdir()
        r = _parse(file_path=str(d / "gone.txt"))
        assert r.error.startswith("File not found"), r.error
        assert USER not in r.error, r.error

    def test_the_masked_url_is_still_a_usable_uri(self, a_file_under_a_foreign_root):
        """遮法不许把 URI 弄坏：`quality` 还要拿 path 判扩展名与登录墙信号。"""
        from urllib.parse import urlparse
        r = _parse(file_path=str(a_file_under_a_foreign_root))
        parsed = urlparse(r.url)
        assert parsed.scheme == "file", r.url
        assert parsed.path.endswith("report.txt"), r.url


class TestNotOvermasking:
    """反向守卫：越遮越多不是修法。调用方自己写的路径要原样读得回来。"""

    def test_a_foreign_path_without_an_identity_is_returned_verbatim(self):
        text = "E:/somewhere/else/report.csv"
        assert _mask(text) == text, _mask(text)

    def test_a_directory_that_merely_starts_with_the_username_is_not_folded(self):
        """与 `_root_boundary` 同一条理由：`<user>project` 是另一颗目录，不是身份本身。"""
        text = f"/tmp/{USER}project/report.txt"
        assert _mask(text) == text, _mask(text)

    def test_a_file_extension_shaped_tail_is_not_folded(self):
        """`probe.<user>` 里那截是扩展名形状的另一样东西，不许顺手折掉。"""
        text = f"/tmp/data.{USER}x.gz"
        assert _mask(text) == text, _mask(text)

    def test_text_without_any_machine_shape_is_untouched(self):
        assert _mask("no paths here at all") == "no paths here at all"

    def test_an_empty_string_stays_empty(self):
        assert _mask("") == ""


class TestOneAuthorForTheRule:

    def test_the_identity_rule_lives_in_the_masker(self):
        """判据住在遮蔽器里，不在六个调用点各数一遍（F77/F80 同一条理由）。"""
        import inspect
        from dhole_mcp import parse as parse_mod
        src = inspect.getsource(parse_mod)
        assert "getpwuid" not in src and "os.environ[\"USERNAME\"]" not in src, (
            "遮蔽身份的第二位作者出现了")

    def test_the_two_roots_still_mask_as_before(self):
        """F81 不许弄坏已有的两格：home → `~`、cwd → `<server cwd>`。"""
        home = os.path.expanduser("~")
        assert paths.MASKED_HOME in _mask(f"{home}/report.csv")
        assert paths.MASKED_CWD in _mask(f"{os.getcwd()}/report.csv")
        assert home not in _mask(f"{home}/report.csv")
