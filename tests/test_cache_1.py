"""test_cache_1.py —— 由 3 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import asyncio
import json
import pytest
from dhole_mcp import sessions
from dhole_mcp.server import MasterFetchServer, SessionCensusRow, _SessionEntry
import http.server
import socket
import threading
import time
from dhole_mcp import identity
from dhole_mcp import server as server_mod
from dhole_mcp.cache import set_cached
from dhole_mcp.security import SecurityError
from dhole_mcp.server import _tier_error_text


# ============================================================================
# 来源：test_sys_state_tool.py
# ============================================================================
# `sys_state` 上 wire：本地状态看得见，点名才抹得掉。

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
    async def test_a_bare_call_lists_what_holds_credentials_and_changes_nothing(self):
        """「我还有哪些会话带着站方凭据」在 wire 上只有一处可问。"""
        sessions.store("alpha", ME, {"sid": SECRET, "csrf": "tok"})
        sessions.store("beta", OTHER, {"p": "q"})
        out = await MasterFetchServer().sys_state()
        assert out.open_count == 2
        assert [r.session_id for r in out.sessions] == ["alpha", "beta"]
        alpha = out.sessions[0]
        assert alpha.hosts == {"shop.example.com": ["csrf", "sid"]}
        assert alpha.cookies == 2
        # a countdown, not an epoch float: "expires in 21h" is actionable,
        # 1790566780.9 is not (and the old engine_health blob got that wrong).
        assert 86000 < alpha.expires_in_s <= 86400
        assert out.closed == 0 and out.cookies_forgotten == 0
        assert out.cleared == [] and out.pending_approval is False
        assert sessions.header_for("alpha", ME) == {"sid": SECRET, "csrf": "tok"}, \
            "看一看不该有任何副作用"

    @pytest.mark.asyncio
    async def test_the_bare_census_covers_the_other_stores_too(self):
        """合并的意义：一颗工具回答「本地存着什么」，而不是每个仓库各问一遍。"""
        from dhole_mcp.cache import set_cached

        await set_cached("https://example.com/", "markdown", ["body"], 200)
        out = await MasterFetchServer().sys_state()
        assert out.cache["entries"] == 1
        assert out.cache["expired"] == 0

    @pytest.mark.asyncio
    async def test_a_jar_without_a_browser_and_a_browser_without_a_jar_both_appear(self):
        """两个存储按同一个 id 各自被填：HTTP 层登录留 jar，浏览器会话可以只有浏览器。
        任一半单独存在都是"这个 id 还带着东西"的理由。"""
        server = MasterFetchServer()
        sessions.store("jar-only", ME, {"sid": "v"})
        _open_browser(server, "browser-only")
        rows = {r.session_id: r for r in (await server.sys_state()).sessions}
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
        out = await MasterFetchServer().sys_state()
        assert out.open_count == 0 and out.sessions == []
        assert "Nothing is open" in out.message


class TestErasingASession:

    @pytest.mark.asyncio
    async def test_naming_a_session_forgets_its_jar_and_shuts_its_browser(self):
        server = MasterFetchServer()
        sessions.store("bob", ME, {"sid": "v"})
        fake = _open_browser(server, "bob")
        out = await server.sys_state(clear=["bob"])
        assert out.session_id == "bob" and out.cookies_forgotten == 1
        assert out.browser_closed is True and out.closed == 1
        assert out.cleared == ["sessions"]
        assert sessions.header_for("bob", ME) == {}
        assert fake.closes == 1 and "bob" not in server._sessions

    @pytest.mark.asyncio
    async def test_erasing_every_session_waits_for_a_confirm(self):
        """§3-32：丢掉的是一站的登录态，而它要到下次访问那个站才露出来。"""
        server = MasterFetchServer()
        sessions.store("keep", ME, {"sid": "v"})
        paused = await server.sys_state(clear=["sessions"])
        assert paused.pending_approval is True and paused.cleared == []
        assert "confirm" in paused.next_action
        assert sessions.header_for("keep", ME) == {"sid": "v"}, "没批准就不能动手"

        gone = await server.sys_state(clear=["sessions"], confirm=True)
        assert sessions.header_for("keep", ME) == {}
        assert gone.cleared == ["sessions"] and gone.closed == 1

    @pytest.mark.asyncio
    async def test_the_all_token_prices_the_prewarm(self):
        """抹掉全部会话会连预热好的共享浏览器一起放弃——那是调用方没要求、但必须
        知道的代价（下一次隐身抓取要重付 3-5 秒冷启动）。"""
        server = MasterFetchServer()
        sessions.store("a", ME, {"sid": "v"})
        sessions.store("b", OTHER, {"x": "y"})
        server._auto_stealthy_id = "auto-1"
        _open_browser(server, "auto-1")
        out = await server.sys_state(clear=["all"], confirm=True)
        assert out.closed == 3 and out.cookies_forgotten == 2
        assert out.prewarm_closed is True and "pre-warmed" in out.message
        assert server._auto_stealthy_id is None, \
            "指针留着，下一次抓取就会去开一台已经不存在的浏览器"
        assert (await server.sys_state()).open_count == 0

    @pytest.mark.asyncio
    async def test_naming_one_session_and_the_class_never_double_counts(self):
        """token 列表的语义是并集，不是两条命令：点名一个又点名全部，关掉的还是全部，
        但每个会话只被处理一次。"""
        server = MasterFetchServer()
        sessions.store("keep", ME, {"sid": "v"})
        sessions.store("other", OTHER, {"sid": "v"})
        out = await server.sys_state(clear=["keep", "sessions"], confirm=True)
        assert out.closed == 2 and out.cookies_forgotten == 2
        assert out.session_id == "", "并集不是一个会话的回执"

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
        out = await server.sys_state(clear=["stuck"])
        assert out.cookies_forgotten == 1
        assert sessions.header_for("stuck", ME) == {}


class TestTheOtherStores:

    @pytest.mark.asyncio
    async def test_clearing_the_cache_leaves_the_credentials_alone(self):
        """旧 cache_clear(all=true) 顺手抹掉 cookie，正是这条合并要修的谎。"""
        from dhole_mcp.cache import get_cached, set_cached

        sessions.store("alpha", ME, {"sid": SECRET})
        await set_cached("https://example.com/", "markdown", ["body"], 200)
        out = await MasterFetchServer().sys_state(clear=["cache"])
        assert await get_cached("https://example.com/", "markdown") is None
        assert out.purged == 1 and out.cleared == ["cache"]
        assert sessions.header_for("alpha", ME) == {"sid": SECRET}, \
            "清缓存不该顺手把人登出"

    @pytest.mark.asyncio
    async def test_a_cache_wipe_reports_the_engine_pool_as_nothing_of_its_business(self, monkeypatch):
        """D-10：每次清缓存都附上 ~1KB 的逐引擎状态，是写给没问这个问题的调用方的。"""
        from dhole_mcp import search_metasearch as ms

        taken: list[str] = []

        def fake_snapshot():
            taken.append("snapshot")
            return {"brave": {"n": 3}}

        monkeypatch.setattr(ms, "engine_state_snapshot", fake_snapshot, raising=False)
        out = await MasterFetchServer().sys_state(clear=["cache"])
        assert taken == [], "没问引擎池就不该去取它的快照"
        assert out.engine_health == {} and out.engine_state_reset is False

    @pytest.mark.asyncio
    async def test_show_can_read_the_pool_without_resetting_it(self, monkeypatch):
        """合并前只有「抹掉引擎状态」这一条路径能看见池子；看清它不该要先毁掉它。"""
        from dhole_mcp import search_metasearch as ms

        monkeypatch.setattr(ms, "engine_state_snapshot",
                            lambda: {"brave": {"cooldown_seconds_left": 30}},
                            raising=False)
        out = await MasterFetchServer().sys_state(show=["engine_state"])
        assert out.engine_health == {"brave": {"cooldown_seconds_left": 30}}
        assert out.engine_state_reset is False, "show 不是 clear 的别名"

class TestTheArgumentSurface:

    def test_a_stringified_false_confirm_wipes_nothing(self):
        """cache_clear 踩过的坑在 confirm 上重来一次：`"false"` 是非空字符串。"""
        sessions.store("target", ME, {"sid": "v"})
        sessions.store("untouched", OTHER, {"sid": "v"})
        _content, structured = _dispatch("sys_state", {"clear": ["sessions"],
                                                       "confirm": "false"})
        assert structured["pending_approval"] is True
        assert sessions.header_for("target", ME) == {"sid": "v"}
        assert sessions.header_for("untouched", OTHER) == {"sid": "v"}

    def test_a_bare_string_instead_of_a_list_is_refused(self):
        """show="cache" 是「一个 token」还是「一串字符」？两种读法都不该由服务端猜。"""
        with pytest.raises(ValueError, match="must be an array of strings"):
            _dispatch("sys_state", {"clear": "cache"})

    def test_a_non_string_token_is_refused_before_anything_erases(self):
        sessions.store("real", ME, {"sid": "v"})
        with pytest.raises(ValueError, match="must be a string"):
            _dispatch("sys_state", {"clear": [12]})
        assert sessions.header_for("real", ME) == {"sid": "v"}

    @pytest.mark.asyncio
    async def test_an_unknown_token_lists_the_vocabulary_and_the_open_ids(self):
        """既不落"某个会话"那一支，也不能只回一句 not found——调用方要的是「那我该抹
        哪一类」，不是「你给的名字不存在」。"""
        sessions.store("open-one", ME, {"sid": "v"})
        out = await MasterFetchServer().sys_state(clear=["typo-ed"])
        assert "typo-ed" in out.error
        assert "open-one" in out.error
        for token in MasterFetchServer._STATE_TOKENS:
            assert token in out.error
        assert out.closed == 0 and out.cookies_forgotten == 0
        assert [r.session_id for r in out.sessions] == ["open-one"]
        assert sessions.header_for("open-one", ME) == {"sid": "v"}, \
            "报错不该顺手抹掉别人的凭据"

    @pytest.mark.asyncio
    async def test_an_unknown_show_token_is_an_error_not_an_empty_report(self):
        out = await MasterFetchServer().sys_state(show=["everithing"])
        assert out.error.startswith("Nothing matches"), out.error
        assert out.cleared == []


class TestTheWireContract:

    def test_a_cookie_value_never_reaches_the_wire(self):
        """名册的设计目的就是"告诉你还带着什么"，而值恰恰是不能给的那一半。"""
        sessions.store("alpha", ME, {"sid": SECRET})
        _content, structured = _dispatch("sys_state", {})
        blob = json.dumps(structured, ensure_ascii=False)
        assert SECRET not in blob
        assert "sid" in blob, "名字要给，否则不知道关的是什么"

    def test_a_row_always_carries_its_id_even_after_compaction(self):
        """`session_id` 不在省略表里：一行没有 id 的名册说明不了任何事。"""
        sessions.store("alpha", ME, {"sid": "v"})
        _content, structured = _dispatch("sys_state", {})
        row = structured["sessions"][0]
        assert row["session_id"] == "alpha"
        # browser_open false / browser_type "" are defaults, so they ride off the wire
        assert set(row) == {"session_id", "hosts", "cookies", "expires_in_s"}

    def test_it_is_a_real_tool_with_real_annotations(self):
        import dhole_mcp.server as server_mod

        defs = {td["name"]: td for td in MasterFetchServer._TOOL_DEFS}
        assert "sys_state" in defs
        assert defs["sys_state"]["annotations"]["destructiveHint"] is True
        assert defs["sys_state"]["annotations"]["readOnlyHint"] is False
        assert server_mod._TOP_LEVEL_ARGS["sys_state"] == frozenset(
            {"show", "clear", "confirm"})
        assert "sys_state" in server_mod.DHOLE_INSTRUCTIONS, \
            "常驻指令里没有它，agent 就不知道该用它"

    def test_the_output_schema_mechanism_does_not_exist(self):
        """17.0：不广告 outputSchema —— 机制连同三个 wire 形状开关一起删了。

        名册是 dict-of-lists 的形状，写成 schema 只能写成空承诺，而承诺一旦写下就
        必须永远成立。这里断言的是"没有这套机制"而不是"它返回 None"：留一个永远
        返回 None 的构造函数，等于给下一个人留一条把承诺加回来的路。
        """
        import dhole_mcp.server as server_mod

        assert not hasattr(server_mod, "_output_schema_for"), (
            "开关要与通道一起删除，不许留一个永远无效的入口")
        for td in MasterFetchServer._TOOL_DEFS:
            assert "outputSchema" not in td, f"{td['name']} 不许广告 outputSchema"

    def test_the_state_models_are_covered_by_the_compaction_policy(self):
        """新模型必须被 _WIRE_OMIT 判定过，否则"新增字段未判定即红"的守卫形同虚设。"""
        from dhole_mcp.wire import _WIRE_OMIT

        for model in ("SessionCensusRow", "SysStateModel"):
            assert model in _WIRE_OMIT
        assert "session_id" not in _WIRE_OMIT["SessionCensusRow"]
        assert set(_WIRE_OMIT["SessionCensusRow"]) <= set(SessionCensusRow.model_fields)
        assert "message" not in _WIRE_OMIT["SysStateModel"], \
            "一句「这次做了什么」总要上线"

    def test_the_new_census_classes_are_covered_by_the_compaction_policy(self):
        """G25 评审加的两个类：空名单缺席（默认值上线即噪声），非空才在。"""
        from dhole_mcp.wire import _WIRE_OMIT

        assert {"cdp_hosts", "config_keys"} <= _WIRE_OMIT["SysStateModel"]


class TestTheCdpRoster:
    """G25 评审：撤销粒度从「删整个文件」收到主机；名册把 cdp_hosts / config
    补进册。conftest 已把 cdp_hosts.json 与 config.json 指到 tmp_path 并清过
    server 的名册缓存，这里只管行为。"""

    def _authorize(self, *hosts):
        import dhole_mcp.server as server_mod
        for h in hosts:
            server_mod._cdp_authorize_host(h)

    def _roster(self):
        import dhole_mcp.server as server_mod
        return server_mod._cdp_authorized_hosts()

    def test_show_lists_the_roster_and_the_default_census_carries_it(self):
        self._authorize("chrome.test", "box.test")
        _content, structured = _dispatch("sys_state", {"show": ["cdp_hosts"]})
        assert structured["cdp_hosts"] == ["box.test", "chrome.test"]
        _content, bare = _dispatch("sys_state", {})
        assert bare["cdp_hosts"] == ["box.test", "chrome.test"], \
            "「谁被批准接我的登录态」不该等点名才出现"

    def test_revoking_one_host_needs_confirm_and_leaves_the_other(self):
        self._authorize("a.test", "b.test")
        _content, paused = _dispatch("sys_state", {"clear": ["cdp:host=a.test"]})
        assert paused.get("pending_approval") is True, paused
        assert "confirm" in paused.get("next_action", ""), paused
        assert self._roster() == frozenset({"a.test", "b.test"}), \
            "没有 confirm 的撤销一个主机都不能动"

        _content, out = _dispatch("sys_state",
                                  {"clear": ["cdp:host=a.test"], "confirm": True})
        assert "cdp_hosts" in out["cleared"], out
        assert "a.test" in out["message"], out
        assert self._roster() == frozenset({"b.test"})

    def test_clear_cdp_hosts_revokes_all_and_the_disk_looks_never_authorized(self):
        import pathlib

        import dhole_mcp.server as server_mod
        self._authorize("a.test", "b.test")
        _content, out = _dispatch("sys_state",
                                  {"clear": ["cdp_hosts"], "confirm": True})
        assert self._roster() == frozenset()
        assert not pathlib.Path(server_mod._cdp_hosts_file()).exists(), \
            "名册清空后的磁盘形状 = 从没授权过任何主机，不是空名单文件"

    def test_all_includes_cdp_hosts(self):
        self._authorize("a.test")
        _content, out = _dispatch("sys_state", {"clear": ["all"], "confirm": True})
        assert "cdp_hosts" in out["cleared"], out
        assert self._roster() == frozenset()

    def test_revoking_an_unknown_host_is_an_error_naming_the_roster(self):
        """与 `clear=["cahce"]` 同罪：装没看见就是 no-op，错一个字母要当场说。"""
        self._authorize("a.test")
        _content, out = _dispatch("sys_state",
                                  {"clear": ["cdp:host=nope.test"], "confirm": True})
        assert "nope.test" in out["error"], out
        assert "a.test" in out["error"], "错误要列出名册里真正存在的主机"
        assert out.get("cleared", []) == []
        assert self._roster() == frozenset({"a.test"})

    def test_config_is_show_only_with_a_named_refusal(self):
        """旋钮的另一扇门在 `dhole config unset`——留两扇会各自漂走的门是这个
        仓库自己裁决过的事，所以 clear 这一类要被点名拒绝，不是悄悄吞掉。"""
        _content, out = _dispatch("sys_state", {"clear": ["config"]})
        assert "show-only" in out["error"], out
        assert "dhole config unset" in out["error"], out
        # 拒绝即什么都没清：空的 cleared 是默认值，按省略表不下线（缺席=空）。
        assert out.get("cleared", []) == []

    def test_show_config_lists_names_never_values(self):
        from dhole_mcp import env_registry

        env_registry.set_entry("DHOLE_USAGE_LOG", "1")
        env_registry.set_entry("DHOLE_BOCHA_API_KEY", "hunter2-super-secret")
        _content, structured = _dispatch("sys_state", {"show": ["config"]})
        assert structured["config_keys"] == ["DHOLE_BOCHA_API_KEY", "DHOLE_USAGE_LOG"]
        blob = json.dumps(structured, ensure_ascii=False)
        assert "hunter2-super-secret" not in blob, \
            "值里可能有 secret：名字过线，值永远不过"

    def test_show_all_expands_to_the_state_classes_it_was_asked_to_show(self):
        """批次 6a：`show=["all"]` 必须真的展开 —— 实测它回来的体只有一颗 `message`。

        下游全是精确成员测试（`"cache" in report` / `"engine_state" in report` / …），
        而 "all" 一个都不匹配。clear 侧的 "all" 一直是展开的，同一个词在两扇门上
        一个展开一个不展开，读者只能猜。

        判据落在 message 上而不是字段上，因为这五类各有各的空集形状（引擎池没记录时
        `engine_health` 按契约不上线），而"有没有去查"这件事只在这句话里说得清。
        """
        _content, out = _dispatch("sys_state", {"show": ["all"]})
        msg = out["message"]
        listed = msg.split(":")[0].replace("Reported ", "")
        for cls in ("cache", "sessions", "cred_defaults", "cdp_hosts", "engine_state"):
            assert cls in listed, f"展开的名册少了 {cls}：{listed}"
        assert "all," not in listed and " all" not in listed, (
            f"'all' 自己被当成了一类状态印进名册：{listed}")
        assert "config" not in listed, f"没点名就该不出现的类出现了：{listed}"
        # 查过的类要留痕：cache 的统计是真的上线了（空集也带 entries=0 的形状）。
        assert "cache" in out, sorted(out)

    def test_a_show_that_asks_for_one_class_reports_only_that_one(self):
        """展开不许把默认名册拖进来当副作用：点名一类就只查一类。"""
        _content, out = _dispatch("sys_state", {"show": ["cache"]})
        listed = out["message"].split(":")[0].replace("Reported ", "")
        assert listed.strip() == "cache", listed
        assert "engine_health" not in out, sorted(out)

    def test_an_empty_answer_says_it_is_empty_instead_of_looking_swallowed(self):
        """空回执与被吞的数据字节相同 —— 这是 wire 压缩的事实，不是假设。

        `config_keys` 是 `default_factory=list`，等于默认值的字段整个不上线
        （`wire.py`），所以"这台机器一个旋钮都没存过"与"这颗字段掉了"在字节上是
        同一句话。修法不是新增字段，是把"空"说进那句人读的话。
        """
        _content, out = _dispatch("sys_state", {"show": ["config"]})
        assert "config_keys" not in out, "空列表按契约不上线（这条守的是压缩本身）"
        assert "none are persisted" in out["message"], out["message"]
        assert "built-in default" in out["message"], out["message"]
        assert out.get("error", "") == "", "空集不是一次失败"

    def test_a_persisted_knob_is_counted_in_the_same_sentence(self):
        """同一句话在"有"的时候也要能用：计数变了，措辞不许偷偷换成另一条路。"""
        from dhole_mcp import env_registry

        env_registry.set_entry("DHOLE_USAGE_LOG", "1")
        _content, out = _dispatch("sys_state", {"show": ["config"]})
        assert "persisted config knobs 1" in out["message"], out["message"]
        assert "none are persisted" not in out["message"]

    def test_an_empty_roster_and_config_ride_off_the_wire(self):
        _content, structured = _dispatch("sys_state", {})
        assert "cdp_hosts" not in structured, structured
        assert "config_keys" not in structured, structured


# ============================================================================
# 来源：test_a_concurrent_duplicate_fetch_is_shared_not_repeated.py
# ============================================================================
# 同一发取回，不因为同时来了几个人就发四遍。

#: 真解析器必须在**导入时**抓住：conftest 的 `_offline_dns`（autouse）把
#: `socket.getaddrinfo` 钉成公网地址，夹具里现取拿到的是**已经被钉住的那个**，
#: 于是 127.0.0.1 被判成公网 —— 靶站连不上，用例测的东西整个消失。
_REAL_GETADDRINFO = socket.getaddrinfo

HITS = {"page": 0, "long": 0, "slow": 0, "other": 0}

_PAGE = (b"<html><body><h1>one page</h1><p>"
         b"a reasonably long body so it is not a shell</p></body></html>")
_SLOW = (b"<html><body><h1>slow page</h1><p>"
         b"a reasonably long body so it is not a shell</p></body></html>")

#: 两个主题各占一半的段落，好让 focus 的取舍**看得见**（见
#: `test_a_follower_filters_by_its_own_focus`）。焦点词挑的是只在一半段落里出现的
#: 词，否则 BM25 打分两边一样，断言就退化成"长度差不多"。
_PARAS = "".join(
    (f"<p>alpha 甲主题第{i}段：关于苹果与橙子的详细说明，这一段只谈甲。</p>"
     if i % 2 == 0 else
     f"<p>beta 乙主题第{i}段：关于桥梁与隧道的详细说明，这一段只谈乙。</p>")
    for i in range(30))
_LONG = f"<html><body><h1>两个主题</h1>{_PARAS}</body></html>".encode()


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/robots.txt":
            body, ctype = b"User-agent: *\n", "text/plain"
        elif path == "/page":
            HITS["page"] += 1
            body, ctype = _PAGE, "text/html; charset=utf-8"
        elif path == "/long":
            HITS["long"] += 1
            body, ctype = _LONG, "text/html; charset=utf-8"
        elif path == "/slow":
            HITS["slow"] += 1
            time.sleep(1.2)
            body, ctype = _SLOW, "text/html; charset=utf-8"
        elif path == "/other":
            HITS["other"] += 1
            body, ctype = _PAGE, "text/html; charset=utf-8"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def site(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _REAL_GETADDRINFO)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def server(site):
    identity.set_value("allow_private", True)
    for k in HITS:
        HITS[k] = 0
    return server_mod.MasterFetchServer()


def _burst(srv, path, n=4, **kw):
    """n 个**同时**发出的同一请求。"""
    async def main():
        return await asyncio.gather(*[srv.web_read(path, **kw) for _ in range(n)])
    return asyncio.run(main())


# ─── 1 · D8 的原形状 ─────────────────────────────────────────────

def test_four_identical_cold_reads_hit_the_origin_once(server, site):
    """判据就是那个数：源站被打了几次。"""
    rs = _burst(server, f"{site}/page")
    assert HITS["page"] == 1, (
        f"源站被打了 {HITS['page']} 次 —— 合流没生效（合并前实测就是 4 次）")
    assert [r.status for r in rs] == [200] * 4


def test_every_caller_still_gets_a_usable_answer(server, site):
    """省下的必须是重复的**取回**，不是任何一个人的答案。"""
    rs = _burst(server, f"{site}/page")
    bodies = ["\n".join(r.content) for r in rs]
    assert all("one page" in b for b in bodies), bodies
    assert all(r.content_ok for r in rs), [r.content_ok for r in rs]
    assert len(set(bodies)) == 1, "同一个请求的四个答案不该各不相同"
    assert all(r.total_extracted_chars for r in rs)


def test_the_followers_say_where_their_answer_came_from(server, site):
    """冷缓存上的 `cached=True` 读起来像个 bug，所以那句话必须自己说清。

    这也顺带钉住 `duration_ms`：跟随者确实等了墙钟时间，报 0 就是账不对。
    """
    rs = _burst(server, f"{site}/page")
    leader = [r for r in rs if r.fetcher_used != "cache"]
    followers = [r for r in rs if r.fetcher_used == "cache"]
    assert len(leader) == 1 and len(followers) == 3
    assert leader[0].duration_ms > 0
    for f in followers:
        assert f.cached is True
        assert "concurrent call for this same request" in f.summary, f.summary
        assert "no request was sent for this call" in f.summary, f.summary
        assert f.duration_ms > 0, (
            "等了却报 duration_ms=0 —— 一份等了半秒的回执不该说这次调用没花时间")


def test_a_follower_filters_by_its_own_focus(server, site):
    """合流之后最容易坏掉的一条：跟随者拿到**领头那一发筛过的**正文。

    两个调用同 URL、同缓存键、不同 focus。它们会合流（缓存键相同），而各自必须按
    **自己的**焦点词筛 —— 走缓存那条路天然做到这一点，这条用例把它钉住。
    """
    async def main():
        return await asyncio.gather(
            server.web_read(f"{site}/long", focus="苹果"),
            server.web_read(f"{site}/long", focus="桥梁"),
        )
    rs = asyncio.run(main())
    assert HITS["long"] == 1, f"合流没生效：{HITS['long']} 次源站"
    by_focus = {}
    for r in rs:
        body = "\n".join(r.content)
        by_focus[("甲主题" in body and body.count("甲主题") > body.count("乙主题"))] = body
    focused_on_jia = [r for r in rs if "苹果" in "".join(r.content)][0]
    assert focused_on_jia.fetcher_used == "http", "第一个是领头"
    other = [r for r in rs if r is not focused_on_jia][0]
    assert other.fetcher_used == "cache"
    jia = "\n".join(focused_on_jia.content)
    yi = "\n".join(other.content)
    assert jia.count("甲主题") > jia.count("乙主题"), jia[:200]
    assert yi.count("乙主题") > yi.count("甲主题"), yi[:200]
    assert by_focus, "两个方向都要看得见取舍"


# ─── 2 · 什么时候**不**合流 ──────────────────────────────────────

def test_cache_ttl_zero_is_never_merged(server, site):
    """cache_ttl=0 是"这次不读也不写缓存"：没有可共享的媒介，也就没有合流。"""
    rs = _burst(server, f"{site}/page", cache_ttl=0)
    assert HITS["page"] == 4, HITS["page"]
    assert {r.fetcher_used for r in rs} == {"http"}


def test_a_different_url_is_a_different_request(server, site):
    async def main():
        return await asyncio.gather(
            server.web_read(f"{site}/page"),
            server.web_read(f"{site}/other"),
        )
    rs = asyncio.run(main())
    assert HITS["page"] == 1 and HITS["other"] == 1, HITS
    assert [r.status for r in rs] == [200, 200]


def test_a_leader_that_times_out_does_not_hold_the_follower(server, site):
    """领头那一发没落地（超时，于是什么都没写进缓存），跟随者必须被放走去自己取。

    这是"等待有上限"那条纪律的判据：上限就是跟随者自己那份取回预算，等到了就自己
    去取 —— 也就是合并之前的行为。而且它拿到的预算**已经扣掉了等待的那一段**，
    所以整次调用仍限在自己的预算里，不是"等一段再跑一整份"。
    """
    async def main():
        return await asyncio.gather(
            # 领头：1.2s 的页面配 1s 预算 —— 注定超时，也就注定不写缓存。
            server.web_read(f"{site}/slow", timeout=1000),
            # 跟随者：预算够（4s），扣掉 ~1s 的等待还剩 3s，够取完这页。
            server.web_read(f"{site}/slow", timeout=4000),
        )
    rs = asyncio.run(main())
    assert HITS["slow"] == 2, (
        f"源站被打了 {HITS['slow']} 次：跟随者要么没等到上限，要么根本没去取")
    assert rs[0].status == 0, f"领头那一发本来就该超时，实际 {rs[0].status}"
    assert rs[1].status == 200, (
        f"跟随者被领头那一发拖死了：status={rs[1].status} error={rs[1].error!r}")
    assert "slow page" in "\n".join(rs[1].content)


# ─── 3 · 收尾：放人、清场、不跨循环 ──────────────────────────────

def test_a_leader_that_dies_does_not_take_the_followers_with_it(server, site,
                                                                monkeypatch):
    """领头那一发炸了，等它的人必须被放走，然后自己去取。"""
    real = server_mod.MasterFetchServer._auto_escalate
    calls = {"n": 0}

    async def die_once(self, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("the leader exploded")
        return await real(self, *a, **k)

    monkeypatch.setattr(server_mod.MasterFetchServer, "_auto_escalate", die_once)

    async def main():
        return await asyncio.gather(
            *[server.web_read(f"{site}/page") for _ in range(3)],
            return_exceptions=True)
    rs = asyncio.run(main())
    errors = [r for r in rs if isinstance(r, BaseException)]
    oks = [r for r in rs if not isinstance(r, BaseException)]
    assert len(errors) == 1, "只有领头那一发该炸"
    assert len(oks) == 2 and all(r.status == 200 for r in oks), oks
    assert HITS["page"] == 2, f"跟随者没能自己去取：{HITS['page']}"
    assert server_mod._merge_registry() == {}, "登记处没清干净"


def test_the_registry_is_empty_after_a_burst(server, site):
    _burst(server, f"{site}/page", n=3)
    assert server_mod._merge_registry() == {}


def test_the_registry_does_not_survive_its_event_loop(server, site):
    """`asyncio.Event` 绑在创建它的循环上。

    模块级只放一本登记处的话，上一轮 `asyncio.run()` 留下的 Event 会被下一轮
    await —— 而本仓库的套件在一个进程里跑几千次 `asyncio.run()`。这条用例连跑两轮，
    第二轮必须拿到一本**新的**。
    """
    _burst(server, f"{site}/page", n=2)
    first_loop_registry = server_mod._MERGE_INFLIGHT_LOOP
    _burst(server, f"{site}/page", n=2)
    assert server_mod._MERGE_INFLIGHT_LOOP is not first_loop_registry, (
        "登记处跟着旧的（已关闭的）事件循环留了下来")


def test_a_second_burst_still_reuses_the_cache(server, site):
    """第二轮不该再打源站：缓存里已经有了。这条守的是"合流没把缓存写坏"。"""
    _burst(server, f"{site}/page", n=4)
    assert HITS["page"] == 1
    rs = _burst(server, f"{site}/page", n=4)
    assert HITS["page"] == 1, HITS["page"]
    assert all(r.fetcher_used == "cache" for r in rs), [r.fetcher_used for r in rs]


# ─── 4 · 合流键（单元）──────────────────────────────────────────

class TestTheMergeKey:
    """合流键刻意就是缓存键：本仓库对"同一个请求"的既有答案。

    正确性不靠它（跟随者会用自己的参数重查缓存），所以这里钉的是"它没另发明一套
    判据"，而不是"它覆盖了每一个塑形参数"—— 后者是个证明不完的命题。
    """

    def test_it_is_the_cache_key(self):
        from dhole_mcp.cache import _cache_key
        got = server_mod._merge_key("https://x/a", "markdown", None, None, "")
        assert got == _cache_key("https://x/a", "markdown", None, None,
                                 "live", "fetch", "")

    def test_the_shape_bits_change_it(self):
        base = server_mod._merge_key("https://x/a", "markdown", None, None, "")
        assert server_mod._merge_key("https://x/b", "markdown", None, None, "") != base
        assert server_mod._merge_key("https://x/a", "text", None, None, "") != base
        assert server_mod._merge_key("https://x/a", "markdown", "#id", None, "") != base
        assert server_mod._merge_key("https://x/a", "markdown", None, "1-2", "") != base
        assert server_mod._merge_key("https://x/a", "markdown", None, None, "ck=1") != base

    def test_the_credentials_context_changes_it(self):
        """带凭据取回的那一份，不许被匿名请求合流到一起。"""
        from dhole_mcp.context import _cache_context
        anon = _cache_context({})
        with_auth = _cache_context({"auth": {"type": "bearer", "token": "t"}})
        assert anon != with_auth
        assert server_mod._merge_key("https://x/a", "markdown", None, None, anon) != \
            server_mod._merge_key("https://x/a", "markdown", None, None, with_auth)


# ============================================================================
# 来源：test_a_stale_probe_refuses_with_the_truth_not_argument_blame.py
# ============================================================================
# R3刀1（P1-1 收尾）：探测过期时，那道守卫的拒绝被贴上「参数错了」的标签，还被剪掉了尾巴。

# 现场那一条的骨架：公网名 → 保留段地址（`_resolves_to_internal` 的原话形状）
GUARD_MSG = ("URL hostname docs.example.com resolves to internal/private IP "
             "(10.5.5.5 in 10.0.0.0/8). This guard is fail-closed: dhole auto-detects "
             "fake-ip TUN resolvers (public names answering with reserved addresses) "
             "and skips the layer only for them; this hostname still resolved into a "
             "private range.")


def _flip_the_resolver(monkeypatch, url: str, ip: str = "10.5.5.5",
                      public: str = "93.184.216.34") -> None:
    """让解析器把一个名字领进私网段，其余名字照常回公网地址（stub 翻转探测现场）。

    地址取 10.0.0.0/8 —— 守卫的内网表里认它，198.18.0.0/15 只在**探测**那一步用作
    fake-ip 判据，复验层并不把它列为内网。
    """
    import socket

    from dhole_mcp import security as sec

    sec._DNS_CHECK_CACHE.clear()
    hostname = url.split("//", 1)[1].split("/")[0]

    def fake_getaddrinfo(host_, port=None, *a, **kw):
        addr = ip if host_ == hostname else public
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, port))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    # 进程记着的那句话：public —— 于是 DNS 复查层常开，而它现在会咬。
    monkeypatch.setattr(sec, "probe_fake_ip", lambda: ("public", "", ""))


class TestTheDiagnosis:

    @pytest.mark.asyncio
    async def test_the_refusal_is_not_called_a_wrong_argument(self, monkeypatch):
        url = "https://docs.example.com/page"
        _flip_the_resolver(monkeypatch, url)
        out = await MasterFetchServer().web_read(url=url, cache_ttl=0)
        assert "resolves to internal/private IP" in out.error, out.error
        assert "Correct the argument" not in out.next_action, out.next_action

    @pytest.mark.asyncio
    async def test_the_diagnosis_names_the_real_recovery(self, monkeypatch):
        url = "https://docs.example.com/page"
        _flip_the_resolver(monkeypatch, url)
        out = await MasterFetchServer().web_read(url=url, cache_ttl=0)
        diag = out.diagnosis
        assert diag is not None, "error 有句子，机器可读的孪生也得有"
        assert diag["code"] == "resolver_guard", diag
        assert diag["retryable"] is False, "重发同一发不会改变答案：进程记着旧判定"
        assert "restart" in diag["recover_with"] and "doctor" in diag["recover_with"]

    @pytest.mark.asyncio
    async def test_next_action_says_the_same_thing_as_the_diagnosis(self, monkeypatch):
        url = "https://docs.example.com/page"
        _flip_the_resolver(monkeypatch, url)
        out = await MasterFetchServer().web_read(url=url, cache_ttl=0)
        assert "restart" in out.next_action and "doctor" in out.next_action

    @pytest.mark.asyncio
    async def test_other_ssrf_refusals_keep_their_own_wording(self, monkeypatch):
        """云元数据那条永久边界不许被这次改动改成"重启宿主"——它重启也不变。"""
        out = await MasterFetchServer().web_read(
            url="http://169.254.169.254/latest/meta-data/")
        assert "design boundary" in out.next_action, out.next_action
        assert "restart" not in out.next_action


class TestTheTailIsNotClipped:
    """`[:200]` 的帽子是给 Playwright 的 call-log 准备的，不该吃掉 dhole 自己写的拒绝。"""

    def test_a_security_error_keeps_its_whole_sentence(self):
        out = _tier_error_text(SecurityError(GUARD_MSG))
        assert out == GUARD_MSG, out
        assert out.endswith("private range.")

    def test_other_exception_text_is_still_clipped(self):
        """浏览器层的 call-log 那种噪声仍旧剪：豁免只给写得完整的拒绝句。"""
        assert len(_tier_error_text(RuntimeError("x" * 900))) == 200

    def test_the_cap_is_not_the_problem_for_an_ordinary_error(self):
        assert len(_tier_error_text(ValueError("y" * 900))) == 200

    def test_the_guard_summary_ends_where_a_word_ends(self):
        """重启后实测的另一处：summary 标签 `msg[:80]` 停在 "…can compile (S" 这种半截词上。

        整句仍在 error 里，标签可以短，但不能短得像坏掉的字符串。
        """
        out = server_mod._resolver_guard_result("https://docs.example.com/", GUARD_MSG)
        prefix = "refused by the SSRF resolver guard · "
        assert out.summary.startswith(prefix)
        tail = out.summary[len(prefix):]
        assert GUARD_MSG[len(tail)] == " ", f"summary 停在词中间: {tail!r}"
        # 【原判据搬家 · 评审 R02-113】这一颗从前钉的是 `out.error == GUARD_MSG` —— 那句裸文本
        # 正是"不穿信封"的那一副脸：同一族的私网拒绝、URL 形状拒绝、存储门拒绝都带
        # `invalid_request:` 前缀，只有守卫这一族五扇门不带（r172 的六张脸）。产品补齐前缀之后，
        # 这条断言若原样留着就等于把缺陷钉成契约。**没有放宽**：整句仍然逐字必须在，
        # 只是按信封契约带着前缀在 —— 剪词那一害（上面那颗）与"不许把责任推给参数"那一族断言
        # 一字未动，16 格同文件其余全绿。
        assert out.error == f"invalid_request: {GUARD_MSG}", out.error
        assert GUARD_MSG in out.error, "整句被换了，不只是加了前缀"


class TestDiscoverAndCrawlSayTheWholeSentence:
    """评审现场的第二处：discover/crawl 把 300 字符的解释剪在 200，尾巴正是出路。"""

    @pytest.mark.asyncio
    async def test_the_guard_sentence_survives_the_crawl_envelope(self, monkeypatch):
        url = "https://docs.example.com/"
        _flip_the_resolver(monkeypatch, url)
        out = await MasterFetchServer().web_discover(url=url)
        assert "resolves to internal/private IP" in out.error, out.error
        assert out.error.rstrip().endswith("whatever this machine measured."), out.error
        assert "restart" in out.next_action and "doctor" in out.next_action
        assert len(out.error) > 200, "帽子还在：豁免必须是 SecurityError 才有"


PAGE = "https://docs.example.com/page"


@pytest.fixture()
def stored_copy(monkeypatch):
    """本机缓存里放一份这份页面的正文，并让读侧指纹与写侧对齐。

    `_cache_context` 打平为空串：`set_cached` 的默认 ctx 也是空串，于是这个用例读到
    的就是那份正文。指纹机制本身由 test_cache*.py 那一组守着，不在这里重复。
    """
    monkeypatch.setattr(server_mod, "_cache_context", lambda *a, **k: "")
    return PAGE


class TestTheCacheOutlivesTheStaleVerdict:
    """守卫拦的是**要发出的那一发**；命中一个包都不发，所以它拦不到（R3刀1）。"""

    @pytest.mark.asyncio
    async def test_a_stored_page_is_still_readable(self, monkeypatch, stored_copy):
        await set_cached(stored_copy, "markdown", ["the page as stored"], status=200)
        _flip_the_resolver(monkeypatch, stored_copy)

        out = await MasterFetchServer().web_read(url=stored_copy)

        assert not out.error, out.error
        assert out.content == ["the page as stored"]
        assert out.cached is True and out.fetcher_used == "cache"

    @pytest.mark.asyncio
    async def test_the_hit_says_the_next_request_is_still_refused(self, monkeypatch,
                                                                  stored_copy):
        """答案是真的，门也真的还堵着 —— 只报喜会让下一发撞上去时毫无准备。"""
        await set_cached(stored_copy, "markdown", ["body"], status=200)
        _flip_the_resolver(monkeypatch, stored_copy)

        out = await MasterFetchServer().web_read(url=stored_copy)

        assert "any NEW fetch of this URL is still refused" in out.summary, out.summary

    @pytest.mark.asyncio
    async def test_cache_ttl_0_still_asks_the_guard(self, monkeypatch, stored_copy):
        """cache_ttl=0 要的就是那一发新包 —— 守卫拦的正是它，这份抄本不能顶上去。"""
        await set_cached(stored_copy, "markdown", ["body"], status=200)
        _flip_the_resolver(monkeypatch, stored_copy)

        out = await MasterFetchServer().web_read(url=stored_copy, cache_ttl=0)

        assert "resolves to internal/private IP" in out.error, out.error
        assert out.diagnosis["code"] == "resolver_guard"

    @pytest.mark.asyncio
    async def test_a_schema_call_is_not_answered_with_the_plain_copy(self, monkeypatch,
                                                                     stored_copy):
        """schema 要在原始 HTML 上重跑选择器：匿名抓来的 markdown 正文没被问过。"""
        await set_cached(stored_copy, "markdown", ["body"], status=200)
        _flip_the_resolver(monkeypatch, stored_copy)

        out = await MasterFetchServer().web_read(
            url=stored_copy, schema={"properties": {"title": {"selector": "h1"}}})

        assert "resolves to internal/private IP" in out.error, out.error

    @pytest.mark.asyncio
    async def test_a_check_call_is_not_answered_with_the_plain_copy(self, monkeypatch,
                                                                    stored_copy):
        """`check` 问的是「站方说变了吗」，答案只能是站方的 —— 抄本答不了。"""
        await set_cached(stored_copy, "markdown", ["body"], status=200)
        _flip_the_resolver(monkeypatch, stored_copy)

        out = await MasterFetchServer().web_read(url=stored_copy,
                                                 extraction_type="check")

        assert "resolves to internal/private IP" in out.error, out.error

    @pytest.mark.asyncio
    async def test_a_head_asks_the_server_and_the_copy_cannot_answer(self, monkeypatch,
                                                                    stored_copy):
        await set_cached(stored_copy, "markdown", ["body"], status=200)
        _flip_the_resolver(monkeypatch, stored_copy)

        out = await MasterFetchServer().web_read(url=stored_copy, method="HEAD")

        assert "resolves to internal/private IP" in out.error, out.error

    @pytest.mark.asyncio
    async def test_the_site_s_current_robots_rules_still_stop_the_copy(
            self, monkeypatch, stored_copy):
        """合规门不倒：站方规则（缓存里读得到，不必发包）说不许，就不交抄本。"""
        await set_cached(stored_copy, "markdown", ["body"], status=200)
        _flip_the_resolver(monkeypatch, stored_copy)
        # 这台机器上 DHOLE_IGNORE_ROBOTS=1 是常开的（评审现场如此），要读的这条
        # 分支得先让它"在管"，否则测的是绕过分支。
        monkeypatch.setattr(server_mod, "robots_env_disabled", lambda: False)

        class _Disallowed:
            @staticmethod
            def cached(url, **kw):
                return False

        monkeypatch.setattr(server_mod, "get_robots_cache", lambda: _Disallowed)

        out = await MasterFetchServer().web_read(url=stored_copy)

        assert "resolves to internal/private IP" in out.error, out.error

    @pytest.mark.asyncio
    async def test_the_compliance_stance_of_a_guard_hit_is_said_honestly(
            self, monkeypatch, stored_copy):
        """这次没查 robots.txt（查它要发包），姿态字段不能替它说「complied」。"""
        await set_cached(stored_copy, "markdown", ["body"], status=200)
        _flip_the_resolver(monkeypatch, stored_copy)
        monkeypatch.setattr(server_mod, "robots_env_disabled", lambda: False)

        class _NoRecord:
            @staticmethod
            def cached(url, **kw):
                return None

        monkeypatch.setattr(server_mod, "get_robots_cache", lambda: _NoRecord)

        out = await MasterFetchServer().web_read(url=stored_copy)

        assert out.content == ["body"]
        assert out.metadata.get("robots") == (
            "not checked (no cached robots.txt, and this call sends nothing)"
        ), out.metadata.get("robots")
