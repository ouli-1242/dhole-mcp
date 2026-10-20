# -*- coding: utf-8 -*-
"""`web_discover` 在类型归一那张表里根本没有登记 —— 两颗参数因此把 Python 的话当答案（R02-091）。

量到的半径（`review/.runtime/p1/r102_wrongtype_sweep.json` · 61 颗参数各喂一个相反类型的值 ·
真 in-memory MCP 回路 · 靶站回环数得出发包）：**全部 11 扇里只有 2 个洞，都在这一扇门上** ——

    web_discover(max_pages='not-a-number')
      error       = "invalid literal for int() with base 10: 'not-a-number'"
      next_action = "Internal error, not a blocked site: retry the same call once, …"   ← 教人重试
      summary     = ""
    web_discover(max_total_chars='not-a-number')
      error       = "invalid literal for int() with base 10: 'not-a-number'"
      next_action = ""      summary = ""

来路不在 `crawl.py:1665` 那句 `int(max_pages)`：同一颗错在 `web_crawl` 上门是**先**接住的
（`params._ARG_TYPES` 给 web_crawl 列了 `max_pages: "int"`），交回的是
`invalid_request: max_pages must be an integer, got …` + `Nothing was fetched or changed.`。
`_coerce_arg_types` 的开头是 `spec = _ARG_TYPES.get(tool)` / `if not spec: return args` ——
**这一扇压根不在表里，所以整颗门没上过门**，值一路走到引擎里才撞出 Python 的原话。

本文件钉三件事：① 这两颗现在必须在本扇门口被具名拒掉，且句子与 `web_crawl` 那位作者逐字同形；
② 门不许再"不在表里"（每张 wire 扇的每颗 integer/number/boolean/array/object 参数，要么在表里，
要么有一份写明理由的豁免）；③ 修这一处不许把 tri-state 的 `sitemap` 与"裸字符串就是一个前缀"的
`path_include` 一起带进表里 —— 那是 `web_crawl` 的注释里已经写过的两个刻意例外。
"""
from __future__ import annotations

import http.server
import json
import socketserver
import threading

import pytest

from dhole_mcp import params
from dhole_mcp.server import MasterFetchServer

PAGE = (b"<html><head><title>Fixture</title></head><body><p>plain fixture page</p>"
        b"</body></html>")


class _Handler(http.server.BaseHTTPRequestHandler):
    hits: list = []

    def _answer(self):
        _Handler.hits.append(self.path)
        body = b"User-agent: *\nAllow: /" if self.path.endswith("robots.txt") else PAGE
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_HEAD = _answer

    def log_message(self, *a):
        pass


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


async def _call(tool: str, args: dict) -> dict:
    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport
    transport = InMemoryTransport(MasterFetchServer()._mcp_server())
    async with transport as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            res = await session.call_tool(tool, args)
            text = "".join(getattr(b, "text", "") for b in res.content)
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        return {"_not_json": text[:300]}


# ─── 1 · 两颗漏的参数现在必须在门口被具名拒掉 ──────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("arg", ["max_pages", "max_total_chars"])
async def test_the_map_door_names_the_argument_it_cannot_read(origin, arg):
    mark = len(_Handler.hits)
    out = await _call("web_discover", {"url": origin, arg: "not-a-number"})
    err = out.get("error") or ""
    assert err.startswith("invalid_request:"), err
    assert err.startswith(f"invalid_request: {arg} must be an integer"), err
    assert out.get("next_action"), "拒绝了却没给出路"
    assert out.get("summary"), "拒绝了却没有 summary 那一格"
    assert "invalid literal" not in err and "invalid literal" not in out.get("next_action", ""), (
        "Python 的原话还在当答案")
    assert len(_Handler.hits) == mark, "一句类型拒绝却把包发出去了"


@pytest.mark.asyncio
async def test_the_same_mistake_says_the_same_thing_on_both_doors(origin):
    """同一颗错在 `web_discover` 与 `web_crawl` 上必须逐字同形 —— 类型层只有一位作者。

    这一格是 R02-089 那条轴的正面证据之一：修法不许是给 discover 另写一句门话，
    而是让它经过同一位作者。
    """
    mark = len(_Handler.hits)
    on_discover = await _call("web_discover", {"url": origin, "max_pages": "not-a-number"})
    on_crawl = await _call("web_crawl", {"url": origin, "max_pages": "not-a-number"})
    for key in ("error", "next_action"):
        assert on_discover.get(key) == on_crawl.get(key), (
            f"{key} 两扇不同：{on_discover.get(key)!r} vs {on_crawl.get(key)!r}")
    assert len(_Handler.hits) == mark, "两格都该停在门口"


# ─── 2 · 门不许再"不在表里"；豁免要写明理由 ─────────────────────────

#: 刻意不进 `_ARG_TYPES` 的参数，理由与 `params.py` 里 web_crawl 那两条注释同源；每一颗都有
#: 一次真回执为它作保（`review/.runtime/p1/r102_wrongtype_sweep.json` · 61 颗各喂一个相反类型
#: 的值）：这张名单里的每一颗都在**自己的下游作者**那里交回了具名拒绝，所以缺席是决定，不是漏。
_COERCION_EXEMPT: dict[str, tuple[str, ...]] = {
    # schema/nested_*：extract 那一层按 schema 的own形状判（`pair-required` 那道门），
    # `nested_max` 与 `nested_selector` 是一对，判决住在配对检查那里。
    "web_read": ("schema", "nested_fields", "nested_max"),
    # path_include/path_exclude：裸字符串就是"一个前缀"（crawl 在 fetch 之前就这么读，还会上报）
    "web_crawl": ("path_include", "path_exclude"),
    # 同 crawl 的两颗前缀；timeout 这颗本扇门自己有一句具名拒绝
    # （`timeout= Timeout must be a number - nothing was read.` + 1-120000 的那段预算说明），
    # 把它搬进表里只会换掉那句已经更具体的话，不是修缺陷。
    "web_discover": ("path_include", "path_exclude", "timeout"),
    # fetch_schema 的判决住在 fetch 的那条装配里（与 web_read.schema 同一族）
    "web_search": ("fetch_schema",),
    # 名单类参数：cred/sys_state 的 set/show/unset/clear 各有各的具名作者（sys_state 那几颗
    # 在本扫里交回的是 `must be an array of strings, got …`，同样点名、同样给形状）
    "cred_defaults": ("unset", "show"),
    "sys_state": ("show", "clear", "confirm"),
    # urls：feed_read 自己那句 `urls must be an array of strings, got a single string.` 在场
    "feed_read": ("urls",),
    # 空名单也要**点名**：这一扇只有两颗字符串参数（topic/section），边界没有要归一的东西。
    # 逼这一格写下来就是为了 `web_discover` 那种静默 —— `if not spec: return args` 让
    # "表里没有这一扇"与"这一扇没有可归一的参数"在运行时一模一样。
    "sys_guide": (),
}

_CONTAINER_OR_NUMBER = ("integer", "number", "boolean", "array", "object")


def _base_type(spec: dict) -> str:
    t = spec.get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), "string")
    return t or "string"


@pytest.mark.parametrize("tool", [td["name"] for td in MasterFetchServer._TOOL_DEFS])
def test_every_door_is_either_in_the_table_or_explained(tool):
    """每一扇都必须要么在表里、要么把每颗参数点名豁免 —— 否则新增的一扇会静默没门。

    `web_discover` 这一把的教训正是这种静默：`_coerce_arg_types` 的第一行是
    `if not spec: return args`，所以"表里没有这一扇"与"这一扇没有需要归一的参数"
    在运行时是同一个样子。
    """
    spec = params._ARG_TYPES.get(tool)
    props = next(td for td in MasterFetchServer._TOOL_DEFS
                 if td["name"] == tool)["inputSchema"]["properties"]
    if spec is None:
        assert tool in _COERCION_EXEMPT, f"{tool} 既不在表里、也没有一份点名豁免"
        uncovered = [p for p, s in props.items() if _base_type(s) in _CONTAINER_OR_NUMBER]
        assert all(p in _COERCION_EXEMPT[tool] for p in uncovered), (
            f"{tool} 缺席表但 {uncovered} 没有豁免理由")
        return
    exempt = _COERCION_EXEMPT.get(tool, ())
    silent = [p for p, s in props.items()
              if _base_type(s) in _CONTAINER_OR_NUMBER and p not in spec and p not in exempt]
    assert not silent, f"{tool} 这些参数既不在表里也没在豁免名单：{silent}"


def test_the_map_door_now_shares_the_type_table():
    """正面：这一扇在表里，且那两颗是 int（缺席是这把的根因）。"""
    spec = params._ARG_TYPES.get("web_discover")
    assert spec, "web_discover 还是没登记 —— 缺席的门没人守"
    assert spec["max_pages"] == "int" and spec["max_total_chars"] == "int", spec


# ─── 3 · 反向守卫：两个刻意的例外不许被顺手带进表里 ────────────────

def test_the_tri_state_sitemap_is_still_not_forced_through_the_bool_gate():
    """`sitemap='auto'` 是 trI-state 的一种写法，布尔归一会把它杀掉（web_crawl 同注）。"""
    args = params._coerce_arg_types("web_discover", {"url": "http://x/", "sitemap": "auto"})
    assert args["sitemap"] == "auto", args


def test_a_bare_string_prefix_is_still_read_as_the_one_prefix_it_means():
    """`path_include='/docs'` 是一个前缀，不是一串字符 —— 那颗判决住在 crawl 里（同注）。"""
    args = params._coerce_arg_types("web_discover",
                                    {"url": "http://x/", "path_include": "/docs"})
    assert args["path_include"] == "/docs", args


@pytest.mark.parametrize("arg", ["max_pages", "max_total_chars"])
def test_a_stringified_number_is_accepted_not_refused(arg):
    """字符串客户端的 `"10"` 是同一颗数：门要收下来，不是拒掉。"""
    args = params._coerce_arg_types("web_discover", {"url": "http://x/", arg: "10"})
    assert args[arg] == 10, args
