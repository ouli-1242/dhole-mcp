# -*- coding: utf-8 -*-
"""R01-028 · 旗标与自然语言不许各算各的（本轮最大簇，10 次实测 / 5 条代码路径）。

现场摘要（全部来自本轮真实调用，逐份见 `sessions/`）：

* P06 S1/S2：动作链三条全 `ok`（最后一条是 pending_approval，本来就该停），顶层却是
  `error="auth_wall_detected: page is a login/sign-in wall, not content"` + `content_ok=false`。
  L1 用户把 error 当作"整件事失败"，于是**把同一条填表链再发一次**。
* P08 S3a/S3b：focus 交回 525B / 361B 正文，`content_ok=false`，`next_action` 是**空串** ——
  判它失败，却不给出路。
* P09 S4（最危险的一次）：交回的正文正是用户要的官方可引原文，
  `diagnosis.recover_with` 却写着 "the page carried no usable text - switch source" ——
  这句话与它旁边那份 `content` 直接矛盾，用户因此**放弃正确的源**。
* P05 S5 / P07 S1 / P10 S1b、S7b：同一族（截断、批量行、discover 回退都在把"内容质量"
  与"这次调用成没成"混进一颗旗标）。

三条各自独立的修法，共同点只有一个：**判决不许否认同一份响应里已经交付的东西。**

P4 复测又加了一条：修法写在下游是无效的。第一版把降级放在 `_annotate_quality` 里、
判据读 `metadata.actions` —— 而那一格由 `server._report_action_outcomes` 在信封写好
**之后**才填，真调用实测照旧（`error=auth_wall_detected` / `content_issue` 缺席）。
D 组因此打在真路径上：浏览器跑完链 → 回执随行到判决 → 判决才有权说话。
"""

from __future__ import annotations

import asyncio
import uuid

from dhole_mcp.context import _arg_notes_scope
from dhole_mcp.models import ResponseModel
from dhole_mcp.quality import (_agent_hints, _annotate_quality, _chain_ran_ok,
                               _diagnose, _is_auth_wall, _with_agent_hints)
from dhole_mcp.server import MasterFetchServer


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
