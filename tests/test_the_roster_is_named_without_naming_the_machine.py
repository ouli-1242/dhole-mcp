"""名册要说得出是**哪个文件**，但不必说出它在这台机器的哪一块盘上（R02-055 · R02-F72）。

这个仓库里本来就有两句相反的话：

* `server.py` 三处把 `~/.dhole/cdp_hosts.json` 的**绝对路径**原样印进回执，钉它的测试写的理由是
  「读不出文件在哪，撤销就只能靠猜」；
* 本机信息遮蔽那一族（R02-045 / 048 / 049、`paths.mask_machine_paths` 的条例、
  `test_the_answer_carries_no_disk_path`）说的是**相反**的一件事：回执替 transcript 留下一份
  本机路径，泄漏的是一位用户的主目录与用户名，而它要表达的"哪个文件"用名字就说清了。

两句话不必由"哪一族赢了"来收场，因为它们各自要满足的读者不同：**撤销**这件事从来不需要路径
（`sys_state(clear=['cdp:host=<name>'])` 自己就找得到文件），需要路径的是**手删/巡检**那一侧，
而那一侧的读者坐在自己 terminal 前 —— `dhole doctor` 今天就在打印绝对路径
（`updater.py` 的 cdp 两行，并被 `…:278` 钉着"体检报告里要有落脚点"）。

所以这一把把门统一成：**回执只给名字与"去哪问位置"，位置由体检报告给**。三条 wire 句子共用
一个作者（`server._cdp_roster_hint`），免得下一把刀只改一处而把相反的两句话留在同一个产品里。
"""

from __future__ import annotations

import asyncio
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

from dhole_mcp import updater
import dhole_mcp.server as server_mod
from dhole_mcp.server import (
    MasterFetchServer,
    _cdp_attach_gate,
    _cdp_hosts_file,
    _cdp_write_records,
)

CDP = "http://127.0.0.1:9222"
HOST = "a.example.test"
SITE = "https://a.example.test/1"
OTHER = "b.example.test"
FILE_NAME = "cdp_hosts.json"


def _iso(days_ago: float) -> str:
    return (datetime.now(timezone.utc)
            - timedelta(days=days_ago)).isoformat(timespec="seconds")


def _roster_dir() -> str:
    """名册所在的**那一个目录**（挂具里是每条用例的 tmp_path，真机上是 dhole 的 home）。

    判据比的是"目录本身出没上回执"，不比绝对路径的写法：把盘符或分隔符写进断言，
    下一台机器上同一句话就会假绿。
    """
    return str(pathlib.Path(_cdp_hosts_file()).parent)


@pytest.fixture(autouse=True)
def _fresh_roster():
    _roster_dir_path = pathlib.Path(_cdp_hosts_file())
    _roster_dir_path.unlink(missing_ok=True)
    server_mod._cdp_forget_roster()
    yield
    server_mod._cdp_forget_roster()


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("DHOLE_HOME", str(home))
    return home


def _gate(confirm: bool = False):
    return asyncio.run(_cdp_attach_gate(CDP, confirm=confirm, targets=[SITE]))


def _sys_state(**kwargs):
    return asyncio.run(MasterFetchServer().sys_state(**kwargs))


# ─────────────────── 三条 wire 句子：名字要在，目录不能在 ───────────────────

def test_the_refusal_names_the_file_but_not_this_disk():
    """拒答那句要留得下"是哪个文件"，但不该顺手把用户的主目录交进 transcript。"""
    error, allowed = _gate()
    assert allowed is None and error is not None
    assert FILE_NAME in error, error
    assert _roster_dir() not in error, "回执替这台机器留了地址: " + error[-200:]
    assert "doctor" in error, "没告诉读者去哪问位置: " + error[-200:]


def test_the_census_names_the_file_but_not_this_disk():
    _cdp_write_records({HOST: _iso(4)})
    out = _sys_state(show=["cdp_hosts"])
    breath = " ".join([out.message, out.next_action])
    assert FILE_NAME in breath, out.next_action
    assert _roster_dir() not in breath, breath[-260:]
    assert "doctor" in breath, breath[-260:]
    # 撤销的处方本身没被牵连：它一直是"命令"而不是"路径"
    assert "clear=['cdp:host=" in breath, breath[-260:]


def test_the_revoke_receipt_names_the_file_but_not_this_disk():
    _cdp_write_records({HOST: _iso(2), OTHER: _iso(20)})
    out = _sys_state(clear=[f"cdp:host={OTHER}"], confirm=True)
    breath = " ".join([out.message, out.next_action])
    assert "1 CDP attach approval(s) revoked" in out.message, out.message
    assert FILE_NAME in breath, out.message
    assert _roster_dir() not in breath, breath[-260:]
    assert not out.error, f"成功的撤销回执里记了一次存储失败: {out.error}"
    # 句号重复是这一族句子的老毛病（修前那句尾巴就是 `cdp_hosts.json..`：模板自己点一个，
    # 外层装配又补一个）。新句子不带结尾句号，所以这里能钉得住。
    assert ".." not in out.message.replace("cdp_hosts.json", "<f>"), out.message[-160:]


def test_the_three_sites_quote_one_author():
    """三处句子必须抄同一句话 —— 分头写就迟早只改一处，两句相反的话又回到同一个产品里。"""
    hint = getattr(server_mod, "_cdp_roster_hint", None)
    assert callable(hint), "还没有那个唯一作者（本刀要建它，而不是在三处各写一遍）"
    text = hint()
    assert FILE_NAME in text and _roster_dir() not in text, text

    refusal = _gate()[0]
    # 普查那一句只在名册**非空**时才说得出（名册空时它没有可报的），所以先把记录落到盘上：
    # 反过来写会得到一句"Nothing erased."的绿，把要钉的那一格静默跳过。
    _cdp_write_records({HOST: _iso(2)})
    census = _sys_state(show=["cdp_hosts"])
    _cdp_write_records({HOST: _iso(2), OTHER: _iso(20)})
    receipt = _sys_state(clear=[f"cdp:host={OTHER}"], confirm=True)
    census_breath = " ".join([census.message, census.next_action])
    receipt_breath = " ".join([receipt.message, receipt.next_action])
    for label, blob in (("拒答", refusal), ("普查", census_breath), ("撤销", receipt_breath)):
        assert text in blob, f"{label} 那句不是同一个作者: {blob[-200:]}"
    for label, blob in (("拒答", refusal), ("普查", census_breath), ("撤销", receipt_breath)):
        assert _roster_dir() not in blob, f"{label} 仍把目录印上线: {blob[-200:]}"


# ────────────────── 操作员那一侧的出口不许被顺手拆掉 ──────────────────

def test_doctor_still_prints_the_absolute_path(isolated_home, capsys):
    """门挪到 wire 这一侧，另一侧必须还在：手删/巡检的人要拿得到真实地址。

    这一格与上面三格是同一把刀的两半 —— 只改 wire 而把 doctor 一起遮了，才是真的把
    "撤销有落脚点"这一条要求丢掉了。
    """
    _cdp_write_records({HOST: _iso(4)})
    updater.doctor()
    out = capsys.readouterr().out
    assert _cdp_hosts_file() in out, "体检报告不再说出名册在哪，操作员这一侧就没了地址"
    assert FILE_NAME in out


def test_the_pointer_answers_even_with_an_empty_roster(isolated_home, capsys):
    """名册**空着**时那句去处也要落得下 —— 那恰恰是拒答最常出现的情形。

    `dhole doctor` 的 cdp 那两行从前只在名册非空时才说地址（`updater.py` 的 `if _cdp_live or
    _cdp_stale` 两支；else 那一支的 fix 是空字符串），于是"位置由体检报告给"这句话在最需要它的
    那一刻恰好问不到。本刀把 else 那一支补上一行 `the roster file is <path> (nothing in it yet)`。

    判据走 fix 那一行而不是 detail：detail 一律过 `_short(...)`，第一步就把本机 home 折成 `~`，
    长路径还会被裁成尾巴；fix 是原样印的（操作员那一侧要的就是能复制的地址）。
    """
    assert not pathlib.Path(_cdp_hosts_file()).exists(), "挂具没从空名册开始"
    updater.doctor()
    out = capsys.readouterr().out
    assert "cdp fence" in out, out[-400:]
    assert _cdp_hosts_file() in out, (
        "名册空着时体检报告不说名册在哪 ⇒ 回执那句「run `dhole doctor` for its full path」"
        "在第一次 attach 被拒的那一刻是一次空头承诺")
    assert "nothing in it yet" in out, "说了地址却没说这是空的，读的人会以为名册里有人"


def test_the_masker_is_still_the_last_resort_not_the_first_line():
    """形状对照：产品里已有`paths.mask_machine_paths`这一手，但这一把不靠它兜底。

    靠遮蔽器意味着**先**把绝对路径拼进句子再折回去 —— 那台机器上 DHOLE_HOME 指向别处
    （`D:\\data\\dhole`）时遮蔽器一个都不折，路径照样上线。第一句就不写地址才与
    `paths.py` 那条条例一致：「可诊断性不该靠泄漏来换」。
    """
    elsewhere = pathlib.Path("D:/somewhere-else/dhole-home")
    raw = str(elsewhere / FILE_NAME)
    hint = getattr(server_mod, "_cdp_roster_hint", None)
    assert callable(hint)
    assert raw not in hint(), "句子把某个具体 home 拼进去了 —— 那就换不了家"
