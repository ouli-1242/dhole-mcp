"""`sys_state` 上 wire：本地状态看得见，点名才抹得掉。

这颗工具是 17.0 §4 把 cache_clear 与 close_session 合上来的（B5-12）。合并前两个
入口各自只看得到自己那一半，而 cache_clear(all=true) 会顺手抹掉 cookie jar ——
那句副作用写在另一颗工具的描述里，拿着「清缓存」意图的人正好读不到它。合成一颗之
后 token 词表负责说清「抹哪一类」，§3-32 的闸（抹完不会立刻察觉的那一类才要
confirm）才有地方落。

本文件钉的是行为：名册的读法、每一类的抹法与不抹、以及 cookie 值一律不出境（G7
的既有边界：工具输出会落进 transcript）。注册面与参数表本身由
test_tool_split_b5.py 钉，不在这里重写一遍。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from dhole_mcp import sessions
from dhole_mcp.server import MasterFetchServer, SessionCensusRow, _SessionEntry

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
