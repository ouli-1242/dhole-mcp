"""回执可以说"你按什么顺序试了哪几处"，不可以说"这台机器把东西放在哪"。

2026-10-09 独立审计 D11（真机实测）：

    doc_parse(file_path='nope.docx')
      error = "File not found: D:\\tools\\dhole-mcp\\nope.docx, "
              "C:\\Users\\ouli\\nope.docx. Pass an absolute path, ..."

两个候选**都不是调用方写的**：`_relative_path_roots()` 自己从进程 cwd 和用户主目录
拼出来的。在 MCP 宿主下进程 cwd 就是宿主自己的安装目录 —— 实测里那次是
``D:\\Program Files\\Qoder`` —— 于是一句"文件没找到"顺手把宿主的安装位置和用户名
交了出去。契约 C15 说的「错误不泄漏本机绝对路径」，在这句话上被放宽了。

修法是**写名字，不写拼法**：`<server cwd>\\nope.docx, ~\\nope.docx`。诊断力一点没
少 —— 调用方真正需要的"按什么顺序试了哪几处"原样都在 —— 而顺序里那个"哪一处"本来
就是调用方自己给的 `cwd=` 或它自己的绝对路径。

同一次审计里还有**同一类泄漏的第二处**，比第一处更常出现：成功回执的 `url` 字段。

    doc_parse(file_path='d11_probe.txt', cwd='.scratch')     # 成功
      url = file:///D:/tools/dhole-mcp/.scratch/d11_probe.txt

每一次相对路径成功解析都会把它带回来。两处一起遮蔽（用户 2026-10-09 决策）。

本文件钉的是**回执契约**，不是遮蔽器的算术 —— 那部分在 test_paths.py 里。所以每个
用例都问两件事：本机根有没有漏出去（否），以及那个字段还**能不能用**（能）。只测前半
句的话，把 `url` 整段清空也能全绿。
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

import pytest

from dhole_mcp import paths
from dhole_mcp.server import MasterFetchServer


def _parse(**kw):
    import asyncio
    return asyncio.run(MasterFetchServer().doc_parse(**kw))


def _roots_as_a_uri_spells_them():
    """本机根在 `url` 里的两种拼法。

    `Path.as_uri()` 会把分隔符换成 `/`，所以拿 `os.path.expanduser("~")`
    （`C:\\Users\\ouli`）去 `in r.url`（`file:///C:/Users/ouli/...`）**永远为假** ——
    这条断言在修复前也是绿的，等于没测。实测踩过：把遮蔽器换成恒等函数之后，
    `home not in r.url` 照样通过。所以这里两种拼法都列上。
    """
    roots = []
    for root in (os.path.expanduser("~"), os.getcwd()):
        if root and root not in ("~", "/", "\\"):
            roots.append(root)
            roots.append(root.replace("\\", "/"))
    return roots


def _spells_a_machine_root(text: str) -> str:
    """``text`` 里逐字出现的本机根（空串表示没有）。"""
    for root in _roots_as_a_uri_spells_them():
        if root in text:
            return root
    return ""


@pytest.fixture
def a_readable_file(tmp_path):
    f = tmp_path / "report.txt"
    f.write_text("第一行\n第二行\n", encoding="utf-8")
    return f


# ─── 1 · 失败那一句 ──────────────────────────────────────────────

def test_a_missing_relative_file_does_not_spell_the_server_cwd():
    """D11 的原形状：两个候选都是机器自己造的，一个都不许原样出去。"""
    r = _parse(file_path="definitely-missing-d11.docx")
    assert r.error.startswith("File not found"), r.error
    assert not _spells_a_machine_root(r.error), r.error


def test_the_names_are_there_so_the_order_is_still_readable():
    """反向：遮蔽不许退化成"把路径删掉"。

    调用方要的是"你按什么顺序试了哪几处" —— 少了名字，这句话就只剩"没找到"，而
    `next_action` 让它去传 `cwd=`，它却不知道自己原本的候选落在哪一支上。
    """
    r = _parse(file_path="definitely-missing-d11.docx")
    assert paths.MASKED_CWD in r.error, r.error
    assert os.path.join(paths.MASKED_CWD, "definitely-missing-d11.docx") in r.error, \
        r.error


def test_a_cwd_whose_name_carries_the_username_does_not_ride_along():
    """调用方自己给的 `cwd=` 里，**机器身份**那一格不许出去。

    这一格从前写的是「临时目录整条不许出现在 error 里」，而它成立的条件没写在句子里：
    临时目录恰好住在主目录下，于是它作为"主目录的一段"被遮掉了。评审 R02-090 量到的
    正是这个假设的另一面 —— 把 `TMP` 指到别的盘，整条路径原样回显，目录名里还带着用户名
    （pytest 自己的 `pytest-of-<user>` 就是这个形状）。这条承诺的对象从来是**身份**，
    不是"临时目录"：所以这里造一颗名字里带用户名的目录来撞，并把判据写成身份不许出去。
    """
    import tempfile
    user = os.path.basename(os.path.expanduser("~"))
    with tempfile.TemporaryDirectory() as base:
        d = os.path.join(base, f"probe-of-{user}")
        os.mkdir(d)
        r = _parse(file_path="gone.txt", cwd=d)
        assert r.error.startswith("File not found"), r.error
        assert user.lower() not in r.error.lower(), r.error
        assert "gone.txt" in r.error, r.error


def test_a_foreign_cwd_the_caller_wrote_is_still_readable_back(tmp_path):
    """反向守卫：遮蔽不许吞掉调用方自己写的路径 —— 那句承诺写在遮蔽器的 docstring 里。

    这一格刻意用一颗**名字里没有身份**的目录：没有身份、又不撞任何机器根的 `cwd=` 是调用方
    的字符串，回执里要逐字读得回来。把它一起遮掉就退化成"把路径删掉"（同一反面在
    `test_the_names_are_there_so_the_order_is_still_readable` 里钉过）。
    """
    d = tmp_path / "caller-eyes-only"
    d.mkdir()
    r = _parse(file_path="gone.txt", cwd=str(d))
    assert r.error.startswith("File not found"), r.error
    assert "caller-eyes-only" in r.error, r.error
    assert "gone.txt" in r.error, r.error


# ─── 2 · 成功那一句（更常出现的那一处）──────────────────────────

def test_a_successful_parse_does_not_spell_the_resolved_path(a_readable_file):
    r = _parse(file_path=str(a_readable_file))
    assert r.status == 200 and not r.error, r.error
    assert not _spells_a_machine_root(r.url), r.url


def test_the_masked_url_still_names_the_file(a_readable_file):
    """`url` 是回执的身份字段：遮过之后仍要答得出"读的是哪一份"。

    从前这一格断言 `MASKED_HOME in r.url` —— 那句里的"遮过"其实是环境给的（临时目录恰好
    住在主目录下）。评审 R02-090 之后判据写成它本来的样子：**名字要在**，**机器身份不许在**。
    两条都不依赖 `TMP` 被指到哪个盘。
    """
    r = _parse(file_path=str(a_readable_file))
    user = os.path.basename(os.path.expanduser("~"))
    assert r.url.endswith("report.txt"), r.url
    assert not _spells_a_machine_root(r.url), r.url
    assert user.lower() not in r.url.lower(), r.url


def test_the_masked_url_is_still_a_usable_uri(a_readable_file):
    """下游按 URL 解析它（`quality` 取 path 判扩展名与登录墙信号），形状不能坏。"""
    r = _parse(file_path=str(a_readable_file))
    parsed = urlparse(r.url)
    assert parsed.scheme == "file", r.url
    assert parsed.path.endswith("report.txt"), r.url


# ─── 3 · 遮蔽不许碰到真读文件的那条路 ───────────────────────────

def test_the_real_path_still_reaches_the_filesystem(a_readable_file):
    """只遮回执。真去读的还是那个绝对路径 —— 正文与字节数都得对得上。"""
    r = _parse(file_path=str(a_readable_file))
    assert r.status == 200
    assert "第一行" in "\n".join(r.content), r.content
    assert r.total_size_bytes == a_readable_file.stat().st_size


def test_the_suffix_decides_on_the_real_name_not_the_masked_one(a_readable_file):
    """`_PARSE_CONTENT_TYPES.get(Path(target).suffix)` 吃的是真路径。

    遮蔽器把根换成一个带尖括号的名字，如果顺手把它接到 suffix 那一步，`.txt` 就没了
    —— 这条用例把它钉住：扩展名从名字里读得出来，与根是谁无关。
    """
    csv = a_readable_file.with_suffix(".csv")
    csv.write_text("a,b\n1,2\n", encoding="utf-8")
    r = _parse(file_path=str(csv))
    assert r.status == 200 and r.content_type == "text/csv", r.content_type


# ─── 4 · 两条路说的是同一件事 ───────────────────────────────────

def test_both_branches_name_a_root_the_same_way(tmp_path):
    """同一个根在成功与失败两条路上必须是同一个名字。

    这正是本仓库反复点名的那类缺陷（"同一件事在两条路上两种说法"）：一处写
    `<server cwd>`、另一处写 `~\\...\\<server cwd>`，读的人会以为那是两个地方。

    判据不许写成"那一格一定是 `~`"（评审 R02-090 的教训）：那个名字由**这个根今天住在哪个盘**
    决定 —— 主目录下是 `~`，别的根就是它自己的拼法。两条路一致这件事与它一致与否无关，
    所以这里比的是"两条路用的是不是同一个名字"。
    """
    good = tmp_path / "report.txt"
    good.write_text("x\n", encoding="utf-8")
    ok = _parse(file_path="report.txt", cwd=str(tmp_path))
    bad = _parse(file_path="missing.txt", cwd=str(tmp_path))
    assert ok.status == 200, ok.error
    assert bad.error.startswith("File not found"), bad.error

    user = os.path.basename(os.path.expanduser("~"))
    #: 两条路要认的是**同一个名字**，而不是"那个名字一定是 `~`"。遮蔽之后的调用方根就是
    #: 那个名字 —— 成功格把它写成 POSIX 拼法、失败格写成反斜杠拼法（这一条差异本身记在
    #: R02-090 的 repro 里），所以这里按分隔符归一之后再比。
    def _norm(s: str) -> str:
        return s.replace("\\", "/").lower()

    named = _norm(paths.mask_machine_paths(str(tmp_path)))
    assert named and named in _norm(ok.url), ok.url
    assert named in _norm(bad.error), bad.error
    # 反过来：两条路上都不许出现机器自己的拼法，也不许出现身份
    assert not _spells_a_machine_root(ok.url), ok.url
    assert not _spells_a_machine_root(bad.error), bad.error
    assert user.lower() not in (ok.url + bad.error).lower(), (ok.url, bad.error)


# ─── 5 · 那两格下游不许被这次修补改坏 ───────────────────────────

def test_the_path_advice_survives_the_masking():
    """`quality` 按 `startswith("File not found")` 认这一格，而遮蔽发生在句子内部。

    原本这一格钉的是 `"cwd=" in next_action`。R02-062 之后那句出路只有一位作者（server 按
    `os.path.isabs` 写一次，进 `error`），下游改为指回 `error` 而不复述 —— 所以这里钉的
    还是同一件事（那句出路没被遮蔽改动），只是去它现在住的地方钉，并且钉的是**整句原文**：
    这一发传的是相对写法，两句出路必须逐字还在。
    """
    r = _parse(file_path="definitely-missing-d11.docx")
    assert (r.diagnosis or {}).get("code") == "file_not_found", r.diagnosis
    assert "Pass an absolute path, or pass cwd=<working directory> so relative paths " \
           "resolve against it." in r.error, r.error
    assert "error" in r.next_action.lower(), r.next_action
    assert "PATH was refused" not in r.next_action, r.next_action


def test_a_path_the_caller_invented_outside_every_root_reads_back(tmp_path):
    """不过度遮蔽：本机根之外的路径是调用方自己写的，回执里原样读得回来。

    这里用 `_masked_file_uri` 直接问，因为要在 e2e 里造一个"既不在 cwd 下也不在主
    目录下"的真实文件，得往盘根写东西 —— 那是测试不该做的事。
    """
    from dhole_mcp import server as server_mod
    foreign = (r"D:\somewhere\else\report.csv" if os.name == "nt"
               else "/srv/data/report.csv")
    assert server_mod._masked_file_uri(foreign) == Path(foreign).as_uri()
