"""每一颗 wire 参数用错类型去传，到达的都必须是**一句人话**（2026-10-08 真实使用模拟 · 刀11）

起因是评审清单上一条小的："engines / path_include / path_exclude 是列表却读着像字符串"。
把它做成一次全参数扫（`.scratch/sweep_ascii.py`：111 颗参数各喂一个类型不对的值，
走真 SDK 回路）之后，那条小的其实是一整面的塌方，四种坏法都在场上：

  * **设计过的拒绝从 transport 兜底裸逃**（刀1 同一类，换了出口）：约 20 颗参数的
    句子本来就写完了 —— `confirm must be a boolean, got 'one'. Pass true or false
    (default false).` —— 却整条只回一个裸 `{"error": ...}`：没有 summary、没有
    next_action、没有 content_ok。最需要出路的一条失败反而一个字段都没有。
  * **Python 内部话当答案**：`doc_parse(file_path=7)` → `'int' object has no attribute
    'strip'`；`web_request(url=7)`、`web_crawl(resume_token=7)`、`sys_guide(topic=7)`
    同病。那句话是真的，只是没人读得懂，也不指出是哪一颗参数写错了。
  * **pydantic 把响应模型的校验漏到线上**：`web_read(url=7)` →
    `1 validation error for ResponseModel / url / Input should be a valid string …`。
    工具一路跑到底、在**装填响应**时才炸 —— 类型问题被拖成一次真抓取的副作用。
  * 另有两颗**连类型对的时候都裸逃**的设计拒绝：`web_search(mode='bogus')` 与
    `freshness='bogus'` —— 句子写完了（`Invalid mode: 'bogus' (auto|neural|find_similar)`），
    出口没接上。

契约（本文件钉住，扫的对象是 `_TOOL_DEFS` 自己，新增参数自动在册）：
  * 只要回来的带 `error`，就必须同时带 `next_action` —— 哪颗错了、该改成什么、下一步做什么。
    成功的那条路三格都齐，失败反而更瘦是没道理的。
    【评审 R02-103 更正这一条的适用范围 · 原判据本身带着它守的洞】它原来还要求每一扇的拒绝
    都带 `summary` 与 `content_ok=False`。量在 `review/.runtime/p1/r184_state_door_shapes.json`：
    `SysStateModel` / `CredDefaultsModel` **没有**这两格（模型里没有的字段不可能出现在该扇的
    成功回执里，sys_state 成功交 3 格、cred_defaults 交 4 格），于是这两扇唯一的满足方式是让
    拒绝穿一副它成功时从不穿的衣服 —— r183 量到的正是那样：13 格、带着 `content` /
    `duration_ms` / `fetcher_used`。那正是 R02-103 要修的漂移，所以这里把"要 summary /
    content_ok"的适用范围从"每一扇"更正为"模型里真有这两格的每一扇"，并给那两扇换上更强的
    正面锁：拒绝回执的格集合必须是**自己模型的子集**。
    其余各格一条没松：前缀、点名那颗参数、出路在、一个包都没发。
  * 共用类型门（`params._coerce_*`）拒掉的那一批走标准 `invalid_request` 信封：`error`
    以 `invalid_request:` 开头、点名那颗参数、`content_ok` false（该扇模型里有这一格时）。
    住在 `search.py` 的两颗枚举拒绝按老规矩以参数名/值开头（`test_the_enum_argument_values_are_checked_by
    _dhole` 立的），这里只补它们缺的那格出路。
  * 那句话里不许出现 Python 内部话（`has no attribute` / `validation error for` /
    `Traceback` / `pydantic_core`）。
  * 门在任何一条抓取之前：每一颗被拒的调用都断言"没打到靶站"（靶站是回环的）。
  * sys_guide 是唯一按设计回纯文本的工具（它交的就是手册），只受"不许念内部话"约束。
  * 按设计接受该形状的参数（`web_crawl.path_include="docs"` 就是一个前缀）不算洞。
"""

from __future__ import annotations

import http.server
import json
import socketserver
import threading

import pytest

from dhole_mcp.models import CredDefaultsModel, SysStateModel
from dhole_mcp.server import MasterFetchServer

# 有"自己一副模型"的非抓取门（评审 R02-103）。这两扇按自己的模型交拒绝，
# 抓取壳里的那两格对它们无意义：`content_ok` 问的是"抓回来的内容行不行"，
# 而这一扇什么内容都没抓。tests/test_a_state_door_refuses_in_its_own_shape.py
# 按门名钉这两扇确实交这副形状，本表不是它们唯一的证人。
OWN_MODEL_DOORS = {'sys_state': SysStateModel, 'cred_defaults': CredDefaultsModel}

INTERNAL_TALK = ("has no attribute", "validation error for", "Traceback",
                 "pydantic_core", "object of type", "is not defined",
                 # 兜底那封也是内部话：它把一次调用方的类型错说成服务器的事，还教人重试
                 # （R02-091 的现场就是这一句 + `invalid literal for int()`）
                 "invalid literal for", "Internal error, not a blocked site",
                 "retry the same call once", "traceback is in the server log")

PAGE = (b"<html><head><title>Fixture</title></head><body><p>plain fixture page</p>"
        b"</body></html>")


class _Handler(http.server.BaseHTTPRequestHandler):
    hits: list = []

    def _answer(self):
        _Handler.hits.append(self.path)
        body = PAGE
        if self.path.endswith("robots.txt"):
            body = b"User-agent: *\nAllow: /"
        self.send_response(200)
        self.send_header("content-type", "text/" + ("plain" if "robots" in self.path
                                                    else "html"))
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = _answer

    def log_message(self, *a):
        pass


def _wrong_value(spec: dict):
    """一颗与 schema 声明相反的值：array/integer/number/boolean/object←字符串，string←数字。

    这一支从前对 `integer`/`number` 交回的是 **7** —— 一颗合法的数字。也就是说这张扫表
    从来没反类型地扫过整数与数字那两面（只有手抄的 `WRONG_TYPE_CASES` 撞过字符串），
    于是 `web_discover(max_pages='…')` 那两颗洞在它眼皮底下活了一整轮（评审 R02-091 ·
    半径见 `review/.runtime/p1/r102_wrongtype_sweep.json`）。改成真相反的值，并把兜底
    那封的句子也列进 `INTERNAL_TALK`：那句 "Internal error … retry the same call once"
    教的是重试一颗永远不该重试的错，它本身就是洞的形状。
    """
    t = spec.get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), "string")
    return {"array": "one", "integer": "one", "number": "one", "string": 7,
            "boolean": "one", "object": "one"}.get(t or "string", "one")


def _anchors(tool: str, origin: str) -> dict:
    """让每一次"错一颗"都停在门边的最小真参数。

    这些锚点本身必须是**该工具真收的参数**：带一颗陌生键进去，`_reject_unknown_args`
    会先于被测的那颗拒绝掉，整条就成了假绿（feed_read 收 `urls=[...]`，不收 `url=`）。
    """
    if tool in ("web_read", "web_act", "web_crawl", "web_discover", "web_request"):
        return {"url": origin}
    if tool == "feed_read":
        return {"urls": [origin]}
    if tool == "web_search":
        return {"query": "fixture"}
    if tool == "doc_parse":
        return {"file_path": "nosuch-fixture.txt"}
    return {}


def _cases():
    for td in MasterFetchServer._TOOL_DEFS:
        for name, spec in td["inputSchema"]["properties"].items():
            yield td["name"], name, spec


@pytest.fixture(scope="module")
def origin():
    _Handler.hits = []
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/page"
    srv.shutdown()


@pytest.fixture(autouse=True)
def _the_test_origin_is_fetchable():
    from dhole_mcp import identity
    identity.set_value("allow_private", True)


@pytest.mark.asyncio
async def test_every_wrong_typed_argument_arrives_as_a_sentence(origin):
    """每一颗参数各喂一个反类型值：一次会话跑完，坏的那几颗一起报。

    每一颗**被拒的**参数都验过"没发包"：URL 一律指向回环靶站，一次类型错误若还打到了
    它，那就不只是说法问题。按设计接受该形状的那些（`path_include="docs"` 就是一个前缀）
    会真去抓一页 —— 那是它在干活，不算洞。
    """
    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport

    transport = InMemoryTransport(MasterFetchServer()._mcp_server())
    bad: list[str] = []
    async with transport as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            for tool, param, spec in _cases():
                args = dict(_anchors(tool, origin))
                args[param] = _wrong_value(spec)
                mark = len(_Handler.hits)
                try:
                    res = await session.call_tool(tool, args)
                    text = "".join(getattr(b, "text", "") for b in res.content)
                except Exception as exc:  # noqa: BLE001 - 边界抛异常出来也是洞
                    bad.append(f"{tool}.{param}: raised at boundary "
                               f"{type(exc).__name__}: {exc}")
                    continue
                for talk in INTERNAL_TALK:
                    if talk in text:
                        bad.append(f"{tool}.{param}: 念的是 Python 内部话 "
                                   f"({talk}) {text[:120]}")
                        break
                else:
                    if tool == "sys_guide":
                        if not text.strip():
                            bad.append(f"{tool}.{param}: 空手回话")
                        continue
                    try:
                        payload = json.loads(text)
                    except Exception:  # noqa: BLE001
                        bad.append(f"{tool}.{param}: 到的不是 JSON: {text[:120]}")
                        continue
                    if not payload.get("error"):
                        continue
                    if len(_Handler.hits) != mark:
                        bad.append(f"{tool}.{param}: 拒绝了却还发了 "
                                   f"{len(_Handler.hits) - mark} 次请求")
                    if not payload.get("next_action"):
                        bad.append(
                            f"{tool}.{param}: 拒绝只说了哪错了，没说出路"
                            f" {str(payload.get('error'))[:120]}")
                    if tool in OWN_MODEL_DOORS:
                        # 这一族按自己模型交拒绝（R02-103）：要钉的是"不许越出那副模型"，
                        # 而不是"要带抓取壳里的某一格"。
                        extra = sorted(set(payload) - set(OWN_MODEL_DOORS[tool].model_fields))
                        if extra:
                            bad.append(f"{tool}.{param}: 拒绝带着本门模型以外的格 {extra}")
                    elif not payload.get("summary"):
                        bad.append(
                            f"{tool}.{param}: 拒绝到达时没有 summary"
                            f" {str(payload.get('error'))[:120]}")
    assert not bad, "用错类型的参数换来的不是人话:\n" + "\n".join(bad)


#: 类型错的那一颗必须是"这条参数写错了"的信封：点名它、给出该写的形状、什么都没发。
WRONG_TYPE_CASES = [
    ("web_search", "engines", "one"),
    ("web_search", "exclude_sites", "one"),
    ("web_search", "fetch_content", "one"),
    ("web_search", "max_results", "many"),
    ("web_act", "actions", "one"),
    ("web_act", "confirm", "one"),
    ("web_act", "headed", "one"),
    ("web_act", "extraction_type", 7),
    ("web_act", "anti_detect", 7),
    ("feed_read", "urls", "one"),
    ("feed_read", "max_items", "many"),
    ("feed_read", "since", 7),
    ("cred_defaults", "set", "one"),
    ("cred_defaults", "unset", "one"),
    ("cred_defaults", "show", "one"),
    ("sys_state", "show", "one"),
    ("sys_state", "clear", "one"),
    ("sys_state", "confirm", "one"),
    ("web_read", "urls", "https://example.com"),
    ("web_read", "max_content_chars", "big"),
    ("web_crawl", "force_fetcher", 7),
    ("web_crawl", "ignore_robots", "one"),
    ("web_request", "confirm", "one"),
    ("doc_parse", "offset", "many"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,param,value", WRONG_TYPE_CASES)
async def test_a_type_mistake_names_its_parameter_and_sends_nothing(
        tool, param, value, origin):
    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport

    args = dict(_anchors(tool, origin))
    args[param] = value
    mark = len(_Handler.hits)
    transport = InMemoryTransport(MasterFetchServer()._mcp_server())
    async with transport as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            res = await session.call_tool(tool, args)
    text = "".join(getattr(b, "text", "") for b in res.content)
    payload = json.loads(text)
    error = str(payload.get("error") or "")
    assert error.startswith("invalid_request"), (
        f"{tool}.{param}={value!r} 从 transport 兜底裸逃: {text[:200]}")
    assert param in error, f"那句拒绝没点名是哪颗参数: {error}"
    assert payload.get("next_action"), f"拒绝只说了哪错了，没说出路: {payload}"
    if tool in OWN_MODEL_DOORS:
        # 【评审 R02-103】这两扇的模型没有 summary / content_ok 两格（r184：成功回执
        # 3 格 / 4 格）。从前这两行断言靠的是拒绝**借**抓取壳 —— 换个形状说话，正是这条
        # 要修的洞。如今钉的是更硬的一面：格集合必须是本门模型的子集。
        own = set(OWN_MODEL_DOORS[tool].model_fields)
        assert set(payload) <= own, (
            f"{tool} 的类型拒绝穿着本门以外的格: {sorted(set(payload) - own)}")
        assert "content_ok" not in payload and "duration_ms" not in payload, payload
    else:
        assert payload.get("summary"), f"拒绝到达时没有 summary: {payload}"
        assert payload.get("content_ok") is False, payload
    assert "has no attribute" not in text and "validation error for" not in text
    assert len(_Handler.hits) == mark, "一句类型拒绝却把包发出去了"


@pytest.mark.asyncio
async def test_a_bad_enum_value_is_not_an_exception_story():
    """两颗连类型对的时候都裸逃的：mode / freshness —— 句子写完了，缺的是出口。

    这两句住在 `search.py`（一处只留一份表：`_IMPLEMENTED_MODES` 就是 mode 的定义），
    交回的是 `SearchResponseModel` 而不是 `_invalid_request_result`，所以它们的 `error`
    以参数名/值开头而不套 `invalid_request:` 前缀 —— 那是
    `test_the_enum_argument_values_are_checked_by_dhole` 立的规矩。这里要钉的是另外三件：
    出路在、标签在、一次都没发包。
    """
    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport

    for arg, value, others in (("mode", "bogus", ("auto", "neural", "find_similar")),
                               ("freshness", "bogus", ("day", "week", "month", "year"))):
        transport = InMemoryTransport(MasterFetchServer()._mcp_server())
        async with transport as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                res = await session.call_tool(
                    "web_search", {"query": "fixture", arg: value})
        payload = json.loads("".join(getattr(b, "text", "") for b in res.content))
        error = str(payload.get("error") or "")
        assert value in error, payload
        for legal in others:
            assert legal in error, f"{arg} 的拒绝没列全合法值: {error}"
        assert payload.get("next_action"), f"{arg} 的拒绝只说错，没说出路: {error}"
        assert payload.get("summary"), f"{arg} 的拒绝没有标签可读: {payload}"
        assert payload.get("duration_ms") == 0, "参数拒绝不该已经搜过一次"
