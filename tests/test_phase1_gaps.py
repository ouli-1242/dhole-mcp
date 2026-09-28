"""v16.0 Phase 1 的缺口回归（G1 / G4 / G5 / G15-G17）。

每条都指向报告里的原始症状，而不是代码形状 —— 这样将来把修复退回去，红的是
「agent 会看到什么」，不是「某个内部函数长什么样」。
"""

from __future__ import annotations

import pytest

from dhole_mcp.envelope import (
    NEWS_STALE_DAYS,
    LONG_LIVED_STALE_DAYS,
    STALE_DAYS,
    classify_source,
    compute_freshness,
    _stale_days_for,
)
from dhole_mcp.links import MAX_LINKS_CEILING, MAX_LINKS_FLOOR, extract_links
from dhole_mcp.server import MasterFetchServer, ResponseModel

PAGE = "https://example.com/dir/index"


def _link_dense_html(n: int) -> str:
    body = "".join(f'<a href="/p/{i}">Page {i}</a>' for i in range(n))
    return f"<html><body><main>{body}</main></body></html>"


# ─── G1: links 静默截断 ──────────────────────────────────────────────

class TestLinkTruncationIsDisclosed:
    """报告 G1：`links.external` 只到 ~20 条，而 is_truncated 是 false。

    复现是 Wikipedia 的 300+ 语言链接。这里用等价的链接密集页：关键是「截断了
    必须说得出来」，而不是具体截到第几条。
    """

    def test_a_truncated_page_says_so_and_reports_the_true_total(self):
        out = extract_links(_link_dense_html(120), PAGE)

        assert len(out["citations"]) == 30, "默认上限没变"
        assert out["total_found"] == 120, "total_found 必须是截断前的真实条数"
        assert out["is_truncated"] is True

    def test_an_untruncated_page_is_not_flagged(self):
        out = extract_links(_link_dense_html(3), PAGE)

        assert out["total_found"] == 3
        assert out["is_truncated"] is False

    def test_max_links_raises_the_cap(self):
        out = extract_links(_link_dense_html(120), PAGE, max_links=100)

        assert len(out["citations"]) == 100
        assert out["total_found"] == 120
        assert out["is_truncated"] is True

    def test_max_links_asks_for_fewer(self):
        """要 5 条就回 5 条 —— 不是「反正页面有三类，给你 15 条」。"""
        out = extract_links(_link_dense_html(120), PAGE, max_links=5)

        assert len(out["citations"]) == 5
        assert out["is_truncated"] is True

    @pytest.mark.parametrize("requested", [0, -3, 10_000])
    def test_out_of_range_caps_are_clamped_not_honoured(self, requested):
        out = extract_links(_link_dense_html(120), PAGE, max_links=requested)

        total = len(out["citations"]) + len(out["navigation"]) + len(out["external"])
        assert MAX_LINKS_FLOOR <= total <= MAX_LINKS_CEILING * 3

    def test_none_keeps_the_documented_defaults(self):
        """不传 = 老行为，一个字节都不变（默认值不能悄悄漂移）。"""
        assert extract_links(_link_dense_html(120), PAGE) == \
            extract_links(_link_dense_html(120), PAGE, max_links=None)


# ─── G4: 5xx 不再升级隐身浏览器 ──────────────────────────────────────

def _result(**kwargs):
    defaults = dict(
        status=500, content=[], url="https://example.com/boom",
        fetcher_used="http", content_type="text/html", total_size_bytes=0,
        extracted_type="markdown",
    )
    defaults.update(kwargs)
    return ResponseModel(**defaults)


class TestFiveHundredDoesNotBuyABrowserRun:
    """报告 G4：/status/500 白升一级 stealthy 耗 6.9s，再把同一个服务器错误
    报成 Cloudflare/DataDome。浏览器拿不到 5xx 的第二种答案。
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [500, 502])
    async def test_5xx_never_reaches_the_browser_tier(self, monkeypatch, status):
        import dhole_mcp.fetcher as fetcher

        async def http_5xx(*a, **k):
            return _result(status=status, fetcher_used="http")

        async def browser(*a, **k):
            raise AssertionError(f"{status} 不该升级浏览器：同一个错误页要等 30-40s")

        monkeypatch.setattr(fetcher, "tcp_preflight", lambda url, timeout=2.0: (True, ""))
        server = MasterFetchServer()
        server.get = http_5xx
        server.stealthy_fetch = browser
        out = await server._auto_escalate(
            "https://example.com/boom", "markdown", None, True, True, 0, 0,
            True, False, 0, None, 20000, False, False, False, False,
            None, None, None,
        )

        assert out.fetcher_used == "http", "结果仍是 HTTP 层的，说明没走浏览器"
        assert out.status == status

    @pytest.mark.asyncio
    async def test_503_still_escalates(self, monkeypatch):
        """503 常是 bot-wall 的形态（Cloudflare 排队页），必须保留升级。"""
        import dhole_mcp.fetcher as fetcher

        seen: list[str] = []

        async def http_503(*a, **k):
            return _result(status=503, fetcher_used="http")

        async def browser(*a, **k):
            seen.append("browser")
            return _result(status=200, content=["rendered body"],
                           fetcher_used="stealthy")

        monkeypatch.setattr(fetcher, "tcp_preflight", lambda url, timeout=2.0: (True, ""))
        server = MasterFetchServer()
        server.get = http_503
        server.stealthy_fetch = browser
        out = await server._auto_escalate(
            "https://example.com/boom", "markdown", None, True, True, 0, 0,
            True, False, 0, None, 20000, False, False, False, False,
            None, None, None,
        )

        assert seen == ["browser"], "503 的升级路径被一起收走了"
        assert out.status == 200


# ─── G5: 重排器相关性下限 ────────────────────────────────────────────

class _FakeReranker:
    """Deterministic scorer: relevance is the index the caller encodes in title."""

    def __init__(self, raw_scores):
        self.raw_scores = raw_scores

    def score(self, query, docs):
        return list(self.raw_scores[:len(docs)])


class _Row:
    def __init__(self, i):
        self.title = f"row {i}"
        self.snippet = "s"
        self.position = i


@pytest.fixture
def reranker(monkeypatch):
    from dhole_mcp import reranker as rr

    def _install(raw_scores):
        monkeypatch.setattr(rr, "_reranker", _FakeReranker(raw_scores))
        return rr
    return _install


class TestRelevanceFloor:

    def test_no_reranker_returns_none_rather_than_filtering_silently(self, monkeypatch):
        """None 和 [] 是两件事：None = 没有相关性信息，[] = 有信息且全不合格。

        混为一谈会让「模型判全不相关」被读成「重排不可用，退回共识顺序」。
        """
        from dhole_mcp import reranker as rr

        monkeypatch.setattr(rr, "_reranker", None)
        assert rr.rerank("q", [_Row(0)], min_relevance=0.5) is None

    def test_zero_floor_keeps_everything(self, reranker):
        rr = reranker([0.9, 0.5, 0.1, 0.05])

        pairs = rr.rerank("q", [_Row(i) for i in range(4)], min_relevance=0.0)
        assert len(pairs) == 4
        assert pairs[0][1] == 1.0 and pairs[-1][1] == 0.0, "归一化契约没变（top=1, worst=0）"

    def test_floor_drops_the_tail(self, reranker):
        rr = reranker([0.9, 0.5, 0.1, 0.05])

        pairs = rr.rerank("q", [_Row(i) for i in range(4)], min_relevance=0.3)
        assert len(pairs) == 2
        assert [r.title for r, _ in pairs] == ["row 0", "row 1"]

    def test_a_floor_of_1_0_still_keeps_the_best_result(self, reranker):
        """「全滤空」在公开 API 下不可达 —— 这条把它钉住，别当成实现缺陷去"修"。

        分数是 min-max 归一化过的，top 的定义就是 1.0，而 min_relevance 被钳在
        0..1，所以任何合法下限都至少留下一条。rerank() 里那个返回 [] 的分支是给
        「将来有别的量纲」准备的防御，不是在当前量纲下能触发的路径。
        """
        rr = reranker([0.9, 0.5, 0.1, 0.05])

        pairs = rr.rerank("q", [_Row(i) for i in range(4)], min_relevance=1.0)
        assert len(pairs) == 1 and pairs[0][0].title == "row 0"

    def test_the_floor_cannot_reject_a_uniformly_bad_set(self, reranker):
        """把已知边界钉住，免得将来误以为 min_relevance 能判断「这轮全是垃圾」。

        归一化把 top 定义成 1.0，所以整组原始分相同时全部归一为 1.0，任何 <1.0
        的下限都滤不掉任何东西。这是归一化量纲的结构性限制，不是 bug —— 要判断
        「整组都不相关」需要看原始分。
        """
        rr = reranker([0.0, 0.0, 0.0])

        pairs = rr.rerank("q", [_Row(i) for i in range(3)], min_relevance=0.9)
        assert len(pairs) == 3, "边界变了：如果这条红了，说明归一化语义被改过"


class TestSearchReportsAFullyFilteredFloor:
    """接线：阈值把候选全滤掉时，响应必须说明原因，而不是静默返回空列表。

    这条走的是**接线**：假重排器直接返回 []，绕过 rerank() 的归一化量纲限制
    （见 TestRelevanceFloor.test_a_floor_of_1_0_still_keeps_the_best_result）。
    验的是「真收到空列表时 dhole 会不会说出来」。
    """

    @pytest.mark.asyncio
    async def test_error_names_the_floor_and_the_candidate_count(self, monkeypatch):
        from dhole_mcp import search as S
        from dhole_mcp.search_engines import EngineReport, RawResult

        async def fake_multi_search(query, max_results, **kwargs):
            rows = [RawResult(title=f"r{i}", url=f"https://e{i}.example/",
                              snippet="s", source="bing", position=i + 1)
                    for i in range(4)]
            return rows, [EngineReport(name="bing", ok=True)]

        async def no_cache(*a, **k):
            return None

        async def no_set(*a, **k):
            return None

        async def fake_ensure():
            return None

        monkeypatch.setattr(S, "multi_search", fake_multi_search)
        monkeypatch.setattr(S, "get_cached", no_cache)
        monkeypatch.setattr(S, "set_cached", no_set)
        monkeypatch.setattr(S, "ensure_reranker", fake_ensure)
        # A reranker that rejects everything.
        monkeypatch.setattr(S, "neural_rerank",
                            lambda q, r, **kw: [])

        out = await S.smart_search(None, "dns resolution", 6, min_relevance=0.5)

        assert out.results == []
        assert "min_relevance=0.5" in out.error, out.error
        assert "Engines delivered 4 results" in out.error, out.error

    @pytest.mark.asyncio
    async def test_a_floor_does_not_change_the_cache_key(self, monkeypatch):
        """默认 0.0 必须落回原来的键，否则所有已缓存的结果一次性作废。"""
        from dhole_mcp import search as S

        keys: list[str] = []

        async def spy_get(query, cache_type, css_selector, **kwargs):
            keys.append(cache_type)
            return None

        async def no_set(*a, **k):
            return None

        async def fake_multi_search(query, max_results, **kwargs):
            return [], []

        monkeypatch.setattr(S, "get_cached", spy_get)
        monkeypatch.setattr(S, "set_cached", no_set)
        monkeypatch.setattr(S, "multi_search", fake_multi_search)

        await S.smart_search(None, "plain query", 6)
        assert keys and "minrel" not in keys[0]

        keys.clear()
        await S.smart_search(None, "plain query", 6, min_relevance=0.4)
        assert keys and "minrel=0.4" in keys[0], \
            "非默认阈值必须进缓存键，否则 0.4 的调用会被喂回未加阈值的旧结果"

    @pytest.mark.asyncio
    async def test_an_out_of_range_floor_is_clamped_and_reported(self, monkeypatch):
        """传 5 被钳到 1.0 却不说，读起来就像「结果全都不相关」。"""
        from dhole_mcp import search as S
        from dhole_mcp.search_engines import EngineReport, RawResult

        async def fake_multi_search(query, max_results, **kwargs):
            rows = [RawResult(title=f"r{i}", url=f"https://e{i}.example/",
                              snippet="s", source="bing", position=i + 1)
                    for i in range(3)]
            return rows, [EngineReport(name="bing", ok=True)]

        async def no_cache(*a, **k):
            return None

        async def no_set(*a, **k):
            return None

        async def fake_ensure():
            return None

        monkeypatch.setattr(S, "multi_search", fake_multi_search)
        monkeypatch.setattr(S, "get_cached", no_cache)
        monkeypatch.setattr(S, "set_cached", no_set)
        monkeypatch.setattr(S, "ensure_reranker", fake_ensure)
        monkeypatch.setattr(S, "neural_rerank",
                            lambda q, r, **kw: [(x, 1.0) for x in r])

        out = await S.smart_search(None, "dns resolution", 6, min_relevance=5.0)
        assert "min_relevance=5.0 is outside 0-1" in out.fetch_hint, out.fetch_hint


# ─── G15 / G17: envelope 精度 ────────────────────────────────────────

class TestDomainAwareStaleness:
    """报告 G15：wiki/新闻页的 is_stale 系统性误报。"""

    def test_news_goes_stale_in_weeks(self):
        assert _stale_days_for("https://www.reuters.com/business/x") == NEWS_STALE_DAYS

    @pytest.mark.parametrize("url", [
        "https://en.wikipedia.org/wiki/HTTP",
        "https://docs.python.org/3/library/asyncio.html",
        "https://github.com/psf/requests",
        "https://stackoverflow.com/q/1",
        "https://arxiv.org/abs/1706.03762",
    ])
    def test_long_lived_sources_get_the_long_horizon(self, url):
        assert _stale_days_for(url) == LONG_LIVED_STALE_DAYS

    def test_unclassified_and_url_less_callers_keep_365(self):
        assert _stale_days_for("https://random-site.example.com/x") == STALE_DAYS
        assert _stale_days_for("") == STALE_DAYS

    def test_the_same_age_can_be_stale_on_news_and_fine_on_reference(self):
        """这就是「按域名」的意义：100 天前对新闻是旧的，对参考文档不是。"""
        old = {"published_time": "2026-06-01"}
        fetched = "2026-09-26"

        assert compute_freshness(old, fetched, "https://www.reuters.com/a")[1] is True
        assert compute_freshness(old, fetched, "https://en.wikipedia.org/wiki/A")[1] is False

    def test_omitting_the_url_keeps_the_old_365_semantics(self):
        """老调用点/老测试不该因为这次改动而改判。"""
        meta = {"published_time": "2025-01-01"}

        assert compute_freshness(meta, "2026-01-02") == (366, True)
        assert compute_freshness(meta, "2025-06-01") == (151, False)


class TestContinuouslyEditedPagesAreNotJudgedByCreationDate:
    """报告 G15 的实测形态：Wikipedia 报 content_age_days=9098 / is_stale=true，
    依据是 2001 年的词条创建时间 —— 而那一页一直在被编辑。
    """

    def test_wiki_creation_date_is_not_treated_as_content_age(self):
        meta = {"published_time": "2001-01-01"}

        age, stale = compute_freshness(meta, "2026-09-26", "https://en.wikipedia.org/wiki/HTTP")
        assert age is None, "词条创建时间不是内容时效信号"
        assert stale is False

    def test_a_real_modification_date_is_still_used(self):
        """有 lastmod 时照常判定 —— 禁用的是「只凭创建时间推断」。"""
        meta = {"published_time": "2001-01-01", "modified_time": "2020-01-01"}

        age, stale = compute_freshness(meta, "2026-09-26", "https://en.wikipedia.org/wiki/HTTP")
        assert age is not None and stale is True

    def test_an_immutable_reference_page_keeps_its_age(self):
        """RFC 是不可变的：1998 年发布就是 1998 年发布，这里不该被一起豁免。"""
        meta = {"published_time": "1998-04-01"}

        age, _ = compute_freshness(meta, "2026-09-26", "https://www.rfc-editor.org/rfc/rfc2324")
        assert age is not None

    def test_a_news_page_is_never_exempt(self):
        meta = {"published_time": "2001-01-01"}

        age, stale = compute_freshness(meta, "2026-09-26", "https://www.reuters.com/a")
        assert age is not None and stale is True


class TestSourceTypeStopsSayingUnknown:
    """报告 G17：iana.org / wikipedia.org / rfc-editor.org 全是 unknown，
    这些字段永远不亮就等于噪声。
    """

    @pytest.mark.parametrize("url", [
        "https://en.wikipedia.org/wiki/HTTP",
        "https://www.iana.org/assignments/media-types/",
        "https://www.rfc-editor.org/rfc/rfc2324",
        "https://www.w3.org/TR/html52/",
        "https://developer.mozilla.org/en-US/docs/Web/HTTP",
    ])
    def test_reference_material_is_classified(self, url):
        st, off = classify_source(url)
        assert st in ("reference", "docs-site"), f"{url} -> {st}"

    @pytest.mark.parametrize("url", [
        "https://www.iana.org/assignments/media-types/",
        "https://www.rfc-editor.org/rfc/rfc2324",
        "https://www.w3.org/TR/html52/",
        "https://www.pypi.org/project/requests/",
    ])
    def test_the_bodies_that_run_a_namespace_are_official(self, url):
        """G17 复测问 iana.org / rfc-editor.org 为何不是 official。它们和 gov/edu
        满足同一条判据：这个名字第三方拿不到 —— 运营方就是命名空间的管辖机构本身。
        """
        assert classify_source(url)[1] is True

    @pytest.mark.parametrize("url", [
        "https://en.wikipedia.org/wiki/HTTP",
        "https://developer.mozilla.org/en-US/docs/Web/HTTP",
        "https://medium.com/@someone/notes",
    ])
    def test_readable_and_subdomain_shaped_stay_not_official(self, url):
        """反过来，判据不能被「这页看着权威」满足：百科内容是社区编辑的，
        docs./developer. 只是有人把子域取名成 docs。
        """
        assert classify_source(url)[1] is False

    @pytest.mark.parametrize("url", [
        "https://arxiv.org/abs/1706.03762",
        "https://www.nature.com/articles/s41586-021-03819-2",
        "https://www.science.org/doi/10.1126/science.abj0016",
    ])
    def test_journals_are_papers_not_news(self, url):
        """nature/science 原先挂在 _NEWS_DOMAINS 下 —— 配上 30 天的新闻阈值，
        任何一个月前的论文都会被判过期。"""
        st, _ = classify_source(url)
        assert st == "paper"

    def test_gov_and_edu_stay_official(self):
        assert classify_source("https://www.nasa.gov/mission") == ("gov", True)
        assert classify_source("https://www.mit.edu/research") == ("edu", True)

    def test_a_lookalike_domain_cannot_buy_authority(self):
        assert classify_source("https://foo.gov.attacker.com/a") == ("unknown", False)
        assert classify_source("https://wikipedia.org.attacker.com/a") == ("unknown", False)

    def test_new_wire_pages_still_classify_as_news(self):
        assert classify_source("https://www.bbc.com/news/world")[0] == "news"
        assert classify_source("https://www.wsj.com/tech/x")[0] == "news"


class TestEnvelopeWiring:
    """接线：_apply_envelope 必须把 URL 交给 compute_freshness，否则按域名的
    阈值和 wiki 豁免都只是「写在那里没人用」。
    """

    def test_apply_envelope_uses_the_domain_horizon(self):
        from dhole_mcp.server import _apply_envelope

        news = ResponseModel(url="https://www.reuters.com/business/x", status=200,
                             content=["b"], metadata={"published_time": "2026-06-01"},
                             fetched_at="2026-09-26")
        wiki = ResponseModel(url="https://en.wikipedia.org/wiki/HTTP", status=200,
                             content=["b"], metadata={"published_time": "2001-01-01"},
                             fetched_at="2026-09-26")

        _apply_envelope(news)
        _apply_envelope(wiki)

        assert news.is_stale is True
        assert wiki.is_stale is False and wiki.content_age_days is None
