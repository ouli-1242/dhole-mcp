"""test_quality_3.py —— 由 8 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
import pytest
import dhole_mcp.server as server_mod
from dhole_mcp.models import ResponseModel
from dhole_mcp.quality import _over_budget_result, _with_agent_hints
from dhole_mcp.wire import _wire_dump
from dhole_mcp.quality import _SENTENCE_LINE_MIN_CHARS, _JS_SHELL_MIN_TEXT_CHARS, body_substance
from dhole_mcp.quality import _approx_tokens
from dhole_mcp.quality import _agent_hints, _is_over_budget, _report_action_outcomes
from dhole_mcp import crawl as crawl_mod
from dhole_mcp.quality import _collapsed_by_both_rulers, extraction_collapsed
from dhole_mcp.server import _attach_dom_visible_chars
from dhole_mcp.server import _all_tiers_failed_tips
from pathlib import Path


# ============================================================================
# 来源：test_the_browser_timeout_twin_names_the_lever.py
# ============================================================================
# 浏览器超时的机器可读孪生不能比 prose 瞎 —— finalize 的前缀表不许盖专项 diagnosis。

URL = "https://spa.example.com/dashboard"

_SHELL = ('<html><head><title>Loading…</title></head><body><div id="root"></div>'
          "<noscript>You need to enable JavaScript to run this app.</noscript>"
          "</body></html>")

_TIER_BASE = dict(
    extraction_type="markdown", css_selector=None, cache_ttl=0, offset=0,
    headless=True, real_chrome=False, proxy=None, solve_cloudflare=False,
    block_webrtc=False, hide_canvas=False, extra_headers=None,
    useragent=None, cookies=None,
)


class TestOverBudgetCarriesTheTwin:

    def test_a_specific_note_rides_with_a_structured_twin(self):
        twin = {"code": "timeout", "retryable": False,
                "recover_with": "force_fetcher='http'"}
        out = _over_budget_result(
            URL, 30000, 30500, "stealthy browser tier", "http→stealthy",
            "The HTTP tier had already answered. force_fetcher='http' returns it.",
            diagnosis_struct=twin)
        assert out.diagnosis == twin
        # prose 一字不删：next_action 仍带着那句专项建议
        assert "force_fetcher='http'" in out.next_action
        assert out.error.startswith("timeout: the ")

    def test_without_a_specific_note_the_prefix_table_still_answers(self):
        """没有专项知识时行为不变：finalize 的前缀表归一是兜底，不是被废掉。"""
        out = _with_agent_hints(_over_budget_result(
            URL, 30000, 30500, "the http tier", "http", ""))
        assert out.diagnosis["code"] == "timeout"
        assert out.diagnosis["retryable"] is True
        assert "raise timeout" in out.diagnosis["recover_with"]


async def _browser_ladder_over_budget(monkeypatch):
    """真升级阶梯、假网络：HTTP 层回壳页，浏览器层挂到预算烧尽。

    会话层与安装门一并换掉（tests/test_bug_report_regressions.py 的同一刀）：
    这条测试读的是阶梯与信封，不是浏览器。
    """

    async def http_shell(*args, **kwargs):
        return ResponseModel(url=URL, status=200, content=[_SHELL],
                             content_type="text/html", fetcher_used="http")

    async def hanging_browser(*a, **k):
        await asyncio.sleep(60)

    async def _no_session(**kw):
        return "auto-under-test"

    async def _installed(real_chrome=False):
        return SimpleNamespace(ok=True, detail="", waited_s=0.0)

    monkeypatch.setattr(server_mod, "ensure_chromium_async", _installed)
    monkeypatch.setattr(server_mod, "_browser_deps_available", lambda: True)
    srv = server_mod.MasterFetchServer()
    srv.get = http_shell
    srv.stealthy_fetch = hanging_browser
    srv._ensure_auto_session = _no_session
    return await srv._auto_escalate(URL, timeout=2500, **_TIER_BASE)


class TestTheLadderSaysWhatHappened:

    @pytest.mark.asyncio
    async def test_the_twin_names_the_lever_the_prose_names(self, monkeypatch):
        out = await _browser_ladder_over_budget(monkeypatch)

        assert out.error.startswith("timeout: the ")
        assert "the stealthy browser tier" in out.error
        # prose 那份是对的，一直是 —— 它只在 next_action 里
        assert "browser render" in out.next_action
        assert "force_fetcher='http'" in out.next_action
        # 机器可读那份不再与前缀表合伙说反话
        assert "force_fetcher='http'" in out.diagnosis["recover_with"]
        assert "raise timeout" not in out.diagnosis["recover_with"]
        assert out.diagnosis["retryable"] is False, (
            "原样重发同一调用，渲染照样到不了 —— retryable 说的是这件事")

    @pytest.mark.asyncio
    async def test_the_twin_rides_the_wire(self, monkeypatch):
        out = await _browser_ladder_over_budget(monkeypatch)

        dumped = _wire_dump(out)
        assert "force_fetcher='http'" in dumped["diagnosis"]["recover_with"]


# ============================================================================
# 来源：test_a_page_holding_a_real_sentence_is_not_called_nav_only.py
# ============================================================================
# 一行真的句子就是正文的证据：`nav_only` 不许在正文在场时判出来。

#: 本机 8399 靶站量到的两副真实形状（n=1 与 n=2），逐字取自 web_read 的 content。
WITH_EXTRA_TAG = "作品 1\n街景\n夜景\n人像\n第 1 篇的独有正文，用来证明这一列真的每页不同。"
WITHOUT_EXTRA_TAG = "作品 2\n街景\n夜景\n第 2 篇的独有正文，用来证明这一列真的每页不同。"

#: 靶站 /nav 实测的纯菜单形状：12 行碎片、均行 2.3 字。
PURE_NAV = "\n".join(["首页", "关于", "登录", "文档", "作品一", "集合", "注册",
                      "帮助", "联系", "投稿", "搜索", "地图"])

#: 靶站 /collections 实测的目录形状：15 行、占比 1.00、均行 16.1 字。
CATALOG = "\n".join([f"专题 {i} 的一篇长标题条目" for i in range(1, 16)])


# ─── 1 · 正文在场时不许判 nav_only ───

def test_a_measured_page_with_one_real_sentence_is_not_nav_only():
    verdict, basis = body_substance(WITH_EXTRA_TAG, visible=400)
    assert verdict != "nav_only", f"带着整句正文的页被当成菜单残渣：{verdict} / {basis}"


def test_the_two_measured_shapes_of_the_same_page_agree():
    """18 条同形状的页面不许一翻三分之一：两副实测形状必须同判。"""
    a = body_substance(WITH_EXTRA_TAG, visible=400)
    b = body_substance(WITH_EXTRA_TAG.replace("人像\n", ""), visible=400)
    c = body_substance(WITHOUT_EXTRA_TAG, visible=400)
    assert a[0] == b[0] == c[0], (a, b, c)
    assert a[0] != "nav_only", a


# ─── 2 · 反面锁：那三族照旧 ───

def test_a_page_of_only_fragments_is_still_nav_only():
    verdict, basis = body_substance(PURE_NAV, visible=666)
    assert verdict == "nav_only", f"纯菜单被这刀放过了：{verdict} / {basis}"
    assert "short fragments" in basis, basis


def test_the_whole_page_shell_branch_is_untouched():
    verdict, basis = body_substance("加载中\n<div>", visible=6)
    assert verdict == "nav_only", verdict
    assert "the whole page holds" in basis, basis


def test_a_catalog_page_is_still_ok():
    assert body_substance(CATALOG, visible=1_200)[0] == "ok"


# ─── 3 · 句子的地板是一位作者定的 ───

def test_a_tiny_fragment_with_a_full_stop_is_not_a_sentence():
    """「好的。」这种碎片不算正文 —— 地板在那颗常量上，不在这一发的判决里。"""
    body = "\n".join(["首页", "关于", "登录", "注册", "好的。"])
    assert len("好的。") < _SENTENCE_LINE_MIN_CHARS
    assert body_substance(body, visible=666)[0] == "nav_only"


def test_the_sentence_floor_is_named_once_in_the_source():
    import inspect

    from dhole_mcp import quality

    ruler = inspect.getsource(quality._body_line_shape)
    assert ruler.count("_SENTENCE_LINE_MIN_CHARS") == 1, "地板在尺子里只许写一次"
    assert inspect.getsource(quality.body_substance).count(
        "_SENTENCE_LINE_MIN_CHARS") == 0, "判决处又兜了一次 —— 第二个作者"
    assert _SENTENCE_LINE_MIN_CHARS <= 8, "地板高于一切量过的标签行就没有反面锁可言"


# ─── 4 · 判决位不扩张 ───

def test_nav_only_still_flips_content_ok_when_it_is_nav_only():
    from dhole_mcp.quality import _NOT_SUBSTANTIVE

    assert "nav_only" in _NOT_SUBSTANTIVE
    assert _JS_SHELL_MIN_TEXT_CHARS == 200


# ============================================================================
# 来源：test_the_envelope_carries_a_machine_readable_diagnosis.py
# ============================================================================
# 信封的结构化孪生：`diagnosis` 让 agent 能分支而不是字符串匹配 `error`，
# `approx_tokens` 让 agent 能预算上下文。
# 重名消歧：URL → URL__the_envelope_carries_a_m_e3ee

URL__the_envelope_carries_a_m_e3ee = "https://example.com/page"


def _hinted(**kw) -> ResponseModel:
    base = dict(url=URL__the_envelope_carries_a_m_e3ee, status=200, content=["body"], content_ok=True)
    base.update(kw)
    return _with_agent_hints(ResponseModel(**base))


class TestTheDiagnosis:
    def test_a_clean_success_carries_no_diagnosis(self):
        out = _hinted()
        assert out.diagnosis is None
        assert "diagnosis" not in _wire_dump(out)

    def test_js_shell_names_the_escalation_lever(self):
        out = _hinted(status=200, content=["shell"], error="js_shell_detected: no text")
        assert out.diagnosis["code"] == "js_shell"
        assert out.diagnosis["retryable"] is True
        assert "stealthy" in out.diagnosis["recover_with"]

    def test_a_refusal_is_not_retryable(self):
        out = _hinted(status=200, content=[], error="robots_disallowed: Disallow: /")
        assert out.diagnosis["code"] == "robots_disallowed"
        assert out.diagnosis["retryable"] is False

    def test_a_timeout_is_retryable(self):
        out = _hinted(status=0, content=[],
                      error="timeout: the http tier ran out of budget")
        assert out.diagnosis["code"] == "timeout"
        assert out.diagnosis["retryable"] is True

    def test_a_diagnosis_written_at_construction_is_not_overwritten(self):
        """专项路径构造时写好的结构化孪生不许被前缀表重新归一盖掉。

        浏览器超时路径在 finalize 之前就知道这次的真相：HTTP 层已经答了，烧完的
        是渲染 —— recover_with 点名 force_fetcher='http'。前缀表按 "timeout" 前缀
        归一出的泛化句恰是 "raise timeout, or switch source"，正是那句专项建议
        要拦的动作（第四轮校准在 /shell/slow 夹具上实测到的盖写）。
        """
        out = _hinted(
            status=0, content=[],
            error=("timeout: the 30000ms call budget ran out during the stealthy "
                   "browser tier. No content was extracted."),
            diagnosis={"code": "timeout", "retryable": False,
                       "recover_with": "force_fetcher='http'"})
        assert out.diagnosis["recover_with"] == "force_fetcher='http'"
        assert out.diagnosis["retryable"] is False

    def test_an_unlisted_error_still_gets_a_stable_code(self):
        """新错误前缀也能有个稳定的枚举名，不至于静默缺席。"""
        out = _hinted(status=0, content=[], error="weird_new_failure: something happened")
        assert out.diagnosis["code"] == "weird_new_failure"

    def test_a_broken_browser_makes_the_retry_untruthful(self):
        """建议"再抓一次会升级"对一台起不来的浏览器是假话 —— retryable 要读事实。"""
        out = _hinted(status=200, content=["shell"],
                      error="js_shell_detected: no text",
                      escalation_path="browser_binary_missing")
        assert out.diagnosis["code"] == "js_shell"
        assert out.diagnosis["retryable"] is False
        assert "doctor" in out.diagnosis["recover_with"]

    def test_truncation_is_a_reason_to_reason(self):
        out = _hinted(is_truncated=True, next_offset=8000)
        assert out.diagnosis["code"] == "truncated"
        assert out.diagnosis["retryable"] is True
        assert "offset=8000" in out.diagnosis["recover_with"]

    def test_empty_content_without_an_error_is_no_content(self):
        out = _hinted(status=200, content=[])
        assert out.content_ok is False
        assert out.diagnosis["code"] == "no_content"
        assert out.diagnosis["retryable"] is False

    def test_the_diagnosis_rides_the_wire_when_present(self):
        out = _hinted(status=200, content=["shell"], error="js_shell_detected: no text")
        dumped = _wire_dump(out)
        assert dumped["diagnosis"]["code"] == "js_shell"
        assert dumped["diagnosis"]["retryable"] is True


class TestTheTokenEstimate:
    def test_latin_content_is_about_a_quarter(self):
        assert _approx_tokens(["a" * 400]) == 100

    def test_cjk_content_is_not_under_counted(self):
        """中文按 chars/4 会低报约 4 倍 —— 正是要预算上下文的人最怕的方向。"""
        assert _approx_tokens(["中" * 100]) == 100

    def test_mixed_scripts_are_counted_separately(self):
        # 2 个汉字（各 1 token）+ 4 个拉丁字符（1 token）
        assert _approx_tokens(["中文abcd"]) == 3

    def test_empty_content_is_zero_and_off_the_wire(self):
        assert _approx_tokens([]) == 0
        assert _approx_tokens([""]) == 0
        assert "approx_tokens" not in _wire_dump(_hinted(status=200, content=[]))

    def test_it_rides_the_wire_when_there_is_content(self):
        dumped = _wire_dump(_hinted(content=["a" * 40]))
        assert dumped["approx_tokens"] == 10


# ============================================================================
# 来源：test_a_chain_cut_by_its_budget_says_which_steps_ran.py
# ============================================================================
# 预算掐断一条动作链时，回执要说清哪几步已经真的落在页面上了（D-35b）。

def _cut(stage: str = "actions (stealthy) tier") -> ResponseModel:
    return _over_budget_result("http://127.0.0.1:5331/form", 25000, 26518,
                               stage, "stealthy")


def _page_action(*outcomes):
    return type("PA", (), {"outcomes": list(outcomes)})()


class TestTheBudgetCutIsNotANetworkFailure:

    def test_the_summary_names_the_clock_not_the_network(self):
        summary, _next, ok = _agent_hints(_cut())
        assert summary.startswith("out of time · 0B markdown"), summary
        assert "network error" not in summary, "预算用完不是网络故障，没有代理可查"
        assert ok is False

    def test_a_real_network_failure_still_says_so(self):
        """反面守卫：新认的那一支不许把整个 status==0 阶梯吞掉。"""
        r = ResponseModel(url="http://127.0.0.1:1/x", status=0, content=[],
                          error="network_error: connection refused")
        summary, _next, _ok = _agent_hints(r)
        assert "network error" in summary, summary


class TestTheCutChainSaysWhichStepsRan:

    def test_the_steps_already_on_the_page_are_named(self):
        r = _cut()
        _report_action_outcomes(r, _page_action(
            {"action": "fill 'input[name=note]'", "ok": True},
            {"action": "wait 30000", "ok": False, "error": "cancelled"}))
        assert "1 of 2 actions already ran" in r.summary, r.summary
        assert "fill 'input[name=note]'" in r.summary, r.summary

    def test_the_advice_says_a_replay_is_not_free(self):
        r = _cut()
        _report_action_outcomes(r, _page_action(
            {"action": "fill 'input[name=note]'", "ok": True}))
        assert "ALREADY on the page" in r.next_action, r.next_action
        assert "replays" in r.next_action, r.next_action
        # 原来那句"raise timeout"仍然在 —— 这一刀是补话，不是换建议
        assert "Raise timeout" in r.next_action, r.next_action

    def test_a_summary_that_was_empty_does_not_gain_a_separator(self):
        r = _cut()
        r.summary = ""
        _report_action_outcomes(r, _page_action(
            {"action": "click 'a.next'", "ok": True}))
        assert not r.summary.startswith(" · "), r.summary
        assert "None" not in r.summary, r.summary

    def test_nothing_is_claimed_when_no_step_ran(self):
        """第一条动作就没跑完 —— 不能顺手宣布"有步骤已经落在页面上"。"""
        r = _cut()
        _report_action_outcomes(r, _page_action(
            {"action": "fill 'input[name=note]'", "ok": False, "error": "no such element"}))
        assert "already ran" not in r.summary, r.summary


class TestAFinishedChainIsNotDescribedAsCut:
    """反面守卫：那句话只属于被时钟掐断的那一次。"""

    def test_a_normal_answer_keeps_its_own_receipt(self):
        r = ResponseModel(url="http://127.0.0.1:5331/form", status=200,
                          content=["ok"], extracted_type="markdown")
        r.summary = "200 OK · 2B markdown · stealthy"
        _report_action_outcomes(r, _page_action(
            {"action": "fill 'input[name=note]'", "ok": True},
            {"action": "click 'button[name=send]'", "ok": True, "submitted": True}))
        assert "already ran" not in r.summary, r.summary
        assert "ALREADY on the page" not in (r.next_action or ""), r.next_action

    def test_the_cut_predicate_is_the_one_author(self):
        """判"是不是预算掐断"只有 `_is_over_budget` 一颗，这里不许另起一套字符串匹配。"""
        assert _is_over_budget(_cut()) is True
        assert _is_over_budget(ResponseModel(
            url="http://x", status=0, content=[],
            error="network_error: timeout of 30s exceeded")) is False


# ============================================================================
# 来源：test_a_heavy_nav_does_not_call_a_complete_article_lost.py
# ============================================================================
# R04-016a: heavy chrome must not make a complete article look collapsed.

ARTICLE = "word " * 1_000          # ~5,000 visible characters
CHROME = "menu item " * 4_000      # ~40,000 visible characters


def _heavy_nav(article: str = ARTICLE) -> str:
    return ("<html><body><nav>" + CHROME +
            "</nav><article><p>" + article + "</p></article></body></html>")


def _plain(article: str = ARTICLE) -> str:
    return "<html><body><article><p>" + article + "</p></article></body></html>"


def test_a_complete_article_under_heavy_chrome_is_not_collapsed():
    result = ResponseModel(
        status=200, content=["x" * 4_000], url="https://e.test/a",
        content_type="text/html", fetcher_used="http",
        visible_text_chars=45_000,
    )
    result._dom_chrome_free_chars = 4_100

    assert extraction_collapsed(result) is False
    assert _collapsed_by_both_rulers(result, 4_000) is False


def test_a_real_loss_under_the_same_chrome_is_still_collapsed():
    result = ResponseModel(
        status=200, content=["tiny"], url="https://e.test/a",
        content_type="text/html", fetcher_used="http",
        visible_text_chars=45_000,
    )
    result._dom_chrome_free_chars = 4_100

    assert extraction_collapsed(result) is True


def test_an_unmeasured_second_ruler_keeps_the_old_verdict():
    result = ResponseModel(
        status=200, content=["x" * 1_000], url="https://e.test/a",
        content_type="text/html", fetcher_used="http",
        visible_text_chars=45_000,
    )

    assert result._dom_chrome_free_chars == -1
    assert extraction_collapsed(result) is True


def test_a_suspected_large_collapse_measures_the_prose_floor():
    result = ResponseModel(
        status=200, content=["tiny"], url="https://e.test/a",
        content_type="text/html", fetcher_used="http",
    )

    _attach_dom_visible_chars(result, _heavy_nav(), "https://e.test/a")

    assert result.visible_text_chars > 2_000
    assert result._dom_chrome_free_chars > 0, (
        "large suspected collapse skipped the second ruler")


def test_a_healthy_large_body_does_not_pay_for_the_second_parse():
    result = ResponseModel(
        status=200, content=[ARTICLE], url="https://e.test/a",
        content_type="text/html", fetcher_used="http",
    )

    _attach_dom_visible_chars(result, _plain(), "https://e.test/a")

    assert result._dom_chrome_free_chars == -1, (
        "a healthy page paid for a parse it did not need")


def test_the_crawl_row_uses_the_same_second_ruler():
    stats: dict = {}
    crawl_mod._measure_row(
        stats, _heavy_nav(ARTICLE), ARTICLE, "article",
        "https://e.test/a", "https://e.test/")

    assert not stats.get("extraction_collapsed"), stats
    assert stats["visible_chars"] > 2 * stats["extracted_chars"]


def test_the_crawl_row_still_flags_a_real_loss():
    stats: dict = {}
    crawl_mod._measure_row(
        stats, _heavy_nav(ARTICLE), "tiny", "article",
        "https://e.test/a", "https://e.test/")

    assert stats.get("extraction_collapsed") is True, stats


# ============================================================================
# 来源：test_an_answered_status_is_not_described_as_a_bot_block.py
# ============================================================================
# 站点答过的状态码，不许在另一格里被说成"墙"（2026-10-08 重启 MCP 后的 live 复核 · D-28）

ALL_TIERS_PREFIX = "all_tiers_failed: http_503 (HTTP status 503)\nAttempted: HTTP -> Stealthy"


def _failed(status: int, *, content: str = "", url: str = "https://origin.test/page",
            error: str | None = None) -> tuple[str, str, bool]:
    result = ResponseModel(
        url=url, status=status, content=[content] if content else [],
        fetcher_used="http→stealthy",
        error=error if error is not None else (
            f"{ALL_TIERS_PREFIX}\n{_all_tiers_failed_tips(f'http_{status}', status)}"),
    )
    return _agent_hints(result)


class TestAnAnsweredStatusIsNamedAsAnAnswer:

    def test_a_503_that_every_tier_saw_is_not_reported_as_a_wall(self):
        _summary, next_action, _ok = _failed(503)
        assert "HTTP 503" in next_action, next_action
        assert "unbypassable protection" not in next_action, next_action
        assert "DataDome" not in next_action and "Turnstile" not in next_action, next_action

    def test_it_does_not_claim_the_page_is_unreachable_either(self):
        """`error` 说"答案到了"，`next_action` 就不许说"够不着"——要说也得是否认。"""
        _summary, next_action, _ok = _failed(503)
        assert "the origin answered" in next_action, next_action
        assert "neither an unreachable host" in next_action, next_action

    def test_a_429_is_named_at_429(self):
        _summary, next_action, _ok = _failed(
            429, error="all_tiers_failed: http_429 (HTTP status 429)\n"
            + _all_tiers_failed_tips("http_429", 429))
        assert "HTTP 429" in next_action, next_action
        assert "unbypassable protection" not in next_action, next_action

    def test_the_pointer_goes_to_the_field_that_already_answers(self):
        """两处不许各写一份判据：这一格只说"有答案"，含义指回 `error`。"""
        _summary, next_action, _ok = _failed(503)
        assert "`error`" in next_action, next_action


class TestSilenceStillGetsTheOpenQuestion:
    """反向锁：真的一个字节都没答时，catch-all 那两句仍是唯二可能的解释。"""

    def test_no_answer_at_all_still_names_the_two_candidates(self):
        result = ResponseModel(url="https://origin.test/page", status=0, content=[],
                               fetcher_used="http→stealthy",
                               error="all_tiers_failed: unknown (HTTP status 0)\n"
                                    + _all_tiers_failed_tips("unknown", 0))
        _summary, next_action, _ok = _agent_hints(result)
        assert "unbypassable protection" in next_action or "unreachable" in next_action, \
            next_action

    def test_a_private_target_that_ANSWERED_is_not_asked_whether_it_is_up(self):
        """D-08b 的反向锁：那句"本地服务是不是没起"只属于**一个字节都没答**的那一族。
        回环端口自己回了 503，就说明它在听 —— 再问"是不是没起"是新的假话。"""
        _summary, next_action, _ok = _failed(503, url="http://127.0.0.1:3000/api")
        assert "local service" not in next_action, next_action
        assert "HTTP 503" in next_action, next_action


# ============================================================================
# 来源：test_a_session_cache_hit_says_the_copy_may_be_stale.py
# ============================================================================
# R2刀5（P2-6）：登录会话命中的是**匿名时代**的缓存体，而响应一个字都不提。

ROOT = Path(__file__).resolve().parents[1] / "src" / "dhole_mcp" / "skills" / "dhole-web"


def _body() -> list[str]:
    return ["real page text " * 30]


def _finalize(**kw) -> ResponseModel:
    return _with_agent_hints(ResponseModel(url="https://shop.example.test/account",
                                           status=200, content=_body(),
                                           content_ok=True, **kw))


class TestTheCorpusNamesTheFreshRead:
    """语料守卫：登录那两行必须说出「回读要 cache_ttl=0」。"""

    def test_recipes_login_flow_says_it(self):
        text = (ROOT / "references" / "recipes.md").read_text(encoding="utf-8")
        login = text.split("## Sites behind login", 1)[1].split("\n## ", 1)[0]
        assert "cache_ttl=0" in login, login
        assert "cache is keyed by URL" in login or "not by" in login

    def test_skill_login_row_says_it(self):
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        row = next(line for line in text.splitlines()
                   if "Log into a site WITHOUT handing over a password" in line)
        assert "cache_ttl=0" in row, row


class TestTheCacheHitUnderASessionSaysSo:

    def test_a_session_cache_hit_carries_the_note(self):
        out = _finalize(cached=True, session_id="acct")
        assert "predate" in out.summary, out.summary
        assert "cached" in out.summary

    def test_a_cache_hit_without_a_session_says_nothing(self):
        """没有会话就没有"你的登录态"这回事——那句注记会变成一个假问题。"""
        out = _finalize(cached=True)
        assert "predate" not in out.summary, out.summary

    def test_a_live_fetch_under_a_session_says_nothing(self):
        out = _finalize(cached=False, session_id="acct")
        assert "predate" not in out.summary, out.summary

    def test_the_note_is_appended_after_the_existing_cache_marker(self):
        """注记是追加：`cache · cached` 那两个词本来就是"这一发没发包"的信号。"""
        base = _finalize(cached=True, fetcher_used="cache").summary
        with_session = _finalize(cached=True, fetcher_used="cache",
                                 session_id="acct").summary
        assert with_session.startswith(base), (base, with_session)
        assert "cached" in base and "predate" in with_session[len(base):]


# ============================================================================
# 来源：test_a_redirect_shell_is_not_answered_as_content.py
# ============================================================================
# 客户端跳转页不是内容：page_type=redirect 时 content_ok 必须为假，并给出去哪看的出路。

SCRIPT = """<script>

    var num = 13;
    var href = "./2026/issue_13006/";

    if (num == '0') {
        if (href) {
            location.href='./2026/issue_13006/';
        } else {
            location.href='./2025/issue_12466/';
        }
    } else {
        location.href='./2026/issue_13026/';
    };
</script>"""


def test_a_js_redirect_shell_is_not_answered_as_content():
    r = ResponseModel(
        status=200, url="https://example.com/r", content=[SCRIPT],
        content_type="text/html", fetcher_used="http", page_type="redirect",
        visible_text_chars=4572, total_size_bytes=16000,
    )
    summary, next_action, ok = _agent_hints(r)
    assert ok is False, "跳转脚本被当成可引用的正文"
    lowered = next_action.lower()
    assert "stealthy" in lowered or "redirect" in lowered, next_action


def test_the_redirect_verdict_is_the_same_on_diagnosis_and_next_action():
    """同一封里的三只手（content_ok / diagnosis / next_action）必须都指向「跳转」。"""
    from dhole_mcp.quality import _diagnose

    r = ResponseModel(
        status=200, url="https://example.com/r", content=[SCRIPT],
        content_type="text/html", fetcher_used="http", page_type="redirect",
        visible_text_chars=4572, total_size_bytes=16000, content_ok=False,
    )
    d = _diagnose(r)
    assert d is not None
    assert d["code"] == "redirect", d
    assert "stealthy" in d["recover_with"], d
