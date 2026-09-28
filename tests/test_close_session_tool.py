"""`close_session` 上 wire：先看得见，才谈得上点名关。

这个工具此前处在最难发现的一种状态——**方法在、能跑、被测试覆盖，但客户端调不到**：
它从来没进过 `_TOOL_DEFS`。而文档按它在写（README 的工具表、"九个工具"那句、本地数据
一节），代码也按它在指（`_get_session` 的报错让人去用一个从未存在的 `list_sessions`，
`server.py` 里还有一句 next_action 直接叫人"close_session"）。一个 agent 照着建议去调，
拿到的是 Unknown tool，而且没有任何途径知道自己到底还带着哪些站的凭据。

所以接上，并且比"关一个"多做一步：**无参数就是名册**。cookie 值按 G7 的既有边界
一律不出境（只给 host + 名字 + 还剩多久自然过期），因为工具输出会落进 transcript。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from dhole_mcp import sessions
from dhole_mcp.server import MasterFetchServer, SessionCensusRow, _SessionEntry

HOME = "https://shop.example.com/login"
ME = "https://shop.example.com/account"
OTHER = "https://elsewhere.example.org/x"
SECRET = "session-value-DO-NOT-LEAK"


def _dispatch(tool: str, args: dict):
    return asyncio.run(MasterFetchServer()._dispatch(tool, args))


class _FakeBrowser:
    """Enough of a session object for the census and the close path."""

    def __init__(self):
        self._is_alive = True
        self.closes = 0

    async def close(self):
        self.closes += 1
        self._is_alive = False


def _open_browser(server, sid: str, entry_session=None):
    fake = entry_session or _FakeBrowser()
    server._sessions[sid] = _SessionEntry(session=fake, session_type="stealthy")
    return fake


class TestTheCensus:

    @pytest.mark.asyncio
    async def test_a_bare_call_lists_what_holds_credentials_and_closes_nothing(self):
        """「我还有哪些会话带着站方凭据」此前在 wire 上无处可问。"""
        sessions.store("alpha", ME, {"sid": SECRET, "csrf": "tok"})
        sessions.store("beta", OTHER, {"p": "q"})
        out = await MasterFetchServer().close_session()
        assert out.open_count == 2
        assert [r.session_id for r in out.sessions] == ["alpha", "beta"]
        alpha = out.sessions[0]
        assert alpha.hosts == {"shop.example.com": ["csrf", "sid"]}
        assert alpha.cookies == 2
        # a countdown, not an epoch float: "expires in 21h" is actionable,
        # 1790566780.9 is not (and the old engine_health blob got that wrong).
        assert 86000 < alpha.expires_in_s <= 86400
        assert out.closed == 0 and out.cookies_forgotten == 0
        assert sessions.header_for("alpha", ME) == {"sid": SECRET, "csrf": "tok"}, \
            "看一看不该有任何副作用"

    @pytest.mark.asyncio
    async def test_a_jar_without_a_browser_and_a_browser_without_a_jar_both_appear(self):
        """两个存储按同一个 id 各自被填：HTTP 层登录留 jar，浏览器会话可以只有浏览器。
        任一半单独存在都是"这个 id 还带着东西"的理由。"""
        server = MasterFetchServer()
        sessions.store("jar-only", ME, {"sid": "v"})
        _open_browser(server, "browser-only")
        rows = {r.session_id: r for r in (await server.close_session()).sessions}
        assert rows["jar-only"].browser_open is False
        assert rows["browser-only"].browser_open is True
        assert rows["browser-only"].browser_type == "stealthy"
        assert rows["browser-only"].cookies == 0
        assert rows["browser-only"].browser_created

    @pytest.mark.asyncio
    async def test_a_dead_browser_is_not_reported_as_open(self):
        server = MasterFetchServer()
        fake = _open_browser(server, "zombie")
        fake._is_alive = False
        rows = await server._session_rows()
        assert [r.browser_open for r in rows] == [False]
        assert rows[0].browser_type == "" and rows[0].browser_created == ""

    @pytest.mark.asyncio
    async def test_nothing_open_says_so_rather_than_returning_an_empty_object(self):
        out = await MasterFetchServer().close_session()
        assert out.open_count == 0 and out.sessions == []
        assert "Nothing is open" in out.message


class TestClosing:

    @pytest.mark.asyncio
    async def test_naming_a_session_forgets_its_jar_and_shuts_its_browser(self):
        server = MasterFetchServer()
        sessions.store("bob", ME, {"sid": "v"})
        fake = _open_browser(server, "bob")
        out = await server.close_session("bob")
        assert out.session_id == "bob" and out.cookies_forgotten == 1
        assert out.browser_closed is True and out.closed == 1
        assert sessions.header_for("bob", ME) == {}
        assert fake.closes == 1 and "bob" not in server._sessions

    @pytest.mark.asyncio
    async def test_all_true_forgets_everything_and_prices_the_prewarm(self):
        """all=true 连预热好的共享浏览器一起放弃——那是调用方没要求、但必须知道的
        代价（下一次隐身抓取要重付 3-5 秒冷启动）。"""
        server = MasterFetchServer()
        sessions.store("a", ME, {"sid": "v"})
        sessions.store("b", OTHER, {"x": "y"})
        server._auto_stealthy_id = "auto-1"
        _open_browser(server, "auto-1")
        out = await server.close_session(all=True)
        assert out.closed == 3 and out.cookies_forgotten == 2
        assert out.prewarm_closed is True and "pre-warmed" in out.message
        assert server._auto_stealthy_id is None, \
            "指针留着，下一次抓取就会去开一台已经不存在的浏览器"
        assert (await server.close_session()).open_count == 0

    @pytest.mark.asyncio
    async def test_a_browser_that_refuses_to_close_still_loses_its_jar(self):
        """一个关不掉的浏览器不能让凭据留在盘上——顺序因此是"先记账再关"。"""
        class _Stuck:
            _is_alive = True

            async def close(self):
                raise RuntimeError("browser is wedged")

        server = MasterFetchServer()
        sessions.store("stuck", ME, {"sid": "v"})
        server._sessions["stuck"] = _SessionEntry(session=_Stuck(), session_type="stealthy")
        out = await server.close_session("stuck")
        assert out.cookies_forgotten == 1
        assert sessions.header_for("stuck", ME) == {}


class TestTheArgumentSurface:

    @pytest.mark.asyncio
    async def test_naming_one_and_all_are_two_answers_to_one_question(self):
        server = MasterFetchServer()
        sessions.store("keep", ME, {"sid": "v"})
        out = await server.close_session("keep", all=True)
        assert out.error and "same question" in out.error
        assert out.closed == 0 and out.cookies_forgotten == 0
        assert sessions.header_for("keep", ME) == {"sid": "v"}, "拒绝就该什么都没做"

    def test_all_false_from_a_string_client_wipes_nothing(self):
        """cache_clear 踩过的坑在另一个工具上重来一次：`"false"` 是非空字符串。
        这里点名关一个，另一份 jar 必须还活着。"""
        sessions.store("target", ME, {"sid": "v"})
        sessions.store("untouched", OTHER, {"sid": "v"})
        _content, structured = _dispatch("close_session", {
            "session_id": "target", "all": "false"})
        assert structured["session_id"] == "target"
        assert sessions.header_for("untouched", OTHER) == {"sid": "v"}

    def test_a_non_string_session_id_is_refused_before_anything_closes(self):
        sessions.store("real", ME, {"sid": "v"})
        with pytest.raises(ValueError, match="must be a string"):
            _dispatch("close_session", {"session_id": 12})
        assert sessions.header_for("real", ME) == {"sid": "v"}

    @pytest.mark.asyncio
    async def test_an_unknown_id_answers_with_the_list(self):
        sessions.store("open-one", ME, {"sid": "v"})
        out = await MasterFetchServer().close_session("typo-ed")
        assert out.error.startswith("Nothing is open under 'typo-ed'")
        assert "open-one" in out.next_action
        assert [r.session_id for r in out.sessions] == ["open-one"]


class TestTheWireContract:

    def test_a_cookie_value_never_reaches_the_wire(self):
        """名册的设计目的就是"告诉你还带着什么"，而值恰恰是不能给的那一半。"""
        sessions.store("alpha", ME, {"sid": SECRET})
        _content, structured = _dispatch("close_session", {})
        blob = json.dumps(structured, ensure_ascii=False)
        assert SECRET not in blob
        assert "sid" in blob, "名字要给，否则不知道关的是什么"

    def test_a_row_always_carries_its_id_even_after_compaction(self):
        """`session_id` 不在省略表里：一行没有 id 的名册说明不了任何事。"""
        sessions.store("alpha", ME, {"sid": "v"})
        _content, structured = _dispatch("close_session", {})
        row = structured["sessions"][0]
        assert row["session_id"] == "alpha"
        # browser_open false / browser_type "" are defaults, so they ride off the wire
        assert set(row) == {"session_id", "hosts", "cookies", "expires_in_s"}

    def test_it_is_a_real_tool_with_real_annotations(self):
        from dhole_mcp import server as server_mod
        defs = {td["name"]: td for td in MasterFetchServer._TOOL_DEFS}
        assert "close_session" in defs, "README 说九个工具，wire 就得给九个"
        assert defs["close_session"]["annotations"]["destructiveHint"] is True
        assert defs["close_session"]["annotations"]["readOnlyHint"] is False
        assert server_mod._TOP_LEVEL_ARGS["close_session"] == frozenset({"session_id", "all"})
        assert "close_session" in server_mod.DHOLE_INSTRUCTIONS, \
            "常驻指令里没有它，agent 就不知道该用它"

    def test_it_declares_no_output_schema(self):
        """和 screenshot / resolve_url 同一档：名册是 dict-of-lists 的形状，写成
        schema 只能写成空承诺，而承诺一旦写下就必须永远成立。"""
        from dhole_mcp.server import _output_schema_for
        assert _output_schema_for("close_session") is None

    def test_the_census_row_is_covered_by_the_compaction_policy(self):
        """新模型必须被 _WIRE_OMIT 判定过，否则"新增字段未判定即红"的守卫形同虚设。"""
        from dhole_mcp.server import _WIRE_OMIT
        for model in ("SessionCensusRow", "SessionClosedModel"):
            assert model in _WIRE_OMIT
        assert "session_id" not in _WIRE_OMIT["SessionCensusRow"]
        assert set(_WIRE_OMIT["SessionCensusRow"]) <= set(SessionCensusRow.model_fields)
