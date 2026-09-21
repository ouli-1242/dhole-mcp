"""Search tests: site/exclude_sites hostname filtering (PR #7), GitHub
case-folding dedup (PR #8), URL normalization, multi_search mapping logic,
RawResult consensus, EngineReport status mapping.

The metasearch backend is mocked (no network), but the filtering, dedup,
consensus, and mapping logic being tested is the REAL code that runs on
real search results.
"""

import asyncio
import json
import pytest
from dhole_mcp import search as search
from dhole_mcp.search import SearchResult
from dhole_mcp import search_engines as se
from dhole_mcp.search_engines import (
    _passes_site_filter, _normalize_domain, RawResult, EngineReport, multi_search,
    _INDEX_FAMILY,
)


@pytest.fixture
def smart_search_cache(monkeypatch):
    """In-memory cache and deterministic search backend for cache-key tests."""
    cache = {}
    live_regions = []

    async def fake_get_cached(query, cache_type, css_selector, **kwargs):
        return cache.get((query, cache_type))

    async def fake_set_cached(query, cache_type, content, status, css_selector, ttl, **kwargs):
        cache[(query, cache_type)] = {"content": content}

    async def fake_multi_search(query, max_results, **kwargs):
        region = kwargs["region"]
        live_regions.append(region)
        return [RawResult(
            title=f"Result for {region}",
            url=f"https://{region}.example.test/",
            snippet="regional result",
            source="brave",
            position=1,
        )], [EngineReport(name="brave", ok=True)]

    async def fake_ensure_reranker():
        return None

    monkeypatch.setattr(search, "get_cached", fake_get_cached)
    monkeypatch.setattr(search, "set_cached", fake_set_cached)
    monkeypatch.setattr(search, "multi_search", fake_multi_search)
    monkeypatch.setattr(search, "ensure_reranker", fake_ensure_reranker)
    monkeypatch.setattr(
        search, "_rank",
        lambda query, ranked, mode: (ranked, [1.0] * len(ranked), "merge", ""),
    )
    return cache, live_regions


class TestSmartSearchRegionCache:

    @pytest.mark.asyncio
    async def test_explicit_regions_use_distinct_cache_entries(self, smart_search_cache):
        cache, live_regions = smart_search_cache

        us = await search.smart_search(
            None, "regional cache test", engines=["brave"], region="us-en",
        )
        gb = await search.smart_search(
            None, "regional cache test", engines=["brave"], region="gb-en",
        )

        assert us.cached is False
        assert gb.cached is False
        assert [us.results[0].title, gb.results[0].title] == [
            "Result for us-en", "Result for gb-en",
        ]
        assert live_regions == ["us-en", "gb-en"]
        assert len(cache) == 2

    @pytest.mark.asyncio
    async def test_same_normalized_explicit_region_uses_cached_result(self, smart_search_cache):
        cache, live_regions = smart_search_cache

        first = await search.smart_search(
            None, "regional cache test", engines=["brave"], region="GB-EN",
        )
        second = await search.smart_search(
            None, "regional cache test", engines=["brave"], region="gb-en",
        )

        assert first.cached is False
        assert second.cached is True
        assert second.results[0].title == "Result for GB-EN"
        assert live_regions == ["GB-EN"]
        assert len(cache) == 1

    @pytest.mark.asyncio
    async def test_omitted_region_keeps_existing_cache_key(self, smart_search_cache):
        cache, _ = smart_search_cache

        await search.smart_search(
            None, "regional cache test", engines=["brave"],
        )

        assert list(cache) == [
            ("regional cache test", "search:v5:6:::::0:brave::auto:regional cache test"),
        ]


# ─── Hostname boundary filtering (PR #7) ──────────────────────────

class TestSiteFilter:

    def test_exact_domain_passes_site_filter(self):
        assert _passes_site_filter("https://github.com/repo", "github.com", None) is True

    def test_subdomain_passes_site_filter(self):
        assert _passes_site_filter("https://docs.github.com/en", "github.com", None) is True

    def test_evil_prefix_rejected(self):
        assert _passes_site_filter("https://evilgithub.com/repo", "github.com", None) is False

    def test_evil_suffix_rejected(self):
        assert _passes_site_filter("https://github.com.evil.test/repo", "github.com", None) is False

    def test_similar_domain_rejected(self):
        assert _passes_site_filter("https://notgithub.com/repo", "github.com", None) is False

    def test_port_stripped_for_comparison(self):
        assert _passes_site_filter("https://github.com:8443/repo", "github.com", None) is True

    def test_www_prefix_stripped_correctly(self):
        # www.github.com as site filter should match www.github.com
        assert _passes_site_filter("https://www.github.com/repo", "www.github.com", None) is True
        # But github.com as site should also match www.github.com (subdomain)
        assert _passes_site_filter("https://www.github.com/repo", "github.com", None) is True

    def test_ww_prefix_not_stripped_as_www(self):
        # "wwgithub.com" must not be stripped as if it started with "www."
        assert _passes_site_filter("https://github.com/repo", "wwgithub.com", None) is False


class TestExcludeSitesFilter:

    def test_exclude_exact_domain(self):
        assert _passes_site_filter("https://github.com/repo", None, ["github.com"]) is False

    def test_exclude_subdomain(self):
        assert _passes_site_filter("https://docs.github.com/repo", None, ["github.com"]) is False

    def test_exclude_does_not_block_evil_prefix(self):
        assert _passes_site_filter("https://evilgithub.com/repo", None, ["github.com"]) is True

    def test_exclude_does_not_block_evil_suffix(self):
        assert _passes_site_filter("https://github.com.evil.test/repo", None, ["github.com"]) is True

    def test_exclude_does_not_block_similar(self):
        assert _passes_site_filter("https://notgithub.com/repo", None, ["github.com"]) is True


class TestNormalizeDomain:

    def test_strips_www_prefix(self):
        assert _normalize_domain("www.example.com") == "example.com"

    def test_does_not_strip_ww(self):
        assert _normalize_domain("wwexample.com") == "wwexample.com"

    def test_strips_trailing_dot(self):
        assert _normalize_domain("example.com.") == "example.com"

    def test_lowercases(self):
        assert _normalize_domain("Example.COM") == "example.com"

    def test_empty_returns_empty(self):
        assert _normalize_domain("") == ""

    def test_handles_url_with_scheme(self):
        assert _normalize_domain("https://www.example.com/path") == "example.com"

    def test_handles_url_without_scheme(self):
        assert _normalize_domain("www.example.com") == "example.com"


# ─── GitHub case-folding dedup (PR #8) ────────────────────────────

class TestGitHubCaseFolding:

    def test_owner_repo_casefolded(self):
        from dhole_mcp.search_metasearch import _normalize_url
        a = _normalize_url("https://github.com/nousresearch/hermes-agent")
        b = _normalize_url("https://github.com/NousResearch/hermes-agent")
        assert a == b

    def test_branch_case_preserved(self):
        from dhole_mcp.search_metasearch import _normalize_url
        a = _normalize_url("https://github.com/NousResearch/Hermes-Agent/tree/Main")
        b = _normalize_url("https://github.com/nousresearch/hermes-agent/tree/main")
        assert a != b

    def test_non_github_paths_case_sensitive(self):
        from dhole_mcp.search_metasearch import _normalize_url
        a = _normalize_url("https://example.com/Docs/Readme")
        b = _normalize_url("https://example.com/docs/readme")
        assert a != b

    def test_credential_urls_skip_folding(self):
        from dhole_mcp.search_metasearch import _normalize_url
        a = _normalize_url("https://User:Secret@github.com/NousResearch/Hermes-Agent")
        b = _normalize_url("https://user:secret@github.com/nousresearch/hermes-agent")
        assert a != b


class TestGitHubReservedRoutes:
    """PR #10: GitHub system routes (topics, settings, explore, etc.) should
    NOT have their path segments case-folded, because they are not repositories
    and case can carry meaning (e.g. /topics/Python vs /topics/python)."""

    def test_reserved_route_not_folded(self):
        from dhole_mcp.search_metasearch import _normalize_url
        a = _normalize_url("https://github.com/Settings/Keys")
        b = _normalize_url("https://github.com/settings/keys")
        assert a != b

    def test_topics_route_case_preserved(self):
        from dhole_mcp.search_metasearch import _normalize_url
        a = _normalize_url("https://github.com/topics/Python")
        b = _normalize_url("https://github.com/topics/python")
        assert a != b

    def test_explore_route_case_preserved(self):
        from dhole_mcp.search_metasearch import _normalize_url
        a = _normalize_url("https://github.com/Explore/Rust")
        b = _normalize_url("https://github.com/explore/rust")
        assert a != b

    def test_repo_still_folded_after_fix(self):
        from dhole_mcp.search_metasearch import _normalize_url
        a = _normalize_url("https://github.com/NousResearch/Hermes-Agent")
        b = _normalize_url("https://github.com/nousresearch/hermes-agent")
        assert a == b

    def test_reserved_route_lowercased_unchanged(self):
        """Already-lowercase reserved routes should be unchanged."""
        from dhole_mcp.search_metasearch import _normalize_url
        assert _normalize_url("https://github.com/topics/python") == \
               "https://github.com/topics/python"

    def test_multiple_reserved_routes(self):
        from dhole_mcp.search_metasearch import _normalize_url
        for route in ["settings", "topics", "explore", "dashboard", "notifications",
                      "marketplace", "sponsors", "collections", "trending", "search"]:
            a = _normalize_url(f"https://github.com/{route.title()}/Sub")
            b = _normalize_url(f"https://github.com/{route}/sub")
            assert a != b, f"Route '{route}' should not be case-folded"


# ─── multi_search mapping logic ────────────────────────────────────

class TestMultiSearchMapping:
    """Test the mapping from metasearch dicts to RawResult + EngineReport.
    The metasearch backend is mocked, but the mapping/filtering is real."""

    @pytest.mark.asyncio
    async def test_site_filter_applied_on_results(self, monkeypatch):
        fake_results = [
            {"title": "A", "href": "https://github.com/repo", "body": "b", "backend": "brave", "backends": ["brave"]},
            {"title": "B", "href": "https://evilgithub.com/repo", "body": "b", "backend": "brave", "backends": ["brave"]},
        ]
        async def fake_metasearch(q, n, **kw):
            return fake_results, {"brave": "ok"}
        monkeypatch.setattr(se, "_metasearch", fake_metasearch)

        ranked, reports = await multi_search("test", 6, site="github.com")
        assert len(ranked) == 1
        assert ranked[0].url == "https://github.com/repo"

    @pytest.mark.asyncio
    async def test_exclude_sites_filter_applied(self, monkeypatch):
        fake_results = [
            {"title": "A", "href": "https://github.com/repo", "body": "b", "backend": "brave", "backends": ["brave"]},
            {"title": "B", "href": "https://example.com/repo", "body": "b", "backend": "brave", "backends": ["brave"]},
        ]
        async def fake_metasearch(q, n, **kw):
            return fake_results, {"brave": "ok"}
        monkeypatch.setattr(se, "_metasearch", fake_metasearch)

        ranked, _ = await multi_search("test", 6, exclude_sites=["github.com"])
        assert len(ranked) == 1
        assert ranked[0].url == "https://example.com/repo"

    @pytest.mark.asyncio
    async def test_consensus_from_multiple_backends(self, monkeypatch):
        fake_results = [
            {"title": "A", "href": "https://github.com/repo", "body": "b",
             "backend": "brave", "backends": ["brave", "duckduckgo"]},
        ]
        async def fake_metasearch(q, n, **kw):
            return fake_results, {"brave": "ok", "duckduckgo": "ok"}
        monkeypatch.setattr(se, "_metasearch", fake_metasearch)

        ranked, _ = await multi_search("test", 6)
        assert len(ranked) == 1
        # brave and duckduckgo are different index families
        assert ranked[0].consensus == 2

    @pytest.mark.asyncio
    async def test_consensus_same_index_family(self, monkeypatch):
        # DDG and Yahoo both use Bing's index -> consensus = 1
        fake_results = [
            {"title": "A", "href": "https://example.com", "body": "b",
             "backend": "duckduckgo", "backends": ["duckduckgo", "yahoo"]},
        ]
        async def fake_metasearch(q, n, **kw):
            return fake_results, {"duckduckgo": "ok", "yahoo": "ok"}
        monkeypatch.setattr(se, "_metasearch", fake_metasearch)

        ranked, _ = await multi_search("test", 6)
        assert ranked[0].consensus == 1  # same index family (Bing)

    @pytest.mark.asyncio
    async def test_engine_reports_mapped(self, monkeypatch):
        async def fake_metasearch(q, n, **kw):
            return [], {"brave": "ok", "google": "blocked", "yahoo": "empty", "mojeek": "timeout"}
        monkeypatch.setattr(se, "_metasearch", fake_metasearch)

        _, reports = await multi_search("test", 6)
        report_map = {r.name: r for r in reports}
        assert report_map["brave"].ok is True
        assert report_map["google"].blocked is True
        assert report_map["yahoo"].ok is False
        assert report_map["mojeek"].blocked is True

    @pytest.mark.asyncio
    async def test_freshness_maps_to_timelimit(self, monkeypatch):
        captured = {}
        async def fake_metasearch(q, n, **kw):
            captured["timelimit"] = kw.get("timelimit")
            return [], {"brave": "ok"}
        monkeypatch.setattr(se, "_metasearch", fake_metasearch)

        await multi_search("test", 6, freshness="week")
        assert captured["timelimit"] == "w"

    @pytest.mark.asyncio
    async def test_page_zero_indexed_to_one(self, monkeypatch):
        captured = {}
        async def fake_metasearch(q, n, **kw):
            captured["page"] = kw.get("page")
            return [], {"brave": "ok"}
        monkeypatch.setattr(se, "_metasearch", fake_metasearch)

        await multi_search("test", 6, page=0)
        assert captured["page"] == 1

    @pytest.mark.asyncio
    async def test_site_prefix_added_to_query(self, monkeypatch):
        captured = {}
        async def fake_metasearch(q, n, **kw):
            captured["query"] = q
            return [], {"brave": "ok"}
        monkeypatch.setattr(se, "_metasearch", fake_metasearch)

        await multi_search("test query", 6, site="github.com")
        assert "site:github.com" in captured["query"]

    @pytest.mark.asyncio
    async def test_exclude_prefix_added_to_query(self, monkeypatch):
        captured = {}
        async def fake_metasearch(q, n, **kw):
            captured["query"] = q
            return [], {"brave": "ok"}
        monkeypatch.setattr(se, "_metasearch", fake_metasearch)

        await multi_search("test", 6, exclude_sites=["pinterest.com"])
        assert "-site:pinterest.com" in captured["query"]


# ─── Index family (consensus semantics) ───────────────────────────

class TestEngineConfig:
    """引擎池的成员/数量快照测试已删（DEFAULT_ENGINES 是数据不是逻辑，
    增删引擎属正常演进）。保留 index-family 映射——它是跨引擎共识
    权威信号的正确性契约：DDG 与 Yahoo 共用 Bing 索引，必须算作一个
    家族，否则共识会虚高。"""

    def test_index_family_mapping(self):
        # DDG and Yahoo share Bing's index
        assert _INDEX_FAMILY["duckduckgo"] == _INDEX_FAMILY["yahoo"] == "bing"
        # Brave has its own independent index
        assert _INDEX_FAMILY["brave"] == "brave"
        # Yandex has its own independent index
        assert _INDEX_FAMILY["yandex"] == "yandex"


# ─── Proxy validation ───────────────────────────

class TestSearchProxyValidation:
    def _load_with(self, monkeypatch, tmp_path, env_value):
        """Load proxies from the env var + an empty config file (isolated)."""
        from dhole_mcp import search_proxy as sp
        if env_value is None:
            monkeypatch.delenv("DHOLE_SEARCH_PROXY", raising=False)
        else:
            monkeypatch.setenv("DHOLE_SEARCH_PROXY", env_value)
        monkeypatch.setattr(sp, "_config_path", lambda: tmp_path / "no_such_proxies.json")
        sp._pool = None
        return sp.load_proxies()

    def test_whitespace_stripped(self, monkeypatch, tmp_path):
        """Leading/trailing whitespace is stripped so httpx doesn't crash."""
        proxies = self._load_with(monkeypatch, tmp_path, " http://proxy:8080 ")
        assert proxies == ["http://proxy:8080"]

    def test_whitespace_only_nulled(self, monkeypatch, tmp_path):
        """Whitespace-only proxy becomes empty, not a crash-inducing string."""
        assert self._load_with(monkeypatch, tmp_path, "   ") == []

    def test_invalid_scheme_rejected(self, monkeypatch, tmp_path):
        """Unknown scheme (not http/https/socks5/socks5h) is rejected."""
        assert self._load_with(monkeypatch, tmp_path, "garbage://proxy") == []

    def test_valid_socks5_accepted(self, monkeypatch, tmp_path):
        """socks5 scheme is accepted (primp supports it natively)."""
        assert self._load_with(monkeypatch, tmp_path, "socks5://192.0.2.1:1080") == [
            "socks5://192.0.2.1:1080"
        ]

    def test_no_proxy_env(self, monkeypatch, tmp_path):
        """Unset env var -> [] (direct connection)."""
        assert self._load_with(monkeypatch, tmp_path, None) == []

    def test_multiple_comma_separated(self, monkeypatch, tmp_path):
        """Comma-separated env var yields a rotation pool."""
        proxies = self._load_with(
            monkeypatch, tmp_path, "http://p1:8080,http://p2:8080,http://p3:8080"
        )
        assert proxies == ["http://p1:8080", "http://p2:8080", "http://p3:8080"]

    def test_all_engines_construction_failure_raises(self):
        """If every engine fails to construct (bad deps, etc), raise an error
        instead of silently returning 0 results."""
        import dhole_mcp.search_metasearch as m

        class BrokenEngine:
            disabled = False
            def __init__(self, **kwargs):
                raise RuntimeError("simulated construction failure")

        original = dict(m._TEXT_ENGINES)
        original_keyed = dict(m.KEYED_ENGINES)
        m.KEYED_ENGINES.clear()  # disable keyed backends for this test
        for name in m._TEXT_ENGINES:
            m._TEXT_ENGINES[name] = type(
                f"Broken{name}", (BrokenEngine,),
                {"name": name, "disabled": False, "priority": 1.0}
            )
        try:
            with pytest.raises(m.MetaSearchException, match="No search engines could start"):
                asyncio.run(m.metasearch("test", max_results=3))
        finally:
            m._TEXT_ENGINES.clear()
            m._TEXT_ENGINES.update(original)
            m.KEYED_ENGINES.clear()
            m.KEYED_ENGINES.update(original_keyed)


# ─── Result diversity ──────────────────────────

class TestDiversify:

    def _make_results(self, urls):
        return ([RawResult(title=f"Title {i}", url=u, snippet="",
                          source="brave", position=i) for i, u in enumerate(urls)],
                [0.9 - i * 0.01 for i in range(len(urls))])

    def test_same_domain_capped_at_two(self):
        ranked, scores = self._make_results([
            "https://medium.com/a",
            "https://medium.com/b",
            "https://medium.com/c",
            "https://medium.com/d",
        ])
        r, s = search._diversify(ranked, scores, max_per_domain=2)
        # First two medium.com kept in top, last two deferred to bottom
        top_domains = [search._get_domain(r[i].url) for i in range(2)]
        assert top_domains == ["medium.com", "medium.com"]
        # Third position should NOT be medium.com (deferred)
        assert search._get_domain(r[2].url) != "medium.com" or len(r) == 4
        # Actually all 4 are medium.com so 2 kept + 2 deferred
        assert len(r) == 4

    def test_different_domains_not_affected(self):
        ranked, scores = self._make_results([
            "https://github.com/a",
            "https://arxiv.org/b",
            "https://medium.com/c",
        ])
        r, s = search._diversify(ranked, scores, max_per_domain=2)
        assert [i.url for i in r] == [ranked[0].url, ranked[1].url, ranked[2].url]

    def test_mixed_domains_partial_deferral(self):
        ranked, scores = self._make_results([
            "https://medium.com/a",
            "https://medium.com/b",
            "https://github.com/c",
            "https://medium.com/d",  # 3rd medium.com → deferred
        ])
        r, s = search._diversify(ranked, scores, max_per_domain=2)
        assert len(r) == 4
        # First two are medium.com, third is github, fourth is deferred medium.com
        assert search._get_domain(r[0].url) == "medium.com"
        assert search._get_domain(r[1].url) == "medium.com"
        assert search._get_domain(r[2].url) == "github.com"
        assert search._get_domain(r[3].url) == "medium.com"

    def test_empty_list(self):
        r, s = search._diversify([], [], max_per_domain=2)
        assert r == [] and s == []

    def test_single_result(self):
        ranked, scores = self._make_results(["https://example.com/a"])
        r, s = search._diversify(ranked, scores, max_per_domain=2)
        assert len(r) == 1

    def test_www_prefix_stripped(self):
        """www.medium.com and medium.com should be treated as same domain."""
        ranked, scores = self._make_results([
            "https://www.medium.com/a",
            "https://medium.com/b",
            "https://medium.com/c",
        ])
        r, s = search._diversify(ranked, scores, max_per_domain=2)
        # First two kept (same domain after www strip), third deferred
        assert search._get_domain(r[0].url) == "medium.com"
        assert search._get_domain(r[1].url) == "medium.com"


# ─── Intent-aware fan-out ──────────────────────

class TestQueryMapIntegration:

    def test_research_query_generates_different_queries(self):
        engines = ["duckduckgo", "brave", "mojeek", "yahoo",
                    "yandex", "startpage", "google", "qwant"]
        intent = search._detect_intent("transformer attention mechanism research")
        assert intent == "research"
        qm = search._generate_query_map("transformer attention mechanism research", intent, engines)
        assert qm != {}
        assert qm["duckduckgo"] == "transformer attention mechanism research"
        assert qm["yandex"] != "transformer attention mechanism research"
        assert "paper" in qm["yandex"] or "arxiv" in qm["yandex"]

    def test_comparison_query_no_fan_out(self):
        engines = ["duckduckgo", "brave", "yandex"]
        intent = search._detect_intent("Python vs Rust performance comparison")
        assert intent == "comparison"
        qm = search._generate_query_map("Python vs Rust performance comparison", intent, engines)
        assert qm == {}

    def test_general_query_no_fan_out(self):
        engines = ["duckduckgo", "brave", "yandex"]
        intent = search._detect_intent("best restaurants in paris")
        qm = search._generate_query_map("best restaurants in paris", intent, engines)
        assert qm == {}

    def test_factual_query_fan_out(self):
        engines = ["duckduckgo", "yandex"]
        intent = search._detect_intent("GPT-3 embedding dimension d_model parameters")
        assert intent == "factual"
        qm = search._generate_query_map("GPT-3 embedding dimension d_model parameters", intent, engines)
        assert qm != {}
        assert "specifications" in qm["yandex"]
        assert "table" in qm["yandex"]


class TestIntentFalsePositives:
    """Ambiguous standalone words should NOT trigger wrong intents."""

    def test_area_code_not_code(self):
        assert search._detect_intent("area code 212") != "code"

    def test_toilet_paper_not_research(self):
        assert search._detect_intent("toilet paper brands") != "research"

    def test_database_update_not_news(self):
        assert search._detect_intent("database update syntax") != "news"

    def test_make_a_difference_not_comparison(self):
        assert search._detect_intent("make a difference quotes") != "comparison"

    def test_alternative_music_not_comparison(self):
        assert search._detect_intent("alternative music genres") != "comparison"

    def test_dining_table_not_factual(self):
        assert search._detect_intent("dining table wood") != "factual"

    def test_general_query_no_expansion(self):
        assert search._expand_query("best restaurants in paris", "general") == "best restaurants in paris"


class TestFactualDetectionExpanded:

    def test_architecture_layers_heads_factual(self):
        assert search._detect_intent("GPT-3 architecture layers heads") == "factual"

    def test_context_window_factual(self):
        assert search._detect_intent("transformer model context window") == "factual"

    def test_throughput_latency_factual(self):
        assert search._detect_intent("LLM inference throughput latency") == "factual"

    def test_non_technical_with_data_word_not_factual(self):
        assert search._detect_intent("shoe size guide") != "factual"


class TestQueryLengthLimit:

    def test_long_query_no_expansion(self):
        long_q = " ".join(["word"] * 15)
        assert search._expand_query(long_q, "code") == long_q

    def test_short_query_still_expanded(self):
        short_q = "attention mechanism"
        assert search._expand_query(short_q, "research") != short_q

    def test_exactly_14_words_expanded(self):
        q = " ".join(["attention"] + ["word"] * 13)
        assert search._expand_query(q, "research") != q


class TestNewsYearDynamic:

    def test_news_no_expansion(self):
        assert search._expand_query("latest AI announcement", "news") == "latest AI announcement"


class TestIrrelevantFilter:
    """_filter_irrelevant_results: drop noise when engines return filler."""

    def _res(self, title, url, snippet=""):
        return search.SearchResult(title=title, url=url, snippet=snippet)

    def test_gibberish_query_filters_all(self):
        results = [
            self._res("SpaceX", "https://spacex.com", "rockets"),
            self._res("Facebook", "https://facebook.com", "social media"),
        ]
        out = search._filter_irrelevant_results(results, "xqzkjfhoiqweudhxqzkj")
        assert out == []

    def test_relevant_results_kept(self):
        results = [
            self._res("Python Async Guide", "https://example.com/async", "asyncio best practices"),
        ]
        out = search._filter_irrelevant_results(results, "python asyncio")
        assert out == [results[0]]

    def test_partial_relevance_keeps_all(self):
        # At least one result matches a term -> keep the set (ranking handles the rest)
        results = [
            self._res("SpaceX launch", "https://spacex.com", "python in flight software"),
            self._res("random page", "https://fb.com", "x"),
        ]
        out = search._filter_irrelevant_results(results, "python asyncio")
        assert len(out) == 2

    def test_cjk_query_never_filtered(self):
        results = [self._res("Some English page", "https://example.com/")]
        # '量子计算' has no latin terms -> engines' judgment stands
        assert search._filter_irrelevant_results(results, "量子计算 最新进展") == results

    def test_empty_results_returns_empty(self):
        assert search._filter_irrelevant_results([], "anything") == []

    def test_stopword_only_query_never_filtered(self):
        results = [self._res("The", "https://example.com/")]
        # 'how to' -> terms empty (stopwords) -> keep
        assert search._filter_irrelevant_results(results, "how to") == results


# ─── 共识诚实化（S1）：分母是"本该表态的家族数"，不是"实际返回结果的家族数" ───

class TestFamilyUniverse:

    def test_default_pool_all_healthy_is_every_family(self):
        # 分母 = 池中独立索引家族数，不是引擎数（bing/ddg/yahoo 共用一个索引）。
        # 具体几个由 test_engine_registry 钉住；这里钉的是"全员健康 = 满分"。
        reports = [EngineReport(name=n, ok=True) for n in se.DEFAULT_ENGINES]
        m, c, basis = search._family_universe(None, reports)
        n_families = len({se._INDEX_FAMILY.get(n, n) for n in se.DEFAULT_ENGINES})
        assert (m, c, basis) == (n_families, n_families, "full")
        assert m < len(se.DEFAULT_ENGINES), "池里至少有两条引擎共用一个索引"

    def test_denominator_survives_a_degraded_pool(self):
        # 只有 bing 活着：旧实现会渲染 "1 of 1"（与全员一致不可区分）
        reports = [EngineReport(name="bing", ok=True)] + [
            EngineReport(name=n, blocked=True) for n in ("brave", "yandex")]
        m, c, basis = search._family_universe(None, reports)
        assert m == len({se._INDEX_FAMILY.get(n, n) for n in se.DEFAULT_ENGINES}), \
            "分母不该因为别的引擎挂掉而缩水"
        assert c == 1
        assert basis == "degraded_pool"

    def test_whole_family_preempted_leaves_the_denominator(self):
        # brave 全家（只有它自己）被抢占 = 根本没机会表态，不该占分母
        reports = [EngineReport(name="bing", ok=True), EngineReport(name="brave", preempted=True)]
        m, c, basis = search._family_universe(["bing", "brave"], reports)
        assert (m, c) == (1, 1)
        assert basis == "single_family"

    def test_partial_preemption_within_a_family_keeps_it(self):
        # bing 家族里 ddg 被抢占但 bing 答了 -> 家族仍有发言权，留在分母
        reports = [EngineReport(name="bing", ok=True),
                   EngineReport(name="duckduckgo", preempted=True),
                   EngineReport(name="brave", ok=True)]
        m, c, basis = search._family_universe(["bing", "duckduckgo", "brave"], reports)
        assert (m, c, basis) == (2, 2, "full")

    def test_single_engine_pool_has_no_corroboration(self):
        reports = [EngineReport(name="yandex", ok=True)]
        m, c, basis = search._family_universe(["yandex"], reports)
        assert (m, c, basis) == (1, 1, "single_family")


class TestConsensusRendering:

    def _one(self, consensus: int) -> str:
        raw = RawResult(title="T", url="https://example.com/a", snippet="s",
                        source="bing", position=1, consensus=consensus)
        return search._build_results("q", [raw], [0.9], 3)[0].engines_consensus

    def test_renders_against_the_universe(self):
        assert self._one(1) == "1 of 3"
        assert self._one(2) == "2 of 3"

    def test_numerator_is_clamped_to_the_denominator(self):
        # 家族数缩水后 consensus 可能大于分母，不能渲染出 "5 of 3"
        assert self._one(5) == "3 of 3"

    def test_single_family_is_not_rendered_as_a_ratio(self):
        raw = RawResult(title="T", url="https://example.com/a", snippet="s",
                        source="yandex", position=1, consensus=1)
        got = search._build_results("q", [raw], [0.9], 1)[0].engines_consensus
        assert got == "1 of 1 (no corroboration)"


@pytest.fixture
def degraded_pool(monkeypatch):
    """一个引擎有产出、四个被墙的固定场景，缓存与引擎层都是假的。"""
    cache: dict = {}

    async def fake_multi_search(query, max_results, **kwargs):
        return [RawResult(
            title="sqlite WAL mode explained", url="https://sqlite.org/wal",
            snippet="sqlite WAL mode journaling", source="bing", position=1,
        )], ([EngineReport(name="bing", ok=True)]
             + [EngineReport(name=n, blocked=True)
                for n in ("duckduckgo", "brave", "yahoo", "yandex")])

    async def fake_ensure_reranker():
        return None

    async def fake_get_cached(query, cache_type, css_selector, **kwargs):
        return cache.get((query, cache_type))

    async def fake_set_cached(query, cache_type, content, status, css_selector, ttl, **kwargs):
        cache[(query, cache_type)] = {"content": content}

    monkeypatch.setattr(search, "multi_search", fake_multi_search)
    monkeypatch.setattr(search, "ensure_reranker", fake_ensure_reranker)
    monkeypatch.setattr(search, "get_cached", fake_get_cached)
    monkeypatch.setattr(search, "set_cached", fake_set_cached)
    return cache


class TestDegradedPoolIsHonest:
    """降级池的标注必须在 live 与缓存命中两条路径上一致。

    以前只有 live 路径往 fetch_hint 上追加，TTL 内的重复查询（agent 的常态）
    一条提示都不带 —— 同一份降级结果，第二次问就被包装成干净结果。
    """

    @pytest.mark.asyncio
    async def test_consensus_counts_the_universe_not_the_survivors(self, degraded_pool):
        r = await search.smart_search(None, "sqlite WAL mode", max_results=6, mode="auto")
        n_families = len({se._INDEX_FAMILY.get(n, n) for n in se.DEFAULT_ENGINES})
        # 只有一个家族活着，分母仍是整个池子的家族数（否则 "1 of 1" 与全员一致同形）
        assert r.results and r.results[0].engines_consensus == f"1 of {n_families}"
        assert r.consensus_basis == "degraded_pool"

    @pytest.mark.asyncio
    async def test_live_response_carries_the_low_confidence_note(self, degraded_pool):
        r = await search.smart_search(None, "sqlite WAL mode", max_results=6, mode="auto")
        assert "LOW CONFIDENCE" in r.fetch_hint

    @pytest.mark.asyncio
    async def test_cache_hit_reports_the_same_degradation_as_live(self, degraded_pool):
        live = await search.smart_search(None, "sqlite WAL mode", max_results=6, mode="auto")
        hit = await search.smart_search(None, "sqlite WAL mode", max_results=6, mode="auto")
        assert hit.cached is True, "第二次应当命中缓存"
        assert hit.consensus_basis == live.consensus_basis
        assert "LOW CONFIDENCE" in hit.fetch_hint
        assert hit.results[0].engines_consensus == live.results[0].engines_consensus

    @pytest.mark.asyncio
    async def test_pre_s1_cache_row_still_warns(self, degraded_pool):
        """升级前写入的缓存行没有新字段，也不能变成"看起来干净"。

        旧行的 engines_consensus 字符串是写盘时渲染的（"1 of 1"），改不动；
        但降级标注是从 engines_used/engine_blocked 现推的，必须照旧出现。
        """
        await search.smart_search(None, "sqlite WAL mode", max_results=6, mode="auto")
        (key, payload), = degraded_pool.items()
        row = json.loads(payload["content"][0])
        for gone in ("consensus_basis", "families_contributing", "family_universe"):
            row.pop(gone, None)
        row["results"][0]["engines_consensus"] = "1 of 1"  # 旧版渲染
        payload["content"] = [json.dumps(row)]

        hit = await search.smart_search(None, "sqlite WAL mode", max_results=6, mode="auto")
        assert hit.cached is True
        assert hit.consensus_basis == ""      # 旧行无从得知，不猜
        assert "LOW CONFIDENCE" in hit.fetch_hint
        assert "1 of 1" in hit.results[0].engines_consensus

    @pytest.mark.asyncio
    async def test_healthy_pool_cache_hit_adds_no_warning(self, monkeypatch):
        cache: dict = {}

        async def fake_multi_search(query, max_results, **kwargs):
            return [RawResult(
                title="sqlite WAL mode explained", url="https://sqlite.org/wal",
                snippet="sqlite WAL mode", source="bing", position=1)], [
                EngineReport(name="bing", ok=True), EngineReport(name="brave", ok=True),
                EngineReport(name="yandex", ok=True)]

        async def fake_get_cached(query, cache_type, css_selector, **kwargs):
            return cache.get((query, cache_type))

        async def fake_set_cached(query, cache_type, content, status, css_selector, ttl, **kwargs):
            cache[(query, cache_type)] = {"content": content}

        monkeypatch.setattr(search, "multi_search", fake_multi_search)
        monkeypatch.setattr(search, "ensure_reranker", lambda *a, **k: None)
        async def _none():
            return None
        monkeypatch.setattr(search, "ensure_reranker", _none)
        monkeypatch.setattr(search, "get_cached", fake_get_cached)
        monkeypatch.setattr(search, "set_cached", fake_set_cached)

        for _ in range(2):
            r = await search.smart_search(None, "sqlite WAL mode", max_results=6, mode="auto")
            assert r.consensus_basis == "full"
            assert "LOW CONFIDENCE" not in r.fetch_hint
            assert "didn't contribute" not in r.fetch_hint


# ─── empty 可见性（S4a）：引擎答了但解析出 0 条，此前在两个列表里同时消失 ───

async def _search_with(monkeypatch, reports, results=None, query="sqlite WAL mode",
                     store=None, **kw):
    """用给定的 reports 跑一次真 smart_search（引擎层与缓存都是假的）。"""
    ranked = results if results is not None else [RawResult(
        title="sqlite WAL mode explained", url="https://sqlite.org/wal",
        snippet="sqlite WAL journaling", source="bing", position=1)]

    async def fake_multi(query_, max_results, **kwargs):
        return ranked, reports

    async def _none():
        return None

    if store is None:
        async def no_get(*a, **kwargs):
            return None

        async def no_set(*a, **kwargs):
            return None
    else:
        async def no_get(key_, cache_type, css_selector, **kwargs):
            return store.get((key_, cache_type))

        async def no_set(key_, cache_type, content, status, css_selector, ttl, **kwargs):
            store[(key_, cache_type)] = {"content": content}

    monkeypatch.setattr(search, "multi_search", fake_multi)
    monkeypatch.setattr(search, "ensure_reranker", _none)
    monkeypatch.setattr(search, "get_cached", no_get)
    monkeypatch.setattr(search, "set_cached", no_set)
    return await search.smart_search(None, query, max_results=6, mode="auto", **kw)


class TestEngineEmptyIsVisible:

    @pytest.mark.asyncio
    async def test_empty_status_is_carried_through_the_report(self, monkeypatch):
        async def fake_metasearch(q, n, **kw):
            return [], {"brave": "empty", "bing": "ok", "yahoo": "circuit_open"}
        monkeypatch.setattr(se, "_metasearch", fake_metasearch)
        _, reports = await multi_search("test", 6)
        by = {r.name: r for r in reports}
        # empty 既不是 ok 也不是 blocked —— 正因如此它此前哪儿都不出现
        assert by["brave"].ok is False and by["brave"].blocked is False
        assert by["brave"].status == "empty"
        assert by["yahoo"].status == "circuit_open"

    @pytest.mark.asyncio
    async def test_init_and_no_key_failures_count_as_blocked(self, monkeypatch):
        """构造失败 / 缺 key 不是"这个查询没结果"，不能落到 empty 分支。"""
        async def fake_metasearch(q, n, **kw):
            return [], {"bing": "init_error:RuntimeError", "tavily": "no_key:TAVILY_API_KEY"}
        monkeypatch.setattr(se, "_metasearch", fake_metasearch)
        _, reports = await multi_search("test", 6)
        assert all(r.blocked for r in reports), [r.status for r in reports]

    @pytest.mark.asyncio
    async def test_response_separates_empty_from_blocked_and_preempted(self, monkeypatch):
        reports = ([EngineReport(name="bing", ok=True, status="ok")]
                   + [EngineReport(name=n, status="empty", error="no results")
                      for n in ("brave", "yandex")]
                   + [EngineReport(name="yahoo", blocked=True, status="timeout", error="timed out")]
                   + [EngineReport(name="duckduckgo", preempted=True, status="preempted")])
        r = await _search_with(monkeypatch, reports)
        assert r.engines_used == ["bing"]
        assert r.engine_blocked == ["yahoo"]
        assert r.engine_empty == ["brave", "yandex"]
        assert r.engine_preempted == ["duckduckgo"]

    @pytest.mark.asyncio
    async def test_all_silent_pool_warns_about_diversity(self):
        """全体 empty 时要说"引擎没产出"，而不是把 1/1 当健康然后闭嘴。

        _search_next_action 的分母此前只算 blocked：4/5 解析器坏了会被读成
        blocked=0/total=1 → 警告永不触发。
        """
        r = search._search_next_action(
            [SearchResult(title="t", url="https://a.test", fetch_relevance="high")],
            [], "", ["bing"],
            engine_empty=["brave", "yandex", "yahoo", "duckduckgo"],
        )
        assert "didn't contribute" in r and "LOW diversity" in r, r

    @pytest.mark.asyncio
    async def test_next_action_names_the_two_failure_kinds_separately(self):
        """"被墙"和"答了但解析不出东西"要分开说 —— 修法完全不同。"""
        r = search._search_next_action(
            [SearchResult(title="t", url="https://a.test", fetch_relevance="high")],
            ["bing"], "", ["yandex"], engine_empty=["brave", "yahoo", "duckduckgo"],
        )
        assert "1 blocked" in r and "3 answered but parsed nothing usable" in r, r

    @pytest.mark.asyncio
    async def test_pool_health_notes_cover_empty_engines_too(self):
        got = search._pool_health_notes(
            [SearchResult(title="t", url="https://a.test")], ["brave", "yandex"], 1)
        assert "didn't contribute" in got and "LOW CONFIDENCE" in got

    @pytest.mark.asyncio
    async def test_cache_hit_keeps_empty_visible(self, monkeypatch):
        store: dict = {}

        async def fake_get(query_, cache_type, css_selector, **kwargs):
            return store.get((query_, cache_type))

        async def fake_set(query_, cache_type, content, status, css_selector, ttl, **kwargs):
            store[(query_, cache_type)] = {"content": content}

        store: dict = {}
        reports = [EngineReport(name="bing", ok=True, status="ok"),
                   EngineReport(name="brave", status="empty", error="no results"),
                   EngineReport(name="yandex", preempted=True, status="preempted")]
        live = await _search_with(monkeypatch, reports, store=store)
        hit = await _search_with(monkeypatch, reports, store=store)
        assert hit.cached is True
        assert live.engine_empty == ["brave"] and live.engine_preempted == ["yandex"]
        assert hit.engine_empty == live.engine_empty
        assert hit.engine_preempted == live.engine_preempted


# ─── 全沉默时的改写 gate（S4b）：只对"真太窄"改写，不对"解析器坏了"重复加压 ───

class TestSilentPoolRewriteGate:
    """should_rewrite 此前同时救两类完全不同的情况，单次搜索里又无法区分。

    有了 item_nodes/usable 就能分：容器在而可用为 0 = 我们的 xpath 坏了，再打一轮
    全量 fan-out 只会在上游正改版那天把请求量翻倍；全 0 容器更像查询太窄，改写照旧。
    """

    def _calls(self, monkeypatch, first_reports, second_results=None):
        calls: list[dict] = []

        async def fake_multi(query_, max_results, **kwargs):
            calls.append({"query": query_, "engines": kwargs.get("engines")})
            if len(calls) == 1:
                return [], first_reports
            return (second_results or []), [EngineReport(name="brave", ok=True, status="ok")]

        async def _none():
            return None

        async def no_get(*a, **kwargs):
            return None

        async def no_set(*a, **kwargs):
            return None

        monkeypatch.setattr(search, "multi_search", fake_multi)
        monkeypatch.setattr(search, "ensure_reranker", _none)
        monkeypatch.setattr(search, "get_cached", no_get)
        monkeypatch.setattr(search, "set_cached", no_set)
        return calls

    @pytest.mark.asyncio
    async def test_confirmed_drift_does_not_double_the_fanout(self, monkeypatch):
        reports = [EngineReport(name=n, status="empty", error="no results",
                                item_nodes=8, usable=0, yield_verdict="parser_drift")
                   for n in ("bing", "brave", "yandex")]
        calls = self._calls(monkeypatch, reports)
        r = await search.smart_search(None, "sqlite WAL mode", max_results=6, mode="auto")
        assert len(calls) == 1, "解析器确认坏了，不该再打第二轮全量 fan-out"
        assert "parsed 0 usable results" in r.error
        assert "dhole -v" in r.error
        assert "Try rephrasing" not in r.error, "不该把自家故障诊断成用户的查询问题"

    @pytest.mark.asyncio
    async def test_genuinely_narrow_query_still_gets_the_rewrite(self, monkeypatch):
        """全 0 容器 = 更像查询太窄 —— 改写救回必须原样保留，否则这次修复就是在降召回。"""
        reports = [EngineReport(name=n, status="empty", error="no results",
                                item_nodes=0, usable=0, yield_verdict="unknown_empty")
                   for n in ("bing", "brave", "yandex")]
        calls = self._calls(monkeypatch, reports, second_results=[RawResult(
            title="sqlite", url="https://sqlite.org", snippet="s", source="brave", position=1)])
        r = await search.smart_search(None, "zzzqqx sqlite WAL mode", max_results=6, mode="auto")
        assert len(calls) == 2, "窄查询的改写救回被剥夺了"
        assert r.results, "改写后应当拿到结果"

    @pytest.mark.asyncio
    async def test_partial_drift_rewrites_only_the_healthy_engines(self, monkeypatch):
        reports = [
            EngineReport(name="bing", status="empty", error="no results",
                         item_nodes=9, usable=0, yield_verdict="parser_drift"),
            EngineReport(name="brave", status="empty", error="no results",
                         item_nodes=0, usable=0, yield_verdict="unknown_empty"),
        ]
        calls = self._calls(monkeypatch, reports)
        await search.smart_search(None, "sqlite WAL mode", max_results=6, mode="auto")
        assert len(calls) == 2
        assert calls[1]["engines"] == ["brave"], calls[1]["engines"]

    @pytest.mark.asyncio
    async def test_blocked_pool_message_is_unchanged(self, monkeypatch):
        """被墙那条路径的文案不受本阶段影响。"""
        reports = [EngineReport(name="bing", blocked=True, status="timeout", error="timed out")]
        self._calls(monkeypatch, reports)
        r = await search.smart_search(None, "sqlite WAL mode", max_results=6, mode="auto")
        assert "rate-limited" in r.error or "rephrase" in r.error, r.error


class TestRerankFallbackNote:
    """auto 模式的分寸：精简安装别啰嗦，依赖齐了却没重排必须说。"""

    @pytest.mark.parametrize("mode", ["auto", "neural"])
    def test_note_appears_only_when_deps_are_present(self, monkeypatch, mode):
        raw = [RawResult(title=f"r{i}", url=f"https://a{i}.test", snippet="s",
                         source="bing", position=i + 1) for i in range(3)]
        monkeypatch.setattr(search, "neural_rerank", lambda q, r: None)
        # 精简安装：缺依赖是预期形态，auto 不该每次解释
        monkeypatch.setattr(search, "unavailable_reason",
                            lambda: "neural rerank needs dhole-mcp[all] (ModuleNotFoundError: onnxruntime)")
        _, _, used, note = search._rank("q", raw, mode)
        assert used == "merge"
        assert (note != "") is (mode == "neural")
        # 依赖装了但模型没下下来 = 意外形态，auto 也要报
        monkeypatch.setattr(search, "unavailable_reason",
                            lambda: "reranker model download failed (offline?)")
        _, _, _used, note2 = search._rank("q", raw, mode)
        assert "download failed" in note2, note2
        # 两种模式都要说，但措辞不同：neural 是"你要的用不了"，
        # auto 是"依赖装了却居然没启用"——后者才需要点明这很意外。
        if mode == "auto":
            assert "NOT active despite its deps being installed" in note2, note2
        else:
            assert "neural rerank unavailable" in note2, note2

    def test_no_note_when_the_reranker_actually_ran(self, monkeypatch):
        raw = [RawResult(title="r", url="https://a.test", snippet="s",
                         source="bing", position=1)]
        monkeypatch.setattr(search, "neural_rerank", lambda q, r: [(r[0], 0.9)])
        _, _, used, note = search._rank("q", raw, "auto")
        assert used == "neural" and note == ""


class TestVerticalEnginesLoseTheTieBreakWithoutAReranker:
    """没有相关性模型时的兜底顺序：垂直索引不能靠"答得快"顶到最前。

    sogou_weixin 实测 0.2-0.9s 返回，bing 1.3s、yandex 3.1s —— 兜底排序就是完成
    顺序，于是通用索引还没答完，公众号结果已经占了前几名。有神经重排时不介入
    （判相关性是它的活，覆盖面前验只在没有模型时才成立）。
    """

    @staticmethod
    def _raw(*sources):
        return [RawResult(title=f"r{i}", url=f"https://a{i}.test", snippet="s",
                          source=s[0], sources=tuple(s), position=i + 1)
                for i, s in enumerate(sources)]

    def test_vertical_only_results_go_last(self, monkeypatch):
        monkeypatch.setattr(search, "neural_rerank", lambda q, r: None)
        raw = self._raw(("sogou_weixin",), ("bing",), ("sogou_weixin",), ("yandex",))
        ranked, scores, used, _ = search._rank("q", raw, "auto")
        assert used == "merge"
        assert [r.source for r in ranked] == ["bing", "yandex", "sogou_weixin", "sogou_weixin"]
        assert scores[0] > scores[-1], "分数要按最终顺序给，不能留一个分数更高的头名"

    def test_corroborated_by_a_general_engine_is_not_vertical(self, monkeypatch):
        """同一个 URL 被 bing 也返回过 = 有通用索引佐证，不当垂直结果降级。"""
        monkeypatch.setattr(search, "neural_rerank", lambda q, r: None)
        raw = self._raw(("sogou_weixin", "bing"), ("bing",))
        ranked, _, _, _ = search._rank("q", raw, "auto")
        assert [r.source for r in ranked] == ["sogou_weixin", "bing"]

    def test_neural_order_is_not_touched(self, monkeypatch):
        """重排器在场时这条规则不介入 —— 相关性由模型判，不是覆盖面先验。"""
        raw = self._raw(("sogou_weixin",), ("bing",))
        monkeypatch.setattr(search, "neural_rerank",
                            lambda q, r: [(r[1], 0.9), (r[0], 0.1)])
        ranked, _, used, _ = search._rank("q", raw, "auto")
        assert used == "neural"
        assert [r.source for r in ranked] == ["bing", "sogou_weixin"]


class TestQueryMapNamesAreReal:
    """手工维护的引擎名单会腐烂：这个集合里原先有三个不存在的引擎。

    旧值是 {duckduckgo, brave, mojeek, yahoo} —— mojeek/startpage/google/qwant 从未
    在本项目存在过，而 bing（默认池首位、国内免 VPN）不在集合里，于是它成了唯一被
    改写提问的默认引擎。上面那组测试全都不含 bing，所以抓不到。
    """

    def test_core_set_only_names_existing_engines(self):
        from dhole_mcp.search_engines import DEFAULT_ENGINES
        from dhole_mcp.search_metasearch import _DHOLE_TO_BACKEND, _TEXT_ENGINES
        known = set(DEFAULT_ENGINES) | set(_DHOLE_TO_BACKEND) | set(_TEXT_ENGINES)
        ghost = set(search._CORE_QUERY_ENGINES) - known
        assert not ghost, f"core 集合里有不存在的引擎名: {sorted(ghost)}"

    def test_bing_gets_the_original_query(self):
        intent = search._detect_intent("transformer attention mechanism research")
        assert intent == "research"
        q = "transformer attention mechanism research"
        qm = search._generate_query_map(q, intent, list(se.DEFAULT_ENGINES))
        assert qm, "research 意图应当展开"
        assert qm["bing"] == q, "bing 是默认池首位，不该是被改写提问的那一个"
        assert qm["duckduckgo"] == q and qm["brave"] == q and qm["yahoo"] == q
        assert qm["yandex"] != q, "yandex 才是指定的多样性引擎"

    def test_every_default_engine_is_classified(self):
        """默认池里每个引擎都必须有明确归属，不能靠集合漏项来"恰好"生效。"""
        intent = search._detect_intent("transformer attention mechanism research")
        q = "transformer attention mechanism research"
        qm = search._generate_query_map(q, intent, list(se.DEFAULT_ENGINES))
        assert set(qm) == set(se.DEFAULT_ENGINES)

    def test_sogou_weixin_keeps_the_original_query(self):
        """展开词只有两串英文（paper arxiv... / specifications...），对几乎全中文的
        公众号索引只有反作用 —— 它必须留在核心集合里拿原始 query。"""
        intent = search._detect_intent("transformer attention mechanism research")
        q = "transformer attention mechanism research"
        qm = search._generate_query_map(q, intent, list(se.DEFAULT_ENGINES))
        assert qm, "research 意图应当展开（否则这条测试什么也没测到）"
        assert qm["sogou_weixin"] == q
