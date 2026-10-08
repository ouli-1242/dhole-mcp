"""落在提交型控件上的 click 必须过 submit 的闸（评审R1刀1）。

现状（2026-10-07 评审，quotes.toscrape.com/login 实测）：`[fill, fill,
{"click": 5}]`（5 = Login 按钮）无 confirm 直接提交成功——闸只认动作名
（"submit"），不认动作效果。原生 `{"submit": N}` 正确暂停，同一个不可逆
结果换个动作名就绕过去了。安全红线 3 说的是"提交必须经用户确认"，不是
"名为 submit 的键必须经用户确认"。

契约（本文件钉住）：
  * click 的目标解析为提交型控件（input[type=submit|image]、form 关联的
    缺省/submit 型 button；role=link 除外）→ 走 submit 同一道闸：无
    confirm → 行内 pending_approval，表单不动；有 confirm → 真点，回执
    带 submitted、进写审计。
  * 闸的两个分支（out-of-band ask / 参数 confirm）与 submit 逐位一致：
    能力为真的连接上人点头才放行，decline / 问不到人 → confirm=true 也
    救不回来（fail-closed）。
  * 普通控件（探测答"不是提交型"）的 click 一字不改——闸不发明。
  * 探测失败不发明闸：元素等不到 → TimeoutError 原样传播（click 本来也
    会在这里超时，同款失败）；页面读不出类型 → 返回"不是"，click 走既有
    路径。
  * confirm 落在 click 的 dict 上；字符串简写不带键——pending 行教它改写。
"""

from __future__ import annotations

import pytest

from dhole_mcp.actions import _validate_actions, build_page_action
from dhole_mcp.context import set_mcp_session


class _FakeElement:
    def __init__(self, name="登录", kind=None, probe="ok"):
        self._name = name
        self.kind = kind      # _SUBMIT_CONTROL_JS 的应答；None = 不是提交型控件
        self.probe = probe    # "boom" = 探测调用抛错（等得到元素，读不出类型）
        self.clicked: list[str] = []

    async def click(self, timeout=None):
        self.clicked.append(self._name)

    async def evaluate(self, js):
        if "el.form" in js:  # _SUBMIT_CONTROL_JS 的判别子（_NAME_JS 不含它）
            if self.probe == "boom":
                raise RuntimeError("evaluate unavailable")
            return self.kind
        return self._name

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


class _FakeRequest:
    def __init__(self, method="POST", resource_type="document"):
        self.method = method
        self.resource_type = resource_type


class _FakePage:
    """一页能听 request 的最小事理（D-29）：`submitted` 现在只认从这里发出去的请求。"""

    def __init__(self):
        self.url = "https://shop.example.com/checkout"
        self._listeners: list = []

    def on(self, event, handler):
        if event == "request":
            self._listeners.append(handler)

    def remove_listener(self, event, handler):
        if handler in self._listeners:
            self._listeners.remove(handler)

    def sent(self, request=None) -> None:
        for handler in list(self._listeners):
            handler(request or _FakeRequest())


class _StubSession:
    """SDK ServerSession 的最小桩（与批次8 刀1 的测试同一形状）。"""

    def __init__(self, capable=True, action="accept", fail=False):
        self._capable = capable
        self._action = action
        self._fail = fail
        self.asked: list[str] = []

    def check_client_capability(self, caps):
        return self._capable and bool(getattr(caps, "elicitation", None))

    async def elicit_form(self, message, requested_schema):
        self.asked.append(message)
        if self._fail:
            from mcp.shared.exceptions import NoBackChannelError
            raise NoBackChannelError("elicitation/create")
        from mcp.types import ElicitResult
        return ElicitResult(action=self._action)


class TestAClickThatWouldSubmit:

    @staticmethod
    async def _run(monkeypatch, actions, **el_kwargs):
        el = _FakeElement(**el_kwargs)
        page = _FakePage()

        async def fake_locator(page, target):
            return _FakeLocator(el)

        async def fake_settle(page, loc):
            await loc.click()
            if el.kind == "submit":
                page.sent()

        monkeypatch.setattr("dhole_mcp.actions._target_locator", fake_locator)
        monkeypatch.setattr("dhole_mcp.actions._settle_after_click", fake_settle)
        pa = build_page_action(actions)
        await pa(page)
        return pa, el

    @pytest.mark.asyncio
    async def test_an_unconfirmed_click_on_a_submit_control_pauses(
            self, monkeypatch):
        """与 {"submit": 2} 同一目标、同一后果 → 同一道闸：暂停，不点。"""
        pa, el = await self._run(monkeypatch, [{"click": {"index": 2}}],
                                 kind="submit")
        (row,) = pa.outcomes
        assert row["ok"] is False and row.get("pending_approval") is True
        assert "Nothing was submitted" in row["error"]
        assert el.clicked == [], "闸在点击之前：表单一个字节都没发"

    @pytest.mark.asyncio
    async def test_a_confirmed_click_on_a_submit_control_clicks(
            self, monkeypatch):
        set_mcp_session(None)
        pa, el = await self._run(
            monkeypatch, [{"click": {"index": 2, "confirm": True}}], kind="submit")
        (row,) = pa.outcomes
        assert row["ok"] is True and el.clicked
        assert row.get("submitted") is True, "这条链里没有 submit 动作，提交事实要落回执"

    @pytest.mark.asyncio
    async def test_a_string_selector_click_pauses_and_teaches_the_rewrite(
            self, monkeypatch):
        """字符串简写不带 confirm 键：暂停行教它换成 dict 写法。"""
        pa, el = await self._run(monkeypatch, [{"click": "input[type=submit]"}],
                                 kind="submit")
        (row,) = pa.outcomes
        assert row.get("pending_approval") is True and el.clicked == []
        assert "confirm" in row["error"] and "click" in row["error"]

    @pytest.mark.asyncio
    async def test_a_plain_control_is_never_gated(self, monkeypatch):
        """探测答"不是提交型" → click 一字不改。闸不发明。"""
        pa, el = await self._run(monkeypatch, [{"click": {"index": 2}}], kind=None)
        (row,) = pa.outcomes
        assert row["ok"] is True and el.clicked
        assert "pending_approval" not in row and "submitted" not in row

    @pytest.mark.asyncio
    async def test_a_probe_that_cannot_read_the_element_invents_no_gate(
            self, monkeypatch):
        """探测本身炸了（等得到元素、读不出类型）→ 不虚构关卡，click 照旧。"""
        pa, el = await self._run(monkeypatch, [{"click": {"index": 2}}],
                                 kind="submit", probe="boom")
        (row,) = pa.outcomes
        assert row["ok"] is True and el.clicked
        assert "pending_approval" not in row


class TestClickConfirmValidation:

    def test_confirm_rides_the_click_when_true(self):
        (a,) = _validate_actions([{"click": {"index": 2, "confirm": True}}])
        assert a == {"click": {"index": 2, "confirm": True}}

    def test_confirm_absent_or_false_keeps_the_shape_bit_identical(self):
        (a,) = _validate_actions([{"click": {"index": 2}}])
        assert a == {"click": {"index": 2}}
        (b,) = _validate_actions([{"click": {"selector": "#go", "confirm": False}}])
        assert b == {"click": {"selector": "#go"}}

    def test_confirm_must_be_a_boolean(self):
        with pytest.raises(ValueError):
            _validate_actions([{"click": {"index": 2, "confirm": "yes"}}])


class TestTheGateMatchesTheSubmitGate:

    @staticmethod
    async def _run(monkeypatch, actions, **el_kwargs):
        return await TestAClickThatWouldSubmit._run(monkeypatch, actions,
                                                    **el_kwargs)

    @pytest.fixture
    def connected(self, request):
        def _attach(stub):
            set_mcp_session(stub)
            request.addfinalizer(lambda: set_mcp_session(None))
            return stub
        return _attach

    @pytest.mark.asyncio
    async def test_a_human_accept_via_elicitation_clicks_without_a_param(
            self, monkeypatch, connected):
        """能力为真的连接：钥匙来自人按键——click 自己没带 confirm。"""
        stub = connected(_StubSession(action="accept"))
        pa, el = await self._run(monkeypatch, [{"click": {"index": 2}}],
                                 kind="submit")
        assert pa.outcomes[0]["ok"] is True and el.clicked
        assert "submission" in stub.asked[0].lower()

    @pytest.mark.asyncio
    async def test_a_decline_or_dead_channel_pauses_even_with_confirm(
            self, monkeypatch, connected):
        for stub in (_StubSession(action="decline"), _StubSession(fail=True)):
            connected(stub)
            pa, el = await self._run(
                monkeypatch, [{"click": {"index": 2, "confirm": True}}],
                kind="submit")
            row = pa.outcomes[0]
            assert row["ok"] is False and row.get("pending_approval") is True
            assert el.clicked == [], "fail-closed：confirm=true 不能替人点头"
            assert "skips the ask" in row["error"]


class TestTheAuditSeesIt:

    @pytest.mark.asyncio
    async def test_a_paused_click_gated_submission_is_audited_as_pending(
            self, tmp_path, monkeypatch):
        import json as _json

        log = tmp_path / "usage.jsonl"
        monkeypatch.setenv("DHOLE_USAGE_LOG", str(log))
        pa, el = await TestAClickThatWouldSubmit._run(
            monkeypatch, [{"click": {"index": 2}}], kind="submit")
        from dhole_mcp.models import ResponseModel
        from dhole_mcp.server import _report_action_outcomes
        result = ResponseModel(url="https://shop.example.com/checkout",
                               status=200, content=["body"],
                               fetcher_used="http")
        _report_action_outcomes(result, pa)
        rows = [_json.loads(line) for line
                in log.read_text(encoding="utf-8").splitlines()]
        (write,) = [r for r in rows if r.get("event") == "write"]
        assert write["pending"] is True
        assert "shop.example.com" in write["url"]
