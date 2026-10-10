"""B5-14a：`web_request` —— 写操作从 web_read 里搬出来（§4 第 4 行，第 16 颗工具）。

§4 那张表是目标形状，不是建议：`web_read` 的职责是"读正文"，而 POST/PUT/PATCH/
DELETE/HEAD 是另一件事 —— 它不重试、不进缓存、不升浏览器、不回退快照，因为**重发
就是写两遍**，而浏览器回放答的是同一个 URL 的 GET，不是你的写。这些规矩过去靠
web_read 里一个 `method=` 分支维持，代价是读的那扇门上挂着三颗只服务于"不是读"的
参数。搬出来之后，读的门变小，写的门能把自己的规矩说清楚。

四条不能弯的事实，每条都有用例钉着：
- **一次性**：非 GET 只发一次，答案绝不从缓存或快照来（这在 web_read 里是分支，
  在这颗工具里是定义）；
- **必填**：`url` 与 `method` 缺一个就停，`GET` 归 web_read —— 一句话把调用方送到
  对的门，而不是让它拿到一份"看起来成功、正文是空的"第二次读；
- **旧写法要会说去哪**：`web_read(method=...)` 现在是陌生键，但"陌生键"三个字会把
  人引向"这工具没这个能力"，真相是"能力换了名字"，所以拒答必须点名 web_request
  （`options` 袋子删掉时用的就是这套说法）；
- **身份照旧走存储**：写操作的凭据来自 cred_defaults（§3-17），这颗工具不重开一次
  逐次传凭据的口子。

一处与 §4 的**有意偏离**，也钉在这里：web_request 没有 `anti_detect`。施工图给这
颗工具留了 7 个名字，其中一个就是档位；可写操作永远不进浏览器层（那正是"一次性"
的推论），没有渲染就没有可选的强度。留一颗做不了任何事的参数，就是把 wire 写成谎
—— 6 个名字是这一刀真正的形状，理由写在这里，也写在工具描述里。

第七个名字是后来（G25 评审 Blocker）补上的 `confirm`：写操作出去时带着
cred_defaults 存的凭据，被注入内容诱导的 agent 必须过一道与 submit 同语义的闸。
它必须是 schema 里的已知名——藏在签名里的 confirm 会被 dispatch 当表外名拒掉，
拒绝消息教的重试根本到不了闸门。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from dhole_mcp import identity
from dhole_mcp import server as server_mod
from dhole_mcp.server import MasterFetchServer, ResponseModel

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
        assert server_mod._TOP_LEVEL_ARGS["web_request"] == frozenset({
            "url", "method", "body", "content_type", "extraction_type", "timeout",
            "confirm", "headers"})

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
        assert len(server_mod._TOP_LEVEL_ARGS["web_read"]) == 28, \
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
