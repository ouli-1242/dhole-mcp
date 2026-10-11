"""F9 的另一半：`pin=true` 说得出话，也走得到那一页 —— 每一层都不许把这句话弄丢。

体验报告 S10 的那一句是"我明明看清了它说我错！"：靶站 `/captcha` 每次 `GET` 换一枚
token，而 `web_act` 每次调用都 `new_page()` + `goto()`，所以第二次填进去的必然是第一轮
那道题的答案。驻留（`pin=true`）修的是这个**结构**，而结构修的成败在两处：

一、**参数要到得了那一页**（`web_act` → `web_read` → `_force_fetch` → `stealthy_fetch`
→ `bulk_stealthy_fetch` → `BrowserSession.fetch`）。这一条链上任何一环把 `pin` 丢了，
调用方收到的都是"我驻留了"的一句谎 —— 所以下面第一组量的就是每一环收到的值。
`test_params_reach_the_tool.py` 钉顶层那一段，这一组钉中间三层与**回程**（浏览器层写进
`pin_out` 的那两个词要变成 `summary` 里的句子）。

二、**不能驻留的时候必须出声**。四条路各有它自己的道理：匿名 auto 会话是共享的且会被
空闲回收（那里没有"你的那一页"）；attach 用的是用户自己的浏览器与标签页；显式代理每一发
新起一台一次性机器；一批多 URL 是并发跑的，一页不能同时被两发驻留。每一条都落进
`summary` —— `tests/test_every_clamp_is_announced.py` 认的落地字段只有这一颗，而一次
没人听见的"我没驻留"恰好等于一次"它大概驻留了"。

三、**默认行为一寸不动**。不带 `pin` 的调用，`fetch` 收到的必须是 `pin=False`、
`pin_out` 必须是 None、回执里必须一个关于驻留的字都没有。反面证人是 live 那组
`test_a_chain_answers_the_challenge_of_the_page_it_is_standing_on.py`（不 pin 就必然换
一道题），它本刀一个字没改，仍然要求绿。

浏览器层自己的复用规则（TTL、关页、换站、attach 弃用、钩子摘不掉就弃页）在
`test_the_pinned_page_is_dropped_by_the_guards_that_hold_it.py`；真的答对验证码那一条在
`test_a_pinned_page_answers_the_captcha_the_previous_call_show_it.py`（`-m live`）。
"""

from __future__ import annotations

import asyncio

import pytest

from dhole_mcp import server as server_mod
from dhole_mcp.context import _arg_notes_scope
from dhole_mcp.server import (PIN_NOTES, BulkResponseModel, MasterFetchServer,
                              ResponseModel)

URL = "https://example.com/"
ATTACH = "http://127.0.0.1:9222"
CHAIN = [{"click": "#go"}]


def _call(tool: str, args: dict):
    out = asyncio.run(MasterFetchServer()._dispatch(tool, args))
    if isinstance(out, tuple) and len(out) == 2:
        return out[1]
    return {}


def _page(url: str) -> ResponseModel:
    return ResponseModel(url=url, status=200, content=["body text"],
                         fetcher_used="stealthy", content_ok=True,
                         content_type="text/html")


@pytest.fixture
def tiers(monkeypatch):
    """浏览器层替身：记下每一发收到的参数，并按真浏览器的规矩写回 `pin_out`。

    `fills` 就是 `BrowserSession.fetch` 会写的那两个词（outcome / dropped），由用例
    点名 —— 这样"句子"这一半是在真形状上量的，不是我自己造一个漂亮的假回执。
    """
    box: dict = {"calls": [], "fills": [{"outcome": "pinned"}]}

    async def fake_stealthy(self, url, **kw):
        box["calls"].append(dict(kw, url=url))
        out = kw.get("pin_out")
        if isinstance(out, dict):
            out.update(box["fills"].pop(0) if box["fills"] else {"outcome": "pinned"})
        return _page(url)

    async def auto(self, **kw):
        return "auto-under-test"

    async def named(self, sid, **kw):
        return sid

    monkeypatch.setattr(MasterFetchServer, "stealthy_fetch", fake_stealthy)
    monkeypatch.setattr(MasterFetchServer, "_ensure_auto_session", auto)
    monkeypatch.setattr(MasterFetchServer, "_ensure_session_browser", named)
    return box


def _sent(box: dict, n: int = -1) -> dict:
    return box["calls"][n]


class TestTheWordReachesThePageAndComesBack:

    def test_a_named_session_carries_pin_down_every_layer(self, tiers):
        _call("web_act", {"url": URL, "actions": CHAIN, "session_id": "bob",
                          "pin": True})
        sent = _sent(tiers)
        assert sent["pin"] is True, sent
        assert isinstance(sent["pin_out"], dict), "浏览器层没有可写回的那格"

    def test_a_page_left_open_says_so_in_the_summary(self, tiers):
        out = _call("web_act", {"url": URL, "actions": CHAIN, "session_id": "bob",
                                "pin": True})
        assert PIN_NOTES["pinned"] in (out.get("summary") or ""), out.get("summary")

    def test_a_reuse_says_which_page_it_stood_on(self, tiers):
        tiers["fills"] = [{"outcome": "reused"}]
        out = _call("web_act", {"url": URL, "actions": CHAIN, "session_id": "bob",
                                "pin": True})
        summary = out.get("summary") or ""
        assert PIN_NOTES["reused"] in summary, summary
        # 复用的那一发不该同时说"我新留了一页"以外的话：两句都在，但"pinned"那句
        # 是它自己写的，这里钉的是复用这一句必须站着。
        assert "no re-navigation" in summary or "no navigation happened" in summary, summary

    def test_a_dropped_page_is_named_before_the_new_one_is_described(self, tiers):
        """先听懂"那一页没了"，再看"这一发新留了一页" —— 顺序读起来才是因果。"""
        tiers["fills"] = [{"dropped": "ttl", "outcome": "pinned"}]
        out = _call("web_act", {"url": URL, "actions": CHAIN, "session_id": "bob",
                                "pin": True})
        summary = out.get("summary") or ""
        assert PIN_NOTES["ttl"] in summary, summary
        assert summary.index(PIN_NOTES["ttl"]) < summary.index(PIN_NOTES["pinned"]), summary

    def test_a_string_false_from_a_serializing_client_is_a_false(self, tiers):
        """schema 声明 boolean 而客户端把每颗都发成字符串：`"false"` 按裸值读是真。

        那一发会真的把页留在会话上，而调用方说的是"不要驻留"。
        """
        _call("web_act", {"url": URL, "actions": CHAIN, "session_id": "bob",
                          "pin": "false"})
        assert _sent(tiers)["pin"] is False, _sent(tiers)

    def test_the_wire_admits_the_number_of_booleans_it_reads(self):
        """`_ARG_TYPES` 与 wire 上的 boolean 属性必须一起长 —— 少一个名字就是
        上面那条归一化在这颗参数上根本不存在。"""
        from dhole_mcp.params import _ARG_TYPES

        defs = {d["name"]: d for d in MasterFetchServer._TOOL_DEFS}
        for tool_name, spec in defs.items():
            for key, prop in (spec["inputSchema"].get("properties") or {}).items():
                if '"boolean"' in str(prop):
                    assert _ARG_TYPES.get(tool_name, {}).get(key) == "bool", (
                        f"{tool_name}.{key} 声明 boolean 却没有字符串写法的路径")


class TestEveryLaneThatCannotPinSaysWhy:

    def test_an_anonymous_auto_session_has_no_page_of_yours(self, tiers):
        out = _call("web_act", {"url": URL, "actions": CHAIN, "pin": True})
        assert PIN_NOTES["auto"] in (out.get("summary") or ""), out.get("summary")
        assert _sent(tiers)["pin"] is False, "拒绝之后还把 True 传下去 = 两层各自决定一次"
        assert _sent(tiers)["pin_out"] is None

    @pytest.mark.asyncio
    async def test_an_attach_is_the_users_own_tabs_not_a_page_to_hold(self, tiers):
        await MasterFetchServer()._force_fetch(
            URL, "stealthy", extraction_type="markdown", css_selector=None,
            cache_ttl=0, offset=0, headless=True, real_chrome=False, proxy=None,
            timeout=20000, solve_cloudflare=False, block_webrtc=False,
            hide_canvas=False, extra_headers=None, useragent=None, cookies=None,
            max_chars=1000, cdp_url=ATTACH, pin=True)
        assert _sent(tiers)["pin"] is False, _sent(tiers)

    def test_the_auto_and_the_transport_refusal_are_two_sentences(self, tiers):
        """两条形不成的路各有各的下一步。合成一句"驻留不了"会把人支去拧错的那颗旋钮：
        一条加 `session_id=` 就够了，另一条得先离开 attach 这条车道。"""
        assert PIN_NOTES["auto"] != PIN_NOTES["transport"]
        assert "session_id=" in PIN_NOTES["auto"], PIN_NOTES["auto"]
        assert "session_id=" in PIN_NOTES["transport"], PIN_NOTES["transport"]
        assert "attach" in PIN_NOTES["transport"], PIN_NOTES["transport"]
        assert "auto session" in PIN_NOTES["auto"], PIN_NOTES["auto"]

    def test_no_refusal_sentence_blames_a_parameter_the_tool_does_not_have(self):
        """C2 那一族的判据：拒绝话不许把人支到他够不着的旋钮上（`proxy=` 在 17.0
        已经不是任何工具的参数，它从 cred_defaults 落盘）。"""
        for key, text in PIN_NOTES.items():
            assert "proxy=" not in text, (key, text)
            assert "drop pin" not in text, (key, text)


class TestTheBulkLaneGuardsItsOwnClaim:
    """`bulk_stealthy_fetch` 才是看得见"这台浏览器是谁的、这一发有几页"的那一层。"""

    class _Sess:
        _is_alive = True
        cdp_attached = False
        launch_note = ""
        _hide_canvas = False
        _block_webrtc = False

        def __init__(self):
            self.kw: list[dict] = []

        async def seed_cookies(self, url, cookies):
            pass

        async def harvest_cookies(self, url):
            return {}

        async def fetch(self, url, **kw):
            self.kw.append(kw)
            out = kw.get("pin_out")
            if isinstance(out, dict):
                out["outcome"] = "pinned"
            return _page(url)

    def _wire(self, monkeypatch, session):
        entry = server_mod._SessionEntry(session=session, session_type="stealthy")

        async def fake_get(self, sid, expected_type=None):
            return entry

        monkeypatch.setattr(MasterFetchServer, "_get_session", fake_get)
        monkeypatch.setattr("dhole_mcp.server._browser_deps_available", lambda: True)
        monkeypatch.setattr(server_mod, "_translate_response",
                            lambda page, *a, **kw: _page(page.url))

    def _one_off_browser(self, monkeypatch, box):
        """`entry is None` 那一支自己起一台 —— 真起进程会被 conftest 拦，替身即可。

        这一支的 `session.fetch` 压根不带 `pin`：那台机器一发一换，页留不住。
        """
        from dhole_mcp import browser as browser_mod

        class _Stealthy:
            cdp_attached = False
            launch_note = ""

            def __init__(self, **kw):
                box["launch_kwargs"] = kw
                box.setdefault("own_calls", [])

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def fetch(self, url, **kw):
                box["own_calls"].append(dict(kw, url=url))
                return _page(url)

        monkeypatch.setattr(browser_mod, "StealthyBrowser", _Stealthy)
        return box

    @pytest.mark.asyncio
    async def test_two_urls_one_page_is_not_pinnable_and_that_is_said(
            self, monkeypatch, tmp_path):
        monkeypatch.setenv("DHOLE_HOME", str(tmp_path))
        sess = self._Sess()
        self._wire(monkeypatch, sess)
        s = MasterFetchServer()
        with _arg_notes_scope():
            bulk = await s.bulk_stealthy_fetch(
                urls=["https://example.com/a", "https://example.com/b"],
                session_id="bob", pin=True)
        assert isinstance(bulk, BulkResponseModel)
        assert all(k["pin"] is False for k in sess.kw), sess.kw
        assert any(PIN_NOTES["bulk"] in (r.summary or "") for r in bulk.results), \
            [r.summary for r in bulk.results]

    @pytest.mark.asyncio
    async def test_an_attached_session_never_holds_a_page(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DHOLE_HOME", str(tmp_path))
        sess = self._Sess()
        sess.cdp_attached = True
        self._wire(monkeypatch, sess)
        with _arg_notes_scope():
            bulk = await MasterFetchServer().bulk_stealthy_fetch(
                urls=["https://example.com/a"], session_id="bob", pin=True)
        assert sess.kw[0]["pin"] is False, sess.kw
        assert PIN_NOTES["transport"] in (bulk.results[0].summary or ""), \
            bulk.results[0].summary

    @pytest.mark.asyncio
    async def test_a_session_that_ran_on_another_browser_says_so(self, monkeypatch,
                                                                 tmp_path):
        """anti_detect 要的是启动级硬化 → 这一发落在自己的一台浏览器上，
        那一页留在另一台上：驻留的话必须跟着改口（与"logins did not ride along"同一条）。"""
        monkeypatch.setenv("DHOLE_HOME", str(tmp_path))
        sess = self._Sess()
        sess._hide_canvas = True      # 那台机器开着画布噪声，这一发要的是 off
        self._wire(monkeypatch, sess)
        box = self._one_off_browser(monkeypatch, {})
        with _arg_notes_scope():
            bulk = await MasterFetchServer().bulk_stealthy_fetch(
                urls=["https://example.com/a"], session_id="bob",
                hide_canvas=False, pin=True)
        assert sess.kw == [], "身份不符还复用那台浏览器 = 这一刀说错了自己站在哪"
        assert box["own_calls"], "这一发确实走在另一台上，替身没记录就等于没走到"
        assert all("pin" not in c for c in box["own_calls"]), box["own_calls"]
        summary = bulk.results[0].summary or ""
        assert PIN_NOTES["identity"] in summary, summary

    @pytest.mark.asyncio
    async def test_a_plain_call_still_asks_for_no_pin_at_all(self, monkeypatch,
                                                             tmp_path):
        """反向半边：不带 pin 的每一发，浏览器层收到的必须是默认值 ——
        驻留是叫出来的，不是默认存在的。"""
        monkeypatch.setenv("DHOLE_HOME", str(tmp_path))
        sess = self._Sess()
        self._wire(monkeypatch, sess)
        with _arg_notes_scope():
            await MasterFetchServer().bulk_stealthy_fetch(
                urls=["https://example.com/a"], session_id="bob")
        assert sess.kw[0]["pin"] is False, sess.kw
        assert sess.kw[0]["pin_out"] is None, sess.kw
