"""写门的三把钥匙不能再全是模型自签（批次8 刀1 ＝ F-D-J1 三段式）。

现状（评审复核过）：web_request 的 POST 门、web_act 的 submit 闸、CDP 首挂闸
都只认 agent 自己写进参数里的 ``confirm=true``——"问用户"这一步由 agent 自己
执行且不可验证，被注入的页面正文恰恰能诱导它自签。SDK 侧原语（elicit_form /
ClientCapabilities.elicitation / NoBackChannelError）在本机 mcp 2.2.0 逐一
实测可达（import 即证），全仓 grep 五族原语零命中说明通道从未被用。

三段式（本文件钉住的契约）：
  1. 客户端声明了 elicitation 能力 → 三门改走 ``elicit_form``（空 schema，
     只借 accept/decline/cancel）：accept＝人点头，执行；decline/cancel/问不到
     人（NoBackChannelError 等）＝拒答，**绝不回落参数 confirm**——否则注入方
     制造一次 elicitation 故障就把门推回模型手里。
  2. 能力为假 / 无会话（直连 dispatch、CLI、旧宿主）→ 一字不改，param 门照旧。
  3. 按连接协商，不按次：能力为真的连接上 confirm 参数不参与开门（仍会问人）。

集成两条走**真 SDK in-memory 回路**（InMemoryTransport ＋真 ClientSession 的
elicitation_callback ＝ 一次真握手真回传，宿主侧的形状由批次7 刀3 的实测清单
到真宿主上核），其余用桩。
"""

from __future__ import annotations

import http.server
import threading

import pytest

from dhole_mcp import identity
from dhole_mcp.context import set_mcp_session
from dhole_mcp.models import ResponseModel
from dhole_mcp.server import MasterFetchServer, _cdp_attach_gate, _cdp_authorized_hosts

_CDP = "http://127.0.0.1:9222"
_SITE = "https://example.com/"


class _StubSession:
    """SDK ServerSession 的最小桩：能力协商 + elicit_form，问过什么记账。"""

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


@pytest.fixture(autouse=True)
def _fresh_cdp_store(tmp_path):
    """授权名册是进程级缓存：每个用例从空名册开始，互不继承。"""
    from pathlib import Path as _Path
    store = _Path(tmp_path) / "cdp_hosts.json"
    import dhole_mcp.server as _sm
    _sm._cdp_forget_roster()
    yield
    _sm._cdp_forget_roster()
    store.unlink(missing_ok=True)


@pytest.fixture
def connected(request):
    """把一个桩 session 挂进按次上下文；测试结束摘掉，绝不漏进别的用例。"""
    def _attach(stub):
        set_mcp_session(stub)
        request.addfinalizer(lambda: set_mcp_session(None))
        return stub
    return _attach


@pytest.fixture
def capture_send(monkeypatch):
    """拦住 web_request 批准后的真发送：记账不触网，断言"发了/没发"。"""
    sent: list[dict] = []

    async def fake_web_read(self, **kwargs):
        sent.append(kwargs)
        return ResponseModel(url=kwargs.get("url", ""), status=200,
                             content=["ok"], fetcher_used="http")

    monkeypatch.setattr(MasterFetchServer, "web_read", fake_web_read)
    return sent


# ─── 门 1：web_request 的写门 ────────────────────────────────────────────────


class TestTheWebWriteGateMovesOutOfBand:

    @pytest.mark.asyncio
    async def test_write_gate_moves_out_of_band_when_client_elicts(
            self, connected, capture_send):
        """能力为真的连接：没有 confirm=true 也放行——钥匙来自人按键，不是参数。"""
        stub = connected(_StubSession(action="accept"))
        srv = MasterFetchServer()
        out = await srv.web_request(url="https://api.example.com/v1/x",
                                    method="POST", body={"a": 1})
        assert not (out.error or ""), out.error
        assert len(capture_send) == 1, "人点头之后这一次写要真的发出去"
        assert capture_send[0]["method"] == "POST"
        # 问的内容按报告：method / URL / body 摘要 / 凭据随行提示
        (question,) = stub.asked
        assert "POST" in question and "api.example.com/v1/x" in question
        assert "credentials" in question.lower()

    @pytest.mark.asyncio
    async def test_elicitation_failure_fails_closed_not_open(
            self, connected, capture_send):
        """decline / cancel / 问不到人 → 拒答；confirm=true 也救不回来。"""
        srv = MasterFetchServer()
        for stub in (_StubSession(action="decline"),
                     _StubSession(action="cancel"),
                     _StubSession(fail=True)):
            connected(stub)
            out = await srv.web_request(url="https://api.example.com/v1/x",
                                        method="POST", body={"a": 1},
                                        confirm=True)
            assert (out.error or ""), f"{stub._action or 'fail'} 必须被拒"
            assert "declined" in (out.error or "") or "ask" in (out.error or "")
            assert capture_send == [], "fail-closed：一个请求都不许出"
            assert not (stub.asked and stub.asked and
                        "re-send" in stub.asked[-1]), "拒答不许教 confirm 绕行"

    @pytest.mark.asyncio
    async def test_confirm_param_still_works_without_elicitation_capability(
            self, connected, capture_send):
        """能力为假 / 无会话 → 一字不改：无 confirm 拒答（原话原样），有 confirm 放行。"""
        legacy_refusal = (
            "Ask the user, then re-send the same call with confirm=true")
        # ① 完全没有 MCP 会话（CLI / 直连 dispatch / 旧宿主）
        set_mcp_session(None)
        srv = MasterFetchServer()
        out = await srv.web_request(url="https://api.example.com/v1/x",
                                    method="POST")
        assert legacy_refusal in (out.next_action or "")
        assert capture_send == []
        # ② 会话在场但没声明 elicitation 能力
        connected(_StubSession(capable=False))
        out = await srv.web_request(url="https://api.example.com/v1/x",
                                    method="POST")
        assert legacy_refusal in (out.next_action or "")
        out = await srv.web_request(url="https://api.example.com/v1/x",
                                    method="POST", confirm=True)
        assert not (out.error or "")
        assert len(capture_send) == 1, "param 门照旧：confirm=true 就是钥匙"
        # ③ 声明了能力 → confirm 不再参与开门（按连接协商，不混用两种门）
        stub = connected(_StubSession(action="decline"))
        await srv.web_request(url="https://api.example.com/v1/x",
                              method="POST", confirm=True)
        assert stub.asked, "能力为真的连接照样要问人"


# ─── 门 2：web_act 的 submit 闸（actions.py 执行器） ─────────────────────────


class _FakeElement:
    def __init__(self, name="确认下单"):
        self._name = name
        self.clicked: list[str] = []

    async def click(self, timeout=None):
        self.clicked.append(self._name)


class _FakeLocator:
    def __init__(self, el):
        self._el = el

    async def click(self, timeout=None):
        self._el.clicked.append("clicked")


class _FakeRequest:
    method = "POST"
    resource_type = "document"


class _FakePage:
    """一页能听 request 的最小事理 —— D-29 之后 `submitted` 只认从这里发出去的请求。"""

    def __init__(self):
        self.url = "https://shop.example.com/checkout"
        self._listeners: list = []

    def on(self, event, handler):
        if event == "request":
            self._listeners.append(handler)

    def remove_listener(self, event, handler):
        if handler in self._listeners:
            self._listeners.remove(handler)

    def sent(self) -> None:
        for handler in list(self._listeners):
            handler(_FakeRequest())


def _submit_chain(confirm=None):
    submit = {"index": 2}
    if confirm is not None:
        submit["confirm"] = confirm
    return [{"submit": submit}]


class TestTheSubmitGateMovesOutOfBand:

    async def _run(self, monkeypatch, actions):
        from dhole_mcp.actions import build_page_action
        el = _FakeElement()

        async def fake_locator(page, target):
            return _FakeLocator(el)

        monkeypatch.setattr("dhole_mcp.actions._target_locator", fake_locator)
        async def fake_settle(page, loc):
            await loc.click()  # 真执行器里点击发生在 settle 内部
            page.sent()        # 这一发真的离开了这一页
        monkeypatch.setattr("dhole_mcp.actions._settle_after_click", fake_settle)
        pa = build_page_action(actions)
        await pa(_FakePage())
        return pa, el

    @pytest.mark.asyncio
    async def test_a_human_accept_clicks_the_submit(self, connected, monkeypatch):
        stub = connected(_StubSession(action="accept"))
        pa, el = await self._run(monkeypatch, _submit_chain())
        assert pa.outcomes[0]["ok"] is True
        assert el.clicked, "人点头 = 提交执行"
        assert "submit" in stub.asked[0].lower()

    @pytest.mark.asyncio
    async def test_a_decline_or_dead_channel_pauses_and_never_clicks(
            self, connected, monkeypatch):
        for stub in (_StubSession(action="decline"), _StubSession(fail=True)):
            connected(stub)
            pa, el = await self._run(monkeypatch, _submit_chain(confirm=True))
            row = pa.outcomes[0]
            assert row["ok"] is False and row.get("pending_approval") is True
            assert el.clicked == [], "fail-closed：confirm=true 不能替人点头"
            assert "skips the ask" in row["error"], "拒答不教参数绕行"

    @pytest.mark.asyncio
    async def test_confirm_param_still_works_without_elicitation_capability(
            self, connected, monkeypatch):
        set_mcp_session(None)
        pa, el = await self._run(monkeypatch, _submit_chain(confirm=True))
        assert pa.outcomes[0]["ok"] is True and el.clicked
        connected(_StubSession(capable=False))
        pa, el = await self._run(monkeypatch, _submit_chain())
        (row,) = pa.outcomes
        assert row.get("pending_approval") is True, "能力为假 → 原闸原样"


# ─── 门 3：CDP 首挂闸 ────────────────────────────────────────────────────────


class TestTheCdpGateMovesOutOfBand:

    @pytest.mark.asyncio
    async def test_a_human_accept_authorizes_the_host(self, connected):
        stub = connected(_StubSession(action="accept"))
        error, allowed = await _cdp_attach_gate(_CDP, targets=[_SITE])
        assert error is None
        assert allowed and "example.com" in allowed
        assert _cdp_authorized_hosts() >= {"example.com"}
        assert "example.com" in stub.asked[0]

    @pytest.mark.asyncio
    async def test_a_decline_or_dead_channel_authorizes_nothing(self, connected):
        for stub in (_StubSession(action="decline"), _StubSession(fail=True)):
            connected(stub)
            error, allowed = await _cdp_attach_gate(_CDP, confirm=True,
                                                    targets=[_SITE])
            assert error, "fail-closed：问不到人 = 不授权不执行"
            assert "example.com" not in (_cdp_authorized_hosts() or set())
            assert "does not replace the ask" in error

    @pytest.mark.asyncio
    async def test_confirm_param_still_works_without_elicitation_capability(
            self, connected):
        set_mcp_session(None)
        error, _ = await _cdp_attach_gate(_CDP, targets=[_SITE])
        assert error and "confirm=true" in error, "无会话 → 原拒答原样"
        error, _ = await _cdp_attach_gate(_CDP, confirm=True, targets=[_SITE])
        assert error is None and _cdp_authorized_hosts() >= {"example.com"}
        stub = connected(_StubSession(capable=False))
        error, _ = await _cdp_attach_gate(_CDP, targets=["https://other.example.com/"])
        assert error and "confirm=true" in error, "能力为假 → 原拒答原样"
        assert stub.asked == [], "能力为假 → 不问人"


# ─── 真 SDK in-memory 回路（一次真握手真回传，不是桩） ───────────────────────


class _Recorder(http.server.BaseHTTPRequestHandler):
    posts: list[str] = []

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        _Recorder.posts.append(self.path)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):  # 静默
        pass


@pytest.fixture
def loopback_post():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    _Recorder.posts.clear()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


async def _through_the_real_sdk(answer: str, url: str) -> object:
    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport
    from mcp.types import ElicitResult

    async def human(ctx, params):
        return ElicitResult(action=answer)

    transport = InMemoryTransport(MasterFetchServer()._mcp_server())
    async with transport as streams:
        async with ClientSession(streams[0], streams[1],
                                 elicitation_callback=human) as session:
            await session.initialize()
            return await session.call_tool(
                "web_request", {"url": url, "method": "POST",
                                "body": {"sku": "x"}})


class TestTheRealSdkRoundTrip:

    @pytest.mark.asyncio
    async def test_an_accept_rides_the_real_channel_and_sends_the_write(
            self, loopback_post, monkeypatch):
        identity.set_value("allow_private", True)
        res = await _through_the_real_sdk(
            "accept", f"{loopback_post}/checkout")
        text = "".join(getattr(b, "text", "") for b in res.content)
        assert '"status":200' in text and '"content_ok":true' in text, text[:300]
        assert _Recorder.posts == ["/checkout"], "写真到了服务器"

    @pytest.mark.asyncio
    async def test_a_decline_on_the_real_channel_sends_nothing(
            self, loopback_post, monkeypatch):
        identity.set_value("allow_private", True)
        res = await _through_the_real_sdk(
            "decline", f"{loopback_post}/checkout")
        text = "".join(getattr(b, "text", "") for b in res.content)
        assert "declined" in text, text[:200]
        assert _Recorder.posts == [], "一个字节都没发出去"
