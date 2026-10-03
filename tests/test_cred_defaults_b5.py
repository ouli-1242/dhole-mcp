"""B5-13b：`cred_defaults` —— 设一次的默认身份（§3-17 / §4 第 17 颗）。

要解决的问题很具体：一个 agent  reach 同一个 API 四十次，就要传四十次同一个
proxy / 同一份 auth / 同一个档位，而"改 env 重启宿主"不是答案。所以这些设置有了
第三个家：落盘一次，之后每一次调用都读回来。

两条设计约束不是风格问题：

* **凭据必须带 host。** `auth` 与 `cookies` 不给 host 就拒 —— 一条全局的
  Basic-Auth 头会跟着下一次调用去敲**别人的**门，那正是 web_read 里"跨源重定向
  丢凭据"那条纪律要避免的事，只是被做成了永久状态。五条非凭据的键（proxy /
  profile / cdp / allow_private / anti_detect）本来就是全局语义，一处出口一条
  代理，不给 host。
* **值不许出境。** 工具回执与每次响应的回显只有脱敏视图：cookie 只有名字，auth
  只有类型 + 用户名，proxy 只有主机且凭据在不在以布尔说。存的那一份永远只在
  `~/.dhole/identity.db`（0600）。回显存在的意义是"让调用方不用读值就能撤"。

优先级写死成一条：**调用方这次传的 > 落盘的默认 > 环境/工具自身默认**。环境代理
例外，它是机器配置（既有教义：机器配置不许替一次抓取下结论），所以落盘的显式
默认排在它前面，且和显式参数一样过 TCP 预检。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from dhole_mcp import identity
from dhole_mcp import server as server_mod
from dhole_mcp.server import MasterFetchServer as Server

API = "https://api.example.com/v1/items"
OTHER = "https://elsewhere.test/x"
SECRET = "sup3r-s3cret-NEVER-LEAKS"
TOKEN = "bearer-tok-NEVER-LEAKS"


def _call(tool: str, args: dict):
    _content, structured = asyncio.run(Server()._dispatch(tool, args))
    return structured


def _set(**items):
    return _call("cred_defaults", {"set": dict(items)})


# ─── 存储：读侧只有脱敏的一半 ───────────────────────────────────────

class TestTheStore:

    def test_a_value_round_trips_and_counts_when_forgotten(self):
        identity.set_value("proxy", "http://127.0.0.1:7890")
        assert identity.get("proxy") == "http://127.0.0.1:7890"
        assert identity.clear() == 1
        assert identity.get("proxy") is None

    def test_a_credential_never_applies_without_its_host_matching(self):
        """scope 是精确主机或子域，绝不是子串。

        `evil-example.com`.endswith("example.com") 为真 —— 所以这条匹配如果写成
        子串，一次为 example.com 存的凭据就会发给那个连字符开头的邻居。
        """
        identity.set_value("auth", {"type": "basic", "username": "u",
                                    "password": SECRET}, scope="example.com")
        assert identity.get("auth", "api.example.com")
        assert identity.get("auth", "example.com")
        assert identity.get("auth", "evil-example.com") is None
        assert identity.get("auth", "other.test") is None

    def test_the_longest_matching_scope_wins(self):
        identity.set_value("auth", {"type": "basic", "username": "wide"},
                           scope="example.com")
        identity.set_value("auth", {"type": "basic", "username": "narrow"},
                           scope="api.example.com")
        assert identity.get("auth", "api.example.com")["username"] == "narrow"
        assert identity.get("auth", "www.example.com")["username"] == "wide"

    def test_a_credential_stored_without_a_scope_applies_nowhere(self):
        """即使有人绕过工具直接写了一行无 scope 的凭据，它也不该在任何主机上生效。"""
        identity.set_value("cookies", {"sid": SECRET})
        assert identity.get("cookies", "example.com") is None
        assert identity.in_force("example.com") == {}

    def test_a_row_ages_as_a_countdown_not_an_epoch_float(self):
        """`1790995191.36` 不是"什么时候存的"—— 那是让人自己减的机器数。"""
        identity.set_value("proxy", "http://127.0.0.1:7890")
        row = identity.all_rows()[0]
        assert 0 <= row["stored_s_ago"] < 60
        assert "updated_at" not in row

    def test_the_read_side_redacts_every_credential_shape(self):
        identity.set_value("auth", {"type": "basic", "username": "u",
                                    "password": SECRET}, scope="example.com")
        identity.set_value("cookies", [{"name": "sid", "value": SECRET},
                                       {"name": "csrf", "value": "t"}],
                           scope="example.com")
        identity.set_value("proxy", f"http://user:{SECRET}@proxy.test:8080")
        blob = json.dumps([identity.in_force("example.com"),
                           identity.all_rows()], ensure_ascii=False)
        assert SECRET not in blob, blob
        view = identity.in_force("example.com")
        assert view["auth"] == {"type": "basic", "username": "u"}
        assert view["cookies"] == {"names": ["csrf", "sid"], "count": 2}
        assert view["proxy"]["server"] == "http://proxy.test:8080"
        assert view["proxy"]["credentials"] is True

    def test_a_bearer_token_has_no_redacted_form_so_none_is_shown(self):
        identity.set_value("auth", {"type": "bearer", "token": TOKEN},
                           scope="example.com")
        assert identity.in_force("example.com")["auth"] == {"type": "bearer"}
        assert TOKEN not in json.dumps(identity.all_rows())


# ─── 工具面：三颗参数 ─────────────────────────────────────────────

class TestTheTool:

    def test_it_is_a_real_tool_with_three_arguments(self):
        defs = {td["name"]: td for td in Server._TOOL_DEFS}
        assert "cred_defaults" in defs
        assert server_mod._TOP_LEVEL_ARGS["cred_defaults"] == frozenset(
            {"set", "unset", "show"})
        assert defs["cred_defaults"]["annotations"]["destructiveHint"] is True
        assert "cred_defaults" in server_mod.DHOLE_INSTRUCTIONS

    def test_a_bare_call_shows_the_store_and_changes_nothing(self):
        identity.set_value("anti_detect", "aggressive")
        out = _call("cred_defaults", {})
        assert out["rows"][0]["key"] == "anti_detect"
        assert identity.get("anti_detect") == "aggressive"

    def test_a_credential_without_a_host_is_refused_and_nothing_is_stored(self):
        """这条拒绝是整个工具最要紧的一句话：没有 host 的凭据会跟着下一次调用出门。"""
        out = _set(auth={"type": "basic", "username": "u", "password": SECRET})
        assert out["error"] and "host" in out["error"]
        assert identity.all_rows() == []

    def test_setting_a_credential_with_a_host_echoes_the_redacted_half(self):
        out = _set(host="api.example.com",
                   auth={"type": "basic", "username": "u", "password": SECRET})
        assert SECRET not in json.dumps(out, ensure_ascii=False)
        assert out["changed"] == ["auth"]
        assert out["rows"][0]["scope"] == "api.example.com"
        assert identity.get("auth", "api.example.com")["password"] == SECRET, \
            "存的那一份必须是完整的 —— 脱敏只发生在出口"

    @pytest.mark.parametrize("key,value", [
        ("proxy", "http://127.0.0.1:7890"), ("profile", "work"),
        ("cdp", "http://127.0.0.1:9222"), ("allow_private", True),
        ("anti_detect", "aggressive"),
    ])
    def test_a_global_key_needs_no_host(self, key, value):
        assert key in identity.GLOBAL_KEYS, "这颗已经不是全局的了，换掉探针值"
        out = _set(**{key: value})
        assert not out.get("error"), out
        assert out["changed"] == [key]

    @pytest.mark.parametrize("key,value", [
        ("proxy", True), ("cdp", 42), ("profile", ""),
    ])
    def test_a_value_of_the_wrong_shape_is_a_clean_refusal_not_a_traceback(
            self, key, value):
        """形状错了要在回执里说，而不是抛一个 Python 属性错误给调用方看。"""
        out = _set(**{key: value})
        assert out["error"] and key in out["error"]
        assert identity.all_rows() == []

    def test_an_unknown_key_names_the_seven(self):
        out = _set(useragent=SECRET)
        assert "useragent" in out["error"]
        for key in identity.KEYS:
            assert key in out["error"]

    def test_a_bad_rung_is_refused_at_set_time_not_on_the_next_fetch(self):
        """档位在这里就验，否则一次打错要等到下一次抓取才炸 —— 而且炸在别人的调用上。"""
        out = _set(anti_detect="paranoid")
        assert out["error"]
        for level in server_mod._ANTI_DETECT:
            assert level in out["error"]

    def test_unset_forgots_every_scope_of_a_key_and_says_how_many(self):
        identity.set_value("auth", {"type": "basic", "username": "a"}, "one.test")
        identity.set_value("auth", {"type": "basic", "username": "b"}, "two.test")
        out = _call("cred_defaults", {"unset": ["auth"]})
        assert out["forgotten"] == ["auth"]
        assert identity.all_rows() == []

    def test_naming_a_key_and_forging_it_in_the_same_call_is_refused(self):
        out = _call("cred_defaults", {"set": {"proxy": "http://p.test:1"},
                                      "unset": ["proxy"]})
        assert out["error"]
        assert identity.all_rows() == [], "拒绝就该什么都没做"

    def test_setting_them_all_requires_no_repetition(self):
        """一次调用既能设 proxy 又能给 auth 配 host：一条"换身份"的意图不该拆成两次。"""
        out = _set(host="api.example.com", proxy="http://p.test:1",
                   auth={"type": "bearer", "token": TOKEN})
        assert not out.get("error"), out
        assert sorted(out["changed"]) == ["auth", "proxy"]

    def test_an_audit_line_names_keys_and_hosts_but_never_values(self, monkeypatch,
                                                                  tmp_path):
        monkeypatch.setenv("DHOLE_USAGE_LOG", str(tmp_path / "usage.jsonl"))
        _set(host="api.example.com",
             auth={"type": "basic", "username": "u", "password": SECRET})
        line = (tmp_path / "usage.jsonl").read_text(encoding="utf-8")
        assert SECRET not in line
        assert "identity_set" in line and "auth" in line and "api.example.com" in line


# ─── 接线：每一次调用读回来 ────────────────────────────────────────

def _tier_kwargs(monkeypatch, **call):
    """走一次真实分发，抓抓取层实际收到的参数。

    桩打在 `_force_fetch` 而不是 `stealthy_fetch` 上：显式代理在到层之前要过
    TCP 预检（那是既有的、也是正确的行为），一个指向 127.0.0.1:7890 的落盘默认
    会先被判死、根本走不到层里来 —— 那时读到的空 kwargs 说的是代理探针，不是
    身份回填。`_force_fetch` 是所有档位参数都到齐的最后一站。
    """
    seen: dict = {}

    async def fake(self, url, force_fetcher, **kwargs):
        seen.update(kwargs)
        seen["url"] = url
        from dhole_mcp.server import ResponseModel
        return ResponseModel(url=url, status=200, content=["body"],
                             fetcher_used=force_fetcher)

    monkeypatch.setattr(Server, "_force_fetch", fake)
    args = {"url": API, "force_fetcher": "stealthy", "cache_ttl": 0}
    args.update(call)
    asyncio.run(Server()._dispatch("web_read", args))
    return seen


def _fake_open(monkeypatch, opened: list) -> None:
    """`open_session` 的桩：记下参数，并把一台"活着"的会话登记进去。

    登记是必须的 —— `_ensure_auto_session` 判断"这台还能不能用"看的是 `_sessions`
    里那个对象，桩只返回 id 的话它根本不算存在，轮换那条测试就会什么都看不见。
    """
    from dhole_mcp.server import _SessionEntry

    async def fake_open(self, **kw):
        opened.append(kw)
        sid = "sid-" + str(kw.get("profile_name"))

        class _Live:
            _is_alive = True

            async def close(self):
                pass

        self._sessions[sid] = _SessionEntry(session=_Live(), session_type="stealthy")
        result = type("R", (), {"session_id": sid})()
        return result

    monkeypatch.setattr(Server, "open_session", fake_open)
    monkeypatch.setattr(server_mod, "_browser_deps_available", lambda: True)


class TestTheWireIn:

    def test_a_stored_proxy_reaches_the_tier_without_being_passed(self, monkeypatch):
        _set(proxy="http://127.0.0.1:7890")
        assert _tier_kwargs(monkeypatch)["proxy"] == "http://127.0.0.1:7890"

    def test_what_the_caller_passed_this_time_beats_the_store(self, monkeypatch):
        _set(proxy="http://stored.test:1")
        seen = _tier_kwargs(monkeypatch, proxy="http://explicit.test:2")
        assert seen["proxy"] == "http://explicit.test:2"

    def test_a_stored_credential_only_rides_to_the_host_it_was_given_for(
            self, monkeypatch):
        """`auth` 在 web_read 入口就翻成头，所以断言打在层的 `extra_headers` 上。"""
        _set(host="example.com", auth={"type": "basic", "username": "u",
                                       "password": SECRET})
        sent = _tier_kwargs(monkeypatch)
        elsewhere = _tier_kwargs(monkeypatch, url=OTHER)
        assert "authorization" in {k.lower() for k in
                                   (sent.get("extra_headers") or {})}
        assert "authorization" not in {k.lower() for k in
                                       (elsewhere.get("extra_headers") or {})}, \
            "凭据跟着一次不相关的抓取出了门"

    def test_a_stored_rung_is_the_ladder_default_and_an_explicit_one_still_wins(
            self, monkeypatch):
        _set(anti_detect="aggressive")
        assert _tier_kwargs(monkeypatch)["disable_resources"] is False
        assert _tier_kwargs(monkeypatch, anti_detect="auto")["disable_resources"] is True

    def test_a_stored_allow_private_opens_the_loopback_gate_for_later_calls(
            self, monkeypatch):
        """私有主机的围栏是"每次都要重申"最烦的一处，也是这条默认存在的理由。"""
        _set(allow_private=True)
        out = _call("web_read", {"url": "http://127.0.0.1:9/health",
                                 "force_fetcher": "http", "cache_ttl": 0})
        assert "allow_private" not in str(out.get("error", ""))

    def test_the_response_names_what_was_applied_and_stays_quiet_when_nothing_was(
            self, monkeypatch):
        out = _call("web_read", {"url": API, "force_fetcher": "http", "cache_ttl": 0})
        assert not out.get("identity_applied")
        _set(proxy="http://127.0.0.1:7890", host="example.com",
             cookies={"sid": SECRET})
        out = _call("web_read", {"url": API, "force_fetcher": "http", "cache_ttl": 0})
        assert out["identity_applied"] == "cookies, proxy"
        assert SECRET not in out["identity_applied"]

    def test_every_bulk_row_is_judged_against_its_own_host(self, monkeypatch):
        """`urls=[...]` 里两个主机，凭据只该发给认得的那一个。"""
        seen: dict = {}

        async def fake(self, url, force_fetcher, **kwargs):
            seen[url] = kwargs
            from dhole_mcp.server import ResponseModel, _with_agent_hints
            # 真的 `_force_fetch` 回的是已过最终封装层的结果，回显就在那里落；
            # 桩如果直接回裸模型，测的就成了自己的桩。
            return _with_agent_hints(ResponseModel(
                url=url, status=200, content=["body"], fetcher_used=force_fetcher))

        monkeypatch.setattr(Server, "_force_fetch", fake)
        _set(host="example.com", auth={"type": "basic", "username": "u",
                                       "password": SECRET})
        out = asyncio.run(Server().web_read(API, urls=[API, OTHER],
                                            force_fetcher="stealthy", cache_ttl=0))
        assert len(out.results) == 2
        assert "authorization" in {k.lower() for k in
                                   (seen[API].get("extra_headers") or {})}
        assert "authorization" not in {k.lower() for k in
                                       (seen[OTHER].get("extra_headers") or {})}
        assert SECRET not in json.dumps([r.identity_applied
                                         for r in out.results], ensure_ascii=False)
        assert [r.identity_applied for r in out.results] == ["auth", ""]

    def test_the_stored_profile_is_what_the_auto_browser_gets_built_with(
            self, monkeypatch):
        """`DHOLE_BROWSER_PROFILE` 注销之后，命名 profile 唯一的家就是这里。"""
        _set(profile="work")
        opened: list = []
        _fake_open(monkeypatch, opened)
        asyncio.run(Server()._ensure_auto_session())
        assert opened[0]["profile_name"] == "work", \
            "落盘的 profile 没被读回来，持久上下文就永远建不起来"

    def test_a_profile_that_changed_rotates_the_warm_browser(self, monkeypatch):
        """预热的浏览器带着旧 profile 的登录态，新的默认生效不了还被旧的那台顶着。"""
        server = Server()
        opened: list = []
        closed: list = []

        async def fake_close(self, sid):
            closed.append(sid)
            self._sessions.pop(sid, None)
            return 0, False, False

        _fake_open(monkeypatch, opened)
        monkeypatch.setattr(Server, "_close_one_session", fake_close)
        asyncio.run(server._ensure_auto_session(profile_name="old"))
        assert server._auto_stealthy_profile == "old"
        asyncio.run(server._ensure_auto_session(profile_name="new"))
        assert closed == ["sid-old"], "换了 profile 还复用那台旧上下文的浏览器"
        assert server._auto_stealthy_profile == "new"
        assert opened[-1]["profile_name"] == "new"

    def test_the_same_profile_reuses_the_warm_browser(self, monkeypatch):
        """轮换只在真的换了的时候付钱 —— 否则每次抓取都冷启动一遍。"""
        server = Server()
        opened: list = []
        _fake_open(monkeypatch, opened)
        asyncio.run(server._ensure_auto_session(profile_name="work"))
        asyncio.run(server._ensure_auto_session(profile_name="work"))
        assert len(opened) == 1


# ─── sys_state 里看得见、撤得掉 ───────────────────────────────────

class TestVisibleAndRevocable:

    def test_the_store_is_a_state_class(self):
        assert "cred_defaults" in Server._STATE_TOKENS

    def test_show_lists_the_redacted_rows(self):
        _set(host="example.com", auth={"type": "basic", "username": "u",
                                       "password": SECRET})
        out = _call("sys_state", {"show": ["cred_defaults"]})
        assert SECRET not in json.dumps(out, ensure_ascii=False)
        assert out["cred_defaults"][0]["scope"] == "example.com"

    def test_clearing_it_needs_no_confirm_because_the_next_call_notices(self):
        """§3-32 只拦"抹完不会立刻察觉"的那一类：默认身份没了，下一次请求直接 401。"""
        _set(proxy="http://127.0.0.1:7890")
        out = _call("sys_state", {"clear": ["cred_defaults"]})
        assert out.get("pending_approval") is not True
        assert identity.all_rows() == []
        assert "cred_defaults" in out["cleared"]

    def test_a_bare_sys_state_says_how_many_defaults_are_in_force(self):
        _set(proxy="http://127.0.0.1:7890", anti_detect="aggressive")
        out = _call("sys_state", {})
        assert out["identity_count"] == 2
