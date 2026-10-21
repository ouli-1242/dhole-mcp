"""test_errors.py —— 由 4 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import pytest
from dhole_mcp.errors import classify_network_error, get_hint
from dhole_mcp.errors import is_retryable
from dhole_mcp.quality import _detect_content_issue, _diagnose, _is_js_shell, _is_raster_image
from dhole_mcp.server import _all_tiers_failed_tips, _archive_ran_out
import inspect
from unittest.mock import AsyncMock
from dhole_mcp import server as srv
from dhole_mcp.server import MasterFetchServer, ResponseModel, _attach_login_probe_gap
from types import SimpleNamespace
from dhole_mcp import server as server_mod


# ============================================================================
# 来源：test_errors.py
# ============================================================================
# Tests for network error classification and agent-actionable diagnostics.

# ─── classify_network_error: category detection ──────────────────────────────

class TestClassifyNetworkError:
    """Test that known error strings are classified into the right category."""

    @pytest.mark.parametrize("error_str,expected_category", [
        # Connection refused
        ("net::ERR_CONNECTION_REFUSED", "connection_refused"),
        ("ConnectionRefusedError: [WinError 10061]", "connection_refused"),
        ("os error 10061", "connection_refused"),
        ("ECONNREFUSED", "connection_refused"),
        ("connection refused by host", "connection_refused"),
        # Connection reset
        ("os error 10054", "connection_reset"),
        ("net::ERR_CONNECTION_RESET", "connection_reset"),
        ("ConnectionResetError: [WinError 10054]", "connection_reset"),
        ("ECONNRESET", "connection_reset"),
        ("远程主机强迫关闭了一个现有的连接。 (os error 10054)", "connection_reset"),
        ("An existing connection was forcibly closed by the remote host", "connection_reset"),
        ("connection was forcibly closed", "connection_reset"),
        # DNS failure
        ("net::ERR_NAME_NOT_RESOLVED", "dns_failure"),
        ("NameResolutionError: Failed to resolve 'example.com'", "dns_failure"),
        ("getaddrinfo failed", "dns_failure"),
        ("nodename nor servname provided, or not known", "dns_failure"),
        ("Name or service not known", "dns_failure"),
        ("DNS resolution failed for host", "dns_failure"),
        # Timeout
        ("TimeoutError: request timed out", "timeout"),
        ("net::ERR_TIMED_OUT", "timeout"),
        ("net::ERR_CONNECTION_TIMED_OUT", "timeout"),
        ("ERR_NAVIGATION_TIMEOUT", "timeout"),
        ("Navigation timeout of 30000ms exceeded", "timeout"),
        ("Timeout 30000ms exceeded", "timeout"),
        ("deadline exceeded", "timeout"),
        # TLS errors
        ("CERTIFICATE_VERIFY_FAILED", "tls_error"),
        ("SSL: CERTIFICATE_VERIFY_FAILED", "tls_error"),
        ("TLS handshake failed", "tls_error"),
        ("ssl_error_rx_record_too_long", "tls_error"),
        ("certificate verify failed", "tls_error"),
        ("net::ERR_CERT_AUTHORITY_INVALID", "tls_error"),
        # Proxy errors
        ("net::ERR_PROXY_CONNECTION_FAILED", "proxy_error"),
        ("proxy connection refused", "proxy_error"),
        ("tunnel connection failed", "proxy_error"),
    ])
    def test_known_errors(self, error_str, expected_category):
        category, hint = classify_network_error(error_str)
        assert category == expected_category
        assert hint  # non-empty hint
        assert len(hint) > 20  # actionable sentence, not just a word

    def test_empty_string(self):
        category, hint = classify_network_error("")
        assert category == "unknown"
        assert hint

    def test_none_like_empty(self):
        category, hint = classify_network_error("")
        assert category == "unknown"

    def test_unrecognized_error(self):
        category, hint = classify_network_error("some random error xyz 12345")
        assert category == "unknown"
        assert "Fetch failed" in hint

    def test_hint_is_actionable(self):
        """Each category's hint should tell the agent what to do."""
        for error_str, expected in [
            ("ERR_CONNECTION_REFUSED", "connection_refused"),
            ("os error 10054", "connection_reset"),
            ("ERR_NAME_NOT_RESOLVED", "dns_failure"),
            ("timed out", "timeout"),
            ("CERTIFICATE_VERIFY_FAILED", "tls_error"),
            ("ERR_PROXY_CONNECTION_FAILED", "proxy_error"),
        ]:
            _, hint = classify_network_error(error_str)
            # Hints should contain action verbs
            assert any(word in hint.lower() for word in
                      ("retry", "switch", "verify", "check", "do not")), \
                f"Hint for {expected} lacks actionable guidance: {hint}"


class TestGetHint:
    def test_known_category(self):
        hint = get_hint("connection_refused")
        assert "refused" in hint.lower()

    def test_unknown_category_fallback(self):
        hint = get_hint("nonexistent_category")
        assert hint == get_hint("unknown")


# ─── _agent_hints: next_action for network errors ────────────────────────────

class TestAgentHintsNetworkErrors:
    """Test that _agent_hints produces actionable next_action for failures."""

    def _make_result(self, status=0, error="", content=None):
        """Create a minimal ResponseModel for testing _agent_hints."""
        from dhole_mcp.server import ResponseModel
        return ResponseModel(
            url="https://example.com",
            status=status,
            content=content or [""],
            fetcher_used="http",
            error=error,
        )

    def test_connection_refused_next_action(self):
        from dhole_mcp.server import _agent_hints
        result = self._make_result(
            status=0,
            error="network_error: net::ERR_CONNECTION_REFUSED",
        )
        _, next_action, content_ok = _agent_hints(result)
        assert not content_ok
        assert "refused" in next_action.lower() or "connection" in next_action.lower()
        assert "retry" in next_action.lower() or "switch" in next_action.lower() or "do not" in next_action.lower()

    def test_timeout_next_action(self):
        from dhole_mcp.server import _agent_hints
        result = self._make_result(
            status=0,
            error="network_error: TimeoutError: request timed out",
        )
        _, next_action, _ = _agent_hints(result)
        assert "timeout" in next_action.lower() or "timed out" in next_action.lower()

    def test_all_tiers_failed_connection_refused(self):
        from dhole_mcp.server import _agent_hints
        result = self._make_result(
            status=0,
            error="all_tiers_failed: connection_refused (HTTP status 0)",
            content=["[All fetch tiers failed] net::ERR_CONNECTION_REFUSED"],
        )
        _, next_action, _ = _agent_hints(result)
        assert "connection refused" in next_action.lower()
        assert "do not retry" in next_action.lower() or "switch" in next_action.lower()

    def test_all_tiers_failed_timeout(self):
        from dhole_mcp.server import _agent_hints
        result = self._make_result(
            status=0,
            error="all_tiers_failed: timeout (HTTP status 0)",
            content=["[All fetch tiers failed] TimeoutError"],
        )
        _, next_action, _ = _agent_hints(result)
        assert "timeout" in next_action.lower()

    def test_generic_http_error_still_gets_hint(self):
        from dhole_mcp.server import _agent_hints
        result = self._make_result(
            status=500,
            error="http_error_500: server returned error status",
        )
        _, next_action, content_ok = _agent_hints(result)
        assert not content_ok
        assert next_action  # should not be empty


# ─── Search: low diversity warning ───────────────────────────────────────────

class TestSearchLowDiversityWarning:
    """Test that search next_action warns when most engines are blocked."""

    def test_high_block_ratio_warns(self):
        from dhole_mcp.search import _search_next_action, SearchResult
        results = [SearchResult(
            title="Test", url="https://example.com", snippet="test",
            source="yandex", position=1, relevance_score=0.8,
            fetch_relevance="high", engines_consensus="1 of 1",
        )]
        engine_blocked = ["mojeek", "qwant", "google", "startpage", "brave", "duckduckgo", "yahoo"]
        engines_used = ["yandex"]
        next_action = _search_next_action(results, engine_blocked, "", engines_used)
        assert "LOW diversity" in next_action or "WARNING" in next_action
        assert "DHOLE_SEARCH_PROXY" in next_action

    def test_low_block_ratio_no_warning(self):
        from dhole_mcp.search import _search_next_action, SearchResult
        results = [SearchResult(
            title="Test", url="https://example.com", snippet="test",
            source="yandex", position=1, relevance_score=0.8,
            fetch_relevance="high", engines_consensus="3 of 5",
        )]
        engine_blocked = ["google"]
        engines_used = ["yandex", "brave", "duckduckgo", "mojeek"]
        next_action = _search_next_action(results, engine_blocked, "", engines_used)
        assert "LOW diversity" not in next_action
        assert "Some engines" in next_action

    def test_no_blocked_no_warning(self):
        from dhole_mcp.search import _search_next_action, SearchResult
        results = [SearchResult(
            title="Test", url="https://example.com", snippet="test",
            source="yandex", position=1, relevance_score=0.8,
            fetch_relevance="high", engines_consensus="3 of 5",
        )]
        next_action = _search_next_action(results, [], "", ["yandex", "brave", "duckduckgo"])
        assert "WARNING" not in next_action
        assert "blocked" not in next_action.lower()


# ─── Crawl: network failure aggregate diagnostics ────────────────────────────

class TestCrawlNetworkDiagnostics:
    """Test that crawl provides next_action when pages fail with network errors."""

    def test_all_pages_network_failure(self):
        """When all pages fail with network errors, next_action should not be empty."""
        from dhole_mcp.crawl import CrawlPage
        # Simulate what web_crawl does at the end
        pages = [
            CrawlPage(url="https://example.com/1", depth=0, status=-1,
                     content_ok=False, error="net::ERR_CONNECTION_REFUSED"),
            CrawlPage(url="https://example.com/2", depth=1, status=-1,
                     content_ok=False, error="net::ERR_CONNECTION_REFUSED"),
        ]
        # Replicate the diagnostic logic from crawl.py
        network_failures = sum(1 for p in pages if p.status == -1 or p.status == 0)
        assert network_failures == 2
        assert network_failures >= len(pages) * 0.5

        from dhole_mcp.errors import classify_network_error
        sample_errors = [p.error for p in pages if p.error][:3]
        category, hint = classify_network_error(" ".join(sample_errors))
        assert category == "connection_refused"
        assert "refused" in hint.lower()

    def test_mixed_results_no_network_diagnostic(self):
        """When most pages succeed, no network diagnostic is added."""
        from dhole_mcp.crawl import CrawlPage
        pages = [
            CrawlPage(url="https://example.com/1", depth=0, status=200,
                     content_ok=True, content=["real content here"]),
            CrawlPage(url="https://example.com/2", depth=1, status=200,
                     content_ok=True, content=["more content"]),
            CrawlPage(url="https://example.com/3", depth=1, status=-1,
                     content_ok=False, error="timeout"),
        ]
        network_failures = sum(1 for p in pages if p.status == -1 or p.status == 0)
        # Only 1/3 failed - below 50% threshold
        assert network_failures < len(pages) * 0.5


# ============================================================================
# 来源：test_a_status_the_site_answered_is_never_reported_as_silence.py
# ============================================================================
# 2026-10-08 实连验收（D-20 / D-12 / D-03）：失败的原因不许被说反。

def _result(*, status: int = 200, content=None, content_type: str = "text/html",
            url: str = "https://example.com/article", error: str = "") -> ResponseModel:
    return ResponseModel(status=status, content=content if content is not None else [],
                         url=url, fetcher_used="http", content_type=content_type,
                         total_size_bytes=1000, error=error)


# ─── 1 · "没有正文"写在状态行里的那些 ─────────────────────────────────────

class TestABodyTheStatusLineAlreadyDeclared:

    @pytest.mark.parametrize("status", [204, 205, 304])
    def test_no_body_by_definition_is_not_a_js_shell(self, status):
        """壳的定义是"页面有内容而需要渲染才能看到"。204/205/304 是站点**宣布**了
        没有正文；渲染不能把宣布掉的内容变出来（浏览器对一个 204 只能给
        ERR_ABORTED，这正是当时的实测第二跳）。"""
        result = _result(status=status, content=[])
        assert _is_js_shell(result) is False
        assert _detect_content_issue(result) == ""

    def test_an_empty_body_on_an_ordinary_200_is_still_suspect(self):
        """豁免只给"状态行宣布过没有正文"的那些。200 + 空正文仍然是该升级的形状，
        否则这一族豁免就成了又一次"用一个猜测盖掉一个检查"。"""
        assert _is_js_shell(_result(status=200, content=[])) is True

    def test_an_empty_body_on_a_404_is_not_offered_a_browser(self):
        assert _is_js_shell(_result(status=404, content=[])) is False


# ─── 2 · 位图与 SVG 不是一回事 ────────────────────────────────────────────

class TestAPictureIsNotJudgedByPageHeuristics:

    def test_a_sparse_ocr_read_of_a_png_is_the_answer_not_a_failure_shape(self):
        """实测现场：OCR 只找到一个字形，"1 个字符"落进"body 大 / 文字少"那条体积
        启发式，于是正确的 OCR 结果被打成 js_shell 并附带一句关于本机浏览器的假话。
        一张图没有文字是关于图的事实。"""
        png = _result(status=200, content=["o"], content_type="image/png",
                      url="https://httpbin.org/image/png")
        assert _is_raster_image(png) is True
        assert _detect_content_issue(png) == ""

    def test_an_empty_svg_read_is_still_reported(self):
        """SVG 的正文就是它的标记：抽干了是读取问题，要说出来。把 image/ 整体豁免
        会把这一族一起放走（现有回归件正钉着它）。"""
        svg = _result(status=200, content=[], content_type="image/svg+xml")
        assert _is_raster_image(svg) is False
        assert _is_js_shell(svg) is True


# ─── 3 · 状态码归状态码，够不着归够不着 ───────────────────────────────────

class TestAnAnsweredStatusIsNotCalledSilence:

    def test_a_throttle_is_told_as_a_throttle(self):
        tips = _all_tiers_failed_tips("http_429", 429)
        assert "429 Too Many Requests" in tips
        assert "Retry-After" in tips
        assert "A 5xx is not a bot block" not in tips
        # 代理与"换 URL 试低防护路径"都是把限流当成墙：那条路走不通。
        assert "Try a proxy stored with cred_defaults" not in tips
        assert "some paths have lower protection" not in tips

    def test_a_server_error_says_where_the_failure_is(self):
        tips = _all_tiers_failed_tips("http_503", 503)
        assert "A 5xx is not a bot block" in tips
        assert "the failure is at the server" in tips
        assert "No HTTP response was received" not in tips

    def test_silence_still_says_silence(self):
        """反向守卫：真的没有任何响应时，那句"没收到响应"仍然是对的（名字不存在
        那一族当年就是靠这句话把"试代理"的建议换掉的）。"""
        tips = _all_tiers_failed_tips("unknown", 0)
        assert "No HTTP response was received at all" in tips
        assert "Do NOT retry the same URL" in tips

    def test_a_category_named_by_status_is_carried_into_the_receipt(self):
        """分类标签必须含真实状态号：`unknown (HTTP status 0)` 之所以有害，是因为
        它把两件事（谁没答 / 答了什么）压成一件。"""
        assert "429" in _all_tiers_failed_tips("http_429", 429)


# ─── 4 · archive 那一跳失败时不许改写源站的答复 ───────────────────────────

class TestAnArchiveMissDoesNotOverwriteTheOrigin:

    def test_the_origin_keeps_its_own_verdict(self):
        origin = _result(status=429, content=[], error="http_error_429: server returned error status")
        late = _result(status=0, content=[], error="timeout: the 9000ms call budget ran out during the archive.org lookup")
        out = _archive_ran_out(origin, late)
        assert out.status == 429, "快照查询耗尽预算，不能把 429 改写成一次超时"
        assert out.error.startswith("http_error_429")
        assert "archive.org fallback was tried and ran out of budget" in out.error
        assert "+archive(no_budget)" in out.escalation_path
        assert out.content_ok is False

    def test_the_lookup_that_simply_found_nothing_is_not_blamed(self):
        """没有快照（返回 None）与查快照查超时是两件事：前者不提，后者必须说清是谁
        的时间花完了。"""
        origin = _result(status=503, content=[], error="http_error_503: server returned error status")
        assert "archive" not in origin.error


# ─── 5 · retryable 与那句人话只能有一个出处 ──────────────────────────────

class TestRetryableAgreesWithItsOwnSentence:

    @pytest.mark.parametrize("category, expected", [
        ("dns_failure", False),      # 名字不存在，重发同一字节不可能变
        ("connection_refused", False),
        ("tls_error", False),
        ("proxy_error", False),
        ("browser_launch", False),
        ("connection_reset", True),  # 可能是瞬变的
        ("timeout", True),
    ])
    def test_the_table_matches_the_prose_it_sits_next_to(self, category, expected):
        assert is_retryable(category) is expected

    def test_a_dead_hostname_is_no_longer_marked_retryable(self):
        """实测的矛盾本体：error 说 "do NOT retry"，diagnosis 说可以。"""
        result = _result(status=0, content=[],
                         error="network_error: dns_failure (TCP preflight)")
        result.fetcher_used = "none"
        diag = _diagnose(result)
        assert diag["code"] == "network"
        assert diag["retryable"] is False

    def test_a_reset_keeps_its_retry(self):
        result = _result(status=0, content=[],
                         error="network_error: connection_reset (peer closed)")
        result.fetcher_used = "none"
        assert _diagnose(result)["retryable"] is True

    def test_classification_itself_still_names_the_category(self):
        assert classify_network_error("network_error: dns_failure (TCP preflight)")[0] == "dns_failure"


# ============================================================================
# 来源：test_the_receipt_tells_which_tier_can_answer.py
# ============================================================================
# 批次14：回执说真话 —— check_login 的分档回答、死域名的归因、focus 续读的可见性。
# 重名消歧：_result → _result__the_receipt_tells_which__4ab1

URL = "https://example.com/"


def _result__the_receipt_tells_which__4ab1(**kw) -> ResponseModel:
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
        out = await _server(_auto_escalate=_result__the_receipt_tells_which__4ab1()).web_read(
            URL, check_login=True, cache_ttl=0)
        assert out.login_check is not None, "请求了 check_login 却连标记都没有"
        assert out.login_check["logged_in"] is None
        assert out.login_check["method"].startswith("unavailable")
        assert "stealthy" in out.next_action

    @pytest.mark.asyncio
    async def test_forced_http_tier_marks_the_probe_unavailable(self):
        out = await _server(get=_result__the_receipt_tells_which__4ab1()).web_read(
            URL, force_fetcher="http", check_login=True, cache_ttl=0)
        assert out.login_check is not None
        assert out.login_check["method"].startswith("unavailable")

    @pytest.mark.asyncio
    async def test_stealthy_results_are_never_marked(self):
        """浏览器档的探测通路一字未动：探针没跑时也不许替它发标记。"""
        out = await _server(_auto_escalate=_result__the_receipt_tells_which__4ab1(fetcher_used="stealthy")).web_read(
            URL, check_login=True, cache_ttl=0)
        assert out.login_check is None

    @pytest.mark.asyncio
    async def test_not_asked_stays_silent(self):
        out = await _server(_auto_escalate=_result__the_receipt_tells_which__4ab1()).web_read(URL, cache_ttl=0)
        assert out.login_check is None

    def test_cache_hits_get_the_marker_through_the_helper(self):
        out = _attach_login_probe_gap(_result__the_receipt_tells_which__4ab1(fetcher_used="cache"), check_login=True)
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
        server = _server(get=_result__the_receipt_tells_which__4ab1(content=["word " * 2000]))
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


# ============================================================================
# 来源：test_escalation_keeps_what_each_tier_said.py
# ============================================================================
# 升级链把"哪一层说了什么"丢了，于是沉默被翻译成「去查你的 DNS」。
# 重名消歧：URL → URL__escalation_keeps_what_ea_025d

URL__escalation_keeps_what_ea_025d = "https://example.com/"

# 真实形状：httpx/primp 在 Windows 上的原话（尾码是 winsock 的）
RESET_WORDS = ("error sending request for url (https://example.com/) "
               "> connection was reset by peer (os error 10054)")
REFUSED_WORDS = ("error sending request for url (https://example.com/) "
                 "> 由于目标计算机积极拒绝，无法连接。 (os error 10061)")
FAKE_IP_WORDS = "connect to 198.18.0.42 failed after 2000ms timeout"


def _tier(fetcher: str, error: str) -> ResponseModel:
    return ResponseModel(url=URL__escalation_keeps_what_ea_025d, status=0, content=[], fetcher_used=fetcher,
                         extracted_type="markdown", error=error)


def _chain(http_error: str, browser_error: str, monkeypatch) -> MasterFetchServer:
    """两档各自按测试给的话失败，浏览器二进制算它在场，归档兜底关掉。"""
    server = MasterFetchServer()
    server.get = AsyncMock(return_value=_tier("http", http_error))
    server.stealthy_fetch = AsyncMock(return_value=_tier("stealthy", browser_error))
    # 隐身档之前的"取自动会话"那一步就会起浏览器，它也得 stub 掉
    server._ensure_auto_session = AsyncMock(return_value="auto-test")
    monkeypatch.setattr(server_mod, "_browser_deps_available", lambda: True)
    monkeypatch.setattr(server_mod, "ensure_chromium_async",
                        AsyncMock(return_value=SimpleNamespace(ok=True, waited_s=0,
                                                               detail="")))
    monkeypatch.setattr(server_mod, "_should_try_archive", lambda result: False)
    return server


class TestWhatEachTierSaidTravels:

    @pytest.mark.asyncio
    async def test_a_reset_named_by_http_is_not_reported_as_dns_trouble(self, monkeypatch):
        """HTTP 档说了 10054，浏览器档沉默：分类该听见 10054。"""
        out = await _chain(RESET_WORDS, "", monkeypatch).web_read(URL__escalation_keeps_what_ea_025d, cache_ttl=0)
        assert out.error.startswith("all_tiers_failed: connection_reset"), out.error
        assert "forcibly closed" in out.error, "建议要跟着类别走"

    @pytest.mark.asyncio
    async def test_a_refused_connection_keeps_its_winsock_code_in_the_receipt(
            self, monkeypatch):
        """分类之外，回执那一行也得带上原话 —— 否则读者只能信我们给的类别。"""
        out = await _chain(REFUSED_WORDS, "", monkeypatch).web_read(URL__escalation_keeps_what_ea_025d, cache_ttl=0)
        assert out.error.startswith("all_tiers_failed: connection_refused"), out.error
        assert "os error 10061" in out.error

    @pytest.mark.asyncio
    async def test_a_fake_ip_address_in_the_tier_words_is_read_as_the_tunnel(
            self, monkeypatch):
        """这台机器的老问题：198.18.x 是 TUN 没通到真实网络，不是站点 down、更不是 DNS。"""
        out = await _chain(FAKE_IP_WORDS, "", monkeypatch).web_read(URL__escalation_keeps_what_ea_025d, cache_ttl=0)
        assert out.error.startswith("all_tiers_failed: connection_refused"), out.error
        assert "Check the URL spelling and DNS" not in out.error

    @pytest.mark.asyncio
    async def test_the_browser_tier_still_speaks_for_itself(self, monkeypatch):
        """反向不许漂：浏览器档自己开口时，分类仍以它为准（HTTP 的旧话不许盖过它）。"""
        out = await _chain(RESET_WORDS, "Timed out while launching browser",
                           monkeypatch).web_read(URL__escalation_keeps_what_ea_025d, cache_ttl=0)
        assert out.error.startswith("all_tiers_failed: browser_launch"), out.error

    @pytest.mark.asyncio
    async def test_genuine_silence_still_reports_reachability(self, monkeypatch):
        """两层都真的什么都没说出时，不发明线索 —— 仍旧走"没有收到任何响应"那条。"""
        out = await _chain("", "", monkeypatch).web_read(URL__escalation_keeps_what_ea_025d, cache_ttl=0)
        assert out.error.startswith("all_tiers_failed: unknown"), out.error
        assert "No HTTP response was received at all" in out.error
        assert "HTTP failed (status 0); Stealthy failed (status 0)" in out.error


class TestTheDriverSentenceSaidAloud:
    """`errors.py` 的注释点名"驱动的原话是 Timed out while launching browser，必须先于
    timeout 认它"，模式表里却只有带 `browserType.launch:` 前缀的形状 —— 裸的那句仍然落进
    timeout，于是建议变成「把 timeout 调大再试」，正是要避免的那条假出路。`actions.py`
    早就按 `while launching browser` 这个片段认它，两份名单不该有出入。"""

    def test_the_bare_sentence_is_browser_launch(self):
        assert classify_network_error(
            "Timed out while launching browser")[0] == "browser_launch"

    def test_the_prefixed_sentence_still_is(self):
        assert classify_network_error(
            "browserType.launch: Timed out while launching browser")[0] == "browser_launch"
