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
    ("web_read", "web_crawl",
     "list page 时要把 agent 导向 web_crawl，否则它会一个个 fetch"),
    ("web_crawl", "web_read(urls=",
     "已有 URL 列表时必须把 agent 推回 web_read，否则会为一个已知清单白爬一遍"),
    ("web_search", "fetch_content",
     "不知道 fetch_content 的 agent 会多花 N 次 web_read 去取正文"),
    ("web_search", "web_read",
     "搜索结果只是 URL，必须说清下一步是取正文"),
    ("web_screenshot", "web_read",
     "文本 agent 不该调截图，要明确让位给 web_read"),
    ("doc_parse", "web_read",
     "本地 PDF 只有 web_read 能处理（且仅限 URL 形态），必须指明"),
    ("feed_read", "web_read",
     "feed_read 不是通用抓取器，必须说明何时该换 web_read"),
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
    assert "web_read" in low and "web_crawl" in low
    assert "already have" in low, "未说明「已有 URL 用 web_read」"
    assert "don't have the urls" in low, "未说明「需先发现 URL 才用 web_crawl」"


# ─── 事实性：描述里的声明必须和代码一致 ────────────────────────────────

def test_engine_count_in_instructions_matches_real_pool():
    """instructions 曾写「searches 5 engines」，而默认池实际是 6 个。

    这个数字是人手写的，没有守卫就会一直漂。改成从常量算。
    """
    assert f"searches {len(DEFAULT_ENGINES)} engines" in DHOLE_INSTRUCTIONS


def test_search_description_lists_the_real_default_pool(tools):
    desc = _desc(tools, "web_search")
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


def test_search_engine_list_never_names_a_retired_engine(tools):
    """`web_search` 的每一处引擎点名都不许指着已经不存在的后端。

    17.0 删了 6 家引擎、并把 opt-in 层一起去掉（池子=注册表），所以这条守卫换了
    形状：过去它比对「点名的 opt-in 清单别只列一半」，现在比对「点名的名字里不许
    有退役的那几家」。留着退役名字比漏列更糟 —— 调用方照着点会收到 invalid_request，
    那句话读起来像是我们坏了。

    **逐个位置判，不把整份载荷当一个字符串扫**（写下第一条版本时的真实错误：一处
    列全了就把另一处的漏列"补"成通过，变异实验实测）。
    """
    retired = {"sogou_weixin", "baidu_baike", "wikipedia", "grokipedia",
               "mwmbl", "yahoo"}

    # 机制自检：整词匹配不该把 sogou_weixin 读成 sogou
    assert _engine_tokens("sogou_weixin only") & retired == {"sogou_weixin"}
    # 结构自检：opt-in 层确实没了 —— 这条守卫的前提就是没有第二张名单
    from dhole_mcp.search_engines import _INDEX_FAMILY

    assert set(_INDEX_FAMILY) == set(DEFAULT_ENGINES), (
        "注册表与默认池又不一致了：先修池子，再回来改这条守卫")

    t = tools["web_search"]
    locations = {
        "description": t["description"],
        "engines": t["inputSchema"]["properties"]["engines"]["description"],
    }
    stale: dict[str, list[str]] = {}
    for where, text in locations.items():
        hit = _engine_tokens(text) & retired
        if hit:
            stale[where] = sorted(hit)
    assert not stale, (
        "web_search 点名了 17.0 已删除的引擎，调用方会点到不存在的名字。\n"
        f"  不符处: {stale}\n"
        "能力损失写在 sys_guide 语料里（那里可以讲「以前有、现在没了」），"
        "常驻层只写现在能用的。")


def test_parse_advertises_local_pdf(tools):
    """本地 PDF 必须真的可用，且被如实声明。

    历史：`SUPPORTED_EXTENSIONS` 一直含 `.pdf`，但实现只返回一句
    「用 web_read(url='file://...')」—— 而 file:// 被 SSRF 守卫硬拦、裸路径
    也不是合法 URL，于是本地 PDF 实际无路可走，agent 照描述走会白烧两次调用。
    现在 parse 自己解析，描述与参数说明都必须写明。
    """
    desc = _desc(tools, "doc_parse")
    assert ".pdf" in desc
    assert "NOT supported" not in desc
    props = tools["doc_parse"]["inputSchema"]["properties"]
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

    desc = _desc(tools, "doc_parse")
    prop = tools["doc_parse"]["inputSchema"]["properties"]["file_path"]["description"]
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
    assert "web_read(url='file://" not in src, "parse.py 仍推荐 file:// 用法"
    assert "extract_pdf" in src, "parse.py 没有真的解析 PDF"


# ─── 一致性：描述里提到的参数必须真的存在 ──────────────────────────────

# `foo=` 形式出现、但不算「参数引用」的名字。响应字段现在**从模型推出来**
# （见 _response_field_names），这里只留推导看不见的两类：非模型的子对象字段
# （table_of_contents 的条目是手搓 dict）与文档用词。
_NOT_A_PARAM = {
    "end_page",        # table_of_contents 的字段
    "page",            # 同上（PDF 目录项字段，非本工具参数）
    "example",         # 文档用词
}


def _response_field_names() -> set[str]:
    """回包模型声明过的每一个字段名。

    描述里写 `error='robots_disallowed'`、`summary_truncated=true` 是在说
    「回包长这样」，不是「有这么一个入参」。这个集合从模型推导而不是列清单，
    理由与 _wire_core_schema 的 required 一样：第二份手抄的字段名清单就是
    下一次漂移的来源。
    """
    from pydantic import BaseModel as _BM

    from dhole_mcp import crawl, feed, search, server
    out: set[str] = set()
    for mod in (server, crawl, feed, search):
        for obj in vars(mod).values():
            if isinstance(obj, type) and issubclass(obj, _BM) and obj is not _BM:
                out |= set(obj.model_fields)
    return out

_PARAM_REF = re.compile(r"\b([a-z][a-z0-9_]{2,})=")
# 袋子有两种写法：旧的同段列举 "sitemap (true|false)"，新的**一行一键**
# "sitemap: true | 'auto' | false"。只认前者的话，后者的键一律推导不出来，
# 于是描述里提到 sitemap= 就被当成"承诺了不存在的参数"——假阳性。
_OPTION_KEY = re.compile(r"\b([a-z][a-z0-9_]{2,})\s*[,(]|(?<![\w/])([a-z][a-z0-9_]{2,}):")


def _option_bag_keys(tool: dict) -> set[str]:
    opts = tool["inputSchema"].get("properties", {}).get("options")
    if not opts:
        return set()
    return {m.group(1) or m.group(2) for m in _OPTION_KEY.finditer(opts.get("description", ""))}


@pytest.mark.parametrize("name", sorted(_tools()))
def test_params_named_in_description_exist(tools, name):
    """描述说 `foo=...`，就得真有某个工具接受 foo。

    防的是「描述承诺了一个不存在的参数」—— agent 会照着传，然后被
    _reject_unknown_args 拒绝或静默忽略。跨工具引用是合法的（web_crawl 的
    描述里写 web_read(urls=[...])），所以已知集合取全部工具的并集。
    """
    known = set(_NOT_A_PARAM) | _response_field_names()
    assert "summary_truncated" in known and "next_offset" in known, \
        "响应字段推导空了：这条守卫会退化成把每个描述都判为通过"
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
#    crawl 的两道天花板必须写出来，值由 crawl.py 常量反查），措辞变了要人工判断是不是
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
# 参数说成早已改名的位置，读源码的人会照着写错。
#
# 这条守卫不要求两边内容一致（wire 是刻意瘦身的，docstring 更长）。它只要
# 求一件事：**docstring 里的 snake_case 标识符，若在整个 agent 可见面上找不到，
# 就必须登记在下面的集合里**。新增的漂移必须显式登记，不能默默出现 ——
# 这正是「改了 docstring 以为生效」的拦截点。
#
# agent 可见面有三扇门，不只是 tools/list：initialize 的 instructions、该工具
# 自己的载荷，以及 ``sys_guide`` 按需取的语料（17.0 把常驻层装不下的细节搬进
# 了第三扇门）。只查前两扇会把「搬进说明书」误判成信息丢失 —— 那正是 B5 瘦身
# 要做的事。语料里查不到才算 stray，所以搬走必须真的搬过去，不能搬成一句空指针。

_SNAKE_CASE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")

# 实现细节 / 非契约：本来就不该出现在 agent 可见的载荷里。
_DOCSTRING_ONLY_IMPLEMENTATION = {
    "curl_cffi",        # HTTP 层用的引擎名
    "circuit_breaker",  # ~/.dhole 下的状态文件名
    "engine_stats",     # 同上
}

# 跨工具引用：指向别处，不是本工具的契约。
# `cache_clear` 属于这类：close_session 的 docstring 拿它说明「那个是连正文缓存
# 一起清、这个是指名清」，而这条对比只写在 cache_clear 自己的描述里（警告长在
# 会动手的那个工具上才有用 —— 拿着「清缓存」意图的 agent 才是需要知道会掉登录
# 的人）。两边各写一遍就是两个工具描述互为副本，删掉任一侧都会漂。
_DOCSTRING_ONLY_CROSS_REFERENCE = {
    "web_read", "web_search", "cache_clear",
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


def _handbook_corpus() -> str:
    """sys_guide 那扇门的全部文本 —— agent 不用装任何东西就能取到。"""
    from dhole_mcp.guide import TOPICS, _ROOT

    return "\n".join((_ROOT / rel).read_text(encoding="utf-8").lower()
                     for rel, _when in TOPICS.values()
                     if (_ROOT / rel).is_file()).lower()


def test_docstring_identifiers_are_on_the_wire_or_registered(tools):
    """docstring 里出现 agent 看不到的标识符 = 开发者与代理看到的事实不同。

    拦截场景：往 docstring 里写了一个新参数/新字段，以为客户端能看到 ——
    实际看不到。要么搬进 _TOOL_DEFS，要么登记并说明为什么它是内部细节。

    agent 看得见的界面有三扇门，都算：tools/list 里该工具的那一条、
    initialize 的 instructions（envelope 的读法集中写在这里，八个工具共用
    一份，不在每个描述里各抄一遍），以及 sys_guide 的语料（17.0 把常驻层
    装不下的细节搬了进去）。少认一扇门会把「上收到公共层 / 搬进说明书」
    误判成信息丢失。
    """
    instructions = DHOLE_INSTRUCTIONS.lower()
    handbook = _handbook_corpus()
    unregistered: dict[str, list[str]] = {}
    for name, t in tools.items():
        on_wire = (json.dumps(t, ensure_ascii=False).lower() + instructions
                   + handbook)
        stray = sorted(i for i in _docstring_identifiers(name)
                       if i not in on_wire and i not in _ALLOWED_DOCSTRING_ONLY)
        if stray:
            unregistered[name] = stray
    assert not unregistered, (
        "docstring 里有 agent 看不到的标识符（三扇门里都没有），且未登记。\n"
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


# ─── 声音：操作面是写给代理的说明书，不是给人看的变化日志 ──────────────
#
# 这个 MCP 的使用者只有 agent。`tools/list` 与 `instructions` 里的每个字符都是它
# 每次连接要付的上下文（实测 17k 字符），所以那里不该出现两类东西：
#   · 作者口吻（"Measured:"、"until now"、"reported rather than hidden"）——它在向
#     读者解释设计为什么好，而 agent 要的是下一步做什么；
#   · 内部编号（G21 / P2-7 / BUG-1）——代码里指得清，对 agent 是无意义 token。
# 事实照留（阈值校准数字、条件→动作），删的只是叙述来源的那层皮。同一个理由不适用于
# docstring 与 CHANGELOG：它们不进上下文，写给人看是它们的本职。

_WIRE_VOICE = re.compile(
    r"\bmeasured\b|\buntil now\b|\bused to\b|\bpreviously\b|\bthe report\b"
    r"|\bdeliberat|\bwe (?:now|added|removed|don'?t|didn'?t)\b"
    r"|\bthis version\b|\bv1[0-9]\.", re.I)
_INTERNAL_LABEL = re.compile(r"\bG\d{1,2}\b|\bP2-\d|\bBUG-\d|\bSEP-\d")


def _wire_text_parts(tools: dict[str, dict]) -> list:
    parts = [("instructions", DHOLE_INSTRUCTIONS)]
    for name, t in tools.items():
        parts.append((f"desc:{name}", t["description"]))
        for key, spec in t["inputSchema"].get("properties", {}).items():
            if isinstance(spec, dict) and isinstance(spec.get("description"), str):
                parts.append((f"{name}.{key}", spec["description"]))
    return parts


@pytest.mark.parametrize("which,pattern", [
    ("作者口吻", _WIRE_VOICE),
    ("内部编号", _INTERNAL_LABEL),
])
def test_the_wire_never_becomes_a_changelog(tools, which, pattern):
    offenders = []
    for name, text in _wire_text_parts(tools):
        for match in pattern.finditer(text):
            i = match.start()
            offenders.append(f"{name}: …{text[max(0, i - 45):i + 55]}…")
    assert not offenders, (
        f"{which}不该出现在 agent 每次连接都要读的文本里：\n" + "\n".join(offenders))


# ─── 改名期的说谎守卫：文案里的工具名必须在注册面上 ────────────────────

# 17.0 的每个工具名都带域前缀，而它要替换掉的旧名（smart_*）正好也是这个形状，
# 所以「前缀 + 小写」这一个模式就同时抓住了两类错：拼错一个工具名，和改了名没改
# 文案。后者是 §B5 点名的风险 —— next_action 教 agent 去调一个不存在的工具，
# 而这条错误文案每次调用都在付钱。
_TOOL_SHAPED = re.compile(r"\b(?:smart|web|doc|feed|cred|sys)_[a-z][a-z0-9_]*\b")

# 撞同一个形状但不是工具名的东西，逐个说明为什么可以留着。
_SHAPED_BUT_NOT_A_TOOL = {
    "feed_url",     # feed_read 的参数名（§4 施工图里就這麼写）
}


def _names_on_the_registry() -> set:
    """注册面上的工具名，加上每个工具接受的参数名。"""
    from dhole_mcp.server import _TOP_LEVEL_ARGS

    out = set(_TOP_LEVEL_ARGS)
    for params in _TOP_LEVEL_ARGS.values():
        out |= set(params)
    return out | _SHAPED_BUT_NOT_A_TOOL


def _agent_facing_strings() -> list:
    """agent 读得到的每一段文本：src 里每条字符串字面量 + 说明书语料 + README。

    字符串字面量覆盖了描述、instructions、错误文案和 `_INSTRUCTIONS_ROUTING`；
    注释不算（那是给开发者的，不是 agent 的界面）。语料和 README 单独读文件，
    因为它们不是字面量。CHANGELOG 与 evals/*.md 不在内：那两份记的是当时测过的
    事实，用当时的名字才是如实。
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    out = []
    for path in sorted((root / "src" / "dhole_mcp").rglob("*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                out.append((path.name, node.value))
    for path in sorted((root / "src" / "dhole_mcp" / "skills").rglob("*.md")):
        out.append((str(path.relative_to(root)), path.read_text(encoding="utf-8")))
    out.append(("README.md", (root / "README.md").read_text(encoding="utf-8")))
    return out


def test_nobody_is_told_to_call_a_tool_that_does_not_exist():
    allowed = _names_on_the_registry()
    assert "web_read" in allowed and len(allowed) > 20, \
        "推导空了：这条守卫会退化成把每段文案都判为通过"
    strings = _agent_facing_strings()
    assert len(strings) > 400 and any("web_read" in t for _w, t in strings), \
        "扫描面空了（字面量或语料没读到），守卫同样会永远通过"
    offenders = {}
    for where, text in strings:
        for tok in _TOOL_SHAPED.findall(text):
            if tok not in allowed:
                offenders.setdefault(tok, set()).add(where)
    assert not offenders, (
        "文案里有注册面之外的工具名（改名没跟上，或名字写错）：\n"
        + "\n".join(f"  {t} <- {sorted(w)}" for t, w in sorted(offenders.items())))
