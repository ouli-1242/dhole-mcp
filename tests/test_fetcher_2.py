"""test_fetcher_2.py —— 由 2 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import asyncio
import json
import pytest
from dhole_mcp import identity
from dhole_mcp import server as server_mod
from dhole_mcp.server import MasterFetchServer, ResponseModel
import time
from dhole_mcp import sessions
from dhole_mcp.fetcher import HTTPSession
from dhole_mcp.server import _cache_context, _SESSION_ID


# ============================================================================
# 来源：test_web_request_b5.py
# ============================================================================
# B5-14a：`web_request` —— 写操作从 web_read 里搬出来（§4 第 4 行，第 16 颗工具）。

# 可解析的 URL：非 GET 的路径要先过 TCP 预检，一个不存在的主机会在到层之前就死掉，
# 那时读到的空 kwargs 说的是 DNS，不是这条链。
URL = "https://example.com/orders"
OK = ResponseModel(url=URL, status=200, content=["ok"], fetcher_used="http")


def _defs() -> dict:
    return {d["name"]: d for d in MasterFetchServer._TOOL_DEFS}


def _dispatch(tool: str, args: dict) -> dict:
    _content, structured = asyncio.run(MasterFetchServer()._dispatch(tool, args))
    return structured


def _capture_get(monkeypatch, **call):
    """走一次真实分发，抓 HTTP 层实际收到的参数（非 GET 只可能走这一层）。"""
    seen: dict = {}

    async def fake(self, url, **kwargs):
        seen.update(kwargs)
        seen["url"] = url
        return ResponseModel(url=url, status=200, content=["body"],
                             fetcher_used="http")

    monkeypatch.setattr(MasterFetchServer, "get", fake)
    # confirm=True 是这颗工具的既有闸门（见 TestTheWriteGate）：走写路径的用例
    # 都从这条入口过，这里默认带上，让它们继续测各自的主语。
    args = {"url": URL, "method": "POST", "body": '{"item": 1}', "confirm": True}
    args.update(call)
    asyncio.run(MasterFetchServer()._dispatch("web_request", args))
    return seen


class TestTheToolExists:

    def test_it_is_registered_with_the_eight_names_it_can_honour(self):
        assert "web_request" in _defs(), sorted(_defs())
        # 第七颗之后再加的是 W1 的 `headers`：写请求从前连非凭据的头都递不进去
        # （`content_type` 已经是一颗头的特例，所以"够不着"是漏，不是设计）。
        # 第九颗是 R3 的 `include_headers`（一次 HEAD 探针的全部价值就是那几颗头），
        # 与 `web_read` 那颗同名同义同一只归一化 —— 两边读的必须是同一份东西。
        assert server_mod._TOP_LEVEL_ARGS["web_request"] == frozenset({
            "url", "method", "body", "content_type", "extraction_type", "timeout",
            "confirm", "headers", "include_headers"})

    def test_it_is_a_write_on_the_wire(self):
        """annotations 是客户端决定"要不要先问用户"的输入，不是装饰。"""
        ann = _defs()["web_request"]["annotations"]
        assert ann.get("readOnlyHint") is False
        assert ann.get("idempotentHint") is False
        assert ann.get("openWorldHint") is True

    def test_the_default_surface_is_eleven_tools(self):
        assert len(_defs()) == 11, sorted(_defs())

    def test_every_property_description_stays_within_the_budget(self):
        props = _defs()["web_request"]["inputSchema"]["properties"]
        for key, spec in props.items():
            assert len(spec.get("description") or "") <= 200, key

    def test_the_routing_sends_writes_here(self):
        """instructions 是路由唯一的家：这句话不在，agent 会继续拿 web_read 发 POST。"""
        text = server_mod.DHOLE_INSTRUCTIONS.lower()
        assert "web_request" in text
        assert "post" in text and "put" in text and "delete" in text

    def test_a_knob_that_could_do_nothing_was_not_added_for_the_sake_of_the_plan(self):
        """§4 给的那 7 颗里 anti_detect 在这颗工具上没有落点：写操作不渲染。"""
        assert "anti_detect" not in _defs()["web_request"]["inputSchema"]["properties"]
        assert "anti_detect" not in server_mod._TOP_LEVEL_ARGS["web_request"]
        # 描述里"为什么没有这颗"要能读到 —— 缺席也要有据可查，否则下一个人会"顺手补上"。
        assert "anti_detect" in _defs()["web_request"]["description"]


class TestTheWriteGate:
    """写操作确认门（G25 评审 Blocker）：submit 有闸、sys_state 清会话有闸、
    CDP 首挂有闸，这扇门原来没有——而 cred_defaults 存的凭据按主机自动随行，
    被页面正文（间接注入）诱导的 agent 可以直发已认证写请求。与 submit 同一
    语义：没有用户点过头，一个请求都不出。HEAD 是探测，不需要 confirm。
    """

    @pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
    def test_a_write_without_confirm_is_refused_before_anything_is_sent(
            self, monkeypatch, method):
        sent: list = []

        async def fake(self, url, **kwargs):
            sent.append((url, kwargs))
            return OK

        monkeypatch.setattr(MasterFetchServer, "get", fake)
        out = _dispatch("web_request", {"url": URL, "method": method})
        assert not sent, f"{method} 没有 confirm 竟然发出去了"
        blob = json.dumps(out).lower()
        assert "confirm" in blob, out
        assert "nothing was sent" in blob, out

    def test_confirm_false_is_as_good_as_absent(self, monkeypatch):
        sent: list = []

        async def fake(self, url, **kwargs):
            sent.append(url)
            return OK

        monkeypatch.setattr(MasterFetchServer, "get", fake)
        out = _dispatch("web_request", {"url": URL, "method": "POST",
                                        "confirm": False})
        assert not sent, "confirm=false 被当成了真"
        assert "confirm" in json.dumps(out).lower(), out

    @pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
    def test_confirm_true_reaches_the_http_tier(self, monkeypatch, method):
        seen = _capture_get(monkeypatch, method=method)
        assert seen["url"] == URL, seen

    def test_head_is_a_probe_and_needs_no_confirm(self, monkeypatch):
        sent: list = []

        async def fake(self, url, **kwargs):
            sent.append(url)
            return OK

        monkeypatch.setattr(MasterFetchServer, "get", fake)
        _dispatch("web_request", {"url": URL, "method": "head"})
        assert sent, "HEAD 是探测，不该被确认门拦下"

    def test_the_gate_is_discoverable_on_the_wire(self):
        """闸门要在，通往闸门的路也要在：confirm 不进 schema，重试就被
        dispatch 当表外名拒掉——拒绝消息教的 confirm=true 根本传不进来。"""
        props = _defs()["web_request"]["inputSchema"]["properties"]
        assert "confirm" in props, sorted(props)
        assert "confirm" in _defs()["web_request"]["description"]


class TestWhatItRequires:

    def test_method_is_required_and_says_so(self):
        """缺席的 method 要说"缺什么"，不能顺着 `_clean_method` 的默认答成 GET。

        `_clean_method("")` 返回 GET（那是读的那扇门要的默认值），所以这一问必须在
        GET 分支**之前**问出口 —— 否则调用方会因为自己没填而被告知"你请求的是读"。
        """
        out = _dispatch("web_request", {"url": URL, "body": "x"})
        blob = json.dumps(out).lower()
        assert "required" in blob and "method" in blob, out
        assert "typeerror" not in blob, "缺必填炸成栈异常，等于把契约换了个形状"

    def test_url_is_required(self):
        out = _dispatch("web_request", {"method": "POST"})
        assert "url" in json.dumps(out).lower(), out

    def test_get_is_answered_with_a_pointer_not_a_silent_fetch(self, monkeypatch):
        """GET 是 web_read 的活：那里才有缓存、升级梯子与归档兜底。

        在这里默默做一次 GET，调用方拿到的是"看起来一样、规则不一样"的第二次读，
        而那正是把两颗工具拆开要避免的事。
        """
        sent: list = []

        async def fake(self, url, **kwargs):
            sent.append(url)
            return OK

        monkeypatch.setattr(MasterFetchServer, "get", fake)
        out = _dispatch("web_request", {"url": URL, "method": "GET"})
        assert not sent, "GET 竟然发出去了"
        blob = json.dumps(out).lower()
        assert "web_read" in blob and "get" in blob, out

    @pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "head"])
    def test_the_verbs_that_belong_here_reach_the_http_tier(self, monkeypatch, method):
        seen = _capture_get(monkeypatch, method=method)
        assert seen["url"] == URL, seen

    def test_a_verb_outside_the_known_set_is_refused_with_the_list(self, monkeypatch):
        """`_clean_method` 认不出的动词不是"照着发"，是"这一趟没发出去"。

        原样转发一个拼错的 POSET 看着诚实，实际把一次可能已提交的写变成两次未知
        请求；拒掉它并列出可选的五个，才是调用方能立刻改对的答案。
        """
        sent: list = []

        async def fake(self, url, **kwargs):
            sent.append(kwargs)
            return OK

        monkeypatch.setattr(MasterFetchServer, "get", fake)
        out = _dispatch("web_request", {"url": URL, "method": "POSET"})
        assert not sent, out
        blob = json.dumps(out)
        assert "POST" in blob and "DELETE" in blob, out


class TestTheOneShotRulesSurviveTheMove:
    """搬工具不许搬走规矩：这几条是 web_read 里那条分支的全部理由。"""

    def test_a_write_is_sent_once_at_the_layer_that_sends_it(self):
        """"只发一次"住在传输层，不住在工具的嗓门里。

        `get()` 仍然带着 retries=3 往下走（GET 要这个），真正把写按下来的是 fetcher
        那一行"会改状态的动词重试次数就是 0"。断言打在那条规则上，才是这条承诺真正
        的证人；打在自己这层的 kwargs 上，测的是运气。而行为层面的证人（数过发了几
        次的）在 tests/test_http_methods_g8.py，那里是这条规矩住的地方。

        这一片曾经漏掉 PUT/DELETE：集合按 RFC 的"幂等"来写，工具按"写只出去一次"
        来说，两边各自都成立，合起来是在骗人。现在门槛问的是"会不会改状态"。
        """
        from dhole_mcp.fetcher import _RETRY_SAFE_METHODS
        writes = server_mod._FETCH_METHODS - {"GET", "HEAD"}
        assert writes == {"POST", "PUT", "PATCH", "DELETE"}
        for verb in sorted(writes):
            assert verb not in _RETRY_SAFE_METHODS, verb
        # HEAD 是唯一一颗可以重问的动词：它不改状态，重试只是再看一眼。
        assert "HEAD" in _RETRY_SAFE_METHODS
        # 而 wire 上那句话不许和这条规则脱钩（描述说一次，代码就得只发一次）。
        assert "sent ONCE" in _defs()["web_request"]["description"]

    def test_a_write_is_not_answered_from_the_cache(self, monkeypatch):
        hits: list = []

        async def fake(self, url, **kwargs):
            hits.append(kwargs.get("cache_ttl"))
            return ResponseModel(url=url, status=200, content=["body"],
                                 fetcher_used="http")

        monkeypatch.setattr(MasterFetchServer, "get", fake)
        for _ in range(2):
            asyncio.run(MasterFetchServer(cache_ttl=3600)._dispatch(
                "web_request", {"url": URL, "method": "POST", "body": "{}",
                                "confirm": True}))
        assert len(hits) == 2, "两次 POST 里有第二次走了缓存 —— 重发的是缓存不是写"

    def test_a_write_never_escalates_to_the_browser(self, monkeypatch):
        """新门接的是同一条链，不是自己另起一条会升级的。

        升级/回退本身住在 `get()` 里，由 tests/test_http_methods_g8.py 的
        `test_a_write_never_reaches_the_browser_or_the_archive` 在管道层钉住（那层看得
        到 method，这里看不到 —— 动词是 contextvar 带下去的，kwargs 里没有它）。这条
        只验答案里那条路径：一次写落过的层只有 direct:http。
        """
        _capture_get(monkeypatch, timeout=5000)   # 装桩：这一趟不真发请求
        out = _dispatch("web_request", {"url": URL, "method": "POST", "body": "{}",
                                        "confirm": True})
        assert "stealthy" not in (out.get("escalation_path") or ""), out
        assert out.get("escalation_path") == "direct:http", out

    def test_an_oversized_body_is_refused_before_anything_is_sent(self, monkeypatch):
        sent: list = []

        async def fake(self, url, **kwargs):
            sent.append(url)
            return OK

        monkeypatch.setattr(MasterFetchServer, "get", fake)
        out = _dispatch("web_request", {"url": URL, "method": "POST",
                                        "body": "x" * (300 * 1024),
                                        "confirm": True})
        assert not sent, "正文超上限却还是发出去了"
        # 上限说的是字节数（262144 = 256KB）：拒答要给出可执行的数字，不是"太大了"。
        assert "262144" in json.dumps(out), out


class TestReadSideLostTheThreeNames:

    def test_web_read_no_longer_advertises_the_verb_knobs(self):
        props = _defs()["web_read"]["inputSchema"]["properties"]
        for key in ("method", "body", "content_type"):
            assert key not in props, key
        # 这颗数字的来龙去脉写在 tests/test_anti_detect_b5.py:94（28→25→24→27）。
        # 这里再钉一遍是因为这条用例的主语就是"读的那扇门还剩几颗"：扇出（B5-14d）
        # 加的三颗要在这里被看见，而不是只在别人的文件里解释。27 → 28 是 W1 的
        # `headers`（一次零点名的抓取实测发 15 颗头，而门上那颗都改不了）。
        # 28 → 31 是 2026-10-23 那三颗，它们共用一条轴："这一次要回来的，除了正文
        # 还有什么" —— R3 `include_headers`（证据：状态行 / 头 / 302 链）与 R4
        # `save_to` + `max_save_bytes`（字节本身）。数的不是三颗无关的旋钮，是这扇门
        # 的第三种产出：这里记"还剩几颗"，`tests/test_wire_shape.py` 的 PARAM_CAP
        # 记"为什么还能再加三颗、以及拆成第四颗工具要付多少钱"。
        assert len(server_mod._TOP_LEVEL_ARGS["web_read"]) == 31, \
            sorted(server_mod._TOP_LEVEL_ARGS["web_read"])

    @pytest.mark.parametrize("key", ["method", "body", "content_type"])
    def test_the_old_spelling_is_refused_naming_the_new_tool(self, key):
        with pytest.raises(ValueError, match="Unsupported argument") as e:
            _dispatch("web_read", {"url": URL, key: "x"})
        msg = str(e.value)
        assert key in msg and "web_request" in msg, msg

    def test_the_dispatcher_cannot_smuggle_a_verb_back_into_the_read(self):
        """调度器不许把 `method` 悄悄塞回 web_read：那条通道一开，两扇门就又是一扇。

        只看 web_read 那一段 —— web_request 自己当然读 `method`，那就是它的门牌。
        """
        import inspect
        src = inspect.getsource(MasterFetchServer._dispatch)
        branch = src.split('if name == "web_read":')[1].split('elif name ==')[0]
        assert "method" not in branch, branch


class TestTheDeclaredContentTypeMustMatchTheBytesOnTheWire:
    """W2：声明的 content_type 与实际编码不许各说各话。

    真机复现（2026-10-09 体测）：`content_type="text/plain"` + dict body，回显
    出来的请求头是 `content-type: text/plain`，而 `body_raw` 是
    `amount=3&item=%E6%89%93%E5%8D%B0%E7%BA%B8&...`、`content-length: 128` ——
    表单字节套着纯文本的帽子。前端照这份回显抄实现就会踩坑。

    判据取"头与字节一致"而不是"调用成功"：一条自相矛盾的请求恰恰是**成功的**
    （服务端 200 回显），只有比对两边才看得见。
    """

    def _sent(self, monkeypatch, **call):
        """抓 HTTP 层真正带出去的 header 与 body 字节。

        两者都不走 `get()` 的形参：body 走 `_BODY`、content-type 走
        `_CONTENT_TYPE` 这两颗 ContextVar，在 fetcher 里才并进请求头。所以
        断言必须读那两颗变量 —— 读 `kwargs["headers"]` 会永远拿到空字典，
        测的是"参数怎么传"而不是"请求发出去长什么样"。
        """
        seen: dict = {}

        async def fake(self, url, **kwargs):
            seen["headers"] = dict(kwargs.get("headers") or {})
            seen["declared"] = server_mod._CONTENT_TYPE.get()
            seen["body"] = server_mod._BODY.get()
            return ResponseModel(url=url, status=200, content=["body"],
                                 fetcher_used="http")

        monkeypatch.setattr(MasterFetchServer, "get", fake)
        args = {"url": URL, "method": "POST", "confirm": True}
        args.update(call)
        out = _dispatch("web_request", args)
        return seen, out

    @classmethod
    def _declared(cls, seen) -> str:
        """请求头上那条 content-type —— 显式给的就用它，否则回落到变量。"""
        headers = {k.lower(): v for k, v in (seen.get("headers") or {}).items()}
        return str(headers.get("content-type") or seen.get("declared") or "")

    def test_a_type_the_encoder_cannot_produce_is_refused_before_any_request(
            self, monkeypatch):
        seen, out = self._sent(monkeypatch, body={"item": "打印纸", "n": 3},
                               content_type="text/plain")
        assert seen == {}, "拒绝必须发生在门口：一个字节都不该发出去"
        err = json.dumps(out, ensure_ascii=False)
        assert "text/plain" in err and "dict/list" in err

    def test_the_refusal_names_all_three_ways_out(self, monkeypatch):
        _seen, out = self._sent(monkeypatch, body={"a": 1},
                                content_type="text/plain")
        action = json.dumps(out, ensure_ascii=False).lower()
        assert "application/json" in action, "出路一：改声明"
        assert "x-www-form-urlencoded" in action, "出路二：改声明"
        assert "string" in action, "出路三：把 body 变成字符串，原样发"

    def test_declaring_json_sends_json_under_json(self, monkeypatch):
        seen, _ = self._sent(monkeypatch, body={"item": "打印纸", "n": 3},
                             content_type="application/json")
        assert "json" in self._declared(seen).lower()
        assert json.loads(seen["body"].decode("utf-8")) == {"item": "打印纸", "n": 3}

    def test_declaring_a_form_sends_a_form_under_a_form(self, monkeypatch):
        seen, _ = self._sent(monkeypatch, body={"item": "a"},
                             content_type="application/x-www-form-urlencoded")
        assert "urlencoded" in self._declared(seen).lower()
        assert seen["body"] == b"item=a"

    def test_declaring_nothing_stamps_the_type_it_actually_used(self, monkeypatch):
        """未声明时由工具自己补齐 —— 这条一直是对的，别被新闸改坏。"""
        seen, _ = self._sent(monkeypatch, body={"item": "a"})
        assert "urlencoded" in self._declared(seen).lower()
        assert seen["body"] == b"item=a"

    def test_a_string_body_still_goes_verbatim_under_any_name(self, monkeypatch):
        """字符串 body 本来就能是 text/plain —— 新闸不许把它一起拦掉。"""
        seen, _ = self._sent(monkeypatch, body="just plain text",
                             content_type="text/plain")
        assert seen["body"] == b"just plain text"
        assert self._declared(seen) == "text/plain"

    @pytest.mark.parametrize("declared", [
        "text/plain", "text/csv", "application/xml", "application/x-ndjson",
        "application/octet-stream", "text/html",
    ])
    def test_no_declared_type_is_ever_paired_with_the_wrong_bytes(
            self, monkeypatch, declared):
        """性质：对任何声明，要么具名拒绝，要么 header 与字节说的是同一件事。"""
        seen, _out = self._sent(monkeypatch, body={"a": 1},
                                content_type=declared)
        if not seen:
            return  # 具名拒绝：两边都没发出去，自然一致
        head = self._declared(seen).lower()
        body = seen["body"] or b""
        if "json" in head:
            json.loads(body.decode("utf-8"))
        elif "urlencoded" in head:
            assert b"=" in body and b"{" not in body, body
        else:
            raise AssertionError(
                f"header 说 {head!r} 而字节是 {body!r}：既不是 json 也不是表单，"
                "这条组合就是 W2 的形状")


class TestIdentityStillComesFromTheStore:

    def test_a_stored_proxy_and_credential_reach_a_write(self, monkeypatch):
        # 落盘的代理按显式代理待之，会先过一次 5s TCP 预检（正确的行为），而这里要
        # 测的是"身份到没到层"，不是代理活着没 —— 所以桩打在这一步上，让链路走到层。
        async def alive(proxy, url, start_time=None):
            return None

        monkeypatch.setattr(server_mod, "_dead_explicit_proxy", alive)
        identity.set_value("proxy", "http://127.0.0.1:7890")
        identity.set_value("auth", {"type": "basic", "username": "u",
                                    "password": "sup3r-s3cret"},
                           scope="example.com")
        seen = _capture_get(monkeypatch)
        assert seen["proxy"] == "http://127.0.0.1:7890", seen
        headers = {k.lower() for k in (seen.get("headers") or {})}
        assert "authorization" in headers, seen

    def test_a_stored_credential_for_another_host_does_not_ride_along(self, monkeypatch):
        identity.set_value("auth", {"type": "bearer", "token": "secret-token"},
                           scope="elsewhere.test")
        seen = _capture_get(monkeypatch)
        assert "authorization" not in {k.lower() for k in (seen.get("headers") or {})}


# ============================================================================
# 来源：test_sessions_g7.py
# ============================================================================
# G7 回归：`options.session_id` 的 cookie jar（sessions.py + 逐跳的凭据边界）。

LOGIN = "https://shop.example.com/login"
ME = "https://shop.example.com/account"
OTHER = "https://elsewhere.example.org/landed"


class _Resp:
    def __init__(self, url, status=200, headers=None, cookies=None, body=b"ok"):
        self.url = url
        self.status_code = status
        self.headers = headers or {}
        self.content = body
        self.reason = ""
        self.cookies = [{"name": k, "value": v} for k, v in (cookies or {}).items()]


class _Client:
    """Scripted answers keyed by exact URL; records the headers of every dial."""

    def __init__(self, routes):
        self.routes = routes
        self.dials: list[tuple[str, dict]] = []
        self.methods: list[str] = []

    def request(self, method, url, **kwargs):
        self.dials.append((url, dict(kwargs.get("headers") or {})))
        self.methods.append(method)
        return self.routes.get(url) or _Resp(url, status=404)

    @property
    def last_cookie(self) -> str:
        return self.dials[-1][1].get("cookie", "")


def _session_with(client) -> HTTPSession:
    s = HTTPSession()
    s._client = client
    return s


# ─── jar 的行为 ──────────────────────────────────────────────────────

class TestTheJarRoundTrip:

    @pytest.mark.asyncio
    async def test_a_cookie_the_site_set_comes_back_on_the_next_call(self):
        client = _Client({LOGIN: _Resp(LOGIN, 200, cookies={"sid": "abc"})})
        await _session_with(client).get(LOGIN, session_id="bob")

        assert sessions.header_for("bob", ME) == {"sid": "abc"}, "jar did not keep it"

        await _session_with(client).get(ME, session_id="bob")
        assert client.last_cookie == "sid=abc"

    @pytest.mark.asyncio
    async def test_a_cookie_set_on_a_redirect_is_kept_too(self):
        """登录后 302 到首页、cookie 落在那张 302 上——最常见的形态。"""
        client = _Client({
            LOGIN: _Resp(LOGIN, 302, {"location": "/account"}, cookies={"sid": "abc"}),
            ME: _Resp(ME, 200),
        })
        await _session_with(client).get(LOGIN, session_id="bob", follow_redirects=True)

        assert sessions.header_for("bob", ME) == {"sid": "abc"}
        assert client.dials[-1][1].get("cookie") == "sid=abc", "the hop after the 302 got no cookie"

    @pytest.mark.asyncio
    async def test_no_session_id_means_no_jar_at_all(self):
        """默认（不点名 session_id）必须和 16.0 之前一模一样：一次性 cookie。"""
        client = _Client({LOGIN: _Resp(LOGIN, 200, cookies={"sid": "abc"})})
        await _session_with(client).get(LOGIN)

        assert sessions.header_for("", ME) == {}
        assert client.dials[-1][1].get("cookie") is None or "sid" not in client.last_cookie

    @pytest.mark.asyncio
    async def test_a_session_is_scoped_per_host(self):
        sessions.store("bob", "https://a.example.com/", {"sid": "secret-a"})

        client = _Client({ME: _Resp(ME, 200)})
        await _session_with(client).get(ME, session_id="bob")

        assert "secret-a" not in client.last_cookie, "cookie from another host leaked in"

    @pytest.mark.asyncio
    async def test_a_stored_cookie_expires(self):
        sessions.store("bob", ME, {"sid": "abc"}, now=time.time() - sessions.COOKIE_TTL_S - 1)
        assert sessions.header_for("bob", ME) == {}

    @pytest.mark.asyncio
    async def test_the_caller_named_cookie_wins_for_its_own_origin(self):
        sessions.store("bob", ME, {"sid": "from-jar"})
        client = _Client({ME: _Resp(ME, 200)})

        await _session_with(client).get(ME, session_id="bob", cookies={"sid": "named-now"})

        assert client.last_cookie == "sid=named-now"


# ─── 凭据的边界 ──────────────────────────────────────────────────────

class TestCredentialsDoNotRideARedirect:

    @pytest.mark.asyncio
    async def test_an_off_origin_redirect_gets_no_cookie(self):
        """此前 header 字典建一次、逐跳原样重发：站点把我们转到别的域，cookie（和
        Authorization）就跟着送过去了——那台主机是它选的，不是我们约的。"""
        client = _Client({
            LOGIN: _Resp(LOGIN, 302, {"location": "https://elsewhere.example.org/landed"}),
            OTHER: _Resp(OTHER, 200),
        })
        await _session_with(client).get(
            LOGIN, session_id="bob", cookies={"sid": "abc"},
            headers={"authorization": "Basic c2VjcmV0"}, follow_redirects=True)

        assert len(client.dials) == 2
        assert "cookie" not in client.dials[-1][1], client.dials[-1][1]
        assert "authorization" not in client.dials[-1][1], client.dials[-1][1]

    @pytest.mark.asyncio
    async def test_the_jar_of_the_host_being_dialed_is_still_presented(self):
        """同主机才算数：跳到另一台我们存过 cookie 的机器，那台的 cookie 照带——
        这是正常网页行为，也才是「登录后被转到 CDN 域」能走通的原因。"""
        sessions.store("bob", OTHER, {"cdn": "yes"})
        client = _Client({
            LOGIN: _Resp(LOGIN, 302, {"location": "https://elsewhere.example.org/landed"}),
            OTHER: _Resp(OTHER, 200),
        })
        await _session_with(client).get(LOGIN, session_id="bob", follow_redirects=True)

        assert client.dials[-1][1].get("cookie") == "cdn=yes"

    @pytest.mark.asyncio
    async def test_a_cookie_header_the_caller_placed_in_extra_headers_is_not_duplicated(self):
        """base 里已有 Cookie（大小写都算）时，jar 是替换它而不是再叠一行。"""
        client = _Client({ME: _Resp(ME, 200)})
        sessions.store("bob", ME, {"sid": "jar"})

        await _session_with(client).get(
            ME, session_id="bob", headers={"Cookie": "mine=1"})

        cookie_values = [v for k, v in client.dials[-1][1].items() if k.lower() == "cookie"]
        assert len(cookie_values) == 1, cookie_values
        assert cookie_values[0] == "sid=jar"

    @pytest.mark.asyncio
    async def test_same_origin_keeps_a_base_cookie_when_the_jar_has_nothing(self):
        client = _Client({ME: _Resp(ME, 200)})
        await _session_with(client).get(ME, headers={"cookie": "mine=1"})
        assert client.dials[-1][1].get("cookie") == "mine=1"


# ─── 响应 cookie 到底读得到吗（jar 的地基）────────────────────────────

class TestResponseCookiesAreReadAtAll:
    """primp 的 ``resp.cookies`` 是**映射**（``{'a':'1'}``）。旧代码按「列表 of
    dict / cookie 对象」去遍历它，遍历到的是键（字符串），两个分支都不匹配，于是
    每次都返回 ``{}`` —— 这正是报告里「所有结果 session_id 空、cookie 看不见」的
    机制：不是没人想读，是读的人一直读到空。jar 建在它上面就会一起塌。"""

    class _R:
        def __init__(self, cookies):
            self.cookies = cookies

    def test_the_mapping_shape_the_client_actually_returns(self):
        from dhole_mcp.fetcher import _resp_cookies
        assert _resp_cookies(self._R({"dhole": "probe1", "csrf": "tok"})) == {
            "dhole": "probe1", "csrf": "tok"}

    def test_the_sequence_shapes_still_read(self):
        """浏览器层自己拼的是列表，两种形状都得认。"""
        from dhole_mcp.fetcher import _resp_cookies

        class _C:
            name = "sid"
            value = "v"

        assert _resp_cookies(self._R([{"name": "a", "value": "1"}])) == {"a": "1"}
        assert _resp_cookies(self._R([_C()])) == {"sid": "v"}

    def test_a_response_without_cookies_reads_empty(self):
        from dhole_mcp.fetcher import _resp_cookies
        assert _resp_cookies(self._R(None)) == {}
        assert _resp_cookies(object()) == {}


# ─── web_read 这一层 ──────────────────────────────────────────────

def _html_result(url: str) -> ResponseModel:
    return ResponseModel(url=url, status=200, content=["<p>ok</p>"],
                         content_type="text/html", fetcher_used="http")


class TestSmartFetchSeesTheSession:

    @pytest.mark.asyncio
    async def test_the_id_and_the_names_come_back_but_never_the_values(self, monkeypatch):
        sessions.store("bob", ME, {"sid": "super-secret", "csrf": "tok"})

        async def fake_get(self, url, **kwargs):
            return _html_result(url)
        monkeypatch.setattr(MasterFetchServer, "get", fake_get)

        out = await MasterFetchServer().web_read(ME, cache_ttl=0, session_id="bob")

        assert out.session_id == "bob"
        assert sorted(out.session_cookie_names) == ["csrf", "sid"]
        # The jar's values must not surface in anything dhole derives. (A page body
        # that echoes its own cookies is the site talking, not dhole.)
        derived = json.dumps({
            "names": out.session_cookie_names, "summary": out.summary,
            "next_action": out.next_action, "error": out.error,
            "metadata": out.metadata,
        }, default=str)
        assert "super-secret" not in derived

    @pytest.mark.asyncio
    async def test_a_stateless_call_reports_no_session(self, monkeypatch):
        async def fake_get(self, url, **kwargs):
            return _html_result(url)
        monkeypatch.setattr(MasterFetchServer, "get", fake_get)

        out = await MasterFetchServer().web_read(ME, cache_ttl=0)

        assert out.session_id == "" and out.session_cookie_names == []

    @pytest.mark.asyncio
    async def test_the_bulk_path_does_not_lose_the_session(self, monkeypatch):
        """urls=[...] 会按 URL 再调一次 web_read；不转发 session_id 的话，第二批
        请求全部以「无会话」发出，cookie 静默不带上——而响应看起来完全正常。"""
        seen: list[str] = []

        async def fake_get(self, url, **kwargs):
            seen.append(_SESSION_ID.get())
            return _html_result(url)
        monkeypatch.setattr(MasterFetchServer, "get", fake_get)

        out = await MasterFetchServer().web_read(
            "", urls=[ME, LOGIN], cache_ttl=0, session_id="bob")

        assert out.results[0].session_id == "bob"
        assert out.results[1].session_id == "bob"
        assert seen == ["bob", "bob"], seen

    # 同一条扇出的另一半：session_id 不能把 `anti_detect` 弄丢 —— 扇出去的那两次
    # 各自重问一次身份，档位一旦在 `_smart_fetch_bulk` 里掉了，每一次嵌套都会退回
    # "auto"（或存储里的那一档）而且一句警告都没有。这条和上面那条 session_id 的
    # 用例合起来才是这条扇出的完整证人。
    @pytest.mark.asyncio
    async def test_the_bulk_path_does_not_lose_the_ladder(self, monkeypatch):
        rung: list = []
        real_identity_for = MasterFetchServer._identity_for

        def spy(self, url, *, cdp_url=None, anti_detect=None):
            # web_read 每跑一次就问一次身份：这条记录因此同时覆盖外层那一次与
            # 扇出去的每一次，谁把档位弄丢了都藏不住。
            rung.append(anti_detect)
            return real_identity_for(self, url, cdp_url=cdp_url,
                                     anti_detect=anti_detect)

        async def fake_get(self, url, **kwargs):
            return _html_result(url)

        monkeypatch.setattr(MasterFetchServer, "_identity_for", spy)
        monkeypatch.setattr(MasterFetchServer, "get", fake_get)

        await MasterFetchServer().web_read(
            "", urls=[ME, LOGIN], cache_ttl=0, anti_detect="aggressive")

        assert rung == ["aggressive"] * 3, rung

    @pytest.mark.asyncio
    async def test_an_unusable_id_is_refused_before_anything_is_fetched(self, monkeypatch):
        """2026-10-08 验收（D-02）改的政策：过去回 200 只在 summary 里附一句
        "was ignored"，于是"这次跑在谁的登录态里"这颗身份参数成了全站唯一被静默丢弃
        的那一颗 —— 同一个面里 `location` / `freshness` / `anti_detect` / 没有 host
        的 auth / 解析不出来的 allow_private 全部硬拒。方向也反了：越接近凭据越不该
        猜。一个以为登录态在延续的调用方拿到的是匿名页 + 200。"""
        fetched = []

        async def fake_get(self, url, **kwargs):
            fetched.append(url)
            return _html_result(url)
        monkeypatch.setattr(MasterFetchServer, "get", fake_get)

        out = await MasterFetchServer().web_read(
            ME, cache_ttl=0, session_id="../etc/passwd")

        assert fetched == [], "非法会话名不许先把页面抓下来再解释"
        assert out.status == 0 and out.content_ok is False
        assert out.error.startswith("invalid_request: session_id=")
        assert "1-64 characters" in out.error
        assert "ANONYMOUSLY" in out.next_action, "拒的理由正是那次静默降级"
        assert out.session_id == ""

    @pytest.mark.asyncio
    async def test_a_well_shaped_id_still_runs(self, monkeypatch):
        """反向守卫：闸门只拦形状，不拦使用。"""
        async def fake_get(self, url, **kwargs):
            return _html_result(url)
        monkeypatch.setattr(MasterFetchServer, "get", fake_get)
        out = await MasterFetchServer().web_read(ME, cache_ttl=0, session_id="qa.ok-1_2")
        assert out.status == 200
        assert out.session_id == "qa.ok-1_2"

    def test_the_cache_key_separates_sessions(self):
        """A 会话取回的正文不能被 B 的回放命中——那等于把一个人的页面端给另一个人。"""
        a = _cache_context({"session_id": "alice"})
        b = _cache_context({"session_id": "bob"})
        assert a and b and a != b
        assert _cache_context({}) == ""


# ─── 清得掉 ──────────────────────────────────────────────────────────

class TestTheJarCanBeForgotten:

    @pytest.mark.asyncio
    async def test_the_state_tool_forgets_the_jar(self):
        """17.0 B5-12：清缓存不再顺手抹凭据（那是 cache_clear(all=true) 的谎），
        抹登录态是 sessions 这一类自己的事。"""
        sessions.store("bob", ME, {"sid": "abc"})
        out = await MasterFetchServer().sys_state(clear=["sessions"], confirm=True)
        assert sessions.header_for("bob", ME) == {}
        assert "cookie" in out.message.lower()

    @pytest.mark.asyncio
    async def test_naming_a_session_forgets_the_jar_even_without_a_browser(self):
        """工具以前只认浏览器会话；只有 jar 的 id 会先抛「not found」，
        cookie 就留在磁盘上了。"""
        sessions.store("bob", ME, {"sid": "abc"})
        out = await MasterFetchServer().sys_state(clear=["bob"])
        assert sessions.header_for("bob", ME) == {}
        assert "cookie" in out.message.lower()

    @pytest.mark.asyncio
    async def test_an_unknown_id_is_answered_in_the_envelope(self):
        """以前这里 `raise ValueError("Session 'x' not found.")`。

        接上 wire 之后改成信封里的 `error` + 一句列出**确实开着**的 id：一个只会
        抛的工具名到客户端只剩一句文本，而这条错误的价值全在清单上——调用方要的
        是「那我该关哪个」，不是「你给的名字不存在」。
        """
        sessions.store("real-one", ME, {"sid": "abc"})
        out = await MasterFetchServer().sys_state(clear=["never-existed"])
        assert out.error.startswith("Nothing matches 'never-existed'")
        assert "real-one" in out.error
        assert out.closed == 0 and out.cookies_forgotten == 0
        assert sessions.header_for("real-one", ME) == {"sid": "abc"}, "报错不该顺手关掉别人"
