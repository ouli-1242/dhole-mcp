"""dhole_doc / guide.py 的契约测试。

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
    _SF_OPTIONS_ALLOWED,
    _TOP_LEVEL_ARGS,
)


def _def_of(name: str) -> dict:
    return next(td for td in MasterFetchServer._TOOL_DEFS if td["name"] == name)


@pytest.fixture(scope="module")
def topic_def() -> dict:
    return _def_of("dhole_doc")["inputSchema"]["properties"]["topic"]


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
    assert "- smart_search" in body or "smart_search" in body


def test_topic_and_section_are_the_whole_argument_surface():
    """_TOP_LEVEL_ARGS 也是「已知工具名」的集合：多一个参数就会多一个静默入口。"""
    assert _TOP_LEVEL_ARGS["dhole_doc"] == frozenset({"topic", "section"})
    props = set(_def_of("dhole_doc")["inputSchema"]["properties"])
    assert props == set(_TOP_LEVEL_ARGS["dhole_doc"])


def test_dhole_doc_is_not_opt_in():
    """说明书不需要开关才能读到 —— 要开关的说明书等于没有说明书。"""
    from dhole_mcp import server as s
    assert "dhole_doc" not in s._OPT_IN_TOOLS
    assert "dhole_doc" not in s._OPT_IN_TOOLS_VAULT
    assert "dhole_doc" in s._ENABLED_TOOLS


def test_response_is_a_text_channel():
    """文档走 text 通道：把 markdown 塞进 JSON 会转义每一个换行，换不回任何字段。"""
    from dhole_mcp import server as s

    content = asyncio.run(s.MasterFetchServer(cache_ttl=0)._dispatch(
        "dhole_doc", {"topic": "recipes"}))
    assert isinstance(content, list) and len(content) == 1
    assert content[0].type == "text"
    assert "\n" in content[0].text
    assert not content[0].text.lstrip().startswith("{"), "text 通道不该是 JSON 信封"


# ─── 瘦身的前提：常驻文本删掉的解释，必须能从 dhole_doc 取回来 ───────────
#
# smart_fetch 的 options 袋子从「逐键解释」改成了「列出键名 + 少数几条陷阱」，
# 细节移进 references/tools.md。这个移动只有在语料真的接住时才是优化而不是丢
# 信息，所以覆盖率**从代码里的键集合反查**，而不是比对两份手写清单：以后任何
# 新键进了 _SF_OPTIONS_ALLOWED，它在 wire 上没名字、或在语料里没解释，都会红。

def _corpus() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in guide._ROOT.rglob("*.md"))


def _wire_explanations() -> str:
    """常驻文本里除那份键名清单之外的部分 —— 清单只点名，不解释。"""
    td = _def_of("smart_fetch")
    parts = [td["description"], DHOLE_INSTRUCTIONS]
    for name, spec in td["inputSchema"]["properties"].items():
        if name != "options":
            parts.append(f"{name} {spec.get('description') or ''}")
    return "\n".join(parts)


def _mentions(text: str, key: str) -> bool:
    """按词边界找键名：`wait` 不该被 `network_idle` 里的一段算成已解释。"""
    return re.search(rf"(?<![\w.]){re.escape(key)}(?![\w])", text) is not None


def test_the_mention_matcher_is_not_a_substring_test():
    """机制自检：上面两条守卫全靠它，它若退化成子串匹配就只会一路放行。"""
    assert not _mentions("network_idle and waits", "wait")
    assert _mentions("headless, useragent, wait", "wait")
    assert not _mentions("solve_cloudflare", "cloudflare")
    assert len(_corpus()) > 20_000, "语料空了：覆盖率会伪装成通过"
    assert len(_SF_OPTIONS_ALLOWED) > 20, "键集合空了：同上"


def test_every_smart_fetch_key_is_named_on_the_wire():
    """一个 accepted 却不点名的键 = 这个能力对 agent 不存在。"""
    listed = _def_of("smart_fetch")["inputSchema"]["properties"]["options"]["description"]
    silent = sorted(k for k in _SF_OPTIONS_ALLOWED if not _mentions(listed, k))
    assert not silent, f"这些 options 键没进 wire 上的清单: {silent}"


def test_every_smart_fetch_key_is_explained_somewhere():
    """键名点了，还得有人解释它是什么 —— 常驻文本或语料，两处之一即可。"""
    surface = _wire_explanations() + "\n" + _corpus()
    undocumented = sorted(k for k in _SF_OPTIONS_ALLOWED if not _mentions(surface, k))
    assert not undocumented, (
        f"这些 options 键没有任何地方解释（既不在常驻文本，也不在 dhole_doc 语料）: "
        f"{undocumented}。补进 references/tools.md，或从 _SF_OPTIONS_ALLOWED 里删掉。")


def test_the_opt_in_capability_groups_are_announced_even_when_off():
    """默认关掉四个工具是为了 token，但「有没有这四个」不该靠读 README 才知道。

    常驻文本必须回答两件事：它们存在，以及打开它们是操作者的决定而不是 agent
    能自己改的东西。少了后半句 agent 会去绕，少了前半句用户根本不知道有。
    """
    announced = _wire_explanations() + "\n" + _corpus()
    for tool in ("recipe", "watch", "vault", "login"):
        assert _mentions(announced, tool), f"{tool} 在 agent 读得到的地方没被提到"
    for flag in ("DHOLE_TASKS", "DHOLE_VAULT"):
        assert flag in announced, f"{flag} 没有出现在任何 agent 读得到的地方"
