"""sys_guide / guide.py 的契约测试。

为什么这些守卫必须存在：文档通道是「常驻文本瘦身」的前提 —— 只有当说明书能
被一次调用取到，`tools/list` 才允许只留路由和硬约束。前提塌了（语料没了、
topic 漂了、wire 上没写全），瘦下去的那部分就成了信息丢失，而不是优化。

双向钉住：语料文件 ↔ topic 注册表 ↔ wire 上的 topic 清单，三处各查一次方向。
"""

import asyncio
import re

import pytest

from dhole_mcp import guide
from dhole_mcp.server import (
    DHOLE_INSTRUCTIONS,
    MasterFetchServer,
    _SC_PARAMS,
    _SF_PARAMS,
    _SS_PARAMS,
    _TOP_LEVEL_ARGS,
)


def _def_of(name: str) -> dict:
    return next(td for td in MasterFetchServer._TOOL_DEFS if td["name"] == name)


@pytest.fixture(scope="module")
def topic_def() -> dict:
    return _def_of("sys_guide")["inputSchema"]["properties"]["topic"]


def test_every_volume_on_disk_has_a_topic():
    """新增一份手册却没人点名 = 那份手册对 agent 不存在。"""
    files = {p.relative_to(guide._ROOT).as_posix()
             for p in guide._ROOT.rglob("*.md")}
    registered = {path for path, _ in guide.TOPICS.values()}
    assert files == registered, (
        f"语料与注册表不符：只在磁盘={sorted(files - registered)} "
        f"只在注册表={sorted(registered - files)}")


def test_every_topic_points_at_a_real_file():
    """反向：注册表里承诺过的 topic，文件必须真的在（装坏了要在这里红，
    而不是等 agent 调用时拿到一句道歉）。"""
    missing = [t for t, (path, _) in guide.TOPICS.items() if not (guide._ROOT / path).is_file()]
    assert not missing, f"这些 topic 指向不存在的文件: {missing}"


def test_topics_are_named_on_the_wire(topic_def):
    """注册表反查载荷：topic 清单只写一份在 guide.py，wire 必须点名每一个。

    漏一个 topic，那个 topic 就等于不存在 —— agent 不会去调一个没在描述里的
    取值，而 `topic=''` 的索引只在它**想到**要问的时候才救得了它。
    """
    text = topic_def["description"]
    unadvertised = sorted(t for t in guide.TOPICS if f"{t} =" not in text)
    assert not unadvertised, f"wire 的 topic 说明里没点名的 topic: {unadvertised}"


def test_every_topic_carries_a_when(topic_def):
    """每个 topic 得说清「什么时候值得一次调用」，否则注册表只是文件名列表。"""
    thin = [t for t, (_, when) in guide.TOPICS.items() if len(when) < 20]
    assert not thin, f"这些 topic 没有 when 说明: {thin}"


def test_aliases_resolve_to_their_volume():
    """agent 会按文件名猜 topic。猜中就该拿到内容，而不是拿到一次道歉。"""
    assert guide._resolve("tools") == "behavior"
    assert guide._resolve("SKILL") == "start"          # 大小写不敏感
    assert guide._resolve("troubleshooting") == "fix"
    for alias, topic in guide.ALIASES.items():
        assert guide._resolve(alias) == topic


def test_unknown_topic_answers_with_the_index():
    header, body = guide.read("nonsense-topic")
    assert "unknown topic" in body
    for topic in guide.TOPICS:
        assert f"topic='{topic}'" in body, f"索引里没列出 {topic}"


def test_empty_topic_answers_with_the_index():
    header, body = guide.read("")
    assert "no topic given" in body
    assert "topic='behavior'" in body


def test_a_real_topic_returns_its_volume():
    header, body = guide.read("fields")
    assert "references/fields.md" in header
    assert len(body) > 500, f"fields 卷只有 {len(body)} 字符，通道在送存根"


def test_section_returns_one_chunk_not_the_volume():
    _, whole = guide.read("behavior")
    header, part = guide.read("behavior", section="engine matrix")
    assert "section=" in header
    assert len(part) < len(whole), "section 没有真的切出一节"
    assert "Engine matrix" in part or "engine matrix" in part.lower()


def test_a_wrong_section_lists_the_ones_that_exist():
    """错误猜测的产物必须是可用的下一步，而不是一个死端。"""
    _, body = guide.read("behavior", section="definitely-not-a-heading")
    assert "matched no heading" in _
    assert "- web_search" in body or "web_search" in body


def test_topic_and_section_are_the_whole_argument_surface():
    """_TOP_LEVEL_ARGS 也是「已知工具名」的集合：多一个参数就会多一个静默入口。"""
    assert _TOP_LEVEL_ARGS["sys_guide"] == frozenset({"topic", "section"})
    props = set(_def_of("sys_guide")["inputSchema"]["properties"])
    assert props == set(_TOP_LEVEL_ARGS["sys_guide"])


def test_dhole_doc_is_not_opt_in():
    """说明书不需要开关才能读到 —— 17.0 起根本没有 opt-in 组。"""
    from dhole_mcp import server as s
    assert not hasattr(s, "_OPT_IN_TOOLS")
    assert "sys_guide" in s._ENABLED_TOOLS


def test_response_is_a_text_channel():
    """文档走 text 通道：把 markdown 塞进 JSON 会转义每一个换行，换不回任何字段。"""
    from dhole_mcp import server as s

    content = asyncio.run(s.MasterFetchServer(cache_ttl=0)._dispatch(
        "sys_guide", {"topic": "recipes"}))
    assert isinstance(content, list) and len(content) == 1
    assert content[0].type == "text"
    assert "\n" in content[0].text
    assert not content[0].text.lstrip().startswith("{"), "text 通道不该是 JSON 信封"


# ─── 瘦身的前提：常驻文本删掉的解释，必须能从 sys_guide 取回来 ───────────
#
# web_read 的 options 袋子从「逐键解释」改成了「列出键名 + 少数几条陷阱」，
# 细节移进 references/tools.md。这个移动只有在语料真的接住时才是优化而不是丢
# 信息，所以覆盖率**从代码里的键集合反查**，而不是比对两份手写清单：以后任何
# 新键进了 _SF_PARAMS，它在 wire 上没名字、或在语料里没解释，都会红。

def _corpus() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in guide._ROOT.rglob("*.md"))


def _wire_explanations() -> str:
    """常驻文本里除那份键名清单之外的部分 —— 清单只点名，不解释。"""
    td = _def_of("web_read")
    parts = [td["description"], DHOLE_INSTRUCTIONS]
    for name, spec in td["inputSchema"]["properties"].items():
        if name != "options":
            parts.append(f"{name} {spec.get('description') or ''}")
    return "\n".join(parts)


def _tool_text(name: str) -> str:
    """一个工具的全部 agent 可见文本，**不做 JSON 转义**。

    用 json.dumps 拼会得到 `\\nsite` —— 换行变成两个字符，于是键名前面永远是词
    字符，下面那些词边界匹配全线失效（实测就是这么让四条守卫一起报「没提过」，
    而描述里明明写着）。机制自检里钉了这条，防止改回去。
    """
    td = _def_of(name)
    parts = [td["description"], name]
    for key, spec in td["inputSchema"].get("properties", {}).items():
        parts.append(key)
        if isinstance(spec, dict):
            parts.append(str(spec.get("description") or ""))
            parts.extend(str(v) for v in (spec.get("enum") or []))
    return "\n".join(parts)


def _mentions(text: str, key: str) -> bool:
    """按词边界找键名：`wait` 不该被 `network_idle` 里的一段算成已解释。"""
    return re.search(rf"(?<![\w.]){re.escape(key)}(?![\w])", text) is not None


def test_the_mention_matcher_is_not_a_substring_test():
    """机制自检：上面两条守卫全靠它，它若退化成子串匹配就只会一路放行。"""
    assert not _mentions("network_idle and waits", "wait")
    assert _mentions("headless, useragent, wait", "wait")
    assert not _mentions("solve_cloudflare", "cloudflare")
    assert _mentions(_tool_text("web_search"), "site"), "转义把词边界弄坏了"
    # 反证：把 fetch_schema 的那句说明删掉，覆盖率守卫就该看不见它（W10 的成因）
    stripped = _tool_text("web_search").replace("fetch_schema", "")
    assert not _mentions(stripped, "fetch_schema")
    assert len(_corpus()) > 20_000, "语料空了：覆盖率会伪装成通过"
    assert len(_SF_PARAMS) > 20, "键集合空了：同上"


def test_the_handbook_never_teaches_a_nested_call():
    """语料是「照着抄」的那一卷：一处教了袋形，agent 就白跑一次调用。

    17.0 删掉了 `options` 袋，于是「怎么抄」的权威落点就在语料里 —— 运行时只
    能在被拒的那一刻提醒，抄错的代价已经付掉了。唯一豁免是 ❌ 行：它教的正是
    「这样写会被拒」。
    """
    bad = []
    for path in sorted(guide._ROOT.rglob("*.md")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("❌"):
                continue
            if '"options"' in line or re.search(r"\boptions\.", line):
                bad.append(f"{path.name}:{n}: {line.strip()[:90]}")
    assert not bad, "语料把参数写成袋里的键（或被袋里的写法提到）：\n" + "\n".join(bad)


def test_every_smart_fetch_key_is_named_on_the_wire():
    """一个 accepted 却不点名的键 = 这个能力对 agent 不存在。

    B4 之前这句话查的是"袋子的清单里有没有它"；袋子删了，清单就是属性表本身，
    于是判据升级为**必须有属性，而且属性自己带一句说明** —— 只列名字不给说明，
    agent 仍然不知道该不该用它。
    """
    props = _def_of("web_read")["inputSchema"]["properties"]
    silent = sorted(k for k in _SF_PARAMS
                    if k not in props or not (props[k].get("description") or "").strip())
    assert not silent, f"这些参数在 wire 上没有自己的名字或说明: {silent}"


PARAMS = {
    "web_read": _SF_PARAMS,
    "web_crawl": _SC_PARAMS,
    "web_search": _SS_PARAMS,
}


@pytest.mark.parametrize("tool", sorted(PARAMS))
def test_every_accepted_parameter_is_declared_with_a_description(tool):
    """每个工具的被接受名字，都得是 schema 里一个带说明的属性。

    这是"新参数进了 `_TOP_LEVEL_ARGS` 却没人给它名字"的唯一拦截点 —— 历史上正是
    这个缝让 `fetch_schema` 之类的键活着、能被读、而 wire 上查无此人。
    """
    props = _def_of(tool)["inputSchema"]["properties"]
    silent = sorted(k for k in PARAMS[tool]
                    if k not in props or not (props[k].get("description") or "").strip())
    assert not silent, f"{tool} 接受了这些名字却没有声明: {silent}"


def test_every_smart_fetch_key_is_explained_somewhere():
    """web_read 把逐键的**详细**解释搬进了语料，所以这里查的是搬走的落点。

    属性名与一句话判据留在常驻层（每一次连接都要发），怎么用、边界在哪进语料按需取。
    """
    surface = _wire_explanations() + "\n" + _corpus()
    undocumented = sorted(k for k in _SF_PARAMS if not _mentions(surface, k))
    assert not undocumented, (
        f"这些参数没有任何地方解释（既不在常驻文本，也不在 sys_guide 语料）: "
        f"{undocumented}。补进 references/tools.md，或从 _SF_PARAMS 里删掉。")


def test_the_stateful_tools_are_announced_with_their_discipline():
    """剩下的状态工具必须说明它们存在，且写操作的逐次确认闸是 agent 读得到的纪律
    （没有开关可关，纪律就是全部）。"""
    announced = _wire_explanations() + "\n" + _corpus()
    for tool in ("cred_defaults", "sys_state"):
        assert _mentions(announced, tool), f"{tool} 在 agent 读得到的地方没被提到"
    assert "confirm" in announced, "确认闸必须在常驻文本里"


# ─── 整个参数面的覆盖率（不只是 options 袋）──────────────────────────────
#
# W10 的守卫抓出了 `fetch_schema`：功能活着、_SS_PARAMS 接受它、整个 wire 里
# 没有任何一处提到过它的名字。那种缺陷不会自己冒出来，所以把它变成一条覆盖
# 全部 14 个工具的断言 —— 参数存在而无人提及，等于这个能力不存在。

#: 名字本身已经说明用途的顶层必填项，一句短说明就够了。
_OBVIOUS = {
    ("web_read", "url"), ("web_read", "urls"),
    ("web_search", "query"), ("web_act", "url"),
    ("web_crawl", "url"),
}


@pytest.mark.parametrize("tool", sorted(_TOP_LEVEL_ARGS))
def test_every_property_explains_something(tool):
    """每个参数的说明至少要说清「什么时候用它」，不能只是把名字复读一遍。"""
    props = _def_of(tool)["inputSchema"].get("properties", {})
    thin = [f"{tool}.{k}({len(v.get('description') or '')})" for k, v in props.items()
            if len((v or {}).get("description") or "") < 15
            and (tool, k) not in _OBVIOUS]
    assert not thin, f"这些参数几乎没有说明，且不在显然名单里: {thin}"


@pytest.mark.parametrize("tool", sorted(_TOP_LEVEL_ARGS))
def test_no_accepted_parameter_is_unmentioned(tool):
    """`_TOP_LEVEL_ARGS` 接受的每一个名字，都要在该工具的 wire 文本里出现一次。

    查该工具自己的文本（说明 + 每个属性名与描述；VISUAL ASSIST 纪律已烙进
    web_read / screenshot 的字面量，无需再拼），因为「别的工具提过」不救这个
    调用：agent 在 `recipe` 的描述里读不到 `note`，就不会给 `recipe` 传 `note`。
    """
    text = _tool_text(tool)
    silent = sorted(k for k in _TOP_LEVEL_ARGS[tool] if not _mentions(text, k))
    assert not silent, f"{tool} 接受但它的 wire 文本从未提到的参数: {silent}"
