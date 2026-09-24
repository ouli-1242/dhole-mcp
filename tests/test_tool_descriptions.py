"""工具描述与 instructions 的契约测试。

为什么描述需要测试：描述是 agent 判断「该用哪个工具」的唯一依据。路由规则
写漏了不会报错 —— agent 只会静默选错工具（多花一次调用、多烧一份 token），
或者被描述引导去走一条被代码硬拦的路径。所以描述里的事实必须和代码一致，
关键路由规则必须有守卫。

这里刻意不依赖 tiktoken：用字符数当预算代理，够用且零依赖。
"""

import inspect
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


_ENGINE_TOKEN = re.compile(r"[a-z][a-z0-9_]*")


def _engine_tokens(text: str) -> set[str]:
    """按**整词**取标识符，而不是子串。

    不能用 `"sogou" in text` —— `sogou` 是 `sogou_weixin` 的子串、`bing` 是
    `bing_global` 的子串，子串匹配会让「只列了 sogou_weixin」被读成「也列了
    sogou」。与 `_extensions_mentioned` 同一类陷阱。
    """
    return set(_ENGINE_TOKEN.findall(text.lower()))


def test_search_engine_list_is_never_partial(tools):
    """`smart_search` 里**每一处**点名的 opt-in 引擎清单，都必须点全。

    实测漂移（15.1 裁剪时发现）：描述写 "opt-in baidu_baike,bing_global,mwmbl,
    wikipedia,grokipedia"（**5 个**），而 `search_engines._INDEX_FAMILY` 注册
    14 个后端 = 6 默认 + **8** opt-in —— 漏了 `so360` / `sogou` /
    `sogou_weixin`。调用方读描述会以为 opt-in 池只有 5 个。

    这是 `SUPPORTED_EXTENSIONS` 那类「同一份名单手写两遍」的翻版，所以守卫
    不去比对两份手写清单，而是**从注册表反查载荷**。

    **必须逐个位置判，不能把整份载荷当一个字符串扫** —— 这是写下第一条版本
    时的真实错误：`options` 包里列全了 8 个，把描述里那 5 个"补"成了 8，
    于是整载荷扫法对原始 bug 静默通过（变异实验实测）。每个位置各自满足
    「要么为空，要么等于注册表」才算过。
    """
    from dhole_mcp.search_engines import _INDEX_FAMILY

    registry = set(_INDEX_FAMILY)          # 注册表：14 个后端
    opt_in = registry - set(DEFAULT_ENGINES)

    # 机制自检：整词匹配不该把 sogou_weixin 读成 sogou
    assert _engine_tokens("sogou_weixin only") & opt_in == {"sogou_weixin"}

    t = tools["smart_search"]
    locations = {
        "description": t["description"],
        "options.engines": t["inputSchema"]["properties"]["options"]["description"],
    }
    partial: dict[str, list[str]] = {}
    for where, text in locations.items():
        claimed = _engine_tokens(text) & opt_in
        if claimed and claimed != opt_in:   # 空 = 没写清单，放行
            partial[where] = sorted(claimed)
    assert not partial, (
        "smart_search 有位置点名了 opt-in 引擎清单但与注册表不符"
        "（漏列会让调用方以为池子更小）。\n"
        f"  注册表里: {sorted(opt_in)}\n"
        f"  不符处: {partial}\n"
        "要么补全，要么删掉该处清单改为指向 options.engines（推荐 —— 名单只留一份）。"
    )


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


def _extensions_mentioned(text: str) -> set[str]:
    """从描述里取出被提到的扩展名。

    不能用 `".htm" in text` —— `.htm` 是 `.html` 的子串，只写 `.html` 的描述
    会被判成「提过 .htm」（`.xhtml` 同理在放宽的匹配下会互相冒充）。按 token
    切出来比较，才不会被前缀吞掉。
    """
    return set(re.findall(r"\.[a-z0-9]{1,8}", text))


def test_parse_advertises_every_supported_extension(tools):
    """扩展名清单在 parse.py 与描述里各写一份 —— 又一处「同一份名单多处定义」。

    实测漂移：`SUPPORTED_EXTENSIONS` 含 `.htm`/`.xhtml`，工具描述只写到 `.html`，
    file_path 的说明连 `.htm` 都没有。用户手里是 `.xhtml` 文件时会以为 parse 读不了。
    守卫按**代码里的集合**反查描述，而不是比对两份人手写的清单。
    """
    from dhole_mcp.parse import SUPPORTED_EXTENSIONS

    # 机制自检：`.html` 不该顶替 `.htm`（子串匹配在这里会静默失效）
    assert _extensions_mentioned("only .html here") == {".html"}

    desc = _desc(tools, "parse")
    prop = tools["parse"]["inputSchema"]["properties"]["file_path"]["description"]
    for where, text in (("描述", desc), ("file_path 说明", prop)):
        missing = sorted(SUPPORTED_EXTENSIONS - _extensions_mentioned(text))
        assert not missing, f"parse 的{where}未列出支持的扩展名: {missing}"


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


# ─── 体积：不再设守卫 ──────────────────────────────────────────────────
#
# 这里曾有 CHAR_BUDGET（逐工具字符上限）+ TOOLS_TOTAL_BUDGET /
# INSTRUCTIONS_BUDGET / CONNECT_TOTAL_BUDGET 四道预算。已删除。
#
# 删除理由：它们的期望值是**人手在每次改描述后重新推导的实测值**，属于
# 刻舟求剑 —— 正常改一句描述就会红，红完只能去改常量。守卫拦下的不是
# 缺陷，是「描述和上次不一样」。代价则由每次编辑承担，收益（防缓慢变胖）
# 远低于成本。要防变胖，看 connect-time 总量的量级即可，不必钉死数字。
#
# 保留下来的描述守卫分两类，改描述时的代价完全不同：
#
# 1. **从代码反查描述**（期望值由常量算出，改描述零成本）：
#    test_search_engine_list_is_never_partial、
#    test_parse_advertises_every_supported_extension、
#    test_engine_count_in_instructions_matches_real_pool、
#    test_params_named_in_description_exist。这类是纯收益，留着。
#
# 2. **文本 needle**（改措辞就可能红）：ROUTING_CONTRACT、
#    test_instructions_routes_known_url_list_to_fetch、以及各 report 回归文件里
#    「描述必须出现 X」的断言。它们锁的是**路由规则和对外事实**（比如
#    max_total_chars 的硬顶必须写出来，值由 crawl.py 常量反查），措辞变了要人工判断是不是
#    真丢了这条信息 —— 这是有意的摩擦，不是刻舟求剑，故保留。


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


# ─── docstring / wire 双写守卫 ─────────────────────────────────────────
#
# 客户端收到的是 _TOOL_DEFS 的 description；工具方法的 docstring **不上
# wire**（全项目零处消费 __doc__，见 server.py 中 _TOOL_DEFS 上方的注释）。
# 两边没有同步机制，于是 docstring 必然腐坏 —— screenshot 的 :param: 块曾把
# 早已搬进 `options` 的键描述成顶层参数，读源码的人会照着写错。
#
# 这条守卫不要求两边内容一致（wire 是刻意瘦身的，docstring 更长）。它只要
# 求一件事：**docstring 里的 snake_case 标识符，若在整个 wire 载荷里找不到，
# 就必须登记在下面的集合里**。新增的漂移必须显式登记，不能默默出现 ——
# 这正是「改了 docstring 以为生效」的拦截点。

_SNAKE_CASE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")

# 实现细节 / 非契约：本来就不该出现在 agent 可见的载荷里。
_DOCSTRING_ONLY_IMPLEMENTATION = {
    "curl_cffi",        # HTTP 层用的引擎名
    "circuit_breaker",  # ~/.dhole 下的状态文件名
    "engine_stats",     # 同上
}

# 跨工具引用：指向别处，不是本工具的契约。
_DOCSTRING_ONLY_CROSS_REFERENCE = {
    "smart_fetch", "smart_search",
}

# 刻意不在 wire 暴露的诊断字段：暴露的边际价值低于其 token 成本（agent 基本
# 不会基于 duration_ms / total_size_bytes 做决策）。15.1 已把 escalation_path、
# source_type、is_official 搬进 wire —— 它们影响"要不要引用这份内容"。
_RESPONSE_FIELDS_NOT_ON_WIRE = {
    "content_type", "duration_ms", "total_size_bytes",
}

_ALLOWED_DOCSTRING_ONLY = (
    _DOCSTRING_ONLY_IMPLEMENTATION
    | _DOCSTRING_ONLY_CROSS_REFERENCE
    | _RESPONSE_FIELDS_NOT_ON_WIRE
)


def _docstring_identifiers(name: str) -> set[str]:
    doc = inspect.getdoc(getattr(MasterFetchServer, name)) or ""
    return set(_SNAKE_CASE.findall(doc))


def test_docstring_identifiers_are_on_the_wire_or_registered(tools):
    """docstring 里出现 wire 上看不到的标识符 = agent 与开发者看到的事实不同。

    拦截场景：往 docstring 里写了一个新参数/新字段，以为客户端能看到 ——
    实际看不到。要么搬进 _TOOL_DEFS，要么登记并说明为什么它是内部细节。
    """
    unregistered: dict[str, list[str]] = {}
    for name, t in tools.items():
        on_wire = json.dumps(t, ensure_ascii=False).lower()
        stray = sorted(i for i in _docstring_identifiers(name)
                       if i not in on_wire and i not in _ALLOWED_DOCSTRING_ONLY)
        if stray:
            unregistered[name] = stray
    assert not unregistered, (
        "docstring 里有 wire 上看不到的标识符，且未登记。\n"
        "要么搬进 _TOOL_DEFS 的描述（agent 需要知道），要么加进 "
        "_DOCSTRING_ONLY_* 白名单并写明理由。\n"
        f"{unregistered}"
    )


def test_allowlist_has_no_dead_entries(tools):
    """白名单不许长草：没人再用的条目要删掉，否则守卫会掩盖真实漂移。"""
    used: set[str] = set()
    for name in tools:
        used |= _docstring_identifiers(name)
    dead = sorted(i for i in _ALLOWED_DOCSTRING_ONLY if i not in used)
    assert not dead, f"白名单里这些标识符已无人使用，应删除: {dead}"
