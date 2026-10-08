"""MVP-2：元素索引快照 + 索引化动作 + submit 审批闸。

设计要点（计划第八、九章）：
* dhole 不做 act(prompt)——"用户不用懂"由外层 agent 的语言能力提供：dhole 把
  可交互元素编号列出来（{"snapshot": true}），agent 自己认，动作按 index 寻址。
  全程零模型零密钥（安全红线 5）。
* 索引跨两次页面加载必须可复现：快照与 act 用**同一个**交互元素选择器的文档序；
  `expect` 让过期的索引大声失败（报出现在读什么），而不是点到别的东西。
* submit 是显式动作且默认暂停（pending_approval）：提交是不可逆的一步，必须
  经用户确认（安全红线 3）；confirm=true 是 agent 已拿到用户确认的断言。
"""

from __future__ import annotations

import pytest

from dhole_mcp.actions import (
    INTERACTIVE_SELECTOR,
    MAX_ELEMENT_INDEX,
    SNAPSHOT_CAP,
    _validate_actions,
    build_page_action,
)
from dhole_mcp.server import MasterFetchServer, ResponseModel, _report_action_outcomes

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 32

ROWS = [
    [0, "input", "textbox", "用户名", False, "text"],
    [1, "input", "textbox", "密码", False, "password"],
    [2, "button", "button", "登录", False, "submit"],
    [3, "a", "link", "忘记密码", False, ""],
]


class _FakeElement:
    def __init__(self, name: str = "登录"):
        self.name = name
        self.clicked: list = []
        self.filled: list = []
        self.keyed: list = []

    async def click(self, **kw):
        self.clicked.append(self.name)

    async def evaluate(self, js):
        return self.name

    async def fill(self, text, **kw):
        self.filled.append(text)

    async def press(self, key, **kw):
        self.keyed.append(key)

    async def screenshot(self, **kw):
        return PNG_BYTES

    async def wait_for(self, **kw):
        pass


class _FakeLocator:
    def __init__(self, element: _FakeElement, page=None):
        self._el = element
        self._page = page

    @property
    def first(self):
        return self

    def nth(self, i):
        return self

    async def click(self, **kw):
        await self._el.click()
        # 点下去 produces 什么，由这一页的 `emits` 说了算（D-29：`submitted` 只认
        # 真的离得开这一页的请求）。
        if self._page is not None:
            self._page.sent()

    async def evaluate(self, js):
        return await self._el.evaluate(js)

    async def fill(self, text, **kw):
        await self._el.fill(text)

    async def press(self, key, **kw):
        await self._el.press(key)

    async def screenshot(self, **kw):
        return await self._el.screenshot()


class _FakeRequest:
    def __init__(self, method: str, resource_type: str):
        self.method = method
        self.resource_type = resource_type


class _FakePage:
    url = "https://example.com/login"

    def __init__(self, rows=ROWS, total=None, element: _FakeElement | None = None,
                 enum_broken=False, emits=()):
        self._rows = rows
        self._total = len(rows) if total is None else total
        self.element = element or _FakeElement()
        self._enum_broken = enum_broken
        self.url = "https://example.com/login"
        # 点击之后这一页往外发什么：[(method, resource_type), ...]，默认什么都不发。
        self.emits = list(emits)
        self._listeners: list = []

    def locator(self, sel):
        assert sel == INTERACTIVE_SELECTOR or isinstance(sel, str)
        return _FakeLocator(self.element, self)

    def on(self, event, handler):
        if event == "request":
            self._listeners.append(handler)

    def remove_listener(self, event, handler):
        if handler in self._listeners:
            self._listeners.remove(handler)

    def sent(self) -> None:
        for method, resource_type in self.emits:
            for handler in list(self._listeners):
                handler(_FakeRequest(method, resource_type))

    async def evaluate(self, js, arg=None):
        if self._enum_broken:
            raise RuntimeError("no document")
        if "els.length" in js:  # enumeration JS
            return [self._rows, self._total]
        if "querySelectorAll(sel).length" in js:  # interactive count JS
            return self._total
        raise AssertionError(f"unexpected evaluate: {js[:60]}")

    async def wait_for_timeout(self, ms):
        pass


class _FakeSession:
    def __init__(self, page):
        self.page = page

    async def fetch(self, url, **kwargs):
        action = kwargs.get("page_action")
        if action is not None:
            await action(self.page)


# ─── 校验 ───────────────────────────────────────────────────────────────────


class TestValidation:

    def test_snapshot_accepts_only_true(self):
        assert _validate_actions([{"snapshot": True}]) == [{"snapshot": True}]

    @pytest.mark.parametrize("bad", [False, {}, "yes", 1])
    def test_snapshot_refuses_everything_else(self, bad):
        with pytest.raises(ValueError) as e:
            _validate_actions([{"snapshot": bad}])
        assert "snapshot" in str(e.value)

    def test_click_by_index(self):
        (a,) = _validate_actions([{"click": {"index": 2}}])
        assert a == {"click": {"index": 2}}

    def test_click_by_index_with_expect(self):
        (a,) = _validate_actions([{"click": {"index": 2, "expect": "登录"}}])
        assert a == {"click": {"index": 2, "expect": "登录"}}

    def test_click_keeps_the_selector_forms(self):
        assert _validate_actions([{"click": "#login"}]) == [{"click": "#login"}]
        (a,) = _validate_actions([{"click": {"selector": "#login"}}])
        assert a == {"click": {"selector": "#login"}}

    def test_selector_and_index_are_mutually_exclusive(self):
        with pytest.raises(ValueError) as e:
            _validate_actions([{"click": {"selector": "#a", "index": 3}}])
        assert "exactly one" in str(e.value)

    @pytest.mark.parametrize("idx", [-1, MAX_ELEMENT_INDEX + 1, "2", True, 2.0])
    def test_index_bounds_and_types(self, idx):
        with pytest.raises(ValueError):
            _validate_actions([{"click": {"index": idx}}])

    def test_fill_by_index(self):
        (a,) = _validate_actions([{"fill": {"index": 0, "text": "user"}}])
        assert a == {"fill": {"index": 0, "text": "user"}}

    def test_fill_without_text_is_refused_even_with_an_index(self):
        with pytest.raises(ValueError):
            _validate_actions([{"fill": {"index": 0}}])

    def test_press_by_index(self):
        (a,) = _validate_actions([{"press": {"index": 1, "key": "Enter"}}])
        assert a == {"press": {"index": 1, "key": "Enter"}}

    def test_submit_normalizes_confirm_default_false(self):
        (a,) = _validate_actions([{"submit": {"selector": "#ok"}}])
        assert a == {"submit": {"selector": "#ok", "confirm": False}}
        (b,) = _validate_actions([{"submit": {"index": 5, "confirm": True}}])
        assert b == {"submit": {"index": 5, "confirm": True}}

    def test_submit_refuses_the_bare_string_form(self):
        with pytest.raises(ValueError) as e:
            _validate_actions([{"submit": "#ok"}])
        assert "irreversible" in str(e.value)

    def test_submit_confirm_must_be_boolean(self):
        with pytest.raises(ValueError):
            _validate_actions([{"submit": {"selector": "#ok", "confirm": "yes"}}])

    def test_expect_must_be_a_short_string(self):
        with pytest.raises(ValueError):
            _validate_actions([{"click": {"index": 1, "expect": "x" * 101}}])
        with pytest.raises(ValueError):
            _validate_actions([{"click": {"index": 1, "expect": 5}}])

    def test_screenshot_by_index_is_normalized(self, monkeypatch):
        monkeypatch.setenv("DHOLE_MULTIMODAL", "1")
        (a,) = _validate_actions([{"screenshot": {"index": 2}}])
        # A1：归一化补齐默认的捕获旋钮（image_type 在此定型，执行器不再猜）。
        assert a == {"screenshot": {"index": 2, "image_type": "png",
                                    "decode_qr": False}}


# ─── 快照执行器 ─────────────────────────────────────────────────────────────


class TestSnapshotExecutor:

    @staticmethod
    def _run(page):
        pa = build_page_action([{"snapshot": True}])
        import asyncio
        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            pa(page))
        return pa.outcomes

    def test_the_rows_are_the_agents_menu(self):
        rows = self._run(_FakePage())
        (row,) = rows
        assert row["ok"] is True
        assert row["elements"][2] == {"index": 2, "tag": "button",
                                      "role": "button", "name": "登录",
                                      "input_type": "submit"}
        assert row["elements_total"] == 4
        assert "elements_truncated" not in row

    def test_truncation_is_reported(self):
        page = _FakePage(rows=[[i, "a", "link", f"link {i}", False, ""]
                               for i in range(SNAPSHOT_CAP + 5)],
                         total=SNAPSHOT_CAP + 5)
        rows = self._run(page)
        (row,) = rows
        assert len(row["elements"]) == SNAPSHOT_CAP
        assert row["elements_total"] == SNAPSHOT_CAP + 5
        assert row["elements_truncated"] is True

    def test_a_broken_document_fails_the_row(self):
        rows = self._run(_FakePage(enum_broken=True))
        (row,) = rows
        assert row["ok"] is False
        assert "enumerate" in row["error"]


# ─── 索引化动作执行器 + expect 防呆 ─────────────────────────────────────────


class TestIndexedExecution:

    @staticmethod
    async def _run(page, actions):
        pa = build_page_action(actions)
        await pa(page)
        return pa

    @pytest.mark.asyncio
    async def test_click_fill_press_by_index(self):
        el = _FakeElement()
        page = _FakePage(element=el)
        pa = await self._run(page, [
            {"fill": {"index": 0, "text": "user"}},
            {"fill": {"index": 1, "text": "pass"}},
            {"click": {"index": 2}},
            {"press": {"index": 1, "key": "Enter"}},
        ])
        assert all(r["ok"] for r in pa.outcomes)
        assert el.filled == ["user", "pass"]
        assert el.clicked == ["登录"]
        assert el.keyed == ["Enter"]

    @pytest.mark.asyncio
    async def test_expect_matching_proceeds(self):
        el = _FakeElement(name="登录")
        pa = await self._run(_FakePage(element=el),
                             [{"click": {"index": 2, "expect": "登录"}}])
        assert pa.outcomes[0]["ok"] is True
        assert el.clicked == ["登录"]

    @pytest.mark.asyncio
    async def test_expect_mismatch_fails_loudly_without_clicking(self):
        """过期的索引不能点到别的东西：报错带着现在的名字，指向重新快照。"""
        el = _FakeElement(name="搜索")  # 页面变了：索引 2 现在读"搜索"
        pa = await self._run(_FakePage(element=el),
                             [{"click": {"index": 2, "expect": "登录"}}])
        (row,) = pa.outcomes
        assert row["ok"] is False
        assert "搜索" in row["error"]
        assert "snapshot" in row["error"]
        assert el.clicked == [], "核对失败就不许点"

    @pytest.mark.asyncio
    async def test_index_beyond_the_page_fails_fast(self):
        page = _FakePage(rows=ROWS, total=4)
        pa = await self._run(page, [{"click": {"index": 40}}])
        (row,) = pa.outcomes
        assert row["ok"] is False
        assert "beyond" in row["error"]


# ─── submit 审批闸 ──────────────────────────────────────────────────────────


class TestSubmitGate:

    @pytest.mark.asyncio
    async def test_unconfirmed_submit_pauses_and_does_not_click(self):
        el = _FakeElement()
        pa = await TestIndexedExecution._run(
            _FakePage(element=el),
            [{"fill": {"index": 0, "text": "user"}},
             {"submit": {"index": 2}}])
        assert pa.outcomes[0]["ok"] is True, "填表照常执行"
        (submit_row,) = [r for r in pa.outcomes if "pending_approval" in r]
        assert submit_row["ok"] is False
        assert submit_row["pending_approval"] is True
        assert "Nothing was submitted" in submit_row["error"]
        assert el.clicked == [], "未经确认的提交一个点击都不能发生"

    @pytest.mark.asyncio
    async def test_confirmed_submit_clicks(self):
        el = _FakeElement(name="确认下单")
        pa = await TestIndexedExecution._run(
            _FakePage(element=el, emits=[("POST", "document")]),
            [{"submit": {"index": 2, "confirm": True}}])
        (row,) = pa.outcomes
        assert row["ok"] is True
        assert el.clicked == ["确认下单"]

    @pytest.mark.asyncio
    async def test_pending_approval_dominates_the_response(self):
        """暂停必须成为整个响应的头号信号，next_action 指向'去问用户'。"""
        pa = build_page_action([{"wait": 1}])
        pa.outcomes.append({
            "action": "submit [2] (unconfirmed)", "ok": False,
            "pending_approval": True,
            "error": "pending_approval: ..."})
        result = ResponseModel(url="https://example.com/", status=200,
                               content=["body"], fetcher_used="http",
                               next_action="something generic")
        _report_action_outcomes(result, pa)
        assert "PAUSED for user approval" in result.next_action
        assert "confirm" in result.next_action
        assert "waiting for user approval" in result.summary


# ─── 写操作审计（DHOLE_USAGE_LOG 打开时，哪个 session 对哪个站做了写操作）────


class TestWriteAudit:

    @staticmethod
    def _log_lines(tmp_path, monkeypatch):
        import json as _json
        log = tmp_path / "usage.jsonl"
        monkeypatch.setenv("DHOLE_USAGE_LOG", str(log))

        def _read():
            if not log.exists():
                return []
            return [_json.loads(line) for line in
                    log.read_text(encoding="utf-8").splitlines()]
        return log, _read

    @pytest.mark.asyncio
    async def test_browser_submits_landed_in_the_audit_log(
            self, tmp_path, monkeypatch):
        log, read = self._log_lines(tmp_path, monkeypatch)
        from dhole_mcp.server import _SESSION_ID
        el = _FakeElement(name="确认下单")
        pa = await TestIndexedExecution._run(
            _FakePage(element=el, emits=[("POST", "document")]),
            [{"submit": {"index": 2, "confirm": True}}])
        result = ResponseModel(url="https://shop.example.com/checkout",
                               status=200, content=["body"],
                               fetcher_used="http")
        token = _SESSION_ID.set("bob")
        try:
            _report_action_outcomes(result, pa)
        finally:
            _SESSION_ID.reset(token)
        rows = read()
        writes = [r for r in rows if r.get("event") == "write"
                  and r.get("channel") == "browser_submit"]
        assert len(writes) == 1
        assert writes[0]["session_id"] == "bob"
        assert "shop.example.com" in writes[0]["url"]
        assert writes[0]["pending"] is False

    @pytest.mark.asyncio
    async def test_a_paused_submit_is_audited_too(self, tmp_path, monkeypatch):
        log, read = self._log_lines(tmp_path, monkeypatch)
        pa = await TestIndexedExecution._run(
            _FakePage(element=_FakeElement()),
            [{"submit": {"index": 2}}])
        result = ResponseModel(url="https://shop.example.com/checkout",
                               status=200, content=["body"],
                               fetcher_used="http")
        _report_action_outcomes(result, pa)
        (row,) = [r for r in read() if r.get("event") == "write"]
        assert row["pending"] is True

    @staticmethod
    async def _force_http(s, url):
        # 13d：_force_fetch 不再收 wait / network_idle —— 渲染时机不再是参数，浏览器
        # 层认出壳页才付那 5 秒（证人 tests/test_render_settle_b5.py）。本条走的是
        # HTTP 层，钉的是写审计，时机从来与它无关。
        return await s._force_fetch(
            url, "http", extraction_type="markdown", css_selector=None,
            cache_ttl=0,
            offset=0, headless=True, real_chrome=False, proxy=None,
            timeout=30000, solve_cloudflare=True,
            block_webrtc=True, hide_canvas=True, extra_headers=None,
            useragent=None, cookies=None)

    @staticmethod
    def _stub_http_client(monkeypatch):
        """桩到最底下的 primp client（与 test_sessions_g7 同一手法）。

        桩 `MasterFetchServer.get` 会把审计所在的 bulk_get 一起绕掉——写审计钉的
        就是真实漏斗（_force_fetch -> get -> bulk_get -> HTTPSession），所以只能在
        client 层换答案。
        """
        import dhole_mcp.fetcher as fetcher_mod

        class _Resp:
            def __init__(self, url):
                self.url = url
                self.status_code = 200
                self.headers = {"content-type": "text/html; charset=utf-8"}
                self.content = b"<html><body>ok</body></html>"
                self.reason = ""
                self.cookies: list = []

        class _Client:
            def request(self, method, url, **kwargs):
                return _Resp(url)

        class _StubSession(fetcher_mod.HTTPSession):
            async def _init_client(self):
                self._client = _Client()

        monkeypatch.setattr(fetcher_mod, "HTTPSession",
                            lambda **kw: _StubSession(**kw))

    @pytest.mark.asyncio
    async def test_http_writes_are_audited(self, tmp_path, monkeypatch):
        log, read = self._log_lines(tmp_path, monkeypatch)
        from dhole_mcp.server import _METHOD, _SESSION_ID

        self._stub_http_client(monkeypatch)
        s = MasterFetchServer()
        mt = _METHOD.set("POST")
        st = _SESSION_ID.set("bob")
        try:
            await self._force_http(s, "https://shop.example.com/api")
        finally:
            _METHOD.reset(mt)
            _SESSION_ID.reset(st)
        rows = [r for r in read() if r.get("event") == "write"
                and r.get("channel") == "http"]
        assert len(rows) == 1
        assert rows[0]["method"] == "POST"
        assert rows[0]["session_id"] == "bob"
        assert rows[0]["url"].startswith("https://shop.example.com/api")

    @pytest.mark.asyncio
    async def test_http_reads_are_not_audited(self, tmp_path, monkeypatch):
        log, read = self._log_lines(tmp_path, monkeypatch)
        from dhole_mcp.server import _METHOD

        self._stub_http_client(monkeypatch)
        s = MasterFetchServer()
        mt = _METHOD.set("GET")
        try:
            await self._force_http(s, "https://example.com/")
        finally:
            _METHOD.reset(mt)
        assert read() == [], "GET 不该进写审计"
