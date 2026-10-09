"""成对参数的**反向**组合也要具名拒绝，不许静默忽略。

2026-10-09 独立审计 D6（真机复现）。正向早就具名拒绝了：

    {"nested_selector": ".card"}   无 nested_fields → 「nested_selector= needs
                                                   nested_fields=」

反向却是**静默忽略**，summary 与普通读取一字不差：

    {"nested_fields": [...]}       无 nested_selector → 整页照读，两层抽取没发生
    {"nested_max": 3}              无 nested_selector → 一个"收窄"旋钮点了没反应
    {"focus_mode": "best"}         无 focus          → schema 明写 "Requires focus."

后一颗最刺眼：**wire 上自己承诺了会拒**（`focus_mode` 的描述里写着 Requires
focus），而实现把它吞了。wire 承诺 / 实现不做，是同一份合同的两半对不上。

判据是「说得清」而不是「失败了」：每一条拒绝都要点名缺的那颗参数、说清它是被
**忽略**了而不是生效了、并给出一条能立刻照做的出路。只报「参数不对」的拒绝等于把
调用方留在原地 —— 而静默忽略更糟，它让调用方带着错误的信念继续往下走。
"""

from __future__ import annotations

import pytest

from dhole_mcp import server as server_mod
from dhole_mcp.models import ResponseModel
from dhole_mcp.server import MasterFetchServer


@pytest.fixture
def server(monkeypatch):
    """一个**一个包都发不出**的 server：这些拒绝必须发生在取数之前。

    绊线挂在 `validate_url` 上而不是某个 tier 上：每一条取数路径（HTTP / stealthy /
    archive / bulk）都要先过它，所以"走到这里"就等于"已经打算发请求了" —— 那时
    拒绝就不再是"在门口拦下"，而这条纪律的全部价值恰恰在门口。
    """
    def _never(*a, **kw):  # pragma: no cover - 被调用即失败
        raise AssertionError("参数配对不成立时不该发出任何请求")
    monkeypatch.setattr(server_mod, "validate_url", _never)
    return MasterFetchServer(cache_ttl=0)


def _err(result: ResponseModel) -> str:
    return (result.error or "") + " || " + (result.next_action or "")


@pytest.mark.asyncio
async def test_nested_fields_without_a_selector_is_refused(server):
    r = await server.web_read(
        "https://site.test/", nested_fields={"title": "h1"})
    assert "nested_selector" in _err(r), r.error
    assert "nested_fields" in _err(r), r.error


@pytest.mark.asyncio
async def test_nested_max_without_a_selector_is_refused(server):
    """`nested_max` 单飞最容易被忽略：它看起来只是个上限旋钮。"""
    r = await server.web_read("https://site.test/", nested_max=3)
    assert "nested_selector" in _err(r), r.error
    assert "nested_max" in _err(r), r.error


@pytest.mark.asyncio
async def test_focus_mode_without_focus_is_refused(server):
    """wire 上写着 "Requires focus." —— 实现必须真的拒，否则是合同的两半对不上。"""
    r = await server.web_read("https://site.test/", focus_mode="best")
    assert "focus_mode" in _err(r), r.error
    assert "focus" in _err(r), r.error


@pytest.mark.asyncio
async def test_the_refusal_says_it_was_ignored_not_applied(server):
    """出路要说得清是"被忽略了"，否则调用方会以为自己拿到的是整页抽取结果。"""
    r = await server.web_read("https://site.test/", nested_fields={"title": "h1"})
    assert "ignored" in (r.next_action or ""), r.next_action
    assert "Nothing was fetched" in (r.next_action or ""), r.next_action


@pytest.mark.asyncio
async def test_a_whitespace_focus_still_counts_as_missing(server):
    """`focus="  "` 不是一次聚焦 —— `_FOCUS` 那条路本来就把它当"没给"。"""
    r = await server.web_read("https://site.test/", focus="   ", focus_mode="best")
    assert "focus_mode" in _err(r), r.error


@pytest.mark.asyncio
async def test_the_forward_pairing_is_still_refused_too(server):
    """反向修好了，正向不许被顺手改坏 —— 两个方向是同一条纪律。"""
    r = await server.web_read("https://site.test/", nested_selector=".card")
    assert "nested_fields" in _err(r), r.error
