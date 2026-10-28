"""test_actions.py —— 由 2 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""


from __future__ import annotations


import pytest
from dhole_mcp.actions import INTERACTIVE_SELECTOR, MAX_ELEMENT_INDEX, SNAPSHOT_CAP, _CONFIRM_SNIPPET, _validate_actions, build_page_action
from dhole_mcp.server import MasterFetchServer, ResponseModel, _report_action_outcomes
import base64
from dhole_mcp.actions import MAX_ACTION_SHOT_BYTES
from dhole_mcp.wire import _wire_result

pytestmark = pytest.mark.smoke


# ============================================================================
# 来源：test_element_snapshot_mvp2.py
# ============================================================================
# MVP-2：元素索引快照 + 索引化动作 + submit 审批闸。

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
        # E13：顶层的 `{"confirm": true}` 读起来像调用参数，照着重试会二次被闸。
        # next_action 必须给那串**唯一可抄**的嵌套形状（动作内），与动作行同一个作者。
        assert _CONFIRM_SNIPPET in result.next_action, result.next_action
        assert '{"confirm": true} on that submit' not in result.next_action


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


# ============================================================================
# 来源：test_multimodal_visual_g34.py
# ============================================================================
# G34 → 17.0 → A1：元素级截图 + 本地二维码解码，**常开**（无 DHOLE_MULTIMODAL 门）。
# 重名消歧：PNG_BYTES → PNG_BYTES__multimodal_visual_g34、_FakeLocator → _FakeLocator__multimodal_visual_g34、_FakePage → _FakePage__multimodal_visual_g34、_FakeSession → _FakeSession__multimodal_visual_g34

PNG_BYTES__multimodal_visual_g34 = b"\x89PNG\r\n\x1a\n" + b"0" * 64

URL = "https://example.com/login"


def _qr_png(payload: str) -> bytes:
    """Encode a payload into a PNG QR (needs the opencv extra; callers skip)."""
    import cv2

    encoder = cv2.QRCodeEncoder_create()
    ok, buf = cv2.imencode(".png", encoder.encode(payload))
    assert ok
    return buf.tobytes()


class _FakeLocator__multimodal_visual_g34:
    def __init__(self, sel: str, data: bytes = PNG_BYTES__multimodal_visual_g34, fail: bool = False):
        self._sel, self._data, self._fail = sel, data, fail

    @property
    def first(self):
        return self

    async def count(self) -> int:
        # 真的 Locator 有 count()。元素裁切路径现在先查零匹配再截图（缺陷 5：
        # `.first` 对零匹配的 locator 会白等满 timeout，然后把「元素不在 DOM 里」
        # 说成「页面没渲染完」），所以替身也得有。这里的 fail 语义是「元素在、
        # 截图超时」，因此计数为 1。
        return 1

    async def screenshot(self, **kwargs):
        if self._fail:
            raise TimeoutError(f"waiting for locator({self._sel!r})")
        return self._data

    async def fill(self, *a, **kw):
        raise AssertionError("fill should not be part of a capture test")


class _FakePage__multimodal_visual_g34:
    url = URL

    def __init__(self, data: bytes = PNG_BYTES__multimodal_visual_g34, fail: bool = False):
        self._data, self._fail = data, fail

    def locator(self, sel: str):
        return _FakeLocator__multimodal_visual_g34(sel, self._data, self._fail)

    async def screenshot(self, **kwargs):
        if self._fail:
            raise TimeoutError("waiting for the page")
        return self._data

    async def wait_for_timeout(self, ms):
        pass


class _FakeSession__multimodal_visual_g34:
    def __init__(self, page: _FakePage__multimodal_visual_g34):
        self.page = page

    async def fetch(self, url, **kwargs):
        action = kwargs.get("page_action")
        if action is not None:
            await action(self.page)


# ─── 无门控：动作在任何 env 状态下都能通过校验，读 env 的入口已删 ──────────


class TestNoActionGate:

    @pytest.mark.parametrize("raw", [None, "0", "1", "flase", "yes-please"])
    def test_the_action_passes_regardless_of_env(self, monkeypatch, raw):
        """17.0 起这个能力不在任何门后面 —— 连旧旋钮的残留值也不改变行为
        （注销的旋钮运行时静默忽略）。"""
        import dhole_mcp.actions as actions_mod
        if raw is None:
            monkeypatch.delenv("DHOLE_MULTIMODAL", raising=False)
        else:
            monkeypatch.setenv("DHOLE_MULTIMODAL", raw)
        assert not hasattr(actions_mod, "multimodal_enabled")
        assert build_page_action([{"screenshot": "img.captcha"}]) is not None

    def test_the_dict_form_normalizes_selector_and_decode_qr(self):
        (a,) = _validate_actions([{"screenshot": {"selector": "img.qr",
                                                  "decode_qr": True}}])
        # A1：归一化把 image_type 定型（执行器不再猜默认），其余旋钮缺席即省略。
        assert a == {"screenshot": {"selector": "img.qr", "image_type": "png",
                                    "decode_qr": True}}
        (b,) = _validate_actions([{"screenshot": "img.qr"}])
        assert b == {"screenshot": {"selector": "img.qr", "image_type": "png",
                                    "decode_qr": False}}

    def test_a_bare_row_number_is_the_index_form(self):
        """刀9-6c（F8）：从 snapshot 那一列读来的数可以原样写回来，与 {"index": N} 同义。"""
        (c,) = _validate_actions([{"screenshot": 3}])
        assert c == {"screenshot": {"index": 3, "image_type": "png",
                                    "decode_qr": False}}, c

    @pytest.mark.parametrize("bad", [
        {"screenshot": {"selector": "img.qr", "decode_qr": "yes"}},
        {"screenshot": {"decode_qr": True}},
        {"screenshot": {"selector": ""}},
        {"screenshot": {"selector": None}},
        {"screenshot": 3.5},
        {"screenshot": True},
    ])
    def test_bad_shapes_are_refused(self, bad):
        with pytest.raises((ValueError, Exception)) as e:
            build_page_action([bad])
        assert "element_screenshot_disabled" not in str(e.value)


# ─── 执行器：像素进行、收据齐、失败不连坐 ───────────────────────────────────


class TestTheCaptureExecutor:

    @pytest.mark.asyncio
    async def test_a_capture_rides_its_outcome_row(self, monkeypatch):
        pa = build_page_action([{"screenshot": "img.captcha"}])
        await pa(_FakePage__multimodal_visual_g34())
        (row,) = pa.outcomes
        assert row["ok"] is True
        assert row["image_mime"] == "image/png"
        assert base64.b64decode(row["image_b64"]) == PNG_BYTES__multimodal_visual_g34

    @pytest.mark.asyncio
    async def test_decode_qr_reports_the_payload_in_the_row(self, monkeypatch):
        pytest.importorskip("cv2")
        payload = "https://example.com/checkin?token=abc123"
        pa = build_page_action([{"screenshot": {"selector": "img.qr",
                                                "decode_qr": True}}])
        await pa(_FakePage__multimodal_visual_g34(data=_qr_png(payload)))
        (row,) = pa.outcomes
        assert row["ok"] is True
        assert row["qr"] == [payload]

    @pytest.mark.asyncio
    async def test_a_decoder_problem_never_fails_the_capture(self, monkeypatch):
        """图是产品，解码是赠品：缺 opencv / 空白图都不能把动作标成失败。"""

        import dhole_mcp.qr as qr_mod

        def boom(data):
            raise ImportError("decode_qr requires OpenCV")
        monkeypatch.setattr(qr_mod, "decode_qr_bytes", boom)
        pa = build_page_action([{"screenshot": {"selector": "img.qr",
                                                "decode_qr": True}}])
        await pa(_FakePage__multimodal_visual_g34())
        (row,) = pa.outcomes
        assert row["ok"] is True
        assert row["image_b64"]
        assert row["qr"] == []
        assert "ImportError" in row["qr_error"]

    @pytest.mark.asyncio
    async def test_an_oversized_capture_is_a_failed_action_not_a_dos(self,
                                                                     monkeypatch):
        big = b"\x89PNG\r\n\x1a\n" + b"0" * (MAX_ACTION_SHOT_BYTES + 1)
        pa = build_page_action([{"screenshot": "body"}])
        await pa(_FakePage__multimodal_visual_g34(data=big))
        (row,) = pa.outcomes
        assert row["ok"] is False
        assert "image_b64" not in row, "超限的像素一个字节都不能出门"
        assert "bytes" in row["error"]

    @pytest.mark.asyncio
    async def test_a_failed_capture_does_not_abort_the_rest(self, monkeypatch):
        pa = build_page_action([{"wait": 1},
                                {"screenshot": "img.missing"},
                                {"wait": 1}])
        await pa(_FakePage__multimodal_visual_g34(fail=True))
        ok_flags = [r["ok"] for r in pa.outcomes]
        assert ok_flags == [True, False, True]
        assert "locator" in pa.outcomes[1]["error"]


# ─── 收据与 wire：像素只走内容列表 ──────────────────────────────────────────


class TestTheReceiptAndTheWire:

    @staticmethod
    def _result_with_capture() -> ResponseModel:
        pa = build_page_action([{"wait": 1}])
        pa.outcomes.append({"action": "screenshot 'img.qr'", "ok": True,
                            "image_b64": base64.b64encode(PNG_BYTES__multimodal_visual_g34).decode(),
                            "image_mime": "image/png",
                            "qr": ["https://example.com/t"]})
        result = ResponseModel(url=URL, status=200, content=["body"],
                               fetcher_used="http")
        _report_action_outcomes(result, pa)
        return result

    def test_metadata_keeps_a_marker_and_the_qr_text_not_the_bytes(self):
        result = self._result_with_capture()
        (row,) = result.metadata["actions"]
        assert row["image"] is True
        assert "image_b64" not in row, "base64 进 JSON 会摊到每个 transcript 副本"
        assert row["qr"] == ["https://example.com/t"]

    def test_the_pixels_reach_action_images_for_the_content_list(self):
        result = self._result_with_capture()
        (img,) = result._action_images
        assert img["mime_type"] == "image/png"
        assert base64.b64decode(img["data"]) == PNG_BYTES__multimodal_visual_g34

    def test_wire_result_prepends_the_image_and_keeps_the_json_clean(self):
        result = self._result_with_capture()
        content, payload = _wire_result(result, images=result._action_images)
        assert content[0].type == "image"
        assert content[0].data == base64.b64encode(PNG_BYTES__multimodal_visual_g34).decode()
        assert content[0].mime_type == "image/png"
        assert content[-1].type == "text"
        assert "image_b64" not in str(payload)
        assert payload["metadata"]["actions"][0]["image"] is True

    def test_wire_result_without_images_is_the_old_single_text_shape(self):
        result = ResponseModel(url=URL, status=200, content=["body"],
                               fetcher_used="http")
        content, payload = _wire_result(result)
        assert len(content) == 1
        assert content[0].type == "text"


# ─── 捕获旋钮（A1）：归一化即拒 + 执行器落盘 ────────────────────────────────
# 原 TestTheToolGate/TestTheToolCapture 守的是 web_screenshot 方法本体；工具下线
# 后同一批裁决改由动作链的两个环节承担：形状冲突在 build_page_action（校验期，
# 浏览器还没起），旋钮在执行器（落盘/内联/编码）。


class TestTheCaptureKnobs:

    @pytest.mark.asyncio
    async def test_full_page_captures_the_page_not_an_element(self):
        pa = build_page_action([{"screenshot": {"full_page": True}}])
        await pa(_FakePage__multimodal_visual_g34())
        (row,) = pa.outcomes
        assert row["ok"] is True
        assert row["image_mime"] == "image/png"
        assert base64.b64decode(row["image_b64"]) == PNG_BYTES__multimodal_visual_g34

    @pytest.mark.asyncio
    async def test_full_page_cannot_combine_with_a_selector(self):
        with pytest.raises(ValueError) as e:
            build_page_action([{"screenshot": {"full_page": True,
                                               "selector": "img.captcha"}}])
        assert "full_page" in str(e.value)

    def test_quality_needs_jpeg(self):
        with pytest.raises(ValueError) as e:
            build_page_action([{"screenshot": {"selector": "img",
                                               "quality": 80}}])
        assert "quality" in str(e.value)
        (a,) = _validate_actions([{"screenshot": {"selector": "img",
                                                  "image_type": "jpeg",
                                                  "quality": 80}}])
        assert a["screenshot"]["quality"] == 80

    def test_an_image_type_off_the_enum_is_refused(self):
        with pytest.raises(ValueError) as e:
            build_page_action([{"screenshot": {"selector": "img",
                                               "image_type": "webp"}}])
        assert "image_type" in str(e.value)

    @pytest.mark.asyncio
    async def test_save_to_writes_disk_and_keeps_the_bytes_off_the_transcript(
            self, tmp_path):
        target = tmp_path / "shots" / "page.png"
        pa = build_page_action([{"screenshot": {"selector": "img.cap",
                                                "save_to": str(target)}}])
        await pa(_FakePage__multimodal_visual_g34())
        (row,) = pa.outcomes
        assert row["ok"] is True
        assert "image_b64" not in row, "落盘的捕获不再搭 transcript 的税"
        assert row["saved_bytes"] == len(PNG_BYTES__multimodal_visual_g34)
        assert target.read_bytes() == PNG_BYTES__multimodal_visual_g34, "父目录要自动创建"

    def test_a_non_image_save_to_is_refused_before_the_browser_starts(self):
        with pytest.raises(ValueError) as e:
            build_page_action([{"screenshot": {"selector": "img",
                                               "save_to": "notes.txt"}}])
        assert "not named like an image" in str(e.value)

    @pytest.mark.asyncio
    async def test_jpeg_type_sets_the_mime(self):
        pa = build_page_action([{"screenshot": {"selector": "img",
                                                "image_type": "jpeg"}}])
        await pa(_FakePage__multimodal_visual_g34())
        (row,) = pa.outcomes
        assert row["image_mime"] == "image/jpeg"


# ─── 描述：纪律跟着 flag 上线，类字面量保持原样 ─────────────────────────────


class TestTheDescriptions:

    def test_discipline_is_in_the_literal_itself(self):
        """VISUAL ASSIST 不再是运行时拼接：它在 _TOOL_DEFS 字面量里
        （能力常开，纪律不是模式）。A1 之后只有 web_act 这扇门。

        2026-10-07 F-C1-J2 判决对齐：捕获旋钮的枚举撤到手册（tools.md 逐项
        承重，wire 换 sys_guide 指针）—— 旋钮的「怎么选」按需付费，存在性
        与验证码用途留在 wire。原断言「旋钮在描述里点得出名字」钉的是旧放置
        决定，随本判决退役；手册侧由
        test_visual_assist_options_live_in_the_manual_not_the_wire 钉住。
        """
        defs = {td["name"]: td for td in MasterFetchServer._enabled_tool_defs()}
        assert "web_screenshot" not in defs, "独立工具该下线了"
        act = defs["web_act"]["description"]
        assert "VISUAL ASSIST" in act
        assert '{"screenshot"' in act
        assert "The dict form's options" in act, "指针句要替掉枚举句"
        assert "actions" not in defs["web_read"]["inputSchema"]["properties"],             "web_read 还挂着动作链，两扇门就又是一扇"
        # 纪律（安全红线 6 的 ≤N 次与一律转交）：act 留 ≤3 次与转交动词，墙的
        # 枚举随 web_screenshot 下线搬进 behavior 卷 —— 指针从 act 的字面一路
        # 可达，纪律不因合并而失联。
        assert "at most 3" in act
        assert "DataDome" not in act, "墙的清单按需付费，不再常驻 wire"
        assert "hand off" in act
        assert "sys_guide(topic='behavior')" in act, "act 丢了指向墙清单的指针"

    def test_repeated_calls_do_not_accumulate(self):
        first = [td["description"] for td in MasterFetchServer._enabled_tool_defs()]
        again = [td["description"] for td in MasterFetchServer._enabled_tool_defs()]
        assert first == again
