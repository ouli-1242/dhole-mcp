"""工具描述与 instructions 的契约测试。

为什么描述需要测试：描述是 agent 判断「该用哪个工具」的唯一依据。路由规则
写漏了不会报错 —— agent 只会静默选错工具（多花一次调用、多烧一份 token），
或者被描述引导去走一条被代码硬拦的路径。所以描述里的事实必须和代码一致，
关键路由规则必须有守卫。

这里刻意不依赖 tiktoken：用字符数当预算代理，够用且零依赖。
"""

import json
import re

import pytest
from mcp.types import Tool

from dhole_mcp.search_engines import DEFAULT_ENGINES
from dhole_mcp.server import DHOLE_INSTRUCTIONS, MasterFetchServer


def _tools() -> dict[str, dict]:
    return {td["name"]: Tool(**td).model_dump(by_alias=True, exclude_none=True)
            for td in MasterFetchServer._TOOL_DEFS}


@pytest.fixture(scope="module")
def tools() -> dict[str, dict]:
    return _tools()


def _desc(tools: dict[str, dict], name: str) -> str:
    return tools[name]["description"]


# ─── 路由契约：这些规则一旦丢失，agent 会选错工具 ──────────────────────
#
# 每条都对应一个真实的误用场景，不是泛泛的「描述要写清楚」。

ROUTING_CONTRACT = [
    ("smart_fetch", "smart_crawl",
     "list page 时要把 agent 导向 smart_crawl，否则它会一个个 fetch"),
    ("smart_crawl", "smart_fetch(urls=",
     "已有 URL 列表时必须把 agent 推回 smart_fetch，否则会为一个已知清单白爬一遍"),
    ("smart_search", "fetch_content",
     "不知道 fetch_content 的 agent 会多花 N 次 smart_fetch 去取正文"),
    ("smart_search", "smart_fetch",
     "搜索结果只是 URL，必须说清下一步是取正文"),
    ("screenshot", "smart_fetch",
     "文本 agent 不该调截图，要明确让位给 smart_fetch"),
    ("parse", "smart_fetch",
     "本地 PDF 只有 smart_fetch 能处理（且仅限 URL 形态），必须指明"),
    ("feed_fetch", "smart_fetch",
     "feed_fetch 不是通用抓取器，必须说明何时该换 smart_fetch"),
    ("cache_clear", "cache_ttl=0",
     "清缓存不是刷新单页的手段，要导向 cache_ttl=0"),
]


@pytest.mark.parametrize("tool,needle,why", ROUTING_CONTRACT,
                         ids=[f"{t}-{n}" for t, n, _ in ROUTING_CONTRACT])
def test_routing_rule_present(tools, tool, needle, why):
    assert needle in _desc(tools, tool), f"{tool} 描述缺少路由规则（{why}）"


def test_instructions_routes_known_url_list_to_fetch(tools):
    """instructions 是跨工具的入口表，必须区分「已有 URL」和「需先发现 URL」。"""
    low = DHOLE_INSTRUCTIONS.lower()
    assert "smart_fetch" in low and "smart_crawl" in low
    assert "already have" in low, "未说明「已有 URL 用 smart_fetch」"
    assert "don't have the urls" in low, "未说明「需先发现 URL 才用 smart_crawl」"


# ─── 事实性：描述里的声明必须和代码一致 ────────────────────────────────

def test_engine_count_in_instructions_matches_real_pool():
    """instructions 曾写「searches 5 engines」，而默认池实际是 6 个。

    这个数字是人手写的，没有守卫就会一直漂。改成从常量算。
    """
    assert f"searches {len(DEFAULT_ENGINES)} engines" in DHOLE_INSTRUCTIONS


def test_search_description_lists_the_real_default_pool(tools):
    desc = _desc(tools, "smart_search")
    missing = [e for e in DEFAULT_ENGINES if e not in desc]
    assert not missing, f"描述里的默认引擎池与 DEFAULT_ENGINES 不符，缺: {missing}"


def test_parse_advertises_local_pdf(tools):
    """本地 PDF 必须真的可用，且被如实声明。

    历史：`SUPPORTED_EXTENSIONS` 一直含 `.pdf`，但实现只返回一句
    「用 smart_fetch(url='file://...')」—— 而 file:// 被 SSRF 守卫硬拦、裸路径
    也不是合法 URL，于是本地 PDF 实际无路可走，agent 照描述走会白烧两次调用。
    现在 parse 自己解析，描述与参数说明都必须写明。
    """
    desc = _desc(tools, "parse")
    assert ".pdf" in desc
    assert "NOT supported" not in desc
    props = tools["parse"]["inputSchema"]["properties"]
    assert ".pdf" in props["file_path"]["description"]


def test_no_description_recommends_the_file_scheme(tools):
    """file:// 在 security._BLOCKED_SCHEMES 里，任何把 agent 往那儿引的措辞都是 bug。"""
    offenders = [name for name, t in tools.items() if "file://" in t["description"]]
    assert not offenders, f"这些工具描述仍推荐 file://（会被 SSRF 守卫拒绝）: {offenders}"


def test_parse_implements_pdf_locally():
    """parse.py 不能只是「提到」PDF —— 必须真的走提取器。

    （提到 file:// 本身没问题：新提示解释它为什么被拒；有问题的是把 file://
    当成可用做法推荐出去。）
    """
    from dhole_mcp import parse as parse_mod

    import inspect
    src = inspect.getsource(parse_mod)
    assert "smart_fetch(url='file://" not in src, "parse.py 仍推荐 file:// 用法"
    assert "extract_pdf" in src, "parse.py 没有真的解析 PDF"


# ─── 一致性：描述里提到的参数必须真的存在 ──────────────────────────────

# `foo=` 形式出现、但属于响应字段或其它工具参数的名字，不算「参数引用」。
_NOT_A_PARAM = {
    "next_offset",     # 响应字段
    "end_page",        # table_of_contents 的字段
    "page",            # 同上（PDF 目录项字段，非本工具参数）
    "example",         # 文档用词
}

_PARAM_REF = re.compile(r"\b([a-z][a-z0-9_]{2,})=")
_OPTION_KEY = re.compile(r"\b([a-z][a-z0-9_]{2,})\s*[,(]")


def _option_bag_keys(tool: dict) -> set[str]:
    opts = tool["inputSchema"].get("properties", {}).get("options")
    if not opts:
        return set()
    return set(_OPTION_KEY.findall(opts.get("description", "")))


@pytest.mark.parametrize("name", sorted(_tools()))
def test_params_named_in_description_exist(tools, name):
    """描述说 `foo=...`，就得真有某个工具接受 foo。

    防的是「描述承诺了一个不存在的参数」—— agent 会照着传，然后被
    _strict_options 拒绝或静默忽略。跨工具引用是合法的（smart_crawl 的
    描述里写 smart_fetch(urls=[...])），所以已知集合取全部工具的并集。
    """
    known = set(_NOT_A_PARAM)
    for other in tools.values():
        known |= set(other["inputSchema"].get("properties", {}))
        known |= _option_bag_keys(other)
    referenced = set(_PARAM_REF.findall(_desc(tools, name)))
    unknown = sorted(referenced - known)
    assert not unknown, f"{name} 描述引用了不存在的参数: {unknown}"


# ─── 预算：描述不能悄悄变胖 ────────────────────────────────────────────
#
# 上限设在当前值之上留约 10% 余量：正常改动用不着调，真要加东西就得
# 显式改这个表 —— 让「描述变胖」变成一个有意识的动作。

CHAR_BUDGET = {
    "smart_fetch": 4400,
    "smart_crawl": 2500,
    "smart_search": 2400,
    "screenshot": 900,
    "feed_fetch": 900,
    "resolve_url": 700,
    "parse": 700,
    # 14.6: 550 -> 860. cache_clear gained the engine_state lever (reset engine
    # cooldowns / yield) plus its "when to use it" line. Without a tool-visible
    # reset, a user whose network changed had only "delete files under ~/.dhole
    # and restart" - which is exactly how a working VPN got misdiagnosed as
    # ignored. parse's 700 was already enough for its path-resolution note.
    "cache_clear": 860,
}
TOOLS_TOTAL_BUDGET = 12500
INSTRUCTIONS_BUDGET = 1500
CONNECT_TOTAL_BUDGET = 14000


@pytest.mark.parametrize("name", sorted(CHAR_BUDGET))
def test_tool_within_char_budget(tools, name):
    size = len(json.dumps(tools[name], ensure_ascii=False))
    assert size <= CHAR_BUDGET[name], (
        f"{name} 的 wire 体积 {size} 超过预算 {CHAR_BUDGET[name]}。"
        f"要么精简，要么显式上调 CHAR_BUDGET 并说明理由。"
    )


def test_tools_list_within_char_budget(tools):
    total = sum(len(json.dumps(t, ensure_ascii=False)) for t in tools.values())
    assert total <= TOOLS_TOTAL_BUDGET, f"tools/list 合计 {total} > {TOOLS_TOTAL_BUDGET}"


def test_instructions_within_char_budget():
    assert len(DHOLE_INSTRUCTIONS) <= INSTRUCTIONS_BUDGET


def test_connect_time_total_within_char_budget(tools):
    """每次 MCP 连接都要付的成本：instructions + 全部工具定义。"""
    total = sum(len(json.dumps(t, ensure_ascii=False)) for t in tools.values())
    total += len(DHOLE_INSTRUCTIONS)
    assert total <= CONNECT_TOTAL_BUDGET, f"connect-time 合计 {total} > {CONNECT_TOTAL_BUDGET}"


# ─── 基本卫生 ──────────────────────────────────────────────────────────

def test_every_tool_has_a_substantive_description(tools):
    thin = [n for n, t in tools.items() if len(t["description"]) < 80]
    assert not thin, f"这些工具描述过短，agent 无法据此判断用途: {thin}"


def test_descriptions_have_no_stray_whitespace(tools):
    """描述里不该有行尾空格或连续空行 —— 它们是纯 token 浪费。"""
    for name, t in tools.items():
        desc = t["description"]
        assert not re.search(r"[ \t]+\n", desc), f"{name} 描述有行尾空格"
        assert "\n\n\n" not in desc, f"{name} 描述有连续空行"
