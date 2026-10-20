# -*- coding: utf-8 -*-
"""装不下一张截图的预算，要在开始之前点名拒绝，而不是把它吃掉。

症状（15 场景真人体测，场景 10）：`timeout` 给的一笔小预算 + 一条带 `screenshot` 的链，
浏览器照样起来、整页资源照样加载（截图那一步要求不丢 image/stylesheet/font —— 丢了就是一
张破图占位符，`decode_qr` 回 `[]` 而动作自己报 `ok:true`）。于是这笔预算必然花光在"一件
本来就做不到的事"上，交回的是 0 字节加一句关于站点的错话。

期 1.2 补的是**事后**那一半（被掐的那一步留下自己的行）；这一条管**事前**那一半：
一个请求都不发、一台浏览器都不起，直接说"这一步装不下"。

判据的形状：

1. 那个下限是**加法**，三个加数各有属主（起进程、导航后那一等安静、捕获自己的等待），
   不是一颗没人认得的魔法数。
2. 拒绝的话必须指出这一扇门上真存在的出路（`timeout=` 抬到那个数，或去掉截图那一步）；
   指一颗收不下的旋钮是期 1.5 那一族。
3. 顺序：拒绝发生在起浏览器之前 —— 由代码位置守，活体那半边在 `evals/verify_fixes.py`
   （它验"真的一个请求都没发"）。
4. 没点名预算的调用不许被拒：那时预算是默认的那一笔（25–30 秒），在门槛之上；拿一个
   调用方没给过的数去拒他，是新造的一种谎。
"""

from __future__ import annotations

import asyncio
import inspect
import json

from dhole_mcp import actions as actions_mod
from dhole_mcp import browser as browser_mod
from dhole_mcp import server as server_mod
from dhole_mcp.server import MasterFetchServer, _min_visual_call_ms


def test_the_floor_is_a_sum_of_its_owners_not_a_magic_number():
    floor = _min_visual_call_ms()
    assert floor == (browser_mod.BROWSER_LAUNCH_TYPICAL_MS
                     + browser_mod.SETTLE_IDLE_MS
                     + actions_mod.STEP_WAIT_MS), floor
    assert floor == 20_000, "加数被改了而这个数没跟上：三处属主里有人动了手"


def test_the_floor_sits_under_the_budget_a_caller_who_said_nothing_gets():
    """没点名预算的调用拿的是默认那一笔，它在门槛之上 —— 所以这一支永远不会误伤谁。"""
    assert server_mod.ACTIONS_DEFAULT_TIMEOUT_MS > _min_visual_call_ms(), (
        "默认预算掉到门槛之下：那么每一次默认的动作链截图都会被当场拒掉，"
        "而调用方一个数都没改过 —— 用一颗他没给过的数去拒他，是新造的一种谎")


def test_the_refusal_is_decided_before_any_browser_is_started():
    src = inspect.getsource(MasterFetchServer.web_read)
    raise_at = src.index("a screenshot needs the page's real resources")
    # 见证人要挑真的那一处：注释里也出现 `_force_fetch`（"它自己的默认值"那句），
    # 拿它当"开始了吗"的依据会一直绿到某天顺序真被换掉。`await self._within_call_budget(`
    # 是浏览器档那一条路的第一道门，它后面才是起进程。
    starts_at = src.index("await self._within_call_budget(")
    assert raise_at < starts_at, (
        "拒绝排在了起浏览器后面：那笔预算已经花掉了，这句拒绝就只是事后诸葛")


def test_the_way_out_names_only_doors_this_tool_actually_takes():
    """期 1.5 那一条判据用在自己身上：出路里的 `timeout=` 得是 `web_act` 收的那一颗。"""
    sentence = inspect.getsource(MasterFetchServer.web_read)
    take = sentence[sentence.index("Two doors:"):]
    assert "timeout=" in take, take[:120]
    for tool in ("web_act", "web_read"):
        owned = server_mod._TOP_LEVEL_ARGS[tool]
        assert "timeout" in owned, (tool, sorted(owned))
        assert "screenshot" not in owned, (
            "那一步是 `actions` 载荷里的键，不是顶层参数 —— 话里也不许把它写成顶层")


def test_the_refusal_reaches_the_protocol_as_a_designed_refusal():
    """`ArgumentRefusal` 而不是裸 ValueError：裸的那一种会从 transport 兜底裸逃（刀1）。"""
    src = inspect.getsource(MasterFetchServer.web_read)
    at = src.index("a screenshot needs the page's real resources")
    head = src[max(0, at - 400):at]
    assert "raise ArgumentRefusal(" in head, head[-200:]


def test_a_call_that_named_nothing_is_not_refused_for_a_number_it_never_gave():
    src = inspect.getsource(MasterFetchServer.web_read)
    at = src.index("actions_need_resources = actions_want_visual(actions)")
    window = src[at:at + 400]
    assert "timeout is not None" in window, window[:300]


def test_the_cause_sentence_still_tells_which_action_needs_the_resources():
    """截图是唯一"产物就是渲染出来的页面"的动作 —— 那句话得说的是这一件，不是泛泛的慢。"""
    assert "the only action whose" in inspect.getsource(MasterFetchServer.web_read)
    doc = inspect.getdoc(actions_mod.actions_want_visual) or ""
    assert "product IS the rendered page" in doc, doc[:120]


def test_the_idle_cap_and_the_launch_estimate_are_public_because_two_modules_read_them():
    """私有名下跨模块读会逼出第二次抄写 —— 这两颗都有了第二个读者，所以是公开名。"""
    for name in ("SETTLE_IDLE_MS", "BROWSER_LAUNCH_TYPICAL_MS"):
        assert hasattr(browser_mod, name), name
        assert not name.startswith("_")
    assert hasattr(actions_mod, "STEP_WAIT_MS")
    # 加法只在一处：browser/actions 都不许**定义**这个下限（注释里指名它读给谁看是好事，
    # 再算一遍才是第二个作者）
    assert "def _min_visual_call_ms" not in inspect.getsource(browser_mod)
    assert "def _min_visual_call_ms" not in inspect.getsource(actions_mod)


def test_the_refusal_says_nothing_was_sent():
    """一支 0 秒的拒绝必须说明"什么都没发生"，否则读的人不知道页面被碰过没有。"""
    src = inspect.getsource(MasterFetchServer.web_read)
    at = src.index("a screenshot needs the page's real resources")
    window = src[at:at + 900]
    assert "Nothing was fetched and no browser was started" in window, window[:220]


# ─── 协议面：agent 拿到的那一封信 ────────────────────────────────

async def _through_the_real_sdk(tool: str, args: dict) -> dict:
    """真握手：InMemoryTransport 包住的正是 stdio/HTTP 跑的同一个 handler。

    robots 那一道预检会真的去取一次 robots.txt，而这一支的拒绝发生在它**之后**、起浏览器
    之前 —— 所以用例把 robots 关掉（这颗开关是配置面现有的），让这条测试保持零网络。
    """
    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport

    transport = InMemoryTransport(MasterFetchServer()._mcp_server())
    async with transport as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            res = await session.call_tool(tool, args)
    return json.loads("".join(getattr(b, "text", "") for b in res.content))


def test_the_agent_sees_an_envelope_not_a_naked_transport_error(monkeypatch):
    """拒绝在 `_dispatch` 抛，折成信封的是 `call_tool` —— agent 那一侧必须是齐全的信封。

    裸的那一种只有 `{"error": str(e)[:300]}`，而 300 个字符剪掉的正是出路那半句（刀1 与
    批次 4 的同一族）。这里断的是**这一次新加的句子**走完整条边界，不是复用别人的结论。
    """
    monkeypatch.setenv("DHOLE_IGNORE_ROBOTS", "1")
    payload = asyncio.run(_through_the_real_sdk(
        "web_act", {"url": "https://example.com/checkout",
                    "actions": [{"screenshot": {"selector": "#qty"}}],
                    "timeout": 3_000}))
    assert payload.get("error", "").startswith("invalid_request"), payload
    assert payload.get("content_ok") is False, payload
    assert str(_min_visual_call_ms() // 1000) in (
        str(payload["error"]) + str(payload.get("next_action"))), payload
    assert "timeout=" in payload["next_action"], payload["next_action"][:200]
    assert "Nothing was fetched and no browser was started" in payload["next_action"]
    assert payload.get("summary"), "拒绝不许到达时没有 summary"


def test_a_budget_above_the_floor_still_runs_the_chain():
    """反面守卫：门槛之上这一支必须完全不出声，否则它就变成新的截图许可门。

    真跑一次截图要起浏览器（活半边在 `evals/verify_fixes.py` 的 D1 —— 那条走的就是**没点名
    timeout** 的路径，误伤会在那一条上红）。这里守的是代码形状：那一支只在两个条件同时成立
    时才存在，`timeout is None` 这一支根本不看下限。
    """
    src = inspect.getsource(MasterFetchServer.web_read)
    at = src.index("if int(timeout) < floor:")
    guard = src[src.index("if actions_need_resources and timeout is not None"):at + 40]
    assert "timeout is not None" in guard and "int(timeout) < floor" in guard, guard


def test_no_wire_face_moved_while_this_landed():
    """这一刀全在响应侧与拒绝话术里：常驻层一个字都没加（棘轮的余量是 0）。"""
    import json
    from pathlib import Path
    lock = json.loads((Path(__file__).resolve().parents[1] / "tests"
                       / "tool_defs_lock.json").read_text(encoding="utf-8"))
    assert lock["tools"] == 11
    assert "screenshot" not in str(
        [p for d in MasterFetchServer._TOOL_DEFS
         for p in d["inputSchema"]["properties"]])
