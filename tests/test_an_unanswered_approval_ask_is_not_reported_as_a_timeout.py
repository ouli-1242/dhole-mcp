"""一次没人答的批准不许自称"这一档超时了" —— 问人有自己的上限，到期走已有的那条 fail-closed 行。

症状（D-35，实测 `.scratch/liveverify/d35_silence.py`：自写的真 stdio 客户端在 `initialize`
里声明 elicitation，收到 dhole 的 ask 之后**按剧本不作答**；靶站仍是本机 127.0.0.1 的自建表单）：

  · 默认预算那一发 **26.5 秒**返回 `timeout: the 25000ms call budget ran out during the
    actions (stealthy) tier`，`metadata.actions` 里**只剩 `fill` 那一行**，本该在的 pending
    提交行整条消失；`summary` 写 "network error · 0B markdown"，`next_action` 劝人
    "Raise timeout (e.g. timeout=60000) … force_fetcher='http' … or switch source"。
  · 带 `timeout=120000` 的那一发同一形状，**121.5 秒**。

安全侧一直是成立的：两发的靶站台账 `posts.jsonl` 都没有多行（提交确实没发出去）。坏的只是
**回执说的话** —— 三句话没有一个读者能从中知道"有一个人在你没答的那颗按钮前面等着"。

机制读得通也修得准：`out_of_band_confirm` 等人按键没有自己的上限，所以先到期的是外面那层
调用预算，而它掐的是整条动作链（`_submit_gate_row` 还没来得及 append）。修法给问人一个
上界（`elicit_wait_s`：不超过 `ELICIT_WAIT_MAX_S`，也不超过这次调用剩余预算的一半；剩余
不足 `ELICIT_ASK_MIN_LEFT_S` 就压根不问），到期与 decline / cancel / 问不到人同权 ——
走的是**那条已经存在的** fail-closed 出路（pending 行、`(unconfirmed)` 后缀、
"declined or could not reach a human" 那句全都现成），链照常在预算内跑完。
上限读的是 `_within_call_budget` 发布的那份预算（`context.call_deadline`），所以
**问人不可能活得比叫它问的这次调用更长** —— 最后一条判据就是这句话的镜像。
"""

from __future__ import annotations

import asyncio
import time

import pytest

from dhole_mcp import context as C
from dhole_mcp.context import (ELICIT_ASK_MIN_LEFT_S, ELICIT_WAIT_MAX_S,
                                call_deadline, elicit_wait_s, out_of_band_confirm,
                                set_mcp_session)


class _Form:
    """一只会按剧本办事的假 elicitation 通道。"""

    def __init__(self, *, capable=True, action=None, hangs=False, raises=None,
                 no_back_channel=False):
        self.capable = capable
        self.action = action
        self.hangs = hangs
        self.raises = raises
        self.no_back_channel = no_back_channel
        self.asked = 0
        self.cancelled = False

    def check_client_capability(self, caps):
        return self.capable

    async def elicit_form(self, message, schema):
        self.asked += 1
        if self.no_back_channel:
            from mcp.shared.exceptions import NoBackChannelError
            raise NoBackChannelError("no channel")
        if self.raises:
            raise self.raises
        if self.hangs:
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                self.cancelled = True
                raise
            raise AssertionError("unreachable")
        return type("R", (), {"action": self.action})()


@pytest.fixture(autouse=True)
def _clean_channel():
    yield
    set_mcp_session(None)
    assert elicit_wait_s() == ELICIT_WAIT_MAX_S, "截止没被摘掉 = 下一次调用按上一次的预算问人"


class TestTheAskHasItsOwnClock:
    """上限不是写死的秒数，是"这次调用还剩多少"的一个分数 —— 两条边界都得有判据。"""

    def test_no_published_budget_gives_the_flat_reading_window(self):
        assert elicit_wait_s() == ELICIT_WAIT_MAX_S

    def test_the_ask_never_takes_more_than_half_of_what_the_call_has_left(self):
        with call_deadline(25.0):
            wait = elicit_wait_s()
        assert 12.0 < wait <= 12.5, f"默认预算那一发：问人占一半, 实测 {wait}"
        assert wait * 2 <= 25.0, "超过一半就是抢走调用自己的时间"

    def test_a_long_budget_still_caps_at_the_reading_window(self):
        with call_deadline(120.0):
            assert elicit_wait_s() == ELICIT_WAIT_MAX_S, (
                "60 秒不是更宽的批准窗口，是一次没人按键的等待把一次调用钉住一分钟")

    @pytest.mark.parametrize("left", [4.9, 2.0, 0.0, -3.0])
    def test_a_call_with_no_room_left_is_not_asked_at_all(self, left):
        with call_deadline(left):
            assert elicit_wait_s() == 0.0, (
                f"剩余 {left}s 连读完一句话都不够，几秒的弹窗不是批准（阈值 "
                f"{ELICIT_ASK_MIN_LEFT_S}s）")

    def test_the_deadline_is_taken_back_even_when_the_body_raises(self):
        with pytest.raises(RuntimeError):
            with call_deadline(10.0):
                raise RuntimeError("tier blew up")
        assert C._CALL_DEADLINE.get() is None


class TestTheUnansweredAskFailsClosed:
    """到期不是"没测出来"，是三个既有答案里的第二个：False。"""

    @pytest.mark.asyncio
    async def test_an_expired_ask_returns_false_inside_its_own_window(self):
        form = _Form(hangs=True)
        set_mcp_session(form)
        with call_deadline(6.0):                  # → 上限 3.0 秒
            t0 = time.monotonic()
            got = await out_of_band_confirm("approve ONE form submission?")
            spent = time.monotonic() - t0
        assert got is False
        assert 2.5 < spent < 4.0, f"到期就该在窗口里回来，实测 {spent:.2f}s"

    @pytest.mark.asyncio
    async def test_the_waited_on_form_is_cancelled_not_abandoned(self):
        """挂着不管就是留一个孤儿请求：下一次同 id 的应答会落在它身上。"""
        form = _Form(hangs=True)
        set_mcp_session(form)
        with call_deadline(6.0):
            await out_of_band_confirm("approve?")
        assert form.cancelled is True

    @pytest.mark.asyncio
    async def test_no_window_means_no_request_sent_at_all(self):
        """契约锁，不是变异实验能抓住的差别：把 `wait <= 0` 那个分支删掉，今天也不发
        （CPython 3.14 的 `wait_for(coro, 0)` 不让协程起步，实测如此）。这里钉的是
        "明知没人来得及答就不把弹窗推出去"这句话本身 —— 内部调度一改，它就是那条不许变的话。
        """
        form = _Form(hangs=True)
        set_mcp_session(form)
        with call_deadline(2.0):
            got = await out_of_band_confirm("approve?")
        assert got is False
        assert form.asked == 0

    @pytest.mark.asyncio
    @pytest.mark.parametrize("action,expect", [("accept", True), ("decline", False),
                                               ("cancel", False)])
    async def test_an_answered_ask_behaves_exactly_as_before(self, action, expect):
        form = _Form(action=action)
        set_mcp_session(form)
        assert await out_of_band_confirm("approve?") is expect

    @pytest.mark.asyncio
    async def test_a_connection_without_the_capability_still_answers_None(self):
        """能力为假 = 参数门那条老路，一字不改（包括不该被新上限碰到的事）。"""
        form = _Form(capable=False)
        set_mcp_session(form)
        assert await out_of_band_confirm("approve?") is None
        assert form.asked == 0

    @pytest.mark.asyncio
    async def test_the_no_backchannel_answer_failure_still_fails_closed(self):
        form = _Form(no_back_channel=True)
        set_mcp_session(form)
        assert await out_of_band_confirm("approve?") is False


class TestTheReceiptIsAboutTheAsk:
    """回执必须说"那颗按钮没人按"，而不是"这一档慢"。"""

    @pytest.mark.asyncio
    async def test_the_gate_row_is_the_existing_fail_closed_one(self):
        """confirm=true 救不回来：能力为真的连接上，参数不是钥匙（F-D-J1 的语义）。"""
        from dhole_mcp.actions import _submit_gate_row

        form = _Form(hangs=True)
        set_mcp_session(form)
        with call_deadline(6.0):
            row = await _submit_gate_row("submit 'button[name=send]'",
                                         confirm=True, rerun="(re-run)")
        assert row is not None
        assert row["pending_approval"] is True and row["ok"] is False
        assert row["error"].startswith("pending_approval: the approval ask was "
                                       "declined or could not reach a human"), row
        assert "nothing was submitted" in row["error"]

    @pytest.mark.asyncio
    async def test_the_ask_cannot_outlive_the_call_that_asked(self):
        """这一条是整件事的镜像：预算 3 秒、宿主不作答，交回的仍是动作链自己的结果，
        而不是 `_over_budget` 那份"这一档超时了"。修之前这里必然是超时结果。"""
        from dhole_mcp.server import MasterFetchServer

        srv = MasterFetchServer(cache_ttl=0)
        form = _Form(hangs=True)
        seen: dict = {}

        async def chain():
            set_mcp_session(form)
            seen["window"] = elicit_wait_s()
            got = await out_of_band_confirm("approve?")
            return f"chain finished, ask={got}"

        out = await srv._within_call_budget(chain(), "http://127.0.0.1:1/form",
                                           3000, "actions (stealthy) tier")
        assert isinstance(out, str), f"预算 3 秒的调用被一次问人拖成了超时回执：{out!r}"
        assert out.endswith("ask=False")
        assert seen["window"] == 0.0, (
            "3 秒的预算里剩余不足阈值，压根不该问 —— 问了就又是一次掐断")
