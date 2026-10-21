"""test_server_2.py —— 由 2 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import asyncio
import json
import pytest
from dhole_mcp import identity
from dhole_mcp import server as server_mod
from dhole_mcp.server import MasterFetchServer as Server
import ast
import re
from pathlib import Path
from dhole_mcp import env_registry


# ============================================================================
# 来源：test_cred_defaults_b5.py
# ============================================================================
# B5-13b：`cred_defaults` —— 设一次的默认身份（§3-17 / §4 第 17 颗）。

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

    def test_a_missing_state_directory_is_created_by_the_first_write(
            self, monkeypatch, tmp_path):
        """一个从未存在过的 DHOLE_HOME 里的第一次 cred_defaults 就是这条路径。

        e2e 抓到过真的 bug：建目录那一步收到的是 str，而 str 没有 .mkdir —— 那句
        `except Exception: pass` 把 AttributeError 吞了，目录没建，sqlite 于是在
        下一行报 "unable to open database file"。存储自己的错，长得像环境的错。

        17.1 起默认是内存库，"第一次写"发生在落盘模式（DHOLE_PERSIST_CREDENTIALS=1
        且平台有加密器）——这条路径契约归到落盘分支；默认模式则反过来：什么都不建。
        """
        identity.close()
        fresh = tmp_path / "fresh-home" / "identity.db"
        monkeypatch.setattr(identity, "_db_path", lambda: str(fresh))
        monkeypatch.setattr(identity, "_PERSIST", True)
        identity.set_value("proxy", "http://127.0.0.1:7890")
        assert fresh.exists(), "第一次写没有把状态目录建出来"
        assert identity.get("proxy") == "http://127.0.0.1:7890"
        identity.close()

    def test_the_default_memory_store_touches_no_disk(self, monkeypatch, tmp_path):
        """默认（B）：cookie 与身份库是凭据，最安全的文件是不存在的文件。

        存了值之后 identity.db 不存在、目录不被建出；值在本进程内照常可用；
        重启（close 即新进程）即空——这是默认模式的承诺，不是缺陷。
        """
        identity.close()
        fresh = tmp_path / "fresh-home" / "identity.db"
        monkeypatch.setattr(identity, "_db_path", lambda: str(fresh))
        identity.set_value("proxy", "http://127.0.0.1:7890")
        assert not fresh.exists(), "内存模式不应该写任何文件"
        assert not fresh.parent.exists(), "内存模式不应该建状态目录"
        assert identity.get("proxy") == "http://127.0.0.1:7890"
        identity.close()
        assert identity.get("proxy") is None, "重启后默认一无所有"

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

            # 17.0 B5-14a-2：这台现在能不能复用，看的是它**带着**哪一档起来的
            # （画布噪声与 WebRTC 屏蔽是启动级的）。桩不带上这两颗，它就不是一个
            # 忠实的 open_session 桩 —— 复用与轮换的判据会看不见它。
            _hide_canvas = kw.get("hide_canvas", False)
            _block_webrtc = kw.get("block_webrtc", False)

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

    def test_the_moved_identity_keys_are_refused_as_unknown_arguments(self):
        """搬进存储的那几颗在参数面上必须变成陌生键 —— 不是被吞掉。

        吞掉一次 `proxy=`，调用方就以为那台代理正在用；§3-20 的静默忽略只管已注销
        的 DHOLE_* 环境变量，参数面从来是「未知键必须抛」。
        """
        for key in ("auth", "cookies", "proxy", "extra_headers", "allow_private"):
            with pytest.raises(ValueError, match="Unsupported argument") as e:
                _call("web_read", {"url": API, key: "x"})
            assert key in str(e.value), str(e.value)

    def test_what_the_caller_passed_this_time_beats_the_store(self, monkeypatch):
        """两条还能逐次传的键里，档位是这条优先级链的证人。"""
        _set(anti_detect="aggressive")
        assert _tier_kwargs(monkeypatch, anti_detect="basic")["hide_canvas"] is True
        assert _tier_kwargs(monkeypatch, anti_detect="basic")["disable_resources"] is True

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

    def test_a_stored_allow_private_opens_the_loopback_gate_for_later_calls(self):
        """私有主机的围栏是"每次都要重申"最烦的一处，也是这条默认存在的理由。

        断言打在闸门的两端：没存的时候同一个 URL 得到的是「internal/private IP」
        这句拒绝，存了之后拒绝换成一次真正的连接失败 —— 这才说明闸门真的开了，
        而不是错误文案恰好不含某个词。
        """
        refused = _call("web_read", {"url": "http://127.0.0.1:9/health",
                                     "force_fetcher": "http", "cache_ttl": 0})
        assert "internal" in str(refused.get("error", "")).lower(), refused
        assert not refused.get("identity_applied")

        _set(allow_private=True)
        out = _call("web_read", {"url": "http://127.0.0.1:9/health",
                                 "force_fetcher": "http", "cache_ttl": 0})
        assert "internal" not in str(out.get("error", "")).lower(), out
        assert "allow_private" in str(out.get("identity_applied")), out

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
        # 只填 urls=：`url=` 与 `urls=` 同时给现在是具名拒绝（A1），而这条的主语是
        # 「每行按自己的主机判凭据」。拒绝行也带 url 字段，读起来像两行都抓过了。
        out = asyncio.run(Server().web_read("", urls=[API, OTHER],
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


# ============================================================================
# 来源：test_env_registry.py
# ============================================================================
# W1 · anti-drift tests for the env registry.

SRC_ROOT = Path(env_registry.__file__).parent
DOC_PATH = (SRC_ROOT / "skills" / "dhole-web" / "references" / "configuration.md")

ENV_LITERAL = re.compile(r"^DHOLE_[A-Z0-9_]+$")


# ─── helpers ────────────────────────────────────────────────────────────
def _py_files() -> list[Path]:
    return sorted(SRC_ROOT.rglob("*.py"))


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _string_constants(tree: ast.AST) -> list[tuple[str, int]]:
    """(value, lineno) for every ``str`` constant in the tree.

    Only ``ast.Constant`` with ``str`` value counts - ``ast.Name`` (e.g. the
    ``DHOLE_INSTRUCTIONS`` module constant) and dict identifiers (e.g.
    ``_DHOLE_TO_BACKEND``) are excluded structurally, which is the whole point
    of AST-scanning instead of text grep.
    """
    out: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append((node.value, getattr(node, "lineno", -1)))
    return out


def _module_level_call_names(tree: ast.Module) -> set[str]:
    """Names of functions *called from* the module body (top-level).

    Skips ``def``/``async def``/``class`` statements themselves - a call inside
    a function body is not "module level", only calls that run on import are.
    """
    names: set[str] = set()
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for node in ast.walk(stmt):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                names.add(node.func.id)
    return names


def _literal_sites(tree: ast.AST, value: str) -> list[tuple[int, str | None]]:
    """Every occurrence of ``value`` as a string constant, tagged with the
    nearest enclosing function name (or ``None`` at module level).
    """
    sites: list[tuple[int, str | None]] = []

    def walk(node: ast.AST, current_func: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            if (isinstance(child, ast.Constant)
                    and isinstance(child.value, str)
                    and child.value == value):
                sites.append((getattr(child, "lineno", -1), current_func))
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk(child, child.name)
            else:
                walk(child, current_func)

    walk(tree, None)
    return sites


# ─── W1.1 positive anti-drift ───────────────────────────────────────────
def test_w1_1_every_env_string_literal_in_src_is_registered():
    """Every ``"DHOLE_..."`` string constant in src/ must be registered.

    Whitelist is EMPTY by design (plan W1.1). If this test starts failing,
    the fix is to add the missing entry to env_registry.ALL, not to teach the
    test to tolerate the drift.
    """
    registered = {e.name for e in env_registry.ALL}
    observed: dict[str, list[Path]] = {}
    for path in _py_files():
        tree = _parse(path)
        for value, _lineno in _string_constants(tree):
            if ENV_LITERAL.match(value) and value not in registered:
                observed.setdefault(value, []).append(path)
    assert not observed, (
        f"unregistered DHOLE_* string literals: "
        f"{ {name: [str(p.relative_to(SRC_ROOT)) for p in files] for name, files in observed.items()} }"
    )


def test_w1_1_ast_approach_excludes_non_env_names():
    """``DHOLE_INSTRUCTIONS`` (module constant) and ``_DHOLE_TO_BACKEND``
    (dict name) are ``ast.Name`` nodes, never ``ast.Constant`` - the AST scan
    must not report them, so no text-regex whitelist is needed.
    """
    server_file = SRC_ROOT / "server.py"
    metasearch_file = SRC_ROOT / "search_metasearch.py"
    literals_server = {v for v, _ in _string_constants(_parse(server_file))}
    literals_metasearch = {v for v, _ in _string_constants(_parse(metasearch_file))}
    assert "DHOLE_INSTRUCTIONS" not in literals_server
    assert "_DHOLE_TO_BACKEND" not in literals_metasearch
    assert "DHOLE_TO_BACKEND" not in literals_metasearch | literals_server


def test_wire_text_never_names_an_unregistered_knob():
    """连接期文案里出现的每一个 ``DHOLE_*`` 名字都必须是活旋钮。

    W1.1 只盯"整串恰好是旋钮名"的字面量（那是 ``os.environ.get()`` 的形状），
    夹在长描述里的一句"需要 operator 设 DHOLE_NEURAL_FOCUS"它看不见 —— 而那正是
    最贵的一类谎：它写在 tools/list 里，每次连接都付费，agent 照着它去教用户改一个
    改了没有任何作用的环境变量。17.0 注销了 27 个旋钮，这条守卫是它们不复活的门。

    扫的是**渲染后的对象**（instructions + 工具声明 + inputSchema + 每个 Field 的
    description），不是源码文本：源码里"某旋钮已注销"的注释是合法教学，wire 里不是。
    """
    import dhole_mcp.server as server_mod

    registered = {e.name for e in env_registry.ALL}
    # 不含 DHOLE_ 字样的同族谎话：B1 删掉的是"许可门"这个概念本身，而文案里留下
    # 的说法常常不点名任何旋钮（"the operator's flag is the permission"就是这句），
    # 所以只扫名字会漏。这几个短语在合法的 17.0 文本里没有任何用处。
    dead_phrases = ("operator's flag", "the flag is on", "with the permission",
                    "whatever the operator configured", "opt-in tool")
    hits: dict[str, list[str]] = {}
    phrase_hits: dict[str, list[str]] = {}

    def walk(obj, where):
        if isinstance(obj, str):
            for name in re.findall(r"DHOLE_[A-Z0-9_]+", obj):
                if name not in registered:
                    hits.setdefault(name, []).append(where)
            for phrase in dead_phrases:
                if phrase in obj:
                    phrase_hits.setdefault(phrase, []).append(where)
        elif isinstance(obj, dict):
            for key, value in obj.items():
                walk(value, where if key == "description" else f"{where}.{key}")
        elif isinstance(obj, (list, tuple)):
            for value in obj:
                walk(value, where)

    walk(server_mod.DHOLE_INSTRUCTIONS, "INSTRUCTIONS")
    for tool_def in server_mod.MasterFetchServer._TOOL_DEFS:
        walk(tool_def, tool_def.get("name", "?"))
    # 这里曾有过第三个循环，走的是每颗工具签名上的 `Field(description=…)`。它一行都没读到
    # 过：`server.py` 是 `from __future__ import annotations`，注解在运行时是**字符串**，
    # `getattr(ann, "description", None)` 永远是 None —— 一条看着像覆盖、实际是空转的判据。
    # 而那份文本本身已经在 D-32 里摘掉了（发上 wire 的描述唯一的作者是 `_TOOL_DEFS`，
    # 守卫在 tests/test_the_wire_tool_descriptions_have_one_author.py）。

    assert not hits, (
        f"连接期文案点了未注册（多半是已注销）的旋钮: "
        f"{ {k: sorted(set(v))[:3] for k, v in hits.items()} } —— "
        f"运行时它已经什么都不做，这句话就是在教用户改一个无效的设置")
    assert not phrase_hits, (
        f"连接期文案还在描述一道已删除的许可门: "
        f"{ {k: sorted(set(v))[:3] for k, v in phrase_hits.items()} } —— "
        f"17.0 的能力全部默认在线，按次参数就是唯一的门")


def test_skill_corpus_never_names_an_unregistered_knob():
    """随包说明书（``skills/dhole-web/**``）与 wire 同一档：它也是 agent 照做的文本。

    这批里有三处就是这么漏的 —— fields.md 还在教 ``DHOLE_WIRE_FULL=1``，
    troubleshooting.md 还在教 ``DHOLE_ENGINE_HEARTBEAT`` / ``DHOLE_ENGINE_COOLDOWN``。
    与 W1.5（"每个活旋钮都要在配置文档里出现"）方向相反：那条盯覆盖率，这条盯谎话。
    """
    registered = {e.name for e in env_registry.ALL}
    bad: dict[str, list[str]] = {}
    for path in sorted((SRC_ROOT / "skills" / "dhole-web").rglob("*.md")):
        for name in re.findall(r"DHOLE_[A-Z0-9_]+", path.read_text(encoding="utf-8")):
            if name not in registered:
                bad.setdefault(name, []).append(path.name)
    assert not bad, (
        f"说明书里教了未注册的旋钮: { {k: sorted(set(v)) for k, v in bad.items()} } —— "
        f"设了没有任何作用，这段话就是在浪费读者的时间")


# ─── W1.2 reverse anti-drift ────────────────────────────────────────────
def test_w1_2_every_registry_name_is_read_as_string_in_src():
    """A registry entry that no reader in src/ actually parses is dead weight -
    either the code was removed or the name was made up. Fail loudly.
    """
    literals: set[str] = set()
    for path in _py_files():
        for value, _lineno in _string_constants(_parse(path)):
            if ENV_LITERAL.match(value):
                literals.add(value)
    missing = sorted({e.name for e in env_registry.ALL} - literals)
    assert not missing, f"registry names never appear as a string constant in src/: {missing}"


# ─── W1.3 no duplicates ─────────────────────────────────────────────────
def test_w1_3_registry_has_no_duplicate_names():
    names = [e.name for e in env_registry.ALL]
    assert len(names) == len(set(names))


def test_registry_covers_all_14_env_vars():
    """17.0 大众化一刀切把注册表从 39 收到 11（§3-20：注销的旋钮运行时静默
    忽略，只有 --doctor 认识它们）；G25 评审为 HTTP 传输加回
    `DHOLE_HTTP_TOKEN` 到 12，为凭据落盘开关加回 `DHOLE_PERSIST_CREDENTIALS`
    到 13。

    批次 10 加过两颗（`DHOLE_BROWSER_CHANNEL` / `DHOLE_BROWSER_HEADED`）到 15，
    批次 11 又整对退掉回到 13 —— 这条账留着不是为了纪念，是因为"**什么时候需要
    人在场**"只有正在做事的那一方知道：写死进机器配置，每次切换都得人跑去命令行，
    而 17.0 的卖点正是配置面小。能力落在 `web_act(headed=true)` 一扇门上。
    再删一个必须先改这里 —— 在有人 --doctor 的输出说谎之前先红。
    加回或新增一个旋钮同理。

    验收刀9-3 加第 14 颗 `DHOLE_SEARCH_ROW_FILTER`（default on）。它为什么必须是
    一颗旋钮而不是一个决定：这一族丢掉的是**别人家的内容**，判据是形状与词形而不是
    名单，而形状这种东西会变异 —— 关掉它不需要重新发版，也不需要谁去维护一张表。

    模型档位那次加了第 15 颗 `DHOLE_RERANK_MODEL`：它选的是本机加载哪一份权重，
    而模型会话是进程级单例 —— 不重启就换档，同一进程会拿两个打分器给检索计分。
    """
    assert len(env_registry.ALL) == 15, (
        f"expected 15 env vars, found {len(env_registry.ALL)}: "
        f"{sorted(e.name for e in env_registry.ALL)}"
    )


# ─── W1.4 read_at correctness ───────────────────────────────────────────
def test_w1_4_import_time_reads_reachable_from_module_body():
    """Every entry tagged ``read_at="import"`` must have at least one read
    that runs at import: either a literal at module level, or a literal
    inside a function that is called at module level in the same file.
    """
    trees: dict[Path, ast.Module] = {p: _parse(p) for p in _py_files()}
    module_calls: dict[Path, set[str]] = {
        p: _module_level_call_names(t) for p, t in trees.items()
    }

    for entry in env_registry.ALL:
        if entry.read_at != "import":
            continue
        hit = False
        for path, tree in trees.items():
            for _lineno, enclosing in _literal_sites(tree, entry.name):
                if enclosing is None:
                    hit = True
                    break
                if enclosing in module_calls[path]:
                    hit = True
                    break
            if hit:
                break
        assert hit, (
            f"{entry.name} claims read_at='import' but no read site is "
            f"reachable from a module body statement"
        )


# ─── W1.5 documentation coverage ────────────────────────────────────────
def test_w1_5_every_registry_name_appears_in_configuration_doc():
    """Plan W1.5 explicitly predicts this to fail on DHOLE_UPDATE_PACKAGE and
    DHOLE_UPDATE_INDEX_URL - W6.4 closes the gap. Until then this test is a
    scoreboard, not a gate on the W1 commit alone.
    """
    text = DOC_PATH.read_text(encoding="utf-8")
    missing = [e.name for e in env_registry.ALL if e.name not in text]
    assert not missing, (
        f"configuration.md is missing entries for: {missing}"
    )


# ─── registry-shape guards ───────────────────────────────────────────
def test_secret_kind_never_default_echos_value():
    """The ``secret`` kind (17.0: Tavily + 博查；G25 评审加 HTTP 传输的
    bearer token —— brightdata/exa 的 keyed 引擎已删). Their ``default`` is a
    marker string, not a value - W2's config audit depends on it.
    """
    secrets = [e for e in env_registry.ALL if e.kind == "secret"]
    assert len(secrets) == 3
    for entry in secrets:
        assert entry.default == "", (
            f"{entry.name} is secret but has a non-empty default - W2 will "
            f"print it"
        )
        # display_value on any raw must never contain the raw
        sample = "hunter2-super-secret-token"
        assert sample not in entry.display_value(sample)
        assert entry.display_value(sample).startswith("set (len=")


def test_fail_safe_closed_matches_plan_d1_gate_list():
    """§A.4 的 fail_safe=closed 门。17.0 收敛后剩两道：内网放行与 robots
    忽略 —— CDP 与 SSRF 复查的门已经从「旋钮」变成「写死 + 审计/自动判定」，
    比旋钮更硬，所以不再出现在这张表里。17.1 加回第三道：凭据落盘开关的
    拼错值必须停在内存（safe 一侧），而不是被 read_flag 当成 ON。"""
    closed = sorted(e.name for e in env_registry.ALL if e.fail_safe == "closed")
    assert closed == sorted([
        "DHOLE_ALLOW_PRIVATE_HOSTS",
        "DHOLE_IGNORE_ROBOTS",
        "DHOLE_PERSIST_CREDENTIALS",
    ])


def test_read_at_import_matches_plan_a1():
    """Import-time reads. 17.0 收敛后剩两个：内容默认值与工具白名单；G25 评审
    的 `DHOLE_HTTP_TOKEN` 在 serve() 读（进程启动即定，改了要重启）—— 同属
    "restart the host" 一档。17.1 的 `DHOLE_PERSIST_CREDENTIALS` 同档：store
    是模块级单例连接，连接一开模式就绑死，中途改旗搬不动已开的库。
    （DHOLE_HOME 也是 ENV_ONLY，但它是 bootstrap —— 定位配置文件的变量不能等
    配置文件来设，所以不进注册表。）drift 在这意味着 W2 的 "restart the host"
    提示要么空响、要么漏报。
    """
    imports = sorted(e.name for e in env_registry.ALL if e.read_at == "import")
    assert imports == sorted([
        "DHOLE_DEFAULT_CONTENT_CHARS",
        "DHOLE_HTTP_TOKEN",
        "DHOLE_PERSIST_CREDENTIALS",
        "DHOLE_RERANK_MODEL",
        "DHOLE_TOOLS",
    ])


# ─── W1 helpers ──────────────────────────────────────────────────────
def test_by_name_round_trips_every_entry():
    for entry in env_registry.ALL:
        assert env_registry.by_name(entry.name) is entry
    assert env_registry.by_name("DHOLE_NOT_A_REAL_VAR") is None


def test_scenario_labels_are_nonempty_and_stable():
    """W2 groups config by scenario. An empty or drifting label would break
    the section headers and make the doctor output feel arbitrary.
    """
    for entry in env_registry.ALL:
        assert entry.scenario and entry.scenario.strip() == entry.scenario
        # summary is user-facing; one short sentence is enough
        assert entry.summary and entry.summary.strip() == entry.summary
        assert len(entry.summary) < 160


@pytest.mark.parametrize("value,expected", [
    ("1", True), ("true", True), ("YES", True), ("on", True),
    ("0", False), ("false", False), ("No", False), ("off", False),
    ("none", False), ("NULL", False), ("", False),
])
def test_read_flag_recognised_tokens_do_not_warn(value, expected):
    """W4.2: a recognised token must read the same as before AND not add a
    warning - the warning list is only for genuinely unrecognised values.
    """
    env_registry.clear_warnings()
    import os
    os.environ["DHOLE_W4_TEST_FLAG"] = value
    try:
        got = env_registry.read_flag("DHOLE_W4_TEST_FLAG")
        assert got == expected
        assert env_registry.warnings_snapshot() == ()
    finally:
        del os.environ["DHOLE_W4_TEST_FLAG"]
        env_registry.clear_warnings()


def test_read_flag_non_safe_keeps_env_flag_semantics_but_warns():
    """W4.1: unrecognised -> ON (matches ``_env_flag``). The new part is only
    that a warning is recorded.
    """
    env_registry.clear_warnings()
    import os
    os.environ["DHOLE_W4_TEST_FLAG"] = "yes-please"
    try:
        assert env_registry.read_flag("DHOLE_W4_TEST_FLAG") is True
        snaps = env_registry.warnings_snapshot()
        assert len(snaps) == 1
        assert snaps[0].name == "DHOLE_W4_TEST_FLAG"
        assert "yes-please" in snaps[0].message
        assert "ON" in snaps[0].message
    finally:
        del os.environ["DHOLE_W4_TEST_FLAG"]
        env_registry.clear_warnings()


def test_read_flag_fail_safe_direction_is_off_not_on():
    """D1: for the 4 safety gates an unrecognised value goes OFF (safe).
    This is the ONLY behavioural change W4 makes, and it is scoped to
    entries marked ``fail_safe="closed"``.
    """
    env_registry.clear_warnings()
    import os
    os.environ["DHOLE_W4_TEST_GATE"] = "flase"
    try:
        assert env_registry.read_flag(
            "DHOLE_W4_TEST_GATE", default=False, fail_safe=True) is False
        snaps = env_registry.warnings_snapshot()
        assert len(snaps) == 1
        assert "OFF" in snaps[0].message
        assert "fail-safe" in snaps[0].message
    finally:
        del os.environ["DHOLE_W4_TEST_GATE"]
        env_registry.clear_warnings()


def test_read_int_records_warning_on_non_numeric():
    env_registry.clear_warnings()
    import os
    os.environ["DHOLE_W4_TEST_INT"] = "abc"
    try:
        assert env_registry.read_int("DHOLE_W4_TEST_INT", 40000) == 40000
        snaps = env_registry.warnings_snapshot()
        assert len(snaps) == 1
        assert "'abc'" in snaps[0].message
        assert "using default 40000" in snaps[0].message
    finally:
        del os.environ["DHOLE_W4_TEST_INT"]
        env_registry.clear_warnings()


def test_is_valid_value_matches_registry_kind():
    """The doctor's colouring of a set env depends on this - if it drifts,
    doctor says the value is fine while the actual reader fell back.
    """
    entry_int = env_registry.by_name("DHOLE_DEFAULT_CONTENT_CHARS")
    assert env_registry.is_valid_value(entry_int, "8000") == (True, "")
    ok, why = env_registry.is_valid_value(entry_int, "abc")
    assert not ok and "not an integer" in why
    ok, why = env_registry.is_valid_value(entry_int, "1")
    assert not ok and "outside 500..200000" in why

    entry_flag = env_registry.by_name("DHOLE_IGNORE_ROBOTS")
    assert env_registry.is_valid_value(entry_flag, "on") == (True, "")
    assert env_registry.is_valid_value(entry_flag, "") == (True, "")
    ok, why = env_registry.is_valid_value(entry_flag, "yes-please")
    assert not ok and "not a recognised" in why
