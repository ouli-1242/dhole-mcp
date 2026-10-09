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


def test_a_named_cwd_is_masked_too_because_the_username_rides_along():
    """调用方自己给的 `cwd=` 也要遮蔽 —— 主目录的拼法里带着用户名。

    这不是"过度遮蔽"：`~\\proj\\gone.txt` 比 `C:\\Users\\ouli\\proj\\gone.txt`
    **更好**读（一眼看出是哪一支根），而用户名没有理由跟着出门。
    """
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        r = _parse(file_path="gone.txt", cwd=d)
        assert r.error.startswith("File not found"), r.error
        assert d not in r.error, "调用方给的 cwd 也不该逐字回显：它在主目录下"
        assert "gone.txt" in r.error


# ─── 2 · 成功那一句（更常出现的那一处）──────────────────────────

def test_a_successful_parse_does_not_spell_the_resolved_path(a_readable_file):
    r = _parse(file_path=str(a_readable_file))
    assert r.status == 200 and not r.error, r.error
    assert not _spells_a_machine_root(r.url), r.url


def test_the_masked_url_still_names_the_file(a_readable_file):
    """`url` 是回执的身份字段：遮掉根之后仍要答得出"读的是哪一份"。"""
    r = _parse(file_path=str(a_readable_file))
    assert r.url.endswith("report.txt"), r.url
    assert paths.MASKED_HOME in r.url, r.url


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
    """
    good = tmp_path / "report.txt"
    good.write_text("x\n", encoding="utf-8")
    ok = _parse(file_path="report.txt", cwd=str(tmp_path))
    bad = _parse(file_path="missing.txt", cwd=str(tmp_path))
    assert ok.status == 200, ok.error
    assert bad.error.startswith("File not found"), bad.error
    assert paths.MASKED_HOME in ok.url, ok.url
    assert paths.MASKED_HOME in bad.error, bad.error
    # 反过来：两条路上都不许出现机器自己的拼法
    assert not _spells_a_machine_root(ok.url), ok.url
    assert not _spells_a_machine_root(bad.error), bad.error


# ─── 5 · 那两格下游不许被这次修补改坏 ───────────────────────────

def test_the_path_advice_survives_the_masking():
    """`quality` 按 `startswith("File not found")` 与 `"cwd="` 认这两格。

    遮蔽发生在句子内部，首词与尾句都没动 —— 但这是"改完顺手确认"，不是"以为它不会动"。
    """
    r = _parse(file_path="definitely-missing-d11.docx")
    assert (r.diagnosis or {}).get("code") == "file_not_found", r.diagnosis
    assert "cwd=" in r.next_action, r.next_action
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
