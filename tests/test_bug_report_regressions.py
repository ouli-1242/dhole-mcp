"""Regressions for the bugs found in the external dhole MCP test report.

Each test names the report item it pins (BUG-n / NEW-n) so a future reader can
trace the change back to the observed failure, and so the measured symptom -
not just the code shape - is what breaks when the fix is reverted.
"""

import json
import os

import pytest

from dhole_mcp.server import (
    ResponseModel, MasterFetchServer, _ARCHIVE_FALLBACK_STATUSES,
    _agent_hints, _apply_chunking, _annotate_quality, _with_agent_hints,
    _backfill_article_json, _blocked_path_prefix, _coerce_options,
    _detect_content_issue, _invalid_request_result, _is_bot_wall,
    _resolve_local_path, _should_try_archive, _strict_options, _translate_response,
    _SF_OPTIONS_ALLOWED, _SF_OPTIONS_FORWARDED,
)


def _result(**kwargs):
    defaults = dict(
        status=200, content=["Real article body text about Python packaging."],
        url="https://example.com/article", fetcher_used="http",
        content_type="text/html", total_size_bytes=4000,
        extracted_type="markdown",
    )
    defaults.update(kwargs)
    return ResponseModel(**defaults)


# ─── BUG-5: error / placeholder text must never be the page body ───────────

class TestFailureContentIsEmpty:

    def test_failed_fetch_has_no_placeholder_body(self):
        """A DNS failure used to return content ["[No more content.]"], which a
        caller reading content[0] took for the page."""
        failed = _result(status=0, content=[], fetcher_used="none",
                         error="network_error: dns_failure (TCP preflight)")
        out = _apply_chunking(failed)
        assert out.content == []
        assert out.total_extracted_chars == 0

    def test_error_text_is_not_counted_as_extracted_content(self):
        """Measured: a TLS failure reported total_extracted_chars 87 - the length
        of the error string, presented as page content."""
        failed = _result(status=0, content=[], fetcher_used="http",
                         error="error sending request for url > tls handshake eof")
        out = _apply_chunking(failed)
        assert out.total_extracted_chars == 0
        assert out.content_ok is False

    @pytest.mark.asyncio
    async def test_all_tiers_failed_keeps_the_advice_out_of_the_body(self, monkeypatch):
        """The both-tiers-failed path used to write a six-line advisory block into
        content[0], where a caller reading the body found it."""
        import dhole_mcp.fetcher as fetcher

        async def http_fail(*a, **k):
            return _result(status=503, content=[], error="blocked")

        async def browser_fail(*a, **k):
            return _result(status=503, content=[], error="blocked",
                           fetcher_used="stealthy")

        monkeypatch.setattr(fetcher, "tcp_preflight", lambda url, timeout=2.0: (True, ""))
        monkeypatch.setattr("dhole_mcp.server._browser_deps_available", lambda: True)
        server = MasterFetchServer()
        server.get = http_fail
        server.stealthy_fetch = browser_fail
        out = await server._auto_escalate(
            "https://blocked.example.com", "markdown", None, True, True, 0, 0,
            True, False, 0, None, 20000, False, False, False, False,
            None, None, None,
        )
        assert out.content == []
        assert out.error.startswith("all_tiers_failed:")
        assert "Try with a proxy" in out.error or "unreachable" in out.error
        assert out.content_ok is False

    def test_exhausted_pagination_still_says_so(self):
        """The placeholder is correct - and expected - when a real page was read
        to the end and the agent asked for a chunk past it."""
        page = _result(content=["a" * 2000])
        out = _apply_chunking(page, offset=5000)
        assert out.content == ["[No more content.]"]
        assert out.total_extracted_chars == 2000

    @pytest.mark.asyncio
    async def test_bulk_failure_carries_error_not_body(self):
        """Measured: content ["[Fetch error: error sending request for url ... >
        tls handshake eof]"] with total_extracted_chars 87 - the exception text
        presented as the page body, and counted as its length."""
        import dhole_mcp.fetcher as fetcher

        class Boom(Exception):
            pass

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, url, **kwargs):
                raise Boom("error sending request for url > tls handshake eof")

        monkey = pytest.MonkeyPatch()
        monkey.setattr(fetcher, "HTTPSession", lambda *a, **k: FakeSession())
        try:
            bulk = await MasterFetchServer.bulk_get(urls=["https://example.com/x"])
        finally:
            monkey.undo()
        r = bulk.results[0]
        assert r.status == 0
        assert r.content == []
        assert "tls handshake eof" in r.error
        assert r.total_extracted_chars == 0
        assert r.content_ok is False


# ─── BUG-6 / BUG-12: walls on HTTP 200 ─────────────────────────────────────

_SOGOU_CAPTCHA = (
    "IP：112.49.187.249访问时间：2026.09.21 23:54:14VerifyCode：b95fc2fcd78a"
    "From：weixin.sogou.com\n\n此验证码用于确认这些请求是您的正常行为而不是"
    "自动程序发出的，需要您协助验证。\n\n验证码：\n\n提交 提交后没解决问题？欢迎反馈。"
)


class TestWallDetectionOn200:

    def test_captcha_page_is_detected(self):
        r = _result(url="https://weixin.sogou.com/antispider/?from=%2Flink",
                    content=[_SOGOU_CAPTCHA])
        assert _is_bot_wall(r) is True
        assert _detect_content_issue(r).startswith("bot_wall_detected")

    def test_captcha_page_is_not_content_ok(self):
        """Measured through the MCP tool: the same page came back
        content_ok:true, and the agent cited a captcha as an article."""
        r = _with_agent_hints(_result(url="https://weixin.sogou.com/antispider/",
                                      content=[_SOGOU_CAPTCHA]))
        annotated = _annotate_quality(_result(url="https://weixin.sogou.com/antispider/",
                                              content=[_SOGOU_CAPTCHA]))
        hinted = _with_agent_hints(annotated)
        assert hinted.content_ok is False
        assert hinted.page_type == "captcha"
        assert "do NOT cite" in hinted.next_action
        assert r.content_ok is not None  # sanity: hints ran

    def test_english_challenge_detected(self):
        r = _result(content=["Please verify you are a human. Checking your browser "
                             "before accessing the site."])
        assert _is_bot_wall(r) is True

    def test_article_about_captchas_is_not_a_wall(self):
        """The guard the old 403/503-only check relied on, kept: long prose that
        happens to mention captchas is content."""
        body = ("A history ofCompletely unrelated but lengthy discussion of " * 40)
        r = _result(content=[body + " The CAPTCHA was invented to tell humans apart."])
        assert _is_bot_wall(r) is False

    def test_login_wall_sets_page_type(self):
        """BUG-12: error said auth_wall_detected while page_type said unknown."""
        r = _result(
            url="https://www.zhihu.com/signin?next=%2F",
            content=["验证码登录 密码登录 获取短信验证码 忘记密码 登录/注册 登录"],
        )
        annotated = _with_agent_hints(_annotate_quality(r))
        assert annotated.page_type == "auth_wall"
        assert annotated.content_ok is False
        assert "login/sign-in wall" in annotated.next_action

    def test_wall_hint_reachable_without_content_ok(self):
        """The page_type block in _agent_hints only runs when content_ok is true,
        so wall advice has to come from the error branch or never fire at all."""
        summary, next_action, content_ok = _agent_hints(
            _result(error="auth_wall_detected: page is a login/sign-in wall, not content")
        )
        assert content_ok is False
        assert next_action


# ─── BUG-16: input validation returns a FetchResult ────────────────────────

class TestStructuredInputErrors:

    def test_shape_matches_the_fetch_contract(self):
        r = _invalid_request_result("", "Either 'url' or 'urls' must be provided")
        assert r.status == 0
        assert r.content == []
        assert r.content_ok is False
        assert r.error.startswith("invalid_request:")
        assert "no request was made" in r.next_action

    @pytest.mark.asyncio
    async def test_missing_url_does_not_raise(self):
        """Measured: the MCP call came back as a protocol error instead of a
        FetchResult, unlike every other smart_fetch failure."""
        server = MasterFetchServer()
        content, structured = await server._dispatch("smart_fetch", {})
        assert structured["error"].startswith("invalid_request:")
        assert structured["content"] == []

    @pytest.mark.asyncio
    async def test_unsupported_scheme_returns_structured_result(self):
        server = MasterFetchServer()
        result = await server.smart_fetch(url="example.com")
        assert result.status == 0
        assert result.content == []
        assert "invalid_request" in result.error

    @pytest.mark.asyncio
    async def test_actions_in_bulk_mode_reported_per_url(self):
        server = MasterFetchServer()
        bulk = await server.smart_fetch(
            url="", urls=["https://a.example.com", "https://b.example.com"],
            actions=[{"click": "button"}],
        )
        assert bulk.successful == 0
        assert all("invalid_request" in r.error for r in bulk.results)
        assert all(r.content == [] for r in bulk.results)


# ─── BUG-19: an options bag that arrives as a JSON string ──────────────────

class TestOptionsCoercion:

    def test_json_string_options_are_parsed(self):
        assert _coerce_options('{"max_results": 8}') == {"max_results": 8}

    def test_none_and_blank_are_empty(self):
        assert _coerce_options(None) == {}
        assert _coerce_options("   ") == {}

    def test_dict_passes_through(self):
        assert _coerce_options({"a": 1}) == {"a": 1}

    def test_bad_json_says_so(self):
        with pytest.raises(ValueError, match="not valid JSON"):
            _coerce_options("{max_results: 8")

    def test_non_mapping_is_rejected(self):
        with pytest.raises(ValueError, match="must be an object"):
            _coerce_options("[1,2]")

    def test_strict_options_never_iterates_a_string_by_character(self):
        """The reported symptom: Unsupported option key(s) ... ['{','"','m','a'...]"""
        with pytest.raises(ValueError, match="must be an object") as exc:
            _strict_options('{"max_results": 8}', _SF_OPTIONS_ALLOWED,
                            _SF_OPTIONS_FORWARDED, "smart_fetch")
        assert "'" not in str(exc.value)


# ─── BUG-8: css_selector in the default markdown mode ──────────────────────

_SELECTOR_PAGE_HTML = """
<html><head><title>Doc</title></head><body>
<nav><a href="/a">nav link a</a><a href="/b">nav link b</a></nav>
<h1>Selected heading text</h1>
<blockquote class="abstract">The abstract sentence that the caller asked for.</blockquote>
<article><p>Body paragraph that must be dropped by the selector. """ + ("Filler prose of the article body. " * 12) + """</p></article>
<footer>footer noise that goes on for a while and should not appear in the narrowed output at all</footer>
</body></html>
"""


def _fake_page(html: str):
    class FakePage:
        status = 200
        body = html.encode("utf-8")
        encoding = "utf-8"
        url = "https://example.com/doc"
        headers = {"content-type": "text/html"}
    return FakePage()


class TestCssSelectorEveryMode:

    # "html" is deliberately absent: the real pipeline routes html mode through
    # extractor.extract_content (which narrows on its own), never through this
    # function - asking for raw markup here returns prose instead.
    @pytest.mark.parametrize("mode", ["markdown", "text", "article"])
    def test_selector_narrows_the_output(self, mode):
        """Measured in markdown mode: css_selector="h1" returned the whole 183
        -char page, and "blockquote.abstract" returned 4051 chars. Only html mode
        honored it, because the narrowing path checked hasattr(page,'css') on a
        Response object that has no such method and silently fell back."""
        from dhole_mcp.trafilatura_extractor import extract_with_trafilatura
        parts = extract_with_trafilatura(_fake_page(_SELECTOR_PAGE_HTML),
                                         extraction_type=mode,
                                         css_selector="blockquote.abstract")
        text = "\n".join(parts)
        assert "abstract sentence" in text
        # The narrowing has to be visible: everything else on the page is gone.
        assert "footer noise" not in text
        assert "nav link" not in text
        assert "Body paragraph that must be dropped" not in text
        assert len(text) < 300, f"output is not narrowed: {len(text)} chars"

    @pytest.mark.parametrize("mode", ["markdown", "text"])
    def test_single_tag_selector(self, mode):
        """The report's own case: h1 must yield just the heading."""
        from dhole_mcp.trafilatura_extractor import extract_with_trafilatura
        parts = extract_with_trafilatura(_fake_page(_SELECTOR_PAGE_HTML),
                                         extraction_type=mode, css_selector="h1")
        text = "\n".join(parts)
        assert "Selected heading text" in text
        assert "abstract sentence" not in text

    def test_multiple_matches_are_all_kept(self):
        from dhole_mcp.trafilatura_extractor import extract_with_trafilatura
        parts = extract_with_trafilatura(_fake_page(_SELECTOR_PAGE_HTML),
                                         extraction_type="markdown",
                                         css_selector="a")
        text = "\n".join(parts)
        assert "nav link a" in text and "nav link b" in text

    def test_selector_matching_nothing_falls_back_to_the_page(self):
        """Existing behavior, kept: no match widens rather than returning empty."""
        from dhole_mcp.trafilatura_extractor import extract_with_trafilatura
        full = "\n".join(extract_with_trafilatura(
            _fake_page(_SELECTOR_PAGE_HTML), extraction_type="markdown", css_selector=None))
        narrowed = "\n".join(extract_with_trafilatura(
            _fake_page(_SELECTOR_PAGE_HTML), extraction_type="markdown",
            css_selector="div.does-not-exist"))
        assert narrowed.strip()
        assert narrowed == full

    def test_bad_selector_does_not_raise(self):
        from dhole_mcp.trafilatura_extractor import extract_with_trafilatura
        parts = extract_with_trafilatura(_fake_page(_SELECTOR_PAGE_HTML),
                                         extraction_type="markdown",
                                         css_selector="<<<not a selector")
        assert parts and any(p.strip() for p in parts)


# ─── BUG-15 / BUG-4 / BUG-14: article JSON + extracted_type ────────────────

class TestArticleMetadataBackfill:

    def test_author_and_date_filled_from_metadata(self):
        """Measured on a Verge article: metadata carried both, the article JSON
        returned empty strings for both."""
        body = json.dumps({"title": "T", "author": "", "date": "",
                           "body": "text", "description": "", "url": "u",
                           "categories": [], "tags": []}, indent=2)
        out = _backfill_article_json(
            [body], {"author": "Emma Roth",
                    "published_time": "2026-09-21T12:41:42+00:00"})
        data = json.loads(out[0])
        assert data["author"] == "Emma Roth"
        assert data["date"] == "2026-09-21T12:41:42+00:00"

    def test_existing_values_are_not_overwritten(self):
        body = json.dumps({"author": "Trafilatura found", "date": "2020"}, indent=2)
        out = _backfill_article_json([body], {"author": "OG person",
                                              "published_time": "2026"})
        data = json.loads(out[0])
        assert (data["author"], data["date"]) == ("Trafilatura found", "2020")

    def test_non_json_content_untouched(self):
        assert _backfill_article_json(["# Heading\nprose"], {"author": "X"}) == \
            ["# Heading\nprose"]

    def test_modified_time_used_when_no_publication_date(self):
        body = json.dumps({"author": "", "date": ""}, indent=2)
        out = _backfill_article_json([body], {"modified_time": "2026-01-02"})
        assert json.loads(out[0])["date"] == "2026-01-02"


class TestExtractedTypeLabel:

    def _page(self, html: str, url: str = "https://example.com/a"):
        class FakePage:
            status = 200
            body = html.encode("utf-8")
            encoding = "utf-8"
            headers = {"content-type": "text/html; charset=utf-8"}
        p = FakePage()
        p.url = url
        return p

    def test_article_mode_reports_article(self):
        html = ("<html><head><title>Only title</title></head><body>"
                "<article><p>" + ("Some article prose here. " * 20) + "</p></article>"
                "</body></html>")
        r = _translate_response(self._page(html), "article", None, True, True, "http", 1)
        assert r.extracted_type == "article"

    def test_markdown_fallback_is_not_labelled_article(self):
        """Saying "article" about a prose fallback is the same lie in the other
        direction; the label must describe what came back."""
        html = "<html><body><div>" + ("lorem ipsum dolor sit amet " * 30) + "</div></body></html>"
        r = _translate_response(self._page(html), "structured", None, True, True, "http", 1)
        assert r.extracted_type in ("structured", "markdown")
        if r.extracted_type == "markdown":
            assert not r.content[0].lstrip().startswith("{")

    def test_html_mode_reports_html(self):
        html = "<html><body><h1>Hi</h1></body></html>"
        r = _translate_response(self._page(html), "html", None, False, False, "http", 1)
        assert r.extracted_type == "html"


# ─── NEW-1 / NEW-2 / BUG-13: what smart_search says when IT dropped the hits ─

def _raw(title: str, url: str, snippet: str = "", source: str = "bing"):
    from dhole_mcp.search_engines import RawResult
    return RawResult(title=title, url=url, snippet=snippet, source=source,
                     position=1, consensus=1, sources=(source,))


def _patch_engines(monkeypatch, results, reports=None, calls=None):
    from dhole_mcp import search as S
    from dhole_mcp.search_engines import EngineReport

    async def fake_multi_search(query, max_results, **kwargs):
        if calls is not None:
            calls.append({"query": query, "max_results": max_results, **kwargs})
        return list(results), (reports or [EngineReport(name="bing", ok=True, status="ok")])

    async def fake_get_cached(*a, **k):
        return None

    async def fake_set_cached(*a, **k):
        return None

    async def fake_ensure_reranker(*a, **k):
        return None

    monkeypatch.setattr(S, "multi_search", fake_multi_search)
    monkeypatch.setattr(S, "get_cached", fake_get_cached)
    monkeypatch.setattr(S, "set_cached", fake_set_cached)
    monkeypatch.setattr(S, "ensure_reranker", fake_ensure_reranker)
    monkeypatch.setattr(
        S, "_rank",
        lambda q, ranked, mode: (ranked, [1.0] * len(ranked), "merge", ""),
    )
    return S


class TestOffTopicFilterIsVisible:

    @pytest.mark.asyncio
    async def test_all_results_dropped_is_reported(self, monkeypatch):
        """Measured: bing delivered 10 usable results, dhole discarded every one,
        and the response read total_results:0 / error:"" / engines_used:["bing"]
        with next_action telling the agent to rephrase."""
        S = _patch_engines(monkeypatch, [
            _raw("Bilibili", "https://www.bilibili.com/", "视频网站"),
            _raw("Other", "https://other.example/", "unrelated text"),
        ])
        out = await S.smart_search(None, "how does dns resolution work", 6)
        assert out.results == []
        assert "dhole dropped them rather than return noise" in out.error
        assert "rephrasing will not help" in out.error
        assert out.engines_used == ["bing"]

    @pytest.mark.asyncio
    async def test_partial_drop_leaves_a_note(self, monkeypatch):
        """Fewer results than the engines delivered is itself a silent-degradation
        shape unless it is said out loud."""
        rows = [_raw(f"DNS resolution doc {i}", f"https://e{i}.example/",
                     "dns resolution walkthrough") for i in range(5)]
        S = _patch_engines(monkeypatch, rows)
        scores = [1.0, 0.4, 0.3, 0.05, 0.02]
        monkeypatch.setattr(
            S, "_rank",
            lambda q, ranked, mode: (ranked, scores[:len(ranked)], "merge", ""),
        )
        out = await S.smart_search(None, "dns resolution", 6)
        assert len(out.results) < len(rows)
        assert "dropped as off-topic/low-relevance" in out.fetch_hint
        assert out.error == ""

    @pytest.mark.asyncio
    async def test_cjk_queries_are_never_filtered(self, monkeypatch):
        """The filter can only judge latin terms; CJK must pass through untouched."""
        S = _patch_engines(monkeypatch, [_raw("标题", "https://example.com/a", "正文")])
        out = await S.smart_search(None, "大模型 推理优化", 6)
        assert len(out.results) == 1
        assert out.error == ""


class TestSiteFilterSurvivesRewrite:

    @pytest.mark.asyncio
    async def test_rewrite_keeps_the_site_restriction(self, monkeypatch):
        """Measured: site=theverge.com + a query that found nothing returned
        trustpilot/g2/pissedconsumer results, because the rewrite round passed
        site=None and dropped the caller's explicit domain constraint."""
        calls: list[dict] = []
        S = _patch_engines(monkeypatch, [], calls=calls)
        await S.smart_search(None, "Googlebooks Android review", 3, site="theverge.com")
        assert len(calls) >= 2, "expected a rewritten second round"
        assert all(c.get("site") == "theverge.com" for c in calls), \
            "the site filter must not be lifted silently on the rewrite round"

    @pytest.mark.asyncio
    async def test_empty_with_site_says_the_filter_was_respected(self, monkeypatch):
        S = _patch_engines(monkeypatch, [])
        out = await S.smart_search(None, "nothing at all here", 3, site="example.invalid")
        assert out.results == []
        assert "site=example.invalid" in out.error


class TestMaxResultsClampIsVisible:

    @pytest.mark.asyncio
    async def test_out_of_range_is_announced(self, monkeypatch):
        S = _patch_engines(monkeypatch, [_raw("A", "https://a.example/")])
        out = await S.smart_search(None, "dns resolution test", 100)
        assert "max_results=100 is outside the supported 1-50 range" in out.fetch_hint


# ─── BUG-9: no invented install instructions ───────────────────────────────

class TestRerankHintWording:

    def test_no_install_advice_when_nothing_failed(self, monkeypatch):
        from dhole_mcp import search as S
        monkeypatch.setattr(S, "unavailable_reason", lambda: "")
        reason = S._rerank_absent_reason()
        assert "install dhole-mcp[all] and retry" not in reason
        assert "dhole -v" in reason

    def test_recorded_reason_is_passed_through(self, monkeypatch):
        from dhole_mcp import search as S
        monkeypatch.setattr(S, "unavailable_reason", lambda: "model file missing")
        assert S._rerank_absent_reason() == "model file missing"


# ─── BUG-1: related_queries mined from one site's boilerplate ──────────────

class TestRelatedQueries:

    def _sr(self, title, snippet, url):
        from dhole_mcp.search import SearchResult
        return SearchResult(title=title, url=url, snippet=snippet, source="bing",
                            position=1, relevance_score=1.0, fetch_relevance="high",
                            engines_consensus="1 of 1", source_type="other")

    def test_one_domain_repeating_a_phrase_is_not_a_topic(self):
        """An author bio under five articles used to mint five 'suggestions'."""
        from dhole_mcp import search as S
        rows = [self._sr(f"Article {i}", "Antonio is a reviewer covering laptops "
                         "and the occasional gadget", "https://theverge.com/a%d" % i)
                for i in range(5)]
        assert S._related_queries("antonio verge", rows) == []

    def test_two_independent_domains_do_make_a_suggestion(self):
        from dhole_mcp import search as S
        rows = [
            self._sr("GPT scoring", "tokenizer overhead dominates inference latency",
                     "https://one.example/"),
            self._sr("LLM notes", "inference latency grows with tokenizer overhead",
                     "https://two.example/"),
        ]
        out = S._related_queries("large language models", rows)
        assert any("inference latency" in p or "tokenizer overhead" in p for p in out)

    def test_sentence_fragments_are_rejected(self):
        from dhole_mcp import search as S
        rows = [
            self._sr("Bio one", "he spent years before joining The Verge as deals writer",
                     "https://one.example/"),
            self._sr("Bio two", "she worked before joining The Verge as deals writer too",
                     "https://two.example/"),
        ]
        out = S._related_queries("verge people", rows)
        assert not any(p.split()[0] in ("before", "joining", "as") for p in out)

    def test_no_results_returns_empty(self):
        from dhole_mcp import search as S
        assert S._related_queries("q", []) == []


# ─── NEW-3: a list page's next_action targets ──────────────────────────────

class TestListPageTargets:

    def test_chrome_links_are_not_offered_as_targets(self):
        """Measured on theverge.com/news: "Top targets: /, /auth/login,
        /subscribe" - the header, in DOM order, instead of the articles."""
        from dhole_mcp.server import _best_list_targets
        cits = [
            {"url": "https://www.theverge.com/", "text": "The Verge"},
            {"url": "https://www.theverge.com/auth/login?returnPath=/", "text": "Login"},
            {"url": "https://www.theverge.com/subscribe?itm_source=navigation", "text": "Subscribe"},
            {"url": "https://www.theverge.com/rss/index.xml", "text": "RSS"},
            {"url": "https://www.theverge.com/tech/998146/meta-subsea-cable", "text": ""},
            {"url": "https://www.theverge.com/policy/997805/press-credentials", "text": "credentials"},
        ]
        top = _best_list_targets(cits, "https://www.theverge.com/news")
        assert all("login" not in u and "subscribe" not in u and "rss" not in u
                   for u in top), top
        assert any("998146" in u for u in top)

    def test_anchor_typed_links_win_over_bare_ones(self):
        from dhole_mcp.server import _best_list_targets
        cits = [
            {"url": "https://ex.com/blog/2026/a-post", "text": ""},
            {"url": "https://ex.com/blog/2026/b-post", "text": "B post title"},
        ]
        top = _best_list_targets(cits, "https://ex.com/blog")
        assert top[0].endswith("b-post")

    def test_no_content_links_returns_nothing(self):
        from dhole_mcp.server import _best_list_targets
        assert _best_list_targets([{"url": "https://ex.com/", "text": "home"}],
                                 "https://ex.com/news") == []


# ─── BUG-17: engine state on disk, and the lever to clear it ───────────────

class TestEngineState:

    @pytest.fixture(autouse=True)
    def _ms(self, monkeypatch):
        from dhole_mcp import search_metasearch as ms
        monkeypatch.setattr(ms, "_BACKEND_HEALTH", {})
        monkeypatch.setattr(ms, "_CONN_FAIL_COUNTS", {})
        monkeypatch.setattr(ms, "_ENGINE_YIELD", {})
        return ms

    def test_expired_cooldowns_are_pruned_from_disk_on_load(self, _ms, tmp_path):
        """The report read circuit_breaker.json, saw timestamps from 17 minutes
        ago, and concluded the block never expires. It does - in memory. The file
        just kept lying about it."""
        import json
        import time
        path = _ms._circuit_state_file()
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"brave": time.time() - 60, "yahoo": time.time() + 600}, f)
        _ms._load_circuit_state()
        assert list(_ms._BACKEND_HEALTH) == ["yahoo"]
        on_disk = json.load(open(path, encoding="utf-8"))
        assert on_disk == {"yahoo": _ms._BACKEND_HEALTH["yahoo"]}
        assert "brave" not in on_disk

    def test_sweep_releases_and_persists(self, _ms):
        import json
        import time
        _ms._BACKEND_HEALTH.update({"brave": time.time() - 1, "bing": time.time() + 300})
        assert _ms.sweep_expired_cooldowns() == 1
        assert "brave" not in _ms._BACKEND_HEALTH
        assert "brave" not in json.load(open(_ms._circuit_state_file(), encoding="utf-8"))
        assert _ms.cooldowns().get("bing", 0) > 290

    def test_reset_clears_memory_and_both_files(self, _ms):
        import json
        import time
        _ms._record_block("brave")
        _ms._record_conn_failure("brave")
        _ms._record_conn_failure("brave")
        _ms._record_conn_failure("brave")   # -> 10 min cooldown
        _ms._ENGINE_YIELD["brave"] = {"n": 3, "status": "preempted", "ts": time.time()}
        _ms._save_engine_stats()
        info = _ms.engine_state_reset()
        assert _ms._BACKEND_HEALTH == {}
        assert _ms._ENGINE_YIELD == {}
        assert _ms.cooldowns() == {}
        assert info["released_cooldowns"].get("brave", 0) > 0
        assert info["engines_forgotten"] == 1
        assert json.load(open(_ms._circuit_state_file(), encoding="utf-8")) == {}
        assert json.load(open(_ms._engine_stats_file(), encoding="utf-8")) == {}

    def test_snapshot_carries_cooldown_seconds(self, _ms):
        import time
        _ms._BACKEND_HEALTH["brave"] = time.time() + 42.0
        _ms._ENGINE_YIELD["brave"] = {"n": 1, "status": "ok", "last": 5,
                                      "last_nodes": 5, "last_kept": 5, "mean": 5,
                                      "zero": 0, "drift": 0, "ts": time.time()}
        snap = _ms.engine_state_snapshot()
        assert 35 < snap["brave"]["cooldown_seconds_left"] <= 42
        assert snap["brave"]["verdict"] == "healthy"

    @pytest.mark.asyncio
    async def test_circuit_open_report_says_when_the_engine_is_retried(self, monkeypatch):
        """An engine skipped as "circuit open" gave no way to tell a permanent
        block from a 90-second cooldown."""
        from dhole_mcp import search_engines as se
        from dhole_mcp import search_metasearch as ms

        async def fake_meta(query, max_results, **kwargs):
            return [], {"brave": "circuit_open", "bing": "preempted"}

        monkeypatch.setattr(se, "_metasearch", fake_meta)
        monkeypatch.setattr(ms, "cooldowns", lambda: {"brave": 90.4})
        _, reports = await se.multi_search("q", 5)  # type: ignore[misc]
        by = {r.name: r for r in reports}
        assert "retried in 90s" in by["brave"].error
        assert "not a block" in by["bing"].error

    @pytest.mark.asyncio
    async def test_cache_clear_can_reset_engine_state(self, _ms):
        _ms._record_block("brave")
        server = MasterFetchServer()
        out = await server.cache_clear(engine_state=True)
        assert out.engine_state_reset is True
        assert "cooldown" in out.message.lower()
        assert _ms.cooldowns() == {}
        assert "brave" in out.engine_health or out.engine_health == {}

    def test_cli_engines_reset_clears_the_files(self, _ms):
        from dhole_mcp.server import _cmd_engines
        _ms._record_block("brave")
        _ms._ENGINE_YIELD["brave"] = {"n": 1, "status": "ok", "ts": 0}
        assert _cmd_engines(["reset"]) == 0
        assert _ms._BACKEND_HEALTH == {}
        assert _cmd_engines(["list"]) == 0
        assert _cmd_engines(["bogus"]) == 2


# ─── NEW-4: the call budget is a wall, not a suggestion ────────────────────

class TestCallBudget:

    @pytest.mark.asyncio
    async def test_backstop_returns_a_fetch_result_inside_the_budget(self):
        """Measured: example.com took 24.1s and 25.0s on a default 30s budget
        (retries × redirect hops), and the report's slow hosts got killed by the
        MCP client (-32001) with no FetchResult at all."""
        import asyncio
        import time

        async def hang():
            await asyncio.sleep(30)

        server = MasterFetchServer()
        t0 = time.monotonic()
        out = await server._within_call_budget(hang(), "https://slow.example.com",
                                               400, "HTTP tier")
        took = time.monotonic() - t0
        assert took < 3.0, f"budget not enforced: {took:.1f}s"
        assert out.status == 0
        assert out.content == []
        assert out.content_ok is False
        assert "timeout" in out.error and "HTTP tier" in out.error
        assert "Raise timeout" in out.next_action

    @pytest.mark.asyncio
    async def test_escalation_reports_which_tier_ran_out(self, monkeypatch):
        import asyncio

        async def slow_get(*args, **kwargs):
            await asyncio.sleep(5)
            return _result()

        import dhole_mcp.fetcher as fetcher
        monkeypatch.setattr(fetcher, "tcp_preflight", lambda url, timeout=2.0: (True, ""))
        server = MasterFetchServer()
        server.get = slow_get
        out = await server._auto_escalate(
            "https://slow.example.com", "markdown", None, True, True, 0, 0,
            True, False, 0, None, 800, False, False, False, False,
            None, None, None,
        )
        assert out.status == 0
        assert out.content == []
        assert "HTTP tier" in out.error
        assert out.duration_ms < 3000

    @pytest.mark.asyncio
    async def test_http_timeout_cannot_exceed_the_caller_budget(self, monkeypatch):
        """_adaptive_timeout learns [5s, 60s]; a 1s call must not inherit 60s."""
        import asyncio

        seen: dict = {}

        async def capture_get(*args, **kwargs):
            seen["timeout"] = kwargs.get("timeout")
            await asyncio.sleep(5)
            return _result()

        import dhole_mcp.fetcher as fetcher
        monkeypatch.setattr(fetcher, "tcp_preflight", lambda url, timeout=2.0: (True, ""))
        monkeypatch.setattr("dhole_mcp.server._adaptive_timeout",
                            lambda url, default_ms: 60000)
        server = MasterFetchServer()
        server.get = capture_get
        await server._auto_escalate(
            "https://slow.example.com", "markdown", None, True, True, 0, 0,
            True, False, 0, None, 1500, False, False, False, False,
            None, None, None,
        )
        assert seen.get("timeout", 99) <= 2


# ─── BUG-10 / BUG-12: parse path resolution and envelope ───────────────────

class TestParsePathResolution:

    def test_relative_path_resolves_against_cwd(self, tmp_path, monkeypatch):
        (tmp_path / "report.csv").write_text("a,b\n1,2\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        target, tried = _resolve_local_path("report.csv")
        assert target == str(tmp_path / "report.csv")
        assert tried[0] == target

    def test_dhole_workdir_is_honored(self, tmp_path, monkeypatch):
        """The MCP host's cwd is its own install directory; DHOLE_WORKDIR is the
        documented way to point relative paths at the project."""
        work = tmp_path / "project"
        work.mkdir()
        (work / "notes.html").write_text("<h1>x</h1>", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DHOLE_WORKDIR", str(work))
        target, _ = _resolve_local_path("notes.html")
        assert target == str(work / "notes.html")

    def test_miss_lists_every_directory_tried(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DHOLE_WORKDIR", str(tmp_path / "elsewhere"))
        target, tried = _resolve_local_path("nope.csv")
        assert target == ""
        assert any(p.endswith("nope.csv") for p in tried)
        assert len(tried) >= 2

    def test_absolute_path_that_exists(self, tmp_path):
        f = tmp_path / "x.csv"
        f.write_text("a\n", encoding="utf-8")
        target, _ = _resolve_local_path(str(f))
        assert target == str(f)

    def test_blocked_prefix_still_applies(self, tmp_path, monkeypatch):
        monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path / p.replace("~/", "")))
        assert _blocked_path_prefix(str(tmp_path / ".ssh" / "id_rsa")) != ""
        assert _blocked_path_prefix(str(tmp_path / "ok.csv")) == ""


class TestParseEnvelope:

    @pytest.mark.asyncio
    async def test_success_fills_the_stats_fields(self, tmp_path, monkeypatch):
        """Measured: a successful parse returned content with data while
        total_extracted_chars, summary, content_type and fetched_at were empty."""
        f = tmp_path / "doc.html"
        f.write_text("<html><head><title>T</title></head><body><h1>Heading</h1>"
                     "<p>Paragraph body text.</p></body></html>", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        server = MasterFetchServer()
        r = await server.parse("doc.html")
        assert r.status == 200
        assert r.content_ok is True
        assert r.total_extracted_chars == len("\n".join(r.content)) > 0
        assert r.content_type == "text/html"
        assert r.fetched_at
        assert r.summary
        assert r.duration_ms >= 0
        assert r.url.startswith("file:///")

    @pytest.mark.asyncio
    async def test_failure_says_what_was_tried(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        server = MasterFetchServer()
        r = await server.parse("missing.html")
        assert r.status == 0
        assert r.content == []
        assert "DHOLE_WORKDIR" in r.error
        assert "missing.html" in r.error


# ─── KB-9: 410 真的走 archive 回退（元组与 gate 不许再漂移） ────────────────

class TestArchiveFallback:

    def test_every_fallback_status_is_accepted_by_the_gate(self):
        """元组里的每个状态都必须过 gate：被 gate 拒绝的状态 = 永远走不到的分支。

        KB-9：410 曾列在元组里而 ``_should_try_archive()`` 一直返回 False，
        于是 410 Gone 的页面从不去 Wayback 找快照，与代码的字面意图不符。
        """
        for status in _ARCHIVE_FALLBACK_STATUSES:
            assert _should_try_archive(_result(status=status, content=[])), (
                f"{status} 在 _ARCHIVE_FALLBACK_STATUSES 里却被 gate 拒绝")

    def test_network_failure_is_not_short_circuited_to_archive(self):
        """status=0 不在元组里：它该升级到浏览器层，而不是在这里拿旧快照了事。"""
        assert 0 not in _ARCHIVE_FALLBACK_STATUSES
        assert _should_try_archive(
            _result(status=0, content=[], fetcher_used="none",
                    error="network_error: dns_failure (TCP preflight)"))

    @pytest.mark.asyncio
    async def test_410_is_answered_from_the_archive(self, monkeypatch):
        """端到端：HTTP 层拿到 410 时，回退确实会去取 archive 快照。"""
        import dhole_mcp.fetcher as fetcher
        monkeypatch.setattr(fetcher, "tcp_preflight", lambda url, timeout=2.0: (True, ""))
        server = MasterFetchServer()

        async def gone(*args, **kwargs):
            return _result(status=410, content=[], fetcher_used="http",
                           error="HTTP status 410")

        server.get = gone
        asked: list[str] = []

        async def fake_archive(url, *args, **kwargs):
            asked.append(url)
            return _result(status=200, content=["Archived snapshot body."],
                           fetcher_used="archive.org", url=url)

        monkeypatch.setattr(server, "_fetch_from_archive", fake_archive)
        out = await server._auto_escalate(
            "https://gone.example.com/x", "markdown", None, True, True, 0, 0,
            True, False, 0, None, 8000, False, False, False, False,
            None, None, None,
        )
        assert asked == ["https://gone.example.com/x"], "410 没走到 archive 回退"
        assert out.content == ["Archived snapshot body."]
        assert out.content_ok is True
