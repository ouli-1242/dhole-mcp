"""一句建议点名的参数，必须是**这一次调用的那扇门**收得下的那一颗。

症状（15 场景真人体测，场景 9；`review/2026-10-10-用户体验模拟报告.md`）：一次
`web_request(headers={'User-Agent': …})` 撞在设计边界上被拒，而 `next_action` 教的是
"传 anti_detect='off'"。那一颗参数在这扇门上不存在 —— 照着做的那一发 0 秒弹回
`Unsupported argument(s) for web_request`。副作用良性（什么都没发出去），代价是一次往返
和一句"这工具连自己要什么都说不清"。同一族里还量到第二条：`if-none-match` 的出路写着
`extraction_type='check'`，而 `check` 恰恰不在 `web_request` 的枚举里（`params._ENUM_ARGS`
就是当场拒它的那张表）—— 换了一颗取值，同一个形状。

修法不是把两扇门的名字抄进句子里，是**让生成那句话的地方去查表**：

* 参数面 = `server._TOP_LEVEL_ARGS`（dispatch 逐颗工具读的那份，"Unsupported
  argument(s)" 就是它写的）；
* 取值面 = `params._ENUM_ARGS`（`_coerce_arg_types` 按它拒）；
* 这一次调用叫什么名字 = `context._TOOL`，由 `call_tool` 在协议层写下（`_MCP_SESSION`
  的同一处、同一条理由）。

于是 `web_read` 的两句话一字未改（那扇门上两颗都在），`web_request` 版说的是它够得着的
两条：删掉那颗头（写照样发出，用 dhole 成套的 UA + TLS 画像），或把档位放进
`cred_defaults`（`identity.KEYS` 里就有 anti_detect，`_identity_for` 读它）。**点名一颗
别扇门的参数是允许的 —— 只要把那扇门的名字一起写出来**（`tools.md` 为 `check` 立的说法），
因为那是调用方撞得到的下一条地址；不写名字才是谎。

一根轴上的两条判据不许混：`quality._never_escalate` 问的是"渲染改不改变得了答案"（看
`_METHOD`，写请求永不升级），这里问的是"调用方能在这扇门上点什么名字"（看 `_TOOL`）。
`web_read(method="POST")` 这类内部路径两颗会分家，而它们本来就在答不同的问题 —— §4 一条
各测一边，防止有人把它们并成一颗。

`diagnosis.evidence.knob_owner` 没有加（计划里那一格写了）：调用方读到建议的地方就是
`next_action`，而一处没人读的字段是同一件事实的第二个作者。这一刀的机器侧判据是 §3 那张
扫描，它读的两张表正是拒绝消息的产地。
"""

from __future__ import annotations

import contextlib
import inspect
import re

import pytest

from dhole_mcp import guide
from dhole_mcp import server as server_mod
from dhole_mcp import quality
from dhole_mcp.context import _METHOD, tool_scope
from dhole_mcp.models import ResponseModel
from dhole_mcp.rungs import _RUNG_TOOLS
from dhole_mcp.server import (_CONDITIONAL_HEADER_NAMES, _CREDENTIAL_HEADER_NAMES,
                              _ENUM_ARGS, _header_door, _headers_note, _knob_here,
                              _TOP_LEVEL_ARGS)

REFUSED_NAMES = _CREDENTIAL_HEADER_NAMES + ("user-agent",) + _CONDITIONAL_HEADER_NAMES
URL = "https://api.example.test/orders"
#: 能走到这扇门的工具 = `headers=` 在那颗工具的参数名单里。§3 的射程由它算，不手写。
HEADER_TOOLS = frozenset(t for t in _TOP_LEVEL_ARGS if "headers" in _TOP_LEVEL_ARGS[t])

#: `param='value'` 是"把这颗传进来"的语法 —— 一句建议一旦被读成动作，它就得是动作。
MENTION_RE = re.compile(r"\b([a-z][a-z0-9_]{2,})='([a-z_]+)'")


def _passable(tool: str, param: str, value: str) -> bool:
    """这颗工具的这一扇门上，`param='value'` 传得进去吗（两张表都不是这里的作者）。"""
    if param not in _TOP_LEVEL_ARGS[tool]:
        return False
    allowed = _ENUM_ARGS.get(tool, {}).get(param)
    return allowed is None or value in allowed


def _doors(tool: str) -> tuple[list[tuple[str, str]], list[str]]:
    """这一扇门上每一条拒绝的 `(为什么, 出路)`，加上 `summary` 里那句头的说明。"""
    with tool_scope(tool):
        doors = [_header_door(name) for name in REFUSED_NAMES]
        notes = [_headers_note({}, True),                 # headers={} 点了个空
                 _headers_note({"X-Trace": "1"}, True),   # 你的键赢在模板之上
                 _headers_note({}, False)]
    return doors, [n for n in notes if n]


def _ua_door(tool: str) -> tuple[str, str]:
    return _doors(tool)[0][REFUSED_NAMES.index("user-agent")]


def _if_door(tool: str) -> tuple[str, str]:
    return _doors(tool)[0][REFUSED_NAMES.index("if-none-match")]


def _note(tool: str, caller, synthesized: bool) -> str:
    with tool_scope(tool):
        return _headers_note(caller, synthesized)


@contextlib.contextmanager
def _verb(method: str):
    token = _METHOD.set(method)
    try:
        yield
    finally:
        _METHOD.reset(token)


# ─── 1 · 现场：写的那一扇门不许教人传它收不下的东西 ────────────────

def test_the_write_is_not_told_to_pass_a_rung_it_does_not_take():
    reason, way = _ua_door("web_request")
    assert "anti_detect='off'" not in way, way
    assert "no anti_detect=" in way and "no" in way and "cdp_url=" in way, (
        "说'这扇门上够不着'要说全：两颗都不在参数面里，缺一颗就是一半的真相")
    # 出路仍然要有两条当场能做的，一句"不行"不是建议
    assert "drop the user-agent key" in way and "cred_defaults" in way, way
    assert "Nothing was sent." in way, way
    # 讲"为什么"的那一半不分工具
    assert "TLS impersonation OS" in reason, reason


def test_the_read_still_gets_both_of_its_doors():
    """反面守卫：给写的那一支改了口，读的那一支一个字不许变弱。"""
    _reason, way = _ua_door("web_read")
    assert "anti_detect='off' stops dhole synthesizing" in way, way
    assert "cdp_url= runs in the user's real Chrome" in way, way


def test_the_validator_door_on_a_write_names_the_tool_that_holds_it():
    """允许点名别扇门的参数 —— 只要那扇门的名字一起写出来（本文件 docstring 的规矩）。"""
    _reason, way = _if_door("web_request")
    assert "is that one door" not in way, way
    assert "web_read" in way, way
    assert "Drop that validator" in way, way


def test_the_validator_door_on_a_read_still_offers_check():
    _reason, way = _if_door("web_read")
    assert "extraction_type='check' is that one door" in way, way


@pytest.mark.parametrize("caller,synthesized", [
    ({}, True), ({"X-Trace": "1"}, True), ({}, False)])
def test_the_summary_note_for_a_write_never_offers_the_rung(caller, synthesized):
    text = _note("web_request", caller, synthesized)
    assert "anti_detect='off'" not in text, text
    if text:
        assert "cred_defaults" in text, text


@pytest.mark.parametrize("caller,synthesized", [
    ({}, True), ({"X-Trace": "1"}, True)])
def test_the_summary_note_for_a_read_still_names_its_dial(caller, synthesized):
    text = _note("web_read", caller, synthesized)
    assert "anti_detect='off'" in text, text


def test_the_credential_door_differs_only_in_what_it_declared_did_not_happen():
    """反向守卫（本来的形状）：这一族的**出路**本来就叫 `cred_defaults`（另一颗工具），
    不需要分岔 —— 把"按工具生成"做成"所有建议都按工具重写一遍"会毁掉这条：一处没有差别的
    地方就是第二份措辞的开始，而两份措辞迟早有一份说错。

    评审 R02-084 动的不是这条保护的**出路**，是句子最后那半句"这一发什么也没发生"：
    写的那一扇必须说 sent（调用方要判的是"对面收过这一发没有"，这一扇门对外承诺 "A write is
    sent ONCE"），读的那一扇必须说 fetched（那一扇能从缓存答题，从没付出去过请求）。
    所以期望从"两处逐字相同"改成"**除那半句之外逐字相同**"：这不是把尺子放宽，是把尺子挪到
    它真正想守的那一位上 —— 前半句（说清为什么）与出路的其余部分只要有一个字不同就红。
    """
    for name in _CREDENTIAL_HEADER_NAMES:
        with tool_scope("web_request"):
            write = _header_door(name)
        with tool_scope("web_read"):
            read = _header_door(name)
        assert write[0] == read[0], f"{name}：说清为什么那一半不许按门改写"
        assert write[1].replace("Nothing was sent.", "@") \
            == read[1].replace("Nothing was fetched.", "@"), (
            f"{name}：出路除了那半句申报口径，一个字都不许多说：\n{write[1]}\n{read[1]}")
        assert "Nothing was sent." in write[1], write[1]
        assert "Nothing was fetched." in read[1], read[1]


# ─── 2 · 分岔读的是参数面，不是一把工具名字 ─────────────────────────

def test_a_door_that_gains_the_knob_loses_the_write_branch(monkeypatch):
    """给 `web_request` 加上那两颗参数，同一句建议必须立刻换回长的那一支。

    这条是"分岔由表决定"的正面证据：写死的 `if tool == "web_request"` 在这一条下会照旧
    走写的那一支，于是这里红。表改了而话没跟上，正是这一族缺陷的祖先。
    """
    widened = _TOP_LEVEL_ARGS["web_request"] | frozenset({"anti_detect", "cdp_url"})
    monkeypatch.setitem(_TOP_LEVEL_ARGS, "web_request", widened)
    _reason, way = _ua_door("web_request")
    assert "anti_detect='off' stops dhole synthesizing" in way, way


def test_the_rung_list_and_the_parameter_surface_still_agree():
    """`_RUNG_TOOLS` 与参数名单必须是同一批工具 —— 两张表各写一半就是 14b 那次 KeyError。"""
    with_rung = {t for t in _TOP_LEVEL_ARGS if "anti_detect" in _TOP_LEVEL_ARGS[t]}
    assert with_rung == set(_RUNG_TOOLS), (with_rung, set(_RUNG_TOOLS))
    for tool in sorted(with_rung):
        with tool_scope(tool):
            assert _knob_here("anti_detect") is True, tool
    for tool in sorted(set(_TOP_LEVEL_ARGS) - with_rung):
        with tool_scope(tool):
            assert _knob_here("anti_detect") is False, tool


def test_knob_here_reads_the_tables_instead_of_owning_one():
    """静态守卫：这颗判据的**代码**里不许出现第二份工具名单。

    量之前先把 docstring 摘掉 —— 那一格里写着场景 9 的现场，而现场必须点名是哪扇门；
    判据要禁的是代码里的那一份（`if tool == "web_request"` 那种颜色）。
    """
    src = inspect.getsource(server_mod._knob_here)
    body = src.replace(src.split('"""')[1], "")
    assert "_TOP_LEVEL_ARGS" in body and "_ENUM_ARGS" in body
    assert "web_request" not in body, "出现了一扇门的颜色 —— 那就是把表抄进了判据"
    assert body.count('"web_read"') == 1, (
        "只许出现一次：`_TOOL` 为空时按 web_read 算的那颗默认值")


def test_a_value_outside_the_enum_is_caught_even_when_the_param_is_there():
    """`extraction_type` 两扇门上都有，`check` 只在读的那一枚上 —— 取值也要问。"""
    with tool_scope("web_read"):
        assert _knob_here("extraction_type", "check") is True
    with tool_scope("web_request"):
        assert _knob_here("extraction_type", "check") is False
        assert _knob_here("extraction_type", "markdown") is True


def test_an_unnamed_call_keeps_every_existing_sentence():
    """`_TOOL` 缺席 = 没经过工具面（内部 bulk、直调方法的测试与 evals）。

    那时按 `web_read` 算，于是每一条既有的路字节不变（`_METHOD` 的 "GET" 默认值同一条
    理由）。这一条钉的是"修一扇门的措辞不许顺手改掉另一条通道的话"。
    """
    from dhole_mcp.context import _TOOL
    assert _TOOL.get() == "", "这颗默认值变了，下面那句就不是在量'没经过工具面'"
    _reason, way = _ua_door("")
    assert "anti_detect='off' stops dhole synthesizing" in way, way


# ─── 3 · 类级扫描：每一扇能问到的门 × 每一条出路 ──────────────────

def test_exactly_two_doors_can_be_asked_about_headers():
    """射程由参数面算出来。第三条门开的时候这条会把它一起纳进来（不是放宽，是自动）。"""
    assert HEADER_TOOLS == frozenset({"web_read", "web_request"}), sorted(HEADER_TOOLS)


@pytest.mark.parametrize("tool", sorted(HEADER_TOOLS))
def test_no_advice_names_a_quoted_value_this_tool_cannot_take(tool):
    """扫这一扇门能产出的**每一句**话：`param='value'` 必须传得进去。

    传不进去的只许一种写法 —— 那句话说清它不在这扇门上，并点出拿着它的另一扇门
    （"is web_read's"）。这一条是本文件所有单点守卫的合订本：它不看措辞，只看名字
    与取值，所以搬家、加一句或抄一段都不会漏。
    """
    doors, notes = _doors(tool)
    offenders = []
    for text in [part for door in doors for part in door] + notes:
        for sentence in re.split(r"(?<=[.;]) |-- ", text):
            for param, value in MENTION_RE.findall(sentence):
                if _passable(tool, param, value):
                    continue
                holders = [t for t in _TOP_LEVEL_ARGS if _passable(t, param, value)]
                named = [t for t in holders if t in sentence]
                if named:
                    continue
                offenders.append(f"{param}={value!r} legal nowhere here "
                                 f"(holders: {holders or 'none'}) :: {sentence!r}")
    assert not offenders, "\n".join(offenders)


# ─── 4 · 同一根轴上的两条判据不许并成一颗 ─────────────────────────

def test_a_write_is_not_told_to_retry_a_tier_it_cannot_reach():
    """`_never_escalate` 那一支看的是动词，不看名字 —— 写的路上渲染改不了答案。"""
    wall = ResponseModel(url=URL, status=403, content=["Forbidden"],
                         error="http_error_403", page_type="unknown",
                         fetcher_used="http")
    with _verb("POST"):
        hint = quality._agent_hints(wall)[1]
    assert "Retry once with force_fetcher='stealthy'" not in hint, hint


def test_the_same_wall_on_a_get_still_hears_about_the_browser():
    """反面守卫：动词是 GET 时那一支一个字不许因为"写的那条更严"而变弱。"""
    wall = ResponseModel(url=URL, status=403, content=["Forbidden"],
                         error="http_error_403", page_type="unknown",
                         fetcher_used="http")
    with _verb("GET"):
        hint = quality._agent_hints(wall)[1]
    assert "Retry once with force_fetcher='stealthy'" in hint, hint


# ─── 5 · 手册必须说得出哪两扇门不在写上 ───────────────────────────

def test_the_manual_says_which_doors_a_write_does_not_have():
    """三处落点上了话，解释就得住在只读手册的 agent 也看得见的卷里。

    常驻层那一格（`web_request.headers` 的描述）说的是"三族同样按名字拒、同一个理由"，
    出路的两颗参数属于读的那一扇门 —— 这件事原先只写在代码注释里。
    """
    corpus = "\n".join(p.read_text(encoding="utf-8") for p in guide._ROOT.rglob("*.md"))
    assert "the two doors above are not parameters" in corpus
    for needle in ('"anti_detect": "off"', "no `anti_detect=`", "web_read's"):
        assert needle in corpus, f"{needle!r} 不在语料里"


# ─── 6 · 端到端：那条名字必须真的从协议层流到那句话 ────────────────

def test_a_real_write_refusal_answers_with_a_door_the_caller_can_walk():
    """走一次真 `_dispatch`（不发请求：闸门在任何 ContextVar 与任何字节之前）。

    上面每一条都是直接在 `tool_scope(...)` 里问生成器；这一条问的是**那条通道**：名字
    从协议层写进 `_TOOL`、穿过 web_read 的装饰器、活到那句话被拼出来。摘掉
    `@_named_call`、或把 scope 挪到 `_caller_headers` 之后，这里红而 §1 全绿 —— 那正是
    "单点守卫测不到接线"的形状。
    """
    import asyncio
    import json

    from dhole_mcp.server import MasterFetchServer
    out = asyncio.run(MasterFetchServer()._dispatch(
        "web_request", {"url": URL, "method": "POST", "body": "x=1", "confirm": True,
                        "headers": {"User-Agent": "curl/8.6"}}))
    text = json.dumps(out, ensure_ascii=False, default=str)
    assert "anti_detect='off'" not in text, text[:400]
    assert "drop the user-agent key" in text and "cred_defaults" in text, text[:400]
    assert "Nothing was sent." in text, text[:400]
