"""test_context_2.py —— 由 3 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import asyncio
import uuid
from dhole_mcp.context import _arg_notes_scope
from dhole_mcp.quality import _agent_hints, _annotate_quality, _chain_ran_ok, _diagnose, _is_auth_wall, _with_agent_hints
from dhole_mcp.server import MasterFetchServer
import contextlib
import inspect
import re
import pytest
from dhole_mcp import guide
from dhole_mcp import server as server_mod
from dhole_mcp import quality
from dhole_mcp.context import _METHOD, tool_scope
from dhole_mcp.rungs import _RUNG_TOOLS
from dhole_mcp.server import _CONDITIONAL_HEADER_NAMES, _CREDENTIAL_HEADER_NAMES, _ENUM_ARGS, _header_door, _headers_note, _knob_here, _TOP_LEVEL_ARGS
from dhole_mcp.server import PIN_NOTES, BulkResponseModel, ResponseModel


# ============================================================================
# 来源：test_a_verdict_must_not_deny_what_the_same_response_delivered.py
# ============================================================================
# R01-028 · 旗标与自然语言不许各算各的（本轮最大簇，10 次实测 / 5 条代码路径）。

WALL_URL = "https://example.com/login"   # 路径信号 + 强词，两个条件都在真版面上才成立


def _page(*, url=WALL_URL, status=200, content=None, error="",
          content_ok=True, page_type="", metadata=None) -> ResponseModel:
    return ResponseModel(url=url, status=status, content=content or [], error=error,
                         content_ok=content_ok, page_type=page_type,
                         fetcher_used="stealthy", content_type="text/html",
                         metadata=metadata or {})


CHAIN_OK = {"actions": [{"action": "fill 0", "ok": True},
                        {"action": "fill 1", "ok": True},
                        {"action": "click 2", "ok": False, "pending_approval": True}]}
CHAIN_FAILED = {"actions": [{"action": "fill 0", "ok": False,
                             "found": "an input named 'q'"}]}


# ─── C · 动作链成功时，页面的"墙"不许冒充这一发的失败 ──────────────────────

def test_a_run_chain_does_not_get_a_wall_verdict_as_its_error():
    """登录墙那句描述的是**页**，不是**这条链办没办成**。"""
    r = _page(content=["请登录", "账号", "密码"], metadata=dict(CHAIN_OK))
    assert _is_auth_wall(r), "用例前提：这份 fixture 必须真被判成墙，否则下面什么都没说"
    with _arg_notes_scope() as notes:
        _annotate_quality(r)
    assert r.error == "", f"链全 ok，error 却宣布整件事失败: {r.error}"
    assert any("auth_wall" in n for n in notes), (
        f"降级不等于丢弃：那句话必须还在 notes/summary 里，找到 {notes!r}")


def test_a_failed_step_still_sets_the_error_as_it_always_did():
    """不许把这一刀变成"动作一跑就什么都算成功"：真失败照常出声。"""
    r = _page(content=["请登录"], metadata=dict(CHAIN_FAILED))
    with _arg_notes_scope():
        _annotate_quality(r)
    assert r.error.startswith("auth_wall_detected"), r.error
    assert "content_issue" not in (r.metadata or {}), "真失败的那一发不许被降级成备注"


def test_the_advice_for_a_run_chain_names_the_receipts_not_a_repeat():
    """P06 的代价是"重复填表"：recover_with 再怎么写，只要它建议重来一遍就是假话。"""
    r = _page(content=["请登录"], content_ok=False, metadata=dict(CHAIN_OK))
    with _arg_notes_scope():
        _annotate_quality(r)
    d = _diagnose(r)
    assert d is not None, "内容判定失败仍然要有结构化原因，不许变成干净成功"
    assert "actions" in d["recover_with"], d
    assert "switch source" not in d["recover_with"], d
    assert d["retryable"] is False, (
        f"文案说别重发，retryable 却 True 就是自相矛盾: {d}")


# ─── A · 交付了正文的响应不许被说成"没有可用文字" ───────────────────────────

def test_delivered_text_is_never_described_as_no_usable_text():
    """P09 那次：交回的正是可引原文，recover_with 却教人换源。"""
    text = " ".join(f"sentence {i} about parcel boundaries and accuracy"
                    for i in range(40))
    r = _page(content=[text], content_ok=False, page_type="nav_only")
    d = _diagnose(r)
    assert d is not None
    assert "no usable text" not in d["recover_with"], d
    assert "switch source" not in d["recover_with"], d
    assert d["code"] != "no_content", d
    # 出路必须指向"怎么把丢掉的拿回来"，而不是"换个源"
    assert any(lever in d["recover_with"] for lever in
               ("extraction_type", "css_selector", "focus")), d


def test_a_genuinely_empty_answer_still_says_so():
    """反向：真的一个字都没有时，旧句子仍然是对的，不许被这一刀改成含糊。"""
    d = _diagnose(_page(content=[], content_ok=False))
    assert "no usable text" in d["recover_with"], d


# ─── B · 判失败却不给出路，是另一半缺陷 ────────────────────────────────────

def test_a_failing_verdict_always_comes_with_a_way_out():
    """P08：content_ok=false 而 next_action="" —— 用户读完只知道"不行"，不知道下一步。"""
    r = _page(content=["导航 首页 产品 登录 关于我们"], content_ok=False,
              error="nav_only_detected: page chrome, no article body",
              page_type="nav_only")
    _summary, next_action, _ok = _agent_hints(r)
    assert next_action.strip(), f"判它失败却一个字出路都没给: {next_action!r}"


def test_the_way_out_for_a_thin_page_names_the_lever_not_a_shrug():
    r = _page(content=["导航 首页 产品 登录 关于我们"], content_ok=False,
              error="nav_only_detected: page chrome, no article body",
              page_type="nav_only")
    _s, next_action, _ok = _agent_hints(r)
    assert "nav_only" in next_action or "content_ok" in next_action, next_action
    assert next_action != "no parameter fixes it", next_action


# ─── 前提自查 ──────────────────────────────────────────────────────────────

def test_content_ok_is_still_the_content_verdict_it_was():
    """这一刀不许把 content_ok 变成"调用成功位"：它说的仍是正文可用性。"""
    r = _page(content=["请登录"], content_ok=False, metadata=dict(CHAIN_OK))
    with _arg_notes_scope():
        _annotate_quality(r)
    _s, _na, ok = _agent_hints(r)
    assert ok is False, "content_ok 说的仍是正文可用性，不许变成调用成功位"


# ─── D · 真路径上的接线：判决必须知道链跑过 ────────────────────────────────
#
# 上面 C 组的用例是**手工**把 `metadata.actions` 摆进 fixture 的，所以它绿灯亮着而
# 真实调用照旧报错（P4/P5 两轮实测：顶层 `error=auth_wall_detected`、`content_issue`
# 缺席）。根因是顺序，不是判据，而且比"上线太晚"更靠前一层：插桩
# （review/.runtime/p4/spy_r01_028.py）显示浏览器档的内容判决写在
# `bulk_stealthy_fetch` 的结果循环里（`_translate_response` → `_annotate_quality` →
# `_with_agent_hints`），而 `page_action.outcomes` 那时已经在手上、只是没被接过去。
# 这一组因此把替身放在**浏览器与 HTML 翻译**上，判决所在的那一圈全部真跑。

LOGIN = "https://example.com/login"
WALL_BODY = ["请登录", "账号", "密码", "Sign in to continue"]
RUN_OK = [{"action": "click #a", "ok": True}, {"action": "click #b", "ok": True}]
STEP_FAILED = [{"action": "click #a", "ok": True},
               {"action": "click #missing", "ok": False, "error": "no such element"}]


def _stub_chain(monkeypatch, receipts):
    """替身只换掉"那一台浏览器"与"HTML→正文"那一步，判决所在的循环全是真的。

    为什么不能替 `stealthy_fetch`（第一版就是这么写的，结果单测绿、真调用照旧）：
    内容判决就写在 `bulk_stealthy_fetch` 的结果循环里（`_translate_response` →
    `_annotate_quality`），替掉那一层等于把被测的接线本身替掉了。
    """
    from types import SimpleNamespace

    from dhole_mcp import server as server_mod

    class _Sess:
        _is_alive = True
        cdp_attached = False
        launch_note = ""
        _hide_canvas = False
        _block_webrtc = False

        async def seed_cookies(self, url, cookies):
            pass

        async def harvest_cookies(self, url):
            return {}

        async def fetch(self, url, **kw):
            pa = kw.get("page_action")
            if pa is not None:
                pa.outcomes = [dict(r) for r in receipts]
            return SimpleNamespace(url=url)

    async def fake_get(self, sid, expected_type=None):
        return server_mod._SessionEntry(session=_Sess(), session_type="stealthy")

    async def auto(self, **kw):
        return "auto-under-test"

    async def named(self, sid, **kw):
        return sid

    monkeypatch.setattr(MasterFetchServer, "_get_session", fake_get)
    monkeypatch.setattr(MasterFetchServer, "_ensure_auto_session", auto)
    monkeypatch.setattr(MasterFetchServer, "_ensure_session_browser", named)
    monkeypatch.setattr(server_mod, "_browser_deps_available", lambda: True)
    # 启动级身份对账（canvas/WebRTC）不是本题要量的东西：让它总是"对上"，否则
    # `bulk_stealthy_fetch` 走 `entry is None` 那一支去起真浏览器，conftest 会拦。
    monkeypatch.setattr(server_mod, "_launch_identity", lambda **kw: ("same",))
    monkeypatch.setattr(server_mod, "_session_identity", lambda s: ("same",))
    monkeypatch.setattr(
        server_mod, "_translate_response",
        lambda page, *a, **kw: ResponseModel(
            url=page.url, status=200, content=list(WALL_BODY),
            fetcher_used="stealthy", content_type="text/html"))


def _dispatch(srv, tool: str, args: dict) -> dict:
    out = asyncio.run(srv._dispatch(tool, args))
    if isinstance(out, tuple) and len(out) == 2:
        return out[1]
    return {}


def _call(tool: str, args: dict) -> dict:
    return _dispatch(MasterFetchServer(), tool, args)


def test_a_run_chain_on_the_real_path_carries_no_top_level_error(monkeypatch):
    """P06 S2 那一发的原样：链全 ok，顶层 error 却宣布整件事失败。"""
    _stub_chain(monkeypatch, RUN_OK)
    out = _call("web_act", {"url": LOGIN, "actions": [{"click": "#a"},
                                                      {"click": "#b"}]})
    assert len(out["metadata"]["actions"]) == 2, out.get("metadata")
    assert out.get("error", "") == "", f"链跑成了，error 仍说失败: {out.get('error')}"
    assert "auth_wall" in (out["metadata"].get("content_issue") or ""), (
        f"降级不等于丢弃：页面的判决必须落在 content_issue，实测 {out['metadata']!r}")
    assert "auth_wall" in (out.get("summary") or ""), out.get("summary")


def test_the_real_path_advice_never_tells_the_user_to_send_the_chain_again(monkeypatch):
    _stub_chain(monkeypatch, RUN_OK)
    out = _call("web_act", {"url": LOGIN, "actions": [{"click": "#a"},
                                                      {"click": "#b"}]})
    na = out.get("next_action") or ""
    assert na.strip(), "判它内容不行却不给出路 —— 用户读完只知道不行"
    assert "re-issue the same chain" in na or "metadata.actions" in na, na
    assert "call again" not in na, f"这一句会让填表跑第二遍: {na}"
    d = out.get("diagnosis") or {}
    assert d.get("retryable") is False, d


def test_a_chain_with_a_failed_step_still_reports_the_error(monkeypatch):
    """反向守卫：这一刀不许变成"只要跑过链就算成功"。真失败照常出声。"""
    _stub_chain(monkeypatch, STEP_FAILED)
    out = _call("web_act", {"url": LOGIN, "actions": [{"click": "#a"},
                                                      {"click": "#missing"}]})
    assert out.get("error", "").startswith("auth_wall_detected"), out.get("error")
    assert "content_issue" not in (out.get("metadata") or {}), out.get("metadata")


def test_the_annotator_reads_the_chain_before_it_reaches_the_wire():
    """判据的输入要在判决之前到场（`_chain_ran_ok` 得认浏览器层带出来的那一份）。"""
    r = _page(content=["请登录"], metadata={})
    r._action_outcomes = [dict(x) for x in RUN_OK]
    assert _chain_ran_ok(r), "回执还随行带着、尚未上线时，判据必须已经看得见它"
    with _arg_notes_scope():
        _annotate_quality(r)
    assert r.error == "", r.error
    assert str(r.metadata.get("content_issue", "")).startswith("auth_wall")


def test_a_chain_is_never_answered_from_the_url_cache(monkeypatch):
    """降级移掉的是**一次巧合**（链那发的正文从前因为 `error` 非空而不落缓存），
    所以必须确认挡在门口的仍是那道有意的闸：`web_act` 进门就 `cache_ttl=0`
    （server.py:5906）。缓存里已经躺着同一颗 URL 的干净正文时，链照样要真跑。"""
    seen = {"n": 0}

    async def fake_stealthy(self, url, **kw):
        seen["n"] += 1
        pa = kw.get("page_action")
        if pa is not None:
            pa.outcomes = [dict(r) for r in RUN_OK]
        return ResponseModel(url=url, status=200,
                             content=["Order summary", "Item subtotal: 12.00 USD",
                                      "A usable body of some length goes here"],
                             fetcher_used="stealthy", content_type="text/html")

    async def session(self, *a, **kw):
        return "sess-under-test"

    monkeypatch.setattr(MasterFetchServer, "stealthy_fetch", fake_stealthy)
    monkeypatch.setattr(MasterFetchServer, "_ensure_auto_session", session)
    monkeypatch.setattr(MasterFetchServer, "_ensure_session_browser", session)
    # 缓存是跨进程落盘的：固定的那颗 URL 会被上一轮的残留条目替本用例"预先暖好"，
    # 于是"第一次就读缓存"——前提自查就此变成假绿。每次一个没人用过的地址。
    page = f"https://example.com/cart-{uuid.uuid4().hex[:8]}"
    srv = MasterFetchServer()
    warm = _dispatch(srv, "web_read", {"url": page, "force_fetcher": "stealthy"})
    assert seen["n"] == 1, warm
    again = _dispatch(srv, "web_read", {"url": page, "force_fetcher": "stealthy"})
    assert again.get("cached") is True, f"前提：缓存里必须真躺着这一发 {again}"
    acted = _dispatch(srv, "web_act", {"url": page, "actions": [{"click": "#a"}]})
    assert seen["n"] == 2, "缓存命中替动作链答了问题 = 用户以为点下去了"
    assert not acted.get("cached"), acted
    assert (acted["metadata"] or {}).get("actions"), acted


def test_the_private_carrier_never_reaches_the_wire():
    """`_action_outcomes` 是随行的判据输入，不是给调用方的字段。"""
    r = _page(content=["请登录"], metadata={})
    r._action_outcomes = [dict(x) for x in RUN_OK]
    assert "action_outcomes" not in r.model_dump(), "私有判据不许上线"


# ─── E · 判决搬家不许留下第二种错（P5 复验抓出来的两条） ────────────────────
#
# 真 CLI 实测（review/.runtime/p4/r028-verify.jsonl）：修法生效后顶层 `error` 空了、
# `content_issue` 有了，但
#   1. summary 里 `auth_wall_detected: …` 出现了**两次** —— 浏览器档一圈
#      （`bulk_stealthy_fetch`）、`_finalize_result` 又一圈，每圈都 `_note_text` 一次；
#   2. `page_type` 从 "auth_wall" 变成**缺席** —— 那一格从前由 `error` 推出，判决搬走
#      之后页面的长相跟着丢了。读的人本来靠它认出这是一张登录页。

def test_the_demoted_verdict_is_said_once_even_when_annotated_twice():
    r = _page(content=["请登录"], metadata={})
    r._action_outcomes = [dict(x) for x in RUN_OK]
    with _arg_notes_scope() as notes:
        _annotate_quality(r)
        _annotate_quality(r)   # 真路径就是两圈：bulk 一圈、_finalize_result 又一圈
    said = [n for n in notes if "auth_wall" in n]
    assert len(said) == 1, f"同一句判决说了 {len(said)} 遍: {notes!r}"
    assert r.error == "", r.error


def test_the_envelope_still_names_the_page_shape_after_the_move():
    r = _page(content=["请登录"], metadata={})
    r._action_outcomes = [dict(x) for x in RUN_OK]
    with _arg_notes_scope():
        _annotate_quality(r)
    stamped = _with_agent_hints(r)
    assert stamped.page_type == "auth_wall", (
        f"判决搬到 content_issue 之后，page_type 不许把页面的长相弄丢："
        f"{stamped.page_type!r}")


def test_a_page_that_is_not_a_wall_still_keeps_its_own_shape():
    """反向守卫：这一格读的是判决那句，不许变成"凡是降级都叫 auth_wall"。"""
    r = _page(url="https://example.com/article", content=["正文一段。" * 40],
              metadata={})
    r._action_outcomes = [dict(x) for x in RUN_OK]
    with _arg_notes_scope():
        _annotate_quality(r)
    stamped = _with_agent_hints(r)
    assert stamped.page_type != "auth_wall", stamped.page_type
    assert "auth_wall" not in str((stamped.metadata or {}).get("content_issue") or "")


# ============================================================================
# 来源：test_advice_names_only_the_knobs_this_tool_can_take.py
# ============================================================================
# 一句建议点名的参数，必须是**这一次调用的那扇门**收得下的那一颗。

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


# ============================================================================
# 来源：test_a_pinned_page_is_the_one_the_next_call_stands_on.py
# ============================================================================
# F9 的另一半：`pin=true` 说得出话，也走得到那一页 —— 每一层都不许把这句话弄丢。
# 重名消歧：URL → URL__a_pinned_page_is_the_one_fbf2、_call → _call__a_pinned_page_is_the_one_fbf2、_page → _page__a_pinned_page_is_the_one_fbf2

URL__a_pinned_page_is_the_one_fbf2 = "https://example.com/"
ATTACH = "http://127.0.0.1:9222"
CHAIN = [{"click": "#go"}]


def _call__a_pinned_page_is_the_one_fbf2(tool: str, args: dict):
    out = asyncio.run(MasterFetchServer()._dispatch(tool, args))
    if isinstance(out, tuple) and len(out) == 2:
        return out[1]
    return {}


def _page__a_pinned_page_is_the_one_fbf2(url: str) -> ResponseModel:
    return ResponseModel(url=url, status=200, content=["body text"],
                         fetcher_used="stealthy", content_ok=True,
                         content_type="text/html")


@pytest.fixture
def tiers(monkeypatch):
    """浏览器层替身：记下每一发收到的参数，并按真浏览器的规矩写回 `pin_out`。

    `fills` 就是 `BrowserSession.fetch` 会写的那两个词（outcome / dropped），由用例
    点名 —— 这样"句子"这一半是在真形状上量的，不是我自己造一个漂亮的假回执。
    """
    box: dict = {"calls": [], "fills": [{"outcome": "pinned"}]}

    async def fake_stealthy(self, url, **kw):
        box["calls"].append(dict(kw, url=url))
        out = kw.get("pin_out")
        if isinstance(out, dict):
            out.update(box["fills"].pop(0) if box["fills"] else {"outcome": "pinned"})
        return _page__a_pinned_page_is_the_one_fbf2(url)

    async def auto(self, **kw):
        return "auto-under-test"

    async def named(self, sid, **kw):
        return sid

    monkeypatch.setattr(MasterFetchServer, "stealthy_fetch", fake_stealthy)
    monkeypatch.setattr(MasterFetchServer, "_ensure_auto_session", auto)
    monkeypatch.setattr(MasterFetchServer, "_ensure_session_browser", named)
    return box


def _sent(box: dict, n: int = -1) -> dict:
    return box["calls"][n]


class TestTheWordReachesThePageAndComesBack:

    def test_a_named_session_carries_pin_down_every_layer(self, tiers):
        _call__a_pinned_page_is_the_one_fbf2("web_act", {"url": URL__a_pinned_page_is_the_one_fbf2, "actions": CHAIN, "session_id": "bob",
                          "pin": True})
        sent = _sent(tiers)
        assert sent["pin"] is True, sent
        assert isinstance(sent["pin_out"], dict), "浏览器层没有可写回的那格"

    def test_a_page_left_open_says_so_in_the_summary(self, tiers):
        out = _call__a_pinned_page_is_the_one_fbf2("web_act", {"url": URL__a_pinned_page_is_the_one_fbf2, "actions": CHAIN, "session_id": "bob",
                                "pin": True})
        assert PIN_NOTES["pinned"] in (out.get("summary") or ""), out.get("summary")

    def test_a_reuse_says_which_page_it_stood_on(self, tiers):
        tiers["fills"] = [{"outcome": "reused"}]
        out = _call__a_pinned_page_is_the_one_fbf2("web_act", {"url": URL__a_pinned_page_is_the_one_fbf2, "actions": CHAIN, "session_id": "bob",
                                "pin": True})
        summary = out.get("summary") or ""
        assert PIN_NOTES["reused"] in summary, summary
        # 复用的那一发不该同时说"我新留了一页"以外的话：两句都在，但"pinned"那句
        # 是它自己写的，这里钉的是复用这一句必须站着。
        assert "no re-navigation" in summary or "no navigation happened" in summary, summary

    def test_a_dropped_page_is_named_before_the_new_one_is_described(self, tiers):
        """先听懂"那一页没了"，再看"这一发新留了一页" —— 顺序读起来才是因果。"""
        tiers["fills"] = [{"dropped": "ttl", "outcome": "pinned"}]
        out = _call__a_pinned_page_is_the_one_fbf2("web_act", {"url": URL__a_pinned_page_is_the_one_fbf2, "actions": CHAIN, "session_id": "bob",
                                "pin": True})
        summary = out.get("summary") or ""
        assert PIN_NOTES["ttl"] in summary, summary
        assert summary.index(PIN_NOTES["ttl"]) < summary.index(PIN_NOTES["pinned"]), summary

    def test_a_string_false_from_a_serializing_client_is_a_false(self, tiers):
        """schema 声明 boolean 而客户端把每颗都发成字符串：`"false"` 按裸值读是真。

        那一发会真的把页留在会话上，而调用方说的是"不要驻留"。
        """
        _call__a_pinned_page_is_the_one_fbf2("web_act", {"url": URL__a_pinned_page_is_the_one_fbf2, "actions": CHAIN, "session_id": "bob",
                          "pin": "false"})
        assert _sent(tiers)["pin"] is False, _sent(tiers)

    def test_the_wire_admits_the_number_of_booleans_it_reads(self):
        """`_ARG_TYPES` 与 wire 上的 boolean 属性必须一起长 —— 少一个名字就是
        上面那条归一化在这颗参数上根本不存在。"""
        from dhole_mcp.params import _ARG_TYPES

        defs = {d["name"]: d for d in MasterFetchServer._TOOL_DEFS}
        for tool_name, spec in defs.items():
            for key, prop in (spec["inputSchema"].get("properties") or {}).items():
                if '"boolean"' in str(prop):
                    assert _ARG_TYPES.get(tool_name, {}).get(key) == "bool", (
                        f"{tool_name}.{key} 声明 boolean 却没有字符串写法的路径")


class TestEveryLaneThatCannotPinSaysWhy:

    def test_an_anonymous_auto_session_has_no_page_of_yours(self, tiers):
        out = _call__a_pinned_page_is_the_one_fbf2("web_act", {"url": URL__a_pinned_page_is_the_one_fbf2, "actions": CHAIN, "pin": True})
        assert PIN_NOTES["auto"] in (out.get("summary") or ""), out.get("summary")
        assert _sent(tiers)["pin"] is False, "拒绝之后还把 True 传下去 = 两层各自决定一次"
        assert _sent(tiers)["pin_out"] is None

    @pytest.mark.asyncio
    async def test_an_attach_is_the_users_own_tabs_not_a_page_to_hold(self, tiers):
        await MasterFetchServer()._force_fetch(
            URL__a_pinned_page_is_the_one_fbf2, "stealthy", extraction_type="markdown", css_selector=None,
            cache_ttl=0, offset=0, headless=True, real_chrome=False, proxy=None,
            timeout=20000, solve_cloudflare=False, block_webrtc=False,
            hide_canvas=False, extra_headers=None, useragent=None, cookies=None,
            max_chars=1000, cdp_url=ATTACH, pin=True)
        assert _sent(tiers)["pin"] is False, _sent(tiers)

    def test_the_auto_and_the_transport_refusal_are_two_sentences(self, tiers):
        """两条形不成的路各有各的下一步。合成一句"驻留不了"会把人支去拧错的那颗旋钮：
        一条加 `session_id=` 就够了，另一条得先离开 attach 这条车道。"""
        assert PIN_NOTES["auto"] != PIN_NOTES["transport"]
        assert "session_id=" in PIN_NOTES["auto"], PIN_NOTES["auto"]
        assert "session_id=" in PIN_NOTES["transport"], PIN_NOTES["transport"]
        assert "attach" in PIN_NOTES["transport"], PIN_NOTES["transport"]
        assert "auto session" in PIN_NOTES["auto"], PIN_NOTES["auto"]

    def test_no_refusal_sentence_blames_a_parameter_the_tool_does_not_have(self):
        """C2 那一族的判据：拒绝话不许把人支到他够不着的旋钮上（`proxy=` 在 17.0
        已经不是任何工具的参数，它从 cred_defaults 落盘）。"""
        for key, text in PIN_NOTES.items():
            assert "proxy=" not in text, (key, text)
            assert "drop pin" not in text, (key, text)


class TestTheBulkLaneGuardsItsOwnClaim:
    """`bulk_stealthy_fetch` 才是看得见"这台浏览器是谁的、这一发有几页"的那一层。"""

    class _Sess:
        _is_alive = True
        cdp_attached = False
        launch_note = ""
        _hide_canvas = False
        _block_webrtc = False

        def __init__(self):
            self.kw: list[dict] = []

        async def seed_cookies(self, url, cookies):
            pass

        async def harvest_cookies(self, url):
            return {}

        async def fetch(self, url, **kw):
            self.kw.append(kw)
            out = kw.get("pin_out")
            if isinstance(out, dict):
                out["outcome"] = "pinned"
            return _page__a_pinned_page_is_the_one_fbf2(url)

    def _wire(self, monkeypatch, session):
        entry = server_mod._SessionEntry(session=session, session_type="stealthy")

        async def fake_get(self, sid, expected_type=None):
            return entry

        monkeypatch.setattr(MasterFetchServer, "_get_session", fake_get)
        monkeypatch.setattr("dhole_mcp.server._browser_deps_available", lambda: True)
        monkeypatch.setattr(server_mod, "_translate_response",
                            lambda page, *a, **kw: _page__a_pinned_page_is_the_one_fbf2(page.url))

    def _one_off_browser(self, monkeypatch, box):
        """`entry is None` 那一支自己起一台 —— 真起进程会被 conftest 拦，替身即可。

        这一支的 `session.fetch` 压根不带 `pin`：那台机器一发一换，页留不住。
        """
        from dhole_mcp import browser as browser_mod

        class _Stealthy:
            cdp_attached = False
            launch_note = ""

            def __init__(self, **kw):
                box["launch_kwargs"] = kw
                box.setdefault("own_calls", [])

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def fetch(self, url, **kw):
                box["own_calls"].append(dict(kw, url=url))
                return _page__a_pinned_page_is_the_one_fbf2(url)

        monkeypatch.setattr(browser_mod, "StealthyBrowser", _Stealthy)
        return box

    @pytest.mark.asyncio
    async def test_two_urls_one_page_is_not_pinnable_and_that_is_said(
            self, monkeypatch, tmp_path):
        monkeypatch.setenv("DHOLE_HOME", str(tmp_path))
        sess = self._Sess()
        self._wire(monkeypatch, sess)
        s = MasterFetchServer()
        with _arg_notes_scope():
            bulk = await s.bulk_stealthy_fetch(
                urls=["https://example.com/a", "https://example.com/b"],
                session_id="bob", pin=True)
        assert isinstance(bulk, BulkResponseModel)
        assert all(k["pin"] is False for k in sess.kw), sess.kw
        assert any(PIN_NOTES["bulk"] in (r.summary or "") for r in bulk.results), \
            [r.summary for r in bulk.results]

    @pytest.mark.asyncio
    async def test_an_attached_session_never_holds_a_page(self, monkeypatch, tmp_path):
        monkeypatch.setenv("DHOLE_HOME", str(tmp_path))
        sess = self._Sess()
        sess.cdp_attached = True
        self._wire(monkeypatch, sess)
        with _arg_notes_scope():
            bulk = await MasterFetchServer().bulk_stealthy_fetch(
                urls=["https://example.com/a"], session_id="bob", pin=True)
        assert sess.kw[0]["pin"] is False, sess.kw
        assert PIN_NOTES["transport"] in (bulk.results[0].summary or ""), \
            bulk.results[0].summary

    @pytest.mark.asyncio
    async def test_a_session_that_ran_on_another_browser_says_so(self, monkeypatch,
                                                                 tmp_path):
        """anti_detect 要的是启动级硬化 → 这一发落在自己的一台浏览器上，
        那一页留在另一台上：驻留的话必须跟着改口（与"logins did not ride along"同一条）。"""
        monkeypatch.setenv("DHOLE_HOME", str(tmp_path))
        sess = self._Sess()
        sess._hide_canvas = True      # 那台机器开着画布噪声，这一发要的是 off
        self._wire(monkeypatch, sess)
        box = self._one_off_browser(monkeypatch, {})
        with _arg_notes_scope():
            bulk = await MasterFetchServer().bulk_stealthy_fetch(
                urls=["https://example.com/a"], session_id="bob",
                hide_canvas=False, pin=True)
        assert sess.kw == [], "身份不符还复用那台浏览器 = 这一刀说错了自己站在哪"
        assert box["own_calls"], "这一发确实走在另一台上，替身没记录就等于没走到"
        assert all("pin" not in c for c in box["own_calls"]), box["own_calls"]
        summary = bulk.results[0].summary or ""
        assert PIN_NOTES["identity"] in summary, summary

    @pytest.mark.asyncio
    async def test_a_plain_call_still_asks_for_no_pin_at_all(self, monkeypatch,
                                                             tmp_path):
        """反向半边：不带 pin 的每一发，浏览器层收到的必须是默认值 ——
        驻留是叫出来的，不是默认存在的。"""
        monkeypatch.setenv("DHOLE_HOME", str(tmp_path))
        sess = self._Sess()
        self._wire(monkeypatch, sess)
        with _arg_notes_scope():
            await MasterFetchServer().bulk_stealthy_fetch(
                urls=["https://example.com/a"], session_id="bob")
        assert sess.kw[0]["pin"] is False, sess.kw
        assert sess.kw[0]["pin_out"] is None, sess.kw
