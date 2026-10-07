"""浏览器超时的机器可读孪生不能比 prose 瞎 —— finalize 的前缀表不许盖专项 diagnosis。

现象（2026-10-07 第四轮校准，回环夹具 /shell/slow 实测）：HTTP 层拿到 200 壳页，
升级后的浏览器渲染挂到预算烧尽，信封 diagnosis = {code: timeout, recover_with:
"raise timeout, or switch source"}。而 server.py 为这条路径写的专项建议（"It was
the browser render, not the page, that ran out of time - force_fetcher='http'
returns the HTTP tier's answer instead of waiting for a render that cannot
arrive"）只活在 next_action 里 —— _over_budget_result 只收 prose，把它拼进
next_action；finalize 时 _with_agent_hints 无条件 _diagnose，前缀表按 "timeout"
前缀把孪生归成泛化句。17.0 对 diagnosis 的承诺是「agent 分支而不用正则 prose」；
此刻机器可读那份给的建议（调大 timeout）恰是专项 prose 要拦的：HTTP 层已经答了，
再抬只是多等一个到不了的渲染。

判决：_over_budget_result 直接携带结构化孪生（diagnosis_struct），_with_agent_hints
优先读现成值，前缀表归一降级为「没有现成值时的兜底」。prose 一个字不删 —— 它是
给人读的那一份。retryable=False 按 fields.md 的口径：原样重发同一调用，答案不会变
（渲染照样到不了）；能变答案的是 recover_with 点名的杠杆。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import dhole_mcp.server as server_mod
from dhole_mcp.models import ResponseModel
from dhole_mcp.quality import _over_budget_result, _with_agent_hints
from dhole_mcp.wire import _wire_dump

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
