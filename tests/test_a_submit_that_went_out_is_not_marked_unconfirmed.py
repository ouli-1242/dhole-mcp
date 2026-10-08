"""一次真的发出去了提交，回执不许还写着 "(unconfirmed)"。

`action_label` 是在**执行之前**渲染的：那时"没有 confirm 旋钮"确实等于"这一步还没被
允许"，pending 行带着 `(unconfirmed)` 是如实的。但批次8 刀1 之后钥匙按连接分成两段
—— 声明了 elicitation 的连接上，是**人按键**批准的，`confirm` 参数根本不在场。于是
一条 `submitted: true` 的行仍然写着 "submit [2] (unconfirmed)"。

这一行同时是 DHOLE_USAGE_LOG 的写审计载荷（`audit.py` 的 browser_submit 通道取的就是
outcome 的 action 串），所以后果不是"读者看见一句别扭的话"，而是**审计里每一笔由人批
准的提交都被记成未经确认** —— 那份日志存在的意义恰好是回答"这一步是谁允许的"。

判据取两头：
  * 发出去的行 —— 后缀必须没有（它说的已经不是事实）。
  * 被闸拦下的行 —— 后缀必须留着（那一步真的没发出去，去掉它才是另一种假话）。

`click` 落在提交型控件上走同一道闸（评审R1刀1），所以同一条规矩也管它那一族的行。
"""
from __future__ import annotations

import pytest

from dhole_mcp.actions import action_label, build_page_action
from dhole_mcp.context import set_mcp_session

MARK = " (unconfirmed)"


class _FakeElement:
    def __init__(self, kind=None):
        self.kind = kind
        self.clicked: list[str] = []

    async def click(self, timeout=None):
        self.clicked.append("buy")

    async def evaluate(self, js):
        if "el.form" in js:
            return self.kind
        return "Buy now"

    async def wait_for(self, **kw):
        pass


class _FakeLocator:
    def __init__(self, el):
        self._el = el

    async def click(self, timeout=None):
        await self._el.click()

    async def evaluate(self, js):
        return await self._el.evaluate(js)

    async def wait_for(self, **kw):
        await self._el.wait_for(**kw)


class _StubSession:
    """SDK ServerSession 的最小桩，只回答两件事：能不能问、人按了哪个键。"""

    def __init__(self, action="accept"):
        self._action = action
        self.asked: list[str] = []

    def check_client_capability(self, caps):
        return bool(getattr(caps, "elicitation", None))

    async def elicit_form(self, message, requested_schema):
        self.asked.append(message)
        from mcp.types import ElicitResult
        return ElicitResult(action=self._action)


async def _run(monkeypatch, actions, el=None):
    el = el or _FakeElement()

    async def fake_locator(page, target):
        return _FakeLocator(el)

    async def fake_settle(page, loc):
        await loc.click()

    monkeypatch.setattr("dhole_mcp.actions._target_locator", fake_locator)
    monkeypatch.setattr("dhole_mcp.actions._settle_after_click", fake_settle)
    pa = build_page_action(actions)
    await pa(None)
    return pa, el


@pytest.fixture(autouse=True)
def _no_session():
    yield
    set_mcp_session(None)


# ─── 1 · 发出去的行 ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_submit_approved_by_a_human_is_not_marked_unconfirmed(monkeypatch):
    """人在弹窗上点了接受 → 行里不该再有"未经确认"那句话。"""
    set_mcp_session(_StubSession(action="accept"))
    pa, el = await _run(monkeypatch, [{"submit": {"index": 2}}])
    (row,) = pa.outcomes
    assert row["ok"] is True and row.get("submitted") is True, row
    assert el.clicked == ["buy"], "行说发出去了，得真的发出去"
    assert row["action"] == "submit [2]", (
        f"一条成功提交的行还挂着审计读不懂的后缀：{row['action']!r}")
    assert MARK not in row["action"]


@pytest.mark.asyncio
async def test_a_submit_approved_by_a_parameter_is_not_marked_either(monkeypatch):
    """反向：`confirm=true` 那条路本来就不带后缀，这次修补不许把它改成别的。"""
    set_mcp_session(None)
    pa, _ = await _run(monkeypatch, [{"submit": {"index": 2, "confirm": True}}])
    (row,) = pa.outcomes
    assert row["ok"] is True and row["action"] == "submit [2]"


@pytest.mark.asyncio
async def test_a_click_on_a_submit_control_follows_the_same_rule(monkeypatch):
    """同一个不可逆结果、同一道闸，也就该有同一句回执。"""
    set_mcp_session(_StubSession(action="accept"))
    pa, _ = await _run(monkeypatch, [{"click": {"index": 5}}],
                       el=_FakeElement(kind="submit"))
    (row,) = pa.outcomes
    assert row["ok"] is True and row.get("submitted") is True, row
    assert not row["action"].endswith(MARK), row["action"]


# ─── 2 · 被拦下的行必须还带着它 ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_paused_submit_keeps_the_marker(monkeypatch):
    """没发出去的那一步，"(unconfirmed)" 是唯一如实的说法 —— 不许一起删掉。"""
    set_mcp_session(_StubSession(action="decline"))
    pa, el = await _run(monkeypatch, [{"submit": {"index": 2}}])
    (row,) = pa.outcomes
    assert row["ok"] is False and row.get("pending_approval") is True, row
    assert el.clicked == [], "被拒的提交不许真的发出去"
    assert row["action"].endswith(MARK), (
        f"pending 行丢了它的状态：{row['action']!r}")


@pytest.mark.asyncio
async def test_a_failed_submit_row_does_not_claim_confirmation(monkeypatch):
    """动作本身失败（ locator 都解析不出来）时，行里的状态位与后缀各说各的。

    这一条守的是修补的边界：`_row_label` 只在**已提交**的时候去掉后缀，别的时候
    一字不动 —— 否则失败行会看起来像"确认过的成功"。
    """
    set_mcp_session(None)

    async def boom(page, target):
        raise RuntimeError("no such element")

    monkeypatch.setattr("dhole_mcp.actions._target_locator", boom)
    pa = build_page_action([{"submit": {"index": 2}}])
    await pa(None)
    (row,) = pa.outcomes
    assert row["ok"] is False and "submitted" not in row, row
    assert row["action"].endswith(MARK), row["action"]


# ─── 3 · 标签函数本身的分工 ──────────────────────────────────────────────────

def test_the_label_describes_the_request_not_the_outcome():
    """执行之前 `action_label` 说的仍是"旋钮在不在"，不是"人点没点"。

    两个时刻各自有各自的真相，混在一起就会写出一个永远是对半错的字符串。
    """
    assert action_label({"submit": {"index": 2}}) == f"submit [2]{MARK}"
    assert action_label({"submit": {"index": 2, "confirm": True}}) == "submit [2]"
