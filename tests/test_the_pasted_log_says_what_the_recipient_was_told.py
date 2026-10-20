# -*- coding: utf-8 -*-
"""日志不许比收件人知道得多（2026-10-18 · R02-F108 · R02-127）

现场（`review/.runtime/p1/r231_audit_outlet.json` · 7 面真接线 HTTP + `r231b_exception_branch.json`
· 7 面异常形状 + `r232_log_mirrors_receipt.json` · 5 面并排取词 + `r232b_exception_branch_mirrors.json`
· 两条不经信封的分支各造一颗抬真异常的格子 · HEAD caf5957 · 零外网）：
14 面里**没有一颗**是「收件人遮过而日志裸着」，而且日志那一格逐字是收件人 `error` 的一段
（`_result_error` 交的就是收件人看到的那一份；参数拒绝那两支交 `str(e)`，信封只往前面加
`invalid_request: ` 一个抬头，所以仍是子集）。

账本先前写的是「遮蔽发生在信封装配那一步，审计走另一条数据流」—— 这条**归因**被量错了：
遮蔽不住在信封里，住在句子的作者手里（`parse.py:266/390`、`server.py:4814/4859/9770/9778/9796`），
所以它在两个出口**分岔之前**就已经发生。历史上 `usage.jsonl` 里那 44 行裸的读数按错误形状分族之后
也支持同一句：31 行 2026-10-08/09 的 `File not found`（D11 之前）、10 行 10-16~10-18 的守卫族
（F107 之前）、1 行写侧 ValueError（F107 之前）、3 行是调用方自己打出去的路径原样回来（F106 的例外）。
每一行的收件人那面同样裸着 —— 日志从来没排在收件人前面。

所以这把刀落的不是文案，是**锁**：今天这个不变量成立但没人钉着，而它一旦松开就是「把日志打包发给
维护者」那条路漏本机身份（§2.9 同一条轴）。契约按作者钉不按文案钉（F93 通则）：

  * 对每一面：日志写的 `error` 必须是收件人 `error` 里出现过的字（**不许自己另写一句**）；
  * 作者遮过的那几族：两个出口都遮过（只钉「一致」会放行「两处都裸」这种退步）；
  * 归属锁：把 `paths.mask_machine_paths` 换成空转，两个出口必须**一起**变裸 —— 各自遮一遍的两副
    面孔在这里会露馅（一个动一个不动）；
  * 调用方自己打出去的路径：两处都原样回来，且这把锁不许把它遮掉（F106 的例外不是漏洞）；
  * 正控制：日志仍然记得到这一发（`tool` / `ok` / 行数），别把审计修成静默。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from dhole_mcp.params import ArgumentRefusal
from dhole_mcp.server import MasterFetchServer

HOME = str(Path.home())
CWD = os.getcwd()
USER = (os.environ.get("USERNAME") or os.path.basename(HOME)).strip()
#: 这顶帽子的四种写法：真反斜杠 / 正斜杠 / 全小写 / 百分号编码（R02-125 认过的那一族）
HATS = [v for b in (HOME, CWD, USER)
        for v in (b, b.replace("\\", "/"), b.lower(),
                  b.replace("\\", "%5C").replace("/", "%2F").replace(":", "%3A")) if v]


def _carries_machine_identity(text: str) -> bool:
    return any(h in text for h in HATS)


@pytest.fixture
def usage_log(tmp_path, monkeypatch):
    """把审计出口搬到临时文件：`env_registry.value_of` 每次调用都读 env，所以钉得住，
    而且测试绝不往这台机器真实的 `~/.dhole/usage.jsonl` 里写字。"""
    target = tmp_path / "usage.jsonl"
    monkeypatch.setenv("DHOLE_USAGE_LOG", str(target))
    return target


def _rows(usage_log: Path, skip: int) -> list[dict]:
    if not usage_log.exists():
        return []
    lines = usage_log.read_text(encoding="utf-8").splitlines()
    return [json.loads(ln) for ln in lines[skip:] if ln.strip()]


async def _call_on(server: MasterFetchServer, tool: str, args: dict,
                   usage_log: Path) -> tuple[dict, list[dict]]:
    """一发调用，同时取回**收件人看到的那一封**与**日志新 append 的那几行**。

    传进来的实例就是跑这一发的实例 —— r232 头一版在这里造了个新对象，于是两棵「分支」格子
    其实走的是正常 `_dispatch`，读数绿得没有意义（§6.3 归量具，原版档案留着）。
    """
    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport

    skip = len(_rows(usage_log, 0))
    transport = InMemoryTransport(server._mcp_server())
    async with transport as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            res = await session.call_tool(tool, args)
    payload = json.loads("".join(getattr(b, "text", "") for b in res.content))
    return payload, _rows(usage_log, skip)


def _audit_error(rows: list[dict]) -> str:
    return " || ".join(str(r.get("error") or "") for r in rows)


#: 作者遮过的那几族（句子由 `parse` / `server` 的路径作者写下，两处都该是遮过的形状）
MASKED_FAMILIES = [
    ("路径不存在（家目录里的名字）",
     lambda: {"file_path": os.path.join(HOME, "r234-definitely-not-here.txt")}),
    ("受限目录守卫（家目录里的孩子）",
     lambda: {"file_path": os.path.join(HOME, ".ssh", "id_ed25519")}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("label,make", MASKED_FAMILIES)
async def test_the_masked_families_are_masked_on_both_outlets(label, make, usage_log):
    """作者遮过的那几族：日志不许留着一份遮前的句子（R02-127 被改口的那一句就是它）。"""
    payload, rows = await _call_on(MasterFetchServer(), "doc_parse", make(), usage_log)
    receipt = str(payload.get("error") or "")
    logged = _audit_error(rows)
    assert receipt and logged, (label, payload, rows)
    assert not _carries_machine_identity(receipt), (label, receipt[:120])
    assert not _carries_machine_identity(logged), (
        f"{label}：收件人这面已经遮过，日志那面却裸着 —— 两个出口两位作者。{logged[:120]}")


@pytest.mark.asyncio
@pytest.mark.parametrize("label,make", MASKED_FAMILIES)
async def test_the_log_writes_the_recipients_own_words(label, make, usage_log):
    """日志那一格必须是收件人 `error` 里出现过的字 —— 它不许自己另装配一句。

    这条比「两处都遮」更硬：它放行不了「日志在出口再套一层自己的遮蔽器」那种修法
    （套出来的句子收件人没见过，就不是镜像）。
    """
    payload, rows = await _call_on(MasterFetchServer(), "doc_parse", make(), usage_log)
    logged = _audit_error(rows)
    assert logged in str(payload.get("error") or ""), (label, logged[:150])


@pytest.mark.asyncio
async def test_a_refusal_raised_before_the_envelope_is_mirrored_too(usage_log):
    """不经信封那两条分支同样只是镜像：参数拒绝支与兜底 Exception 支各造一颗真抬的格子。

    分支取的是 `str(e)`，信封取的是同一颗 `str(e)`（前者只多加 `invalid_request: ` 抬头，
    后者截到 300），所以日志永远排在收件人后面或与他齐平 —— 这一颗用**真抬起来**的句子钉，
    message 里故意放着家目录的根。
    """
    leaky = f"a library exploded while opening {os.path.join(HOME, 'private', 'note.txt')}"
    for boom in (ArgumentRefusal(leaky, next_action="rewrite it."), RuntimeError(leaky)):
        server = MasterFetchServer()

        async def raising(*_a, **_k):
            raise boom

        server._dispatch = raising
        payload, rows = await _call_on(
            server, "doc_parse", {"file_path": os.path.join(HOME, "x.txt")}, usage_log)
        logged = _audit_error(rows)
        receipt = str(payload.get("error") or "")
        assert logged and logged in receipt, (type(boom).__name__, logged[:150])
        assert not (_carries_machine_identity(logged)
                    and not _carries_machine_identity(receipt)), type(boom).__name__


@pytest.mark.asyncio
async def test_the_two_outlets_move_with_one_author(usage_log, monkeypatch):
    """归属锁（F93）：把作者的遮蔽器换成空转，两个出口必须**一起**变裸。

    两处各自遮一遍的两副面孔在这里露馅 —— 那会是一个动、一个不动。
    """
    from dhole_mcp import paths as P

    monkeypatch.setattr(P, "mask_machine_paths", lambda text: text)
    payload, rows = await _call_on(
        MasterFetchServer(), "doc_parse",
        {"file_path": os.path.join(HOME, "r234-definitely-not-here.txt")}, usage_log)
    receipt = str(payload.get("error") or "")
    logged = _audit_error(rows)
    assert _carries_machine_identity(receipt), receipt[:150]
    assert _carries_machine_identity(logged), (
        f"收件人已经裸着而日志还遮着 ⇒ 日志有第二位作者：{logged[:150]}")
    assert logged in receipt, logged[:150]


@pytest.mark.asyncio
async def test_what_the_caller_typed_himself_still_comes_back_in_both(usage_log):
    """调用方自己打出去的路径两处都原样回来（F106 的例外）—— 这把锁不许把它遮掉。

    它要钉的是「镜像」，不是「遮」：把这条也遮了就是把收件人的证据改成了假话，
    而日志只是跟着他看到的那一封。
    """
    typed = os.path.join(HOME, "x.txt")
    payload, rows = await _call_on(MasterFetchServer(), "doc_parse",
                                   {"file_path": typed, "offset": typed}, usage_log)
    receipt = str(payload.get("error") or "")
    logged = _audit_error(rows)
    assert receipt.startswith("invalid_request:") and "offset" in receipt, receipt[:150]
    assert _carries_machine_identity(receipt), "调用方自己打的那颗路径不该被这把锁遮掉"
    assert logged in receipt, logged[:150]
    assert _carries_machine_identity(logged), logged[:150]


@pytest.mark.asyncio
async def test_the_log_still_records_the_call(usage_log, tmp_path):
    """正控制：锁不许把审计修成静默 —— 拒答那一发照样有一行在册且带着那句话。

    `ok` 的意思是「这一发跑完了没抬异常」，所以一次拒答是 `ok: true` **加** `error`
    （`audit._log_tool_call` 的 docstring 立的就是这个加法语义：`ok` 不改含义，失败的话
    搭在 `error` 里走）。这一颗要挡的是另一种退步：行还在、`error` 没了 —— 那正是
    `_result_error` 当初被加进来的原因（一次被 SSRF 守卫拒掉的 `feed_read` 记成了一条
    看着像成功的行）。
    """
    payload, rows = await _call_on(
        MasterFetchServer(), "doc_parse",
        {"file_path": os.path.join(HOME, "r234-definitely-not-here.txt")}, usage_log)
    assert len(rows) == 1, rows
    assert rows[0].get("tool") == "doc_parse"
    assert "error" in rows[0], f"审计被修成静默了：{rows[0]}"
    assert rows[0]["error"] in str(payload.get("error") or ""), rows[0]

    doc = tmp_path / "r234-readable.txt"
    doc.write_text("AAA1 BBB2 CCC3 DDD4 EEE5\n" + "w" * 60, encoding="utf-8")
    ok_payload, ok_rows = await _call_on(MasterFetchServer(), "doc_parse",
                                         {"file_path": str(doc)}, usage_log)
    assert not ok_payload.get("error"), ok_payload.get("error")
    assert len(ok_rows) == 1 and "error" not in ok_rows[0], ok_rows
