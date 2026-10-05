"""批次14：回执说真话 —— check_login 的分档回答、死域名的归因、focus 续读的可见性。

实测（2026-10-06 真机全真模拟）：`check_login=true` 在 HTTP 档的返回体里字段整个
缺席（`login_check=None` 被 wire 折叠删掉，"查了但这档答不了"与"没查"字节相同）；
不存在的域名拿到的是 Turnstile/换代理建议——从沉默里不可证伪；offset 续读的正文里
focus 头消失，无法确认 focus 是否重放。这三处都是"回执不回答它被问的问题"。
"""

import inspect
from unittest.mock import AsyncMock

import pytest

from dhole_mcp import server as srv
from dhole_mcp.errors import classify_network_error
from dhole_mcp.server import (MasterFetchServer, ResponseModel,
                              _all_tiers_failed_tips, _attach_login_probe_gap)

URL = "https://example.com/"


def _result(**kw) -> ResponseModel:
    base = dict(status=200, content=["<html>hello</html>"], url=URL,
                fetcher_used="http", extracted_type="markdown")
    base.update(kw)
    return ResponseModel(**base)


def _server(**mocks) -> MasterFetchServer:
    server = MasterFetchServer()
    for name, ret in mocks.items():
        setattr(server, name, AsyncMock(return_value=ret))
    return server


class TestCheckLoginAnswersByTier:

    @pytest.mark.asyncio
    async def test_auto_http_result_marks_the_probe_unavailable(self):
        out = await _server(_auto_escalate=_result()).web_read(
            URL, check_login=True, cache_ttl=0)
        assert out.login_check is not None, "请求了 check_login 却连标记都没有"
        assert out.login_check["logged_in"] is None
        assert out.login_check["method"].startswith("unavailable")
        assert "stealthy" in out.next_action

    @pytest.mark.asyncio
    async def test_forced_http_tier_marks_the_probe_unavailable(self):
        out = await _server(get=_result()).web_read(
            URL, force_fetcher="http", check_login=True, cache_ttl=0)
        assert out.login_check is not None
        assert out.login_check["method"].startswith("unavailable")

    @pytest.mark.asyncio
    async def test_stealthy_results_are_never_marked(self):
        """浏览器档的探测通路一字未动：探针没跑时也不许替它发标记。"""
        out = await _server(_auto_escalate=_result(fetcher_used="stealthy")).web_read(
            URL, check_login=True, cache_ttl=0)
        assert out.login_check is None

    @pytest.mark.asyncio
    async def test_not_asked_stays_silent(self):
        out = await _server(_auto_escalate=_result()).web_read(URL, cache_ttl=0)
        assert out.login_check is None

    def test_cache_hits_get_the_marker_through_the_helper(self):
        out = _attach_login_probe_gap(_result(fetcher_used="cache"), check_login=True)
        assert out.login_check["method"].startswith("unavailable")


class TestAllTiersFailedTips:

    def test_unknown_and_silent_points_at_reachability_not_bots(self):
        tips = _all_tiers_failed_tips("unknown", 0)
        assert "No HTTP response" in tips and "spelling" in tips
        assert "Cloudflare" not in tips and "proxy" not in tips

    def test_a_status_means_a_wall_is_back_on_the_table(self):
        assert "Cloudflare" in _all_tiers_failed_tips("unknown", 403)

    def test_known_categories_keep_their_tips_even_when_silent(self):
        assert "unreachable" in _all_tiers_failed_tips("connection_refused", 0)
        assert "browser" in _all_tiers_failed_tips("browser_launch", 0)

    def test_fake_ip_addresses_classify_as_refused(self):
        category, _ = classify_network_error(
            "connect to 198.18.0.32 failed after 2000ms timeout")
        assert category == "connection_refused"


class TestFocusContinuationIsVisible:

    @pytest.mark.asyncio
    async def test_offset_readback_says_focus_was_reapplied(self):
        # mock 停在 HTTP 层（server.get），让真实的 _finalize_result →
        # _apply_chunking 链跑起来 —— mock _auto_escalate 会把分页与摘要一起绕掉。
        server = _server(get=_result(content=["word " * 2000]))
        first = await server.web_read(URL, focus="word", max_content_chars=100,
                                      cache_ttl=0)
        second = await server.web_read(URL, focus="word", max_content_chars=100,
                                       offset=first.next_offset, cache_ttl=0)
        assert first.next_offset > 0, "夹具没有产生分页，断言对象不存在"
        assert "focus re-applied" in second.summary
        assert second.next_offset > first.next_offset, "分页算术不许被动过"


class TestCdpAttachPathSaysAttachNotTheTier:

    def test_success_path_dropped_the_tier_suffix(self):
        src = inspect.getsource(srv)
        assert '"cdp-attach"' in src
        assert '"cdp-attach:stealthy"' not in src
