"""v16.0 Phase 1/2 缺口与 v17.0 修复计划的回归守卫

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import pytest
from dhole_mcp.envelope import NEWS_STALE_DAYS, LONG_LIVED_STALE_DAYS, STALE_DAYS, classify_source, compute_freshness, _stale_days_for
from dhole_mcp.links import MAX_LINKS_CEILING, MAX_LINKS_FLOOR, extract_links
from dhole_mcp.server import MasterFetchServer
from dhole_mcp import crawl as crawl_mod
from dhole_mcp import identity
import asyncio
import time
from pathlib import Path
from dhole_mcp import sitemap as sm
from dhole_mcp.actions import _locator_or_raise, _screenshot_error_message
from dhole_mcp.server import BulkResponseModel, _detect_content_issue, _is_js_shell
from dhole_mcp.wire import _wire_json
from dhole_mcp import audit, env_registry
from dhole_mcp.models import ResponseModel


# ============================================================================
# 来源：test_phase1_gaps.py
# ============================================================================
# v16.0 Phase 1 的缺口回归（G1 / G4 / G5 / G15-G17）。

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


async def _escalate(server, url="https://example.com/boom", **over):
    """按名字驱动升级链，网络在 self.get 那一层被换掉。

    B5-13d 从这条链上删掉了渲染时机那两位（时机不再是参数：那 5 秒现在是浏览器
    层认出壳页才付的，证人搬去 tests/test_render_settle_b5.py），照位置传法的调用
    于是把"少了两位"读成"后面那些各自前移一位"，红成一个 TypeError。

    `_ensure_auto_session` 也必须换掉：升级链在调用 `stealthy_fetch` 之前要先拿到
    会话 id，而那颗函数真的会 launch 一台浏览器。这一刀读的是阶梯往哪一级走，不是
    浏览器 —— 挂具没换它的时候，这两条用例每次跑都在套件里起一个真 chromium（批次 1
    的 conftest 启动闸把它抓了出来）。
    """
    async def _no_session(**kw):
        return "auto-under-test"

    server._ensure_auto_session = _no_session
    kwargs = dict(
        extraction_type="markdown", css_selector=None, cache_ttl=0, offset=0,
        headless=True, real_chrome=False, proxy=None, timeout=20000,
        solve_cloudflare=False, block_webrtc=False, hide_canvas=False,
        extra_headers=None, useragent=None, cookies=None,
    )
    kwargs.update(over)
    return await server._auto_escalate(url, **kwargs)


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
        out = await _escalate(server)

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
        out = await _escalate(server)

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

        out = await S.web_search(None, "dns resolution", 6, min_relevance=0.5)

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

        await S.web_search(None, "plain query", 6)
        assert keys and "minrel" not in keys[0]

        keys.clear()
        await S.web_search(None, "plain query", 6, min_relevance=0.4)
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

        out = await S.web_search(None, "dns resolution", 6, min_relevance=5.0)
        assert "min_relevance=5.0 is outside 0-1" in out.summary, out.summary


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


# ============================================================================
# 来源：test_phase2_gaps.py
# ============================================================================
# v16.0 Phase 2 的缺口回归（G2 / G18 / G21 / G9 / G12）。

def _page_html(n_links: int = 30) -> str:
    return "".join(f'<a href="/p/{i}">P{i}</a>' for i in range(n_links))


class _CountingServer:
    """Fake server that records every web_read it is asked to perform."""

    def __init__(self, body: str | None = None, status: int = 200,
                 content_ok: bool = True):
        self.calls: list[str] = []
        self._body = body if body is not None else _page_html()
        self._status = status
        self._content_ok = content_ok

    async def web_read(self, url, **kwargs):
        self.calls.append(url)
        return ResponseModel(url=url, status=self._status, content=[self._body],
                             content_type="text/html", fetcher_used="http",
                             content_ok=self._content_ok)


# ─── G2: discover_only 的代价必须可见且可控 ──────────────────────────

class TestDiscoverOnlyIsHonestAboutItsCost:
    """报告 G2：文档说 "URL map only, no page content"，实测打了 10 个完整请求。

    map 模式要展开链接就必须抓页面 —— 那不是 bug。bug 是**代价不可见**，
    以及 summary 谎报 "10 content_ok"（那 10 页从未抽取正文）。
    """

    @pytest.mark.asyncio
    async def test_the_fetch_count_is_exactly_pages_crawled(self):
        server = _CountingServer()

        out = await crawl_mod.web_crawl(
            server, "https://example.com/", max_pages=10, max_depth=2, discover_only=True)

        assert len(server.calls) == 10, "请求数必须等于 pages_crawled"
        assert out.pages_crawled == 10
        assert out.pages_discovered > out.pages_crawled, "BFS 应该展开出更多 URL"

    @pytest.mark.asyncio
    async def test_map_mode_returns_no_content_and_claims_none(self):
        """`content_ok=true` 曾经是 map 模式的谎：字段定义是「取到并抽取了正文」。"""
        out = await crawl_mod.web_crawl(
            _CountingServer(), "https://example.com/", max_pages=3, max_depth=1,
            discover_only=True)

        assert out.pages, "map 模式仍应返回 URL 列表"
        for p in out.pages:
            assert p.content == []
            assert p.content_chars == 0
            assert p.content_ok is False, f"{p.url} 没有正文却说 content_ok"
            assert p.page_type == "discover_only"

    @pytest.mark.asyncio
    async def test_the_summary_states_the_fetch_count_and_not_a_content_claim(self):
        out = await crawl_mod.web_crawl(
            _CountingServer(), "https://example.com/", max_pages=4, max_depth=1,
            discover_only=True)

        assert "4 page fetch(es)" in out.summary, out.summary
        assert "no content extracted" in out.summary, out.summary
        assert "content_ok" not in out.summary, "map 模式不该报 content_ok"

    @pytest.mark.asyncio
    async def test_max_pages_1_is_the_one_request_map(self):
        """一次请求拿到起始页的链接图 —— 这是「只想发现链接」的正确姿势。"""
        server = _CountingServer()

        out = await crawl_mod.web_crawl(
            server, "https://example.com/", max_pages=1, max_depth=1, discover_only=True)

        assert len(server.calls) == 1
        assert out.pages_discovered == 31, "起始页的 30 条链接 + 自身"
        assert out.pages_crawled == 1

    @pytest.mark.asyncio
    async def test_next_action_names_the_cost_and_the_one_request_escapes(self):
        """代价披露要落在**可达**的分支里。

        map 模式下 `discovered > crawled` 必然意味着被 max_pages 截断，所以真正会
        被读到的是「stopped early」那条；专门的 map 分支只在跨域重定向的边缘情形
        可达。这条测试钉住的是前者。
        """
        out = await crawl_mod.web_crawl(
            _CountingServer(), "https://example.com/", max_pages=3, max_depth=1,
            discover_only=True)

        assert "stopped early" in out.next_action, out.next_action
        assert "one request per page" in out.next_action, out.next_action
        assert "max_pages=1" in out.next_action
        assert "sitemap=true" in out.next_action

    @pytest.mark.asyncio
    async def test_map_mode_is_not_diagnosed_as_an_unreachable_site(self):
        """`ok == 0` 在 map 模式是预期形态，不该被读成「站点可能挂了」。

        这条复现的是一条真实的错位：content_ok 现在诚实地为 False，若不做区分，
        「全部页面都没有正文」的诊断会在一次成功的 map 调用上触发。
        """
        out = await crawl_mod.web_crawl(
            _CountingServer(), "https://example.com/", max_pages=1, max_depth=0,
            discover_only=True)

        assert "may be unreachable" not in out.next_action, out.next_action
        assert "returned no content" not in out.next_action, out.next_action

    @pytest.mark.asyncio
    async def test_content_mode_still_reports_content_ok(self):
        """回归守卫：非 map 模式的 content_ok 语义没被这次改动带偏。"""
        para = "This is a real article paragraph about Python packaging. " * 12
        body = f"<html><body><article><h1>T</h1><p>{para}</p><p>{para}</p></article></body></html>"
        server = _CountingServer(body=body)

        out = await crawl_mod.web_crawl(
            server, "https://example.com/", max_pages=1, max_depth=0)

        assert out.pages[0].content_ok is True
        assert out.pages[0].content_chars > 0


# ─── G12: css_selector 对非 HTML 不能静默忽略 ────────────────────────

def _xml_response() -> ResponseModel:
    return ResponseModel(
        url="https://example.com/feed", status=200,
        content=["<rss><channel><item><title>Hello</title></item></channel></rss>"],
        content_type="application/xml", fetcher_used="http",
    )


class TestCssSelectorOnNonHtml:
    """报告 G12：`httpbin/xml` + `css_selector:'h1'` 返回整段 XML，选择器被忽略且无提示。

    修法是**说清楚**而不是报错：XML/JSON 里本来就没有 h1，整份文档可能正是调用方
    要的东西，把 content_ok 翻成 False 会让它不敢用可用的内容。
    """

    def test_a_selector_on_non_html_is_reported_in_the_summary(self, monkeypatch):
        from dhole_mcp import server as server_mod

        monkeypatch.setattr(server_mod, "_CSS_SELECTOR",
                            server_mod._CSS_SELECTOR)
        token = server_mod._CSS_SELECTOR.set("h1")
        try:
            out = server_mod._with_agent_hints(_xml_response())
        finally:
            server_mod._CSS_SELECTOR.reset(token)

        assert "css_selector 'h1' was NOT applied" in out.summary, out.summary
        assert "application/xml" in out.summary, out.summary
        assert out.content_ok is True, "文档本身可用，不该被标成不可信"

    @pytest.mark.parametrize("ctype", ["text/html", "text/html; charset=utf-8"])
    def test_html_responses_are_not_flagged(self, ctype):
        from dhole_mcp import server as server_mod

        token = server_mod._CSS_SELECTOR.set("h1")
        try:
            result = _xml_response()
            result.content_type = ctype
            out = server_mod._with_agent_hints(result)
        finally:
            server_mod._CSS_SELECTOR.reset(token)

        assert "was NOT applied" not in out.summary, out.summary

    def test_no_selector_means_no_note(self):
        from dhole_mcp import server as server_mod

        out = server_mod._with_agent_hints(_xml_response())
        assert "css_selector" not in out.summary

    def test_an_unknown_content_type_is_not_accused(self):
        """拿不到 content-type 就不能断言选择器失效 —— 未知不等于非 HTML。"""
        from dhole_mcp import server as server_mod

        token = server_mod._CSS_SELECTOR.set("h1")
        try:
            result = _xml_response()
            result.content_type = ""
            out = server_mod._with_agent_hints(result)
        finally:
            server_mod._CSS_SELECTOR.reset(token)

        assert "was NOT applied" not in out.summary

    @pytest.mark.asyncio
    async def test_the_selector_reaches_the_summary_through_a_real_fetch(self, monkeypatch):
        """接线：装饰器必须把 css_selector 放进上下文，否则这条提示永远不会出现。"""
        import dhole_mcp.fetcher as fetcher
        from dhole_mcp.server import MasterFetchServer

        async def fake_get(*a, **k):
            return ResponseModel(
                url="https://example.com/feed", status=200,
                content=["<rss><channel><item><title>Hello</title></item></channel></rss>"],
                content_type="application/xml", fetcher_used="http", content_ok=True,
            )

        monkeypatch.setattr(fetcher, "tcp_preflight", lambda url, timeout=2.0: (True, ""))
        server = MasterFetchServer()
        server.get = fake_get

        out = await server.web_read("https://example.com/feed",
                                       css_selector="h1", cache_ttl=0)

        assert "css_selector 'h1' was NOT applied" in out.summary, out.summary


# ─── G9: 私网 / 本机 allowlist（默认关闭，点名放行）─────────────────


def _verdict(url: str) -> str:
    from dhole_mcp.security import SecurityError, validate_url

    try:
        validate_url(url)
        return "allowed"
    except SecurityError:
        return "blocked"


@pytest.fixture
def allow_private():
    """Push a per-call allowlist and reset it when the test ends."""
    from dhole_mcp import security as sec

    tokens = []

    def _set(value):
        tokens.append(sec.set_allow_private(value))

    yield _set
    for token in reversed(tokens):
        sec._ALLOW_PRIVATE.reset(token)


class TestSsrfGuardIsStillTheDefault:

    @pytest.mark.parametrize("url", [
        "http://127.0.0.1:8899/secret.txt",
        "http://192.168.1.1/",
        "http://10.0.0.1/api",
        "http://169.254.169.254/latest/meta-data/",
        "http://localhost:3000/",
        "http://0177.0.0.1/",
    ])
    def test_private_targets_are_refused_without_an_allowlist(self, url):
        assert _verdict(url) == "blocked"

    @pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://x/y", "data:text/html,x"])
    def test_blocked_schemes_stay_blocked(self, allow_private, url):
        from dhole_mcp.security import SecurityError, validate_url

        allow_private(True)
        with pytest.raises(SecurityError) as e:
            validate_url(url)
        assert "scheme" in str(e.value).lower()


class TestAllowPrivateIsNarrow:

    def test_true_opens_loopback_only(self, allow_private):
        """`true` 不是「开整个局域网」—— 它有明确边界（只有本机）。"""
        allow_private(True)

        assert _verdict("http://127.0.0.1:8899/secret.txt") == "allowed"
        assert _verdict("http://localhost:3000/") == "allowed"
        assert _verdict("http://127.0.0.2:999/") == "allowed"      # 整段 127.0.0.0/8
        assert _verdict("http://[::1]:8080/") == "allowed"
        # 局域网与元数据不在 `true` 的范围里
        assert _verdict("http://192.168.1.1/") == "blocked"
        assert _verdict("http://10.0.0.1/api") == "blocked"

    def test_a_named_list_those_hosts_only(self, allow_private):
        # 只用默认被拦的**内网地址**断言：像 my-service.local 这样的主机名在
        # conftest 的假 DNS 下解析到公网 IP，默认就是放行的，拿它断言等于什么都没测。
        allow_private(["192.168.1.50", "10.0.0.5"])

        assert _verdict("http://192.168.1.50/") == "allowed"
        assert _verdict("http://10.0.0.5:8080/api") == "allowed"
        assert _verdict("http://192.168.1.51/") == "blocked"
        assert _verdict("http://127.0.0.1:8899/") == "blocked"

    @pytest.mark.parametrize("value", [True, ["169.254.169.254"], "169.254.169.254"])
    def test_cloud_metadata_is_never_allowed(self, allow_private, value):
        """SSRF 的第一目标。点名也不放行 —— 那不属于「我的 dev 服务」这类诉求。"""
        allow_private(value)

        assert _verdict("http://169.254.169.254/latest/meta-data/") == "blocked"
        assert _verdict("http://metadata.google.internal/") == "blocked"

    def test_a_named_host_is_matched_literally(self, allow_private):
        """字面匹配：allowlist 写了 127.0.0.1 不代表八进制写法也被放行。

        （`true` 走的是另一条路 —— 按解析后的地址判定 127.0.0.0/8 —— 见
        test_true_opens_loopback_only 里的 127.0.0.2。）
        """
        allow_private(["127.0.0.1"])

        assert _verdict("http://127.0.0.1:8899/") == "allowed"
        assert _verdict("http://0177.0.0.1/") == "blocked"

    def test_dns_rebinding_services_have_no_escape_hatch(self, allow_private):
        """这类服务把任意域名解析到任意 IP，放行它等于把内网判定交给外部解析器。"""
        allow_private(["foo.1u.ms", "127.0.0.1.nip.io"])

        assert _verdict("http://foo.1u.ms/") == "blocked"
        assert _verdict("http://127.0.0.1.nip.io/") == "blocked"

    def test_the_env_var_works_process_wide(self, monkeypatch):
        monkeypatch.setenv("DHOLE_ALLOW_PRIVATE_HOSTS", "10.0.0.5, 192.168.1.50")

        assert _verdict("http://10.0.0.5/") == "allowed"
        assert _verdict("http://192.168.1.50/") == "allowed"
        assert _verdict("http://10.0.0.6/") == "blocked"

    def test_the_allowlist_can_be_reset_so_it_does_not_leak(self):
        """一次调用点名放行的主机，不能变成下一次调用的默认。"""
        from dhole_mcp import security as sec

        token = sec.set_allow_private(["192.168.1.50"])
        try:
            assert _verdict("http://192.168.1.50/") == "allowed"
        finally:
            sec._ALLOW_PRIVATE.reset(token)
        assert _verdict("http://192.168.1.50/") == "blocked"

    @pytest.mark.asyncio
    async def test_the_option_reaches_the_guard_through_a_real_call(self, monkeypatch):
        """接线：装饰器必须把 options.allow_private 送进 guard，否则这个开关不存在。"""
        import dhole_mcp.fetcher as fetcher
        from dhole_mcp.server import MasterFetchServer, ResponseModel

        hit: list[str] = []

        async def fake_get(url, *a, **k):
            hit.append(url)
            return ResponseModel(url=url, status=200, content=["dev server body"],
                                 content_type="text/plain", fetcher_used="http",
                                 content_ok=True)

        monkeypatch.setattr(fetcher, "tcp_preflight", lambda url, t=2.0: (True, ""))
        server = MasterFetchServer()
        server.get = fake_get

        # 17.0 §3-17：allow_private 不再是逐次传的那一颗，它住在 identity.db 里，
        # web_read 入口读回来并把 guard 的 scope 开在校验与抓取那两步上。所以「接线」
        # 量的是「存进去的默认真的开到闸门吗」，而不是「参数送到 guard 了吗」。
        target = "http://127.0.0.1:8899/secret.txt"
        identity.set_value("allow_private", True)
        out = await server.web_read(target, cache_ttl=0, timeout=6000)
        assert hit == [target], out.error
        assert out.content == ["dev server body"]

    @pytest.mark.asyncio
    async def test_without_the_option_the_same_url_is_refused(self, monkeypatch):
        """回归守卫：默认仍然是拒，且拒在任何请求之前。"""
        from dhole_mcp.server import MasterFetchServer

        async def _never(*a, **k):
            raise AssertionError("被 SSRF 守卫拒绝的 URL 不该发出请求")

        server = MasterFetchServer()
        server.get = _never
        server.stealthy_fetch = _never

        out = await server.web_read("http://127.0.0.1:8899/secret.txt",
                                       cache_ttl=0, timeout=6000)
        assert "internal/private" in out.error, out.error
        assert out.content == []

    def test_an_unusable_value_is_reported_not_silently_ignored(self):
        """安全相关的选项被静默忽略是最糟的形态 —— 必须说。"""
        from dhole_mcp.security import parse_allow_private

        # 无法理解的值解析为空集，由调用方写进 summary
        assert parse_allow_private(42) == (frozenset(), False)
        assert parse_allow_private(None) == (frozenset(), False)
        assert parse_allow_private("true")[1] is True
        assert parse_allow_private("a.local,b.local")[0] == {"a.local", "b.local"}


# ─── G18: 错误响应的 body ────────────────────────────────────────────
#
# 报告原文「4xx/5xx 的 body 被整体丢弃」**证伪**：httpbin 的 /status/* 本来就是空
# body，而服务器真回了 body 时 dhole 是保留的（实测 404 + JSON body -> content 保留、
# error=http_error_404）。真正存在的缺口是另一件事：一旦 archive.org 有该 URL 的
# 快照，活的响应体会被 2019 年的页面**顶替**掉，且 error 被清空 —— 服务器刚给出的
# 答案消失且没有痕迹。这一组测试钉的是后者。


def _error_result(status: int, body: str, ctype: str) -> ResponseModel:
    return ResponseModel(url="https://api.example.com/items/42", status=status,
                         content=[body] if body else [], content_type=ctype,
                         fetcher_used="http", total_size_bytes=len(body))


class TestErrorBodyIsKept:

    def test_a_4xx_body_survives_the_pipeline(self):
        """回归守卫：dhole 从来不丢 error body（报告 G18 的字面结论是错的）。"""
        from dhole_mcp.server import _with_agent_hints

        out = _with_agent_hints(_error_result(404, '{"error":"not found"}', "application/json"))
        assert out.content == ['{"error":"not found"}']
        assert out.content_ok is False

    def test_an_api_error_body_is_not_replaced_by_a_snapshot(self):
        from dhole_mcp.server import _should_try_archive

        assert _should_try_archive(
            _error_result(404, '{"error":"not found","hint":"use /v2/items"}', "application/json")
        ) is False
        assert _should_try_archive(
            _error_result(500, "<error><code>42</code></error>", "application/xml")
        ) is False

    def test_an_html_error_page_still_falls_back(self):
        """对照：页没了的时候快照常常比错误页有用，那条回退不能一起收走。"""
        from dhole_mcp.server import _should_try_archive

        assert _should_try_archive(
            _error_result(404, "<html><body>404 Not Found</body></html>", "text/html")
        ) is True

    def test_an_empty_body_still_falls_back(self):
        """没有 body 时快照是唯一的内容 —— 该回退还是得回退。"""
        from dhole_mcp.server import _should_try_archive

        assert _should_try_archive(_error_result(404, "", "application/json")) is True

    @pytest.mark.asyncio
    async def test_the_live_api_error_reaches_the_caller_even_with_a_snapshot(self, monkeypatch):
        import dhole_mcp.fetcher as fetcher
        from dhole_mcp.server import MasterFetchServer

        live = '{"error":"item 42 not found","hint":"use /v2/items"}'

        async def fake_get(url, *a, **k):
            return ResponseModel(url=url, status=404, content=[live],
                                 content_type="application/json", fetcher_used="http",
                                 total_size_bytes=len(live), content_ok=True)

        async def fake_archive(*a, **k):
            return ResponseModel(url="https://api.example.com/items/42", status=200,
                                 content=["wayback snapshot from 2019"],
                                 content_type="text/html", fetcher_used="archive",
                                 metadata={"source": "archive", "archived_at": "2019-05-01"})

        async def no_stealthy(*a, **k):
            raise AssertionError("404 不升级浏览器")

        monkeypatch.setattr(fetcher, "tcp_preflight", lambda url, t=2.0: (True, ""))
        server = MasterFetchServer()
        server.get = fake_get
        server.stealthy_fetch = no_stealthy
        monkeypatch.setattr(server, "_fetch_from_archive", fake_archive)

        out = await server.web_read("https://api.example.com/items/42",
                                       cache_ttl=0, timeout=6000)

        assert out.content == [live], out.content
        assert out.status == 404
        assert out.error.startswith("http_error_404"), out.error
        assert (out.metadata or {}).get("source") != "archive"

@pytest.fixture
def proxy_socket(monkeypatch):
    """Drive ``proxy_preflight``'s socket call deterministically.

    conftest replaces ``socket.getaddrinfo`` with a fixed PUBLIC ip so the suite
    never depends on the machine's resolver — which also means
    ``create_connection(('127.0.0.1', port))`` does NOT reach loopback under
    pytest. A test that connects to a real local listener would therefore pass (or
    fail) for the wrong reason, so the socket is stubbed here and the REAL OS
    behaviour is verified outside pytest (recorded in the changelog).

    What these tests pin is the decision: which failures count as definitive, that
    an accepting endpoint is never rejected, and that a failure is not reported as
    a slow host.
    """
    import socket as _socket
    import types

    state = {"mode": "accept", "calls": []}

    def _fake(address, timeout=None):
        state["calls"].append((address, timeout))
        mode = state["mode"]
        if mode == "refused":
            raise ConnectionRefusedError(10061, "actively refused")
        if mode == "timeout":
            raise TimeoutError("timed out")
        return types.SimpleNamespace(close=lambda: None)

    monkeypatch.setattr(_socket, "create_connection", _fake)
    return state


class TestProxyPreflight:

    def test_a_refused_proxy_endpoint_is_reported(self, proxy_socket):
        from dhole_mcp.fetcher import proxy_preflight

        proxy_socket["mode"] = "refused"
        assert proxy_preflight("http://127.0.0.1:9/", timeout=3.0) == \
            (False, "proxy_unreachable")

    def test_a_timed_out_endpoint_is_also_definitive(self, proxy_socket):
        """过滤端口（SYN 被丢）同样不能"再试试" —— 那正是要消灭的静默 30s。"""
        from dhole_mcp.fetcher import proxy_preflight

        proxy_socket["mode"] = "timeout"
        assert proxy_preflight("http://10.255.255.1:3128/", timeout=3.0) == \
            (False, "proxy_unreachable")

    def test_an_accepting_endpoint_is_not_rejected(self, proxy_socket):
        """只判「有没有接受连接」。代理不答 HTTP 是另一回事，不能在这里杀掉。"""
        from dhole_mcp.fetcher import proxy_preflight

        assert proxy_preflight("http://127.0.0.1:7890/") == (True, "")
        assert proxy_socket["calls"] == [(("127.0.0.1", 7890), 5.0)]

    def test_an_unparseable_proxy_does_not_block_the_call(self, proxy_socket):
        """探测不出来就别拦 —— 未知不等于不可达。"""
        from dhole_mcp.fetcher import proxy_preflight

        assert proxy_preflight("not a url") == (True, "")
        assert proxy_socket["calls"] == [], "解析不出来就不该去连"

    def test_the_endpoint_label_never_carries_credentials(self):
        from dhole_mcp.server import _proxy_endpoint_label

        label = _proxy_endpoint_label("http://user:sekret@127.0.0.1:1080/")
        assert label == "127.0.0.1:1080"
        assert "sekret" not in label and "user" not in label

    def test_a_proxy_failure_is_not_answered_with_a_wayback_snapshot(self):
        """我们从未接触过目标站，快照会被当成那个页面读，而且是另一个问题的答案。"""
        from dhole_mcp.server import ResponseModel, _should_try_archive

        result = ResponseModel(url="https://example.com/", status=0, content=[],
                               error="proxy_unreachable: nothing accepted a TCP connection at 127.0.0.1:9")
        assert _should_try_archive(result) is False

        # 对照：真正的网络失败仍然该去查快照
        plain = ResponseModel(url="https://example.com/", status=0, content=[],
                              error="network_error: dns_failure (TCP preflight)")
        assert _should_try_archive(plain) is True


class TestDeadProxyFailsFast:

    @pytest.mark.asyncio
    async def test_it_returns_proxy_unreachable_without_burning_the_budget(
            self, proxy_socket, monkeypatch):
        """报告 G21 的实测形态：30s 预算烧光，报成「慢主机，调大 timeout」。"""
        from dhole_mcp.server import MasterFetchServer

        proxy_socket["mode"] = "refused"

        async def _never(*a, **k):
            raise AssertionError("死代理不该走到抓取 tier")

        server = MasterFetchServer()
        server.get = _never
        server.stealthy_fetch = _never
        monkeypatch.setattr(server, "_fetch_from_archive", _never)

        import time
        started = time.monotonic()
        identity.set_value("proxy", "http://127.0.0.1:9/")
        out = await server.web_read("https://example.com/",
                                     cache_ttl=0, timeout=30000)
        elapsed = time.monotonic() - started

        assert out.status == 0
        assert out.error.startswith("proxy_unreachable"), out.error
        assert out.fetcher_used == "none"
        assert "proxy_unreachable" in out.escalation_path
        assert elapsed < 10.0, f"仍然烧了 {elapsed:.1f}s"
        # 归因不能指向「慢主机」——那会让人去调 timeout，而问题不在那儿
        assert "raise timeout" not in out.next_action.lower(), out.next_action
        assert "no request was sent" in out.error

    @pytest.mark.asyncio
    async def test_a_pinned_tier_refuses_a_dead_proxy_too(self, proxy_socket):
        """`force_fetcher` returns from `_force_fetch`, which never asked about
        the proxy — the preflight lived only in `_auto_escalate`, the path a
        pinned call skips. Measured live: `force_fetcher='http'` + a dead proxy
        spent 6s and came back `os error 10061` in the local language, which
        reads as "the SITE refused us", for a proxy that never accepted a
        connection. The option description's promise is unconditional, so the
        check has to be too."""
        from dhole_mcp.server import MasterFetchServer

        proxy_socket["mode"] = "refused"

        async def _never(*a, **k):
            raise AssertionError("钉层也不该让死代理发出请求")

        server = MasterFetchServer()
        server.get = _never
        server.stealthy_fetch = _never
        identity.set_value("proxy", "http://127.0.0.1:9/")
        for pinned in ("http", "stealthy"):
            out = await server.web_read(
                "https://example.com/", force_fetcher=pinned, cache_ttl=0)
            assert out.error.startswith("proxy_unreachable"), f"{pinned}: {out.error}"
            assert out.fetcher_used == "none"

    @pytest.mark.asyncio
    async def test_credentials_never_reach_the_response(self, proxy_socket):
        from dhole_mcp.server import MasterFetchServer

        proxy_socket["mode"] = "refused"
        server = MasterFetchServer()

        identity.set_value("proxy", "http://user:sekret@127.0.0.1:9/")
        out = await server.web_read("https://example.com/",
                                     cache_ttl=0, timeout=10000)

        assert "sekret" not in out.error
        assert "127.0.0.1:9" in out.error
        # 代理现在住在落盘的默认里，于是响应多了一条出境的路：identity_applied 的
        # 回显。它只许点名键（"proxy"），所以这里量整份响应而不只是 error。
        assert "sekret" not in str(out.model_dump()), "凭据从响应的另一个字段出境了"

    @pytest.mark.asyncio
    async def test_a_live_proxy_endpoint_is_not_rejected(self, proxy_socket, monkeypatch):
        """回归守卫：本地代理（Clash 等）是常态，不能因为探测就把它们全杀掉。"""
        from dhole_mcp.server import MasterFetchServer, ResponseModel

        proxy_socket["mode"] = "accept"
        hit: list[str] = []

        async def fake_get(url, *a, **k):
            hit.append(url)
            return ResponseModel(url=url, status=200, content=["body"],
                                 content_type="text/html", fetcher_used="http",
                                 content_ok=True)

        server = MasterFetchServer()
        server.get = fake_get

        identity.set_value("proxy", "http://127.0.0.1:7890/")
        out = await server.web_read("https://example.com/",
                                     cache_ttl=0, timeout=5000)

        assert hit == ["https://example.com/"], "接受连接的代理不该被前置探测拦下"
        assert not out.error.startswith("proxy_unreachable")


# ============================================================================
# 来源：test_v17_fix_plan.py
# ============================================================================
# v17.0 实测缺陷修复计划的回归守卫。

def _dispatch(args: dict):
    """跑一次真实 dispatcher，返回 (content, structured envelope)。"""
    return asyncio.run(MasterFetchServer(cache_ttl=0)._dispatch("web_read", args))


# ── 缺陷 1（P0）· 批量路径 100% 抛 AttributeError ────────────────────────────

class TestBulkFetchDoesNotReachForASingleResultAttribute:
    """`_action_images` 只定义在 `ResponseModel` 上；`urls=[...]` 返回的是
    `BulkResponseModel`，那里这个属性是**不存在**而不是空。无守卫的读取抛
    AttributeError，冒泡成客户端可见的原始异常，整个批量调用失败。

    实测覆盖四种形式：带 option、单元素、不带 option、走 options 口袋——
    它们最终都落到同一行，所以这里测那**一行**，而不是四种排列。
    """

    def test_a_bulk_result_goes_through_the_dispatcher(self, monkeypatch):
        async def fake_smart_fetch(self, url="", urls=None, **kw):
            picked = urls or [url]
            return BulkResponseModel(
                results=[ResponseModel(url=u, status=200, content=["x"]) for u in picked],
                total=len(picked), successful=len(picked))

        monkeypatch.setattr(MasterFetchServer, "web_read", fake_smart_fetch)
        _content, payload = _dispatch({"urls": ["http://a.test/", "http://b.test/"]})
        assert payload["total"] == 2
        assert [r["url"] for r in payload["results"]] == ["http://a.test/", "http://b.test/"]

    def test_a_single_result_still_carries_its_images(self, monkeypatch):
        """守卫式读取不许把单条路径的元素截图功能改坏 —— 这是回归，不是新功能。"""
        async def fake_smart_fetch(self, url="", urls=None, **kw):
            row = ResponseModel(url=url, status=200, content=["x"])
            row._action_images = [{"data": "aGk=", "mime_type": "image/png"}]
            return row

        monkeypatch.setattr(MasterFetchServer, "web_read", fake_smart_fetch)
        content, _payload = _dispatch({"url": "http://a.test/"})
        kinds = [getattr(c, "type", None) for c in content]
        assert "image" in kinds, "单条路径的图片必须仍然作为 image content 返回"

    def test_bulk_mode_refuses_actions_so_no_images_can_be_silently_dropped(self):
        """守卫式读取是**完整**修复，不是权宜之计。

        批量模式明确拒绝 `actions`，而 `_action_images` 的唯一写入点
        （`_report_action_outcomes`）只在单条路径上跑 —— 所以「聚合各结果的
        图片」是死代码。这条把那个前提钉住：哪天批量开始支持 actions，这里会红，
        那时才真的需要聚合。
        """
        result = asyncio.run(MasterFetchServer(cache_ttl=0).web_read(
            url="", urls=["http://a.test/"], actions=[{"click": "a"}]))
        assert isinstance(result, BulkResponseModel)
        assert result.successful == 0
        for row in result.results:
            assert "bulk mode" in (row.error or ""), (
                "批量模式必须显式拒绝 actions，否则上面的守卫就不够了")


# ── 缺陷 3（P1）· OCR 成功的 PDF 被误判 js_shell ─────────────────────────────

OCR_TEXT = ("# scanned\n> OCR-extracted from scanned PDF · 1 pages\n\n--- Page 1 ---\n\n"
            "DHOLE OCR TEST 12345\nscanned page marker ABCDE")


def _pdf(**kw) -> ResponseModel:
    base = dict(url="http://h/scanned.pdf", status=200, content=[OCR_TEXT],
                content_type="application/pdf", fetcher_used="http",
                total_size_bytes=9662)
    return ResponseModel(**{**base, **kw})


class TestAnOcrRescuedPdfIsNotCalledAJavaScriptShell:
    """「大 body + 少文本」描述的是 HTML 空壳，而扫描件经 OCR 抢救回来**按构造**
    就是字节多、文本少：单页扫描件 ≈116 字符对 ≈9.6KB 文件，正好落在阈值
    （body > 3000、text < 200）里。

    `_detect_content_issue` 只守住了零文本那条分支，有文本的 PDF 会 fall
    through 到启发式，于是**内容对、判定反**：一个刚被 OCR 救回来的结果被标成
    `content_ok=false`，agent 依此丢弃它或按 next_action 去重取／换源白跑。
    """

    def test_an_ocr_rescued_scan_is_clean(self):
        r = _pdf()
        assert _is_js_shell(r) is False
        assert _detect_content_issue(r) == ""

    def test_a_short_text_layer_pdf_is_clean_too(self):
        """不是夹具特例：证书、封面页、稀疏文本层的 PDF 都是同一个形状。"""
        r = _pdf(url="http://h/cert.pdf", content=["Certificate of completion, 2026."],
                 total_size_bytes=9000)
        assert _is_js_shell(r) is False
        assert _detect_content_issue(r) == ""

    def test_a_scan_with_no_ocr_result_keeps_its_own_error(self):
        """零文本仍走 `pdf_no_text`，不许被这次的修复顺手并掉。"""
        r = _pdf(content=[])
        assert "pdf_no_text" in _detect_content_issue(r)

    def test_the_status_checks_after_it_still_run(self):
        """守卫放在 `_is_js_shell` 里、不是从 `_detect_content_issue` 提前
        `return ""` —— 那样会顺带吃掉后面的 http_error_4xx。"""
        assert "http_error_404" in _detect_content_issue(_pdf(status=404))

    def test_a_real_html_js_shell_is_still_caught(self):
        """回归：守卫只针对 PDF，HTML 空壳的判定一个字节都不许动。"""
        r = ResponseModel(url="https://quotes.toscrape.com/js/", status=200,
                          content=["<html><body></body></html>"],
                          content_type="text/html", fetcher_used="http",
                          total_size_bytes=12000)
        assert _is_js_shell(r) is True
        assert "js_shell_detected" in _detect_content_issue(r)

    def test_the_guard_is_in_the_predicate_not_only_the_diagnosis(self):
        """`_is_js_shell` 还被**升级决策**调用（三处）。对 PDF 升级浏览器毫无
        意义——浏览器不渲染数据文档，它只是给数据文档套个框。守卫放在函数入口
        才能一次性覆盖诊断与全部升级调用点。"""
        assert _is_js_shell(_pdf()) is False, (
            "守卫必须在 _is_js_shell 内部，否则升级决策仍会把 PDF 当空壳")


# ── 缺陷 2（P1）· 本地扫描件不做 OCR 恢复 ───────────────────────────────────

OCR_PAYLOAD = "# scanned\n> OCR-extracted from scanned PDF · 1 pages\n\n--- Page 1 ---\n\nDHOLE OCR TEST 12345"


def _stub_scan(monkeypatch, *, ocr_text=OCR_PAYLOAD):
    """让两条路径都看到「一份整篇扫描的 PDF」，并把 OCR 换成确定性的假实现。

    钉的是**接线**（本地路径有没有跑那一步），不是 OCR 引擎本身——引擎由下面
    那条真跑的用例覆盖。
    """
    from dhole_mcp import ocr as ocr_mod
    from dhole_mcp import pdf_extractor as pe
    from dhole_mcp.pdf_extractor import PdfResult

    def fake_extract(body, **kw):
        return PdfResult(content=["[Scanned/image-only PDF - no extractable text.]"],
                         title="scanned", scanned=True, content_ok=False,
                         error="scanned_pdf: this PDF is image-only")

    def fake_ocr(body, **kw):
        return PdfResult(content=[ocr_text], title="scanned", scanned=True,
                         error="", content_ok=True)

    monkeypatch.setattr(pe, "extract_pdf", fake_extract)
    monkeypatch.setattr(ocr_mod, "ocr_pdf", fake_ocr)
    monkeypatch.setattr(ocr_mod, "ocr_available", lambda: True)


class TestALocalScanGetsTheSameOcrRecoveryAsTheUrlPath:
    """整篇扫描件的 OCR 恢复此前只写在 server.py 的 URL 路径里。`parse` 只调了
    提取器就返回，于是**同一份字节**走 HTTP 被 OCR 成功、走磁盘却回「请安装
    dhole-mcp[all]」——而 `_parse_pdf` 的 docstring 恰好承诺两条路径一致。

    恢复逻辑已抽到 `pdf_extractor.recover_scanned_pdf`，两条路径共用。
    """

    def test_the_local_path_runs_the_recovery(self, monkeypatch, tmp_path):
        _stub_scan(monkeypatch)
        from dhole_mcp.parse import parse_file_detailed

        p = tmp_path / "scan.pdf"
        p.write_bytes(b"%PDF-1.4 fake")
        content, error, extras = parse_file_detailed(str(p))
        assert error == "", "本地路径必须跑 OCR 恢复，而不是回死路"
        assert "DHOLE OCR TEST 12345" in content
        assert extras["content_ok"] is True

    def test_both_paths_return_the_same_text_for_the_same_bytes(
            self, monkeypatch, tmp_path):
        """这条才是缺陷的正面表述：同一份字节，两条路径必须给同一个答案。"""
        _stub_scan(monkeypatch)
        from dhole_mcp.parse import parse_file_detailed
        from dhole_mcp.server import _extract_pdf_response

        body = b"%PDF-1.4 fake"
        p = tmp_path / "scan.pdf"
        p.write_bytes(body)
        local, _err, _extras = parse_file_detailed(str(p))
        remote = _extract_pdf_response(body, "application/pdf", len(body),
                                       "http://h/scan.pdf", "markdown", "http", 1.0)
        assert local.startswith(remote.content[0]), (
            "本地路径只允许在远程内容后追加说明，正文必须逐字一致")

    def test_the_recovery_is_a_no_op_when_ocr_is_not_installed(self, monkeypatch):
        """没装 OCR 时保持原有的诚实死路，不许把错误吞掉或改成静默空内容。"""
        from dhole_mcp import ocr as ocr_mod
        from dhole_mcp.pdf_extractor import PdfResult, recover_scanned_pdf

        monkeypatch.setattr(ocr_mod, "ocr_available", lambda: False)
        dead_end = PdfResult(content=["[Scanned/image-only PDF]"], scanned=True,
                             error="scanned_pdf: this PDF is image-only")
        out = recover_scanned_pdf(dead_end, b"%PDF")
        assert out is dead_end
        assert out.error.startswith("scanned_pdf")

    def test_it_does_not_touch_a_pdf_with_a_text_layer(self, monkeypatch):
        """有文本层的 PDF 一个字节都不许走 OCR。

        让 `ocr_pdf` 直接炸：只要它被调到，这条就红。断言「结果没变」是弱证据
        （本来就可能没变），断言「引擎根本没被调」才是这条要守的东西。
        """
        from dhole_mcp import ocr as ocr_mod
        from dhole_mcp.pdf_extractor import PdfResult, recover_scanned_pdf

        def boom(*a, **kw):
            raise AssertionError("有文本层的 PDF 不该走 OCR")

        monkeypatch.setattr(ocr_mod, "ocr_pdf", boom)
        healthy = PdfResult(content=["# Report\n\n> PDF · 1 pages"], content_ok=True,
                            quality_score=1.0)
        out = recover_scanned_pdf(healthy, b"%PDF")
        assert out is healthy
        assert out.ocr_fallback_used is False

    def test_an_encrypted_pdf_is_left_alone_too(self, monkeypatch):
        """加密件同样不走 —— OCR 打不开它，`ocr_pdf` 会被无谓地调一次并失败。"""
        from dhole_mcp import ocr as ocr_mod
        from dhole_mcp.pdf_extractor import PdfResult, recover_scanned_pdf

        monkeypatch.setattr(ocr_mod, "ocr_pdf",
                            lambda *a, **kw: pytest.fail("加密件不该走 OCR"))
        locked = PdfResult(content=["[encrypted]"], scanned=True, encrypted=True,
                           error="pdf_encrypted: password required")
        assert recover_scanned_pdf(locked, b"%PDF") is locked


# ── 缺陷 2 的伴随缺陷 · 本地路径把标题印两遍 ────────────────────────────────

class TestTheLocalPathDoesNotDuplicateTheTitle:
    """`extract_pdf` 的正文头本来就是 `# <title>`（`_format_metadata` 写的，
    `ocr_pdf` 也一样），而 `_parse_pdf` 又自己拼了一次 —— 于是**每一个带标题的
    PDF 从磁盘读都会把标题印两遍**，同一份字节走 HTTP 只印一遍。

    这是实现缺陷 2 时发现的：两条路径对同一份文档给出不同输出，正是这个函数
    的 docstring 承诺不会发生的事。
    """

    def test_the_title_appears_once(self, tmp_path):
        pytest.importorskip("pdfplumber")
        pytest.importorskip("pypdf")
        from pypdf import PdfReader, PdfWriter

        from dhole_mcp.parse import parse_file_detailed

        src = Path(__file__).resolve().parent / "background_checks.pdf"
        reader, writer = PdfReader(str(src)), PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        writer.add_metadata({"/Title": "Quarterly Report 2026"})
        out = tmp_path / "titled.pdf"
        with open(out, "wb") as fh:
            writer.write(fh)

        content, error, extras = parse_file_detailed(str(out))
        assert error == ""
        assert content.count("# Quarterly Report 2026") == 1, (
            "标题必须只出现一次 —— 提取器的正文头里已经有一个")
        assert extras["metadata"]["title"] == "Quarterly Report 2026", (
            "标题仍然要作为字段可取，去掉的只是重复的正文行")


# ── 缺陷 2 的端到端（真 OCR，无夹具依赖则跳过）─────────────────────────────

class TestARealScannedPdfIsReadFromDisk:
    def test_end_to_end_with_the_real_ocr_stack(self, tmp_path):
        pytest.importorskip("PIL")
        from dhole_mcp import ocr as ocr_mod
        if not ocr_mod.ocr_available():
            pytest.skip("OCR 栈未安装")

        from PIL import Image, ImageDraw

        from dhole_mcp.parse import parse_file_detailed

        img = Image.new("RGB", (1000, 400), "white")
        ImageDraw.Draw(img).text((60, 120), "DHOLE OCR TEST 12345", fill="black")
        p = tmp_path / "scanned.pdf"
        img.save(p, "PDF", resolution=150.0)

        content, error, extras = parse_file_detailed(str(p))
        assert error == ""
        assert extras["content_ok"] is True
        assert "12345" in content, "OCR 必须真的读出那行字"


# ── 缺陷 4（P1）· OCR 提示硬编码，误导已装 [all] 的用户 ──────────────────────

class TestOcrHintsAreCapabilityAware:
    """"Install dhole-mcp[all]" 是一句关于**这台机器**的事实断言。在已经装了
    `[all]` 的机器上它是假的：它把「这条路径没产出 OCR 文本」报成「你没装
    OCR」。实测代价是开发者据此写下「本机未装 [all]」并据此排查，直到人工核对
    解释器才纠正 —— 真实用户会去重装一个已经装好的包。

    提示统一收进 `pdf_extractor.ocr_advice()`，四处（整篇扫描死路、两个逐页
    标记、web_read 的 next_action）同源，不可能对同一台机器给出两种说法。
    """

    def test_it_does_not_tell_you_to_install_when_ocr_is_present(self, monkeypatch):
        from dhole_mcp import ocr as ocr_mod
        from dhole_mcp.pdf_extractor import ocr_advice

        monkeypatch.setattr(ocr_mod, "ocr_available", lambda: True)
        advice = ocr_advice()
        assert "pip install" not in advice, "已装 OCR 却让人去装 —— 事实错误的指引"
        assert "installed" in advice, "要说清 OCR 在，只是这次没救回来"

    def test_it_does_say_install_when_ocr_is_missing(self, monkeypatch):
        from dhole_mcp import ocr as ocr_mod
        from dhole_mcp.pdf_extractor import ocr_advice

        monkeypatch.setattr(ocr_mod, "ocr_available", lambda: False)
        assert "pip install dhole-mcp[all]" in ocr_advice()

    def test_a_probe_that_raises_is_treated_as_missing(self, monkeypatch):
        """探测本身炸了不能连带炸掉响应构造 —— 当成没装，退回安装提示。"""
        from dhole_mcp import ocr as ocr_mod
        from dhole_mcp.pdf_extractor import ocr_advice

        def boom():
            raise RuntimeError("import machinery broke")

        monkeypatch.setattr(ocr_mod, "ocr_available", boom)
        assert "pip install dhole-mcp[all]" in ocr_advice()

    def test_the_scanned_dead_end_uses_the_helper(self, monkeypatch, tmp_path):
        """整篇扫描死路的 error / content 里必须是辅助函数的话，不是硬编码。

        直接调 `extract_pdf`：OCR 恢复是**调用方**的下一步，所以这里拿到的
        正是死路本身，不需要伪造 PDF 对象。
        """
        pytest.importorskip("pdfplumber")
        pytest.importorskip("PIL")
        from PIL import Image

        from dhole_mcp import ocr as ocr_mod
        from dhole_mcp.pdf_extractor import extract_pdf, ocr_advice

        monkeypatch.setattr(ocr_mod, "ocr_available", lambda: True)
        p = tmp_path / "scan.pdf"
        Image.new("RGB", (600, 300), "white").save(p, "PDF", resolution=150.0)

        out = extract_pdf(p.read_bytes(), extraction_type="markdown")
        assert out.scanned is True
        advice = ocr_advice()
        assert advice in out.error, "死路提示必须来自辅助函数"
        assert advice in out.content[0]
        assert "pip install" not in out.error, (
            "OCR 已装，这条死路不该让人去装")

    def test_the_next_action_for_a_scanned_pdf_is_also_capability_aware(
            self, monkeypatch):
        from dhole_mcp import ocr as ocr_mod
        from dhole_mcp.server import _agent_hints

        monkeypatch.setattr(ocr_mod, "ocr_available", lambda: True)
        r = ResponseModel(url="http://h/scan.pdf", status=200, content=[],
                          content_type="application/pdf", fetcher_used="http",
                          error="scanned_pdf: this PDF is image-only",
                          content_ok=False)
        _summary, next_action, _ok = _agent_hints(r)
        assert "pip install" not in next_action, (
            "next_action 也在让已装 OCR 的人去装 —— 与错误文本自相矛盾")


# ── 缺陷 5（P2）· 零匹配选择器白等 30s + 误诊 ────────────────────────────────

class _FakeLocator:
    def __init__(self, count: int):
        self._count = count
        self.screenshot_reached = False

    async def count(self) -> int:
        return self._count

    @property
    def first(self):
        return self

    async def screenshot(self, **kw):
        self.screenshot_reached = True
        return b"\x89PNG"


class _FakePage:
    def __init__(self, count: int):
        self.loc = _FakeLocator(count)

    def locator(self, selector):
        return self.loc


class TestAZeroMatchSelectorFailsFastAndHonestly:
    """`.first` 对零匹配的 locator 会等满 timeout（默认 30s），而错误映射把
    timeout 一律读成「页面没渲染完」——把人引向调 `timeout` 和加
    `wait_selector`，对「元素不在 DOM 里」这两个补救都无效。

    实测（本地靶站，浏览器已热）：修复前该情形与真超时无法区分；修复后零匹配
    3.6s 返回，与一次**成功**的裁切（2.9s）同量级——等待本身消失了，剩下的只是
    页面加载时间。
    """

    def test_a_zero_match_never_reaches_the_screenshot_call(self):
        page = _FakePage(count=0)
        with pytest.raises(LookupError) as got:
            asyncio.run(_locator_or_raise(page, "#nope"))
        assert "matched no element" in str(got.value)
        assert page.loc.screenshot_reached is False, (
            "预检没拦住 —— 这一路就是白等 30s 的那条")

    def test_a_real_match_still_returns_the_locator(self):
        page = _FakePage(count=3)
        loc = asyncio.run(_locator_or_raise(page, "h1"))
        assert loc is page.loc

    def test_the_headline_says_matched_nothing_not_rendering_timeout(self):
        msg = _screenshot_error_message(
            LookupError("selector matched no element on the loaded page: 'div'"))
        assert "matched nothing" in msg
        assert "raise timeout" not in msg, (
            "零匹配不是渲染超时 —— 让人去调 timeout 是无效方向")
        assert "wait_selector" in msg, "只在元素稍后出现时 wait_selector 才有用"

    def test_a_genuine_timeout_keeps_its_own_hint(self):
        msg = _screenshot_error_message(
            TimeoutError("Locator.screenshot: Timeout 30000ms exceeded."))
        assert "never finished rendering" in msg
        assert "matched nothing" not in msg

    def test_the_narrow_needle_is_matched_before_the_wide_one(self):
        """`_BROWSER_ERROR_HINTS` 按 headline 子串匹配、命中即停。新条目排在宽的
        `timeout` 之后就会被吞掉 —— 这条钉的是顺序，不是措辞。"""
        both = LookupError("selector matched no element on the loaded page: 'div' "
                           "(waited for timeout 30000ms)")
        assert "matched nothing" in _screenshot_error_message(both), (
            "同时提到 timeout 的 headline 必须仍走零匹配那条提示")


# ── 缺陷 6（P2）· discover_only + sitemap：地图短于站点时没人说 ──────────────
#
# 计划把这一条写成「无截断上限」，实测**不成立**：上限一直都在
# （`cap = min(5000, max(SITEMAP_MAP_FLOOR, max_pages * 10))`，floor 1000），
# 349 条不被截断只是因为 349 < 1000。真正的缺口是两处**没人说的截断**：
#
#   1. 走 <sitemapindex> 时的文件数 / 层数上限（`_MAX_SITEMAPS` /
#      `_MAX_INDEX_DEPTH`）独立于 URL 上限。撞上它时 `capped` 是 False，
#      计数也看不出来——站点把 1,200 页拆进 30 个小 sitemap，这里映射出
#      1,000 条，剩下 200 条没有任何痕迹。
#   2. `capped` 为 False 的那条分支**一个字都没有**，于是「mapped 349 URL(s)」
#      读起来就是「这就是整站」。而 sitemap 是站点**选择发布**的索引。
#
# 另外 `path_include` 过去在**截断之后**才过滤，于是 `next_action` 里那句
# 「用 path_include 看全子树」在长 sitemap 上是假的：过滤的是前 1000 条。
# 这条一起修，见 `discover_sitemap` 的 `accept`。


def _stub_sitemap(monkeypatch, *, n=3, capped=False, index_truncated=False,
                  urls=None, sitemaps=None, filtered_out=0):
    """把 `discover_sitemap` 换成一份现成结果：缺陷 6 考的是**报告**，不是抓取。

    替身**不**施加 `accept`——过滤谓词由被测的 `discover_sitemap` 自己负责
    （下面那个类单独测它）。这里要能精确指定 `listed` 与 `ok` 的差。
    `sitemaps=[]` 表示「一个 sitemap 都没抓到」，与「抓到了但没产出」是两回事。
    """
    res = sm.SitemapResult()
    res.urls = [sm.SitemapURL(url=u) for u in
                (urls if urls is not None
                 else [f"https://site.test/p{i}" for i in range(n)])]
    res.sitemaps_used = (["https://site.test/sitemap.xml"] if sitemaps is None
                         else list(sitemaps))
    res.capped = capped
    res.index_truncated = index_truncated
    res.filtered_out = filtered_out
    res.via = "conventional"
    monkeypatch.setattr(sm, "discover_sitemap", lambda start_url, **kw: res)
    return res


def _map(monkeypatch, **kw):
    _stub_sitemap(monkeypatch, **kw)
    return asyncio.run(crawl_mod._sitemap_map(
        "https://site.test/", None, None, max_pages=10, deadline_t=time.time() + 30))


class TestASitemapMapSaysWhenItIsShortOfTheSite:
    """`sitemap_truncated` 是给 agent 提前判断响应规模用的（计划第 7 节验收 3）。

    修复前这条地图的截断状态只活在 `summary` 的散文里：三个 `truncated_by_*`
    在一条**确实被截断**的地图上全是 False，照结构化字段判断的调用方会读反——
    又是「看起来正常、实际答错」。
    """

    def test_a_capped_map_flags_itself(self, monkeypatch):
        r = _map(monkeypatch, n=1000, capped=True)
        assert r.sitemap_truncated is True
        assert r.pages_discovered == 1000
        assert "map capped at 1000 URLs" in r.summary

    def test_the_flag_is_a_field_not_only_a_sentence(self, monkeypatch):
        """线上形态：标志必须真的出现在序列化结果里，而不是只在散文里。"""
        r = _map(monkeypatch, n=1000, capped=True)
        _text, payload = _wire_json(r)
        assert payload["sitemap_truncated"] is True
        assert payload["pages_discovered"] == 1000

    def test_the_small_map_wire_shape_does_not_change(self, monkeypatch):
        """计划验收：小 sitemap 不受影响、字段不变。默认值不上线，所以这条
        地图的 payload 与修复前逐键相同。"""
        r = _map(monkeypatch, n=3)
        _text, payload = _wire_json(r)
        assert "sitemap_truncated" not in payload

    def test_an_uncapped_map_does_not_read_as_the_whole_site(self, monkeypatch):
        r = _map(monkeypatch, n=349)
        # 先断措辞：这条用例要钉的是「没触顶那条分支一个字都不说」，
        # 把新字段的断言放前面会让修复前的失败停在一个无关的 AttributeError 上。
        assert "349 URL(s)" in r.summary
        assert "capped" not in r.summary
        assert "a sitemap is an index" in r.summary, (
            "没触顶的那条分支过去一个字都不说 —— 「mapped 349」于是成了「整站」")
        assert r.sitemap_truncated is False

    def test_the_index_limit_is_its_own_case_and_names_no_dead_knob(self, monkeypatch):
        r = _map(monkeypatch, n=900, index_truncated=True)
        assert r.sitemap_truncated is True
        assert "prefix of the site" in r.summary
        assert "max_pages" not in r.next_action, (
            "max_pages 动不了 sitemap 文件的读取上限 —— 点了没反应的旋钮比不点更糟")

    def test_entries_that_did_not_become_rows_are_counted(self, monkeypatch):
        """`ok` 与 sitemap 实际列出的条数可以不等（归一化后重复、或撞上
        deadline），而 summary 过去只报 `ok`，差值无声消失。"""
        r = _map(monkeypatch, urls=["https://site.test/a", "https://site.test/a/"])
        assert r.pages_discovered == 1
        assert "1 of the sitemap's 2" in r.summary


class TestAnEmptyMapIsNotEmptySite:
    """`_sitemap_map` 过去用同一个 `None` 表示两件事：「没抓到 sitemap」和
    「抓到了、解析了，但一条都没产出」。调用方按后者报了**前者**的话：

        no sitemap.xml found at ... (robots.txt had no Sitemap directive and
        /sitemap.xml returned nothing)

    ——而 sitemap 就在那儿，dhole 刚读完它。这是本仓库最忌讳的形状：调用看起来
    成功了，诊断却自信地答错，还把 agent 指向 BFS 去重新发现一个已经读过的站点。
    实测（真 HTTP 靶站）：`path_include=["/nothing/"]` 在修复前得到上面那句，
    修复后得到「sitemap.xml was read ... none of its 40 URL(s) are
    path_include=['/nothing']」。
    """

    def test_a_found_but_empty_map_is_not_reported_as_missing(self, monkeypatch):
        _stub_sitemap(monkeypatch, urls=[], filtered_out=40)
        r = asyncio.run(crawl_mod._sitemap_map(
            "https://site.test/", ["/nothing/"], None,
            max_pages=10, deadline_t=time.time() + 30))
        assert r is not None, "抓到了 sitemap 却说「没找到」—— 这就是那句假话的来源"
        assert r.sitemap_used is True
        assert r.sitemaps == ["https://site.test/sitemap.xml"]
        assert "was read" in r.summary and "the map is empty" in r.summary
        assert "40" in r.summary, "过滤掉多少条要说出来，否则无从判断是站点还是过滤器"
        assert "path_include" in r.summary
        assert "no sitemap.xml found" not in r.summary
        assert "path_include" in r.next_action or "scope" in r.next_action

    def test_no_sitemap_at_all_is_still_not_found(self, monkeypatch):
        """真的没抓到时必须仍然返回 None —— 调用方的「没找到」那句要留着。"""
        _stub_sitemap(monkeypatch, urls=[], sitemaps=[])
        assert asyncio.run(crawl_mod._sitemap_map(
            "https://site.test/", None, None,
            max_pages=10, deadline_t=time.time() + 30)) is None

    def _empty_map(self):
        return crawl_mod.CrawlResponseModel(
            start_url="https://site.test/", pages=[], discover_only=True,
            sitemap_used=True, summary="sitemap.xml was read ... but the map is empty")

    def _run(self, monkeypatch, sitemap):
        empty = self._empty_map()

        async def fake_map(*a, **kw):
            return empty

        calls: list[str] = []

        async def fake_fetch(self, url="", **kw):
            calls.append(url)
            return ResponseModel(url=url, status=200, content=["# body"],
                                 content_ok=True)

        monkeypatch.setattr(crawl_mod, "_sitemap_map", fake_map)
        monkeypatch.setattr(MasterFetchServer, "web_read", fake_fetch)
        out = asyncio.run(crawl_mod.web_crawl(
            MasterFetchServer(cache_ttl=0), "https://site.test/",
            sitemap=sitemap, discover_only=True, max_pages=1, ignore_robots=True))
        return out, empty, calls

    def test_an_explicit_sitemap_true_returns_the_empty_map(self, monkeypatch):
        out, empty, calls = self._run(monkeypatch, True)
        assert out is empty
        assert not calls, "sitemap=true 是「只用 sitemap」，不该偷偷去 BFS"

    def test_auto_still_falls_through_to_bfs_on_an_empty_map(self, monkeypatch):
        """`auto` = 「有 sitemap 就用，没有就 BFS」。空地图不等于「没有」，
        但 BFS 能到 sitemap 没列的页面，所以这里必须保持退回 BFS 的老行为 ——
        修「找不到」那句假话时不能顺手把它改掉。"""
        out, empty, calls = self._run(monkeypatch, "auto")
        assert calls, "auto + 空地图仍然要走 BFS"
        assert out is not empty and out.sitemap_used is False


class TestThePathFilterIsSpentBeforeTheMapCap:
    """`path_include` 过去在 `_sitemap_map` 里、`discover_sitemap` **返回之后**
    才生效，而返回时列表已经截到 cap。站点把 docs 排在 2000 条之后时，
    `path_include=["/docs/"]` 过滤的是前 1000 条（全是 blog）——结果为空，
    `_sitemap_map` 于是返回 None，整条调用**静默**退回 BFS。

    没有报错，只是答了另一个问题：这正是本仓库最忌讳的形状，也是
    `next_action` 里「scope with path_include/path_exclude to see a subtree in
    full」在长 sitemap 上不成立的原因。
    """

    PATHS = [f"/blog/{i}" for i in range(10)] + [f"/docs/{i}" for i in range(5)]

    def _http_get(self, paths=None):
        xml = ("<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>"
               + "".join(f"<url><loc>https://site.test{p}</loc></url>"
                         for p in (self.PATHS if paths is None else paths))
               + "</urlset>").encode()

        def get(u):
            return (200, xml) if u.endswith("/sitemap.xml") else None
        return get

    @pytest.fixture(autouse=True)
    def _no_ssrf_dns(self, monkeypatch):
        """每个子 sitemap 抓取前都跑 SSRF 校验，其中含 DNS 复查；本机 DNS 是
        fake-ip 代理，`site.test` 解析不到（见 evals/README）。这里只测「走到
        哪、报了什么」，网络判定不是本用例的对象。"""
        monkeypatch.setattr("dhole_mcp.security.validate_url", lambda u, **kw: u)

    def test_the_cap_is_spent_on_the_subtree_that_was_asked_for(self):
        res = sm.discover_sitemap("https://site.test/", http_get=self._http_get(),
                                  max_urls=3, accept=lambda u: "/docs/" in u)
        assert len(res.urls) == 3
        assert all("/docs/" in u.url for u in res.urls), (
            "cap 又被花在 blog 上了 —— 调用方要的子树一条都没进来")

    def test_without_a_predicate_the_cap_still_counts_raw_entries(self):
        """对照：没有谓词时行为与修复前一致（就是前 N 条），cap 的语义没被改。"""
        res = sm.discover_sitemap("https://site.test/", http_get=self._http_get(),
                                  max_urls=3)
        assert [u.url for u in res.urls] == [
            "https://site.test/blog/0", "https://site.test/blog/1",
            "https://site.test/blog/2"]

    def test_a_predicate_that_raises_drops_only_its_own_url(self):
        """本模块的契约是「永不抛」。一条坏 URL 不能让整张地图消失——那会静默
        退回 BFS，而调用方连地图都没见过。"""
        def accept(u):
            if "/blog/3" in u:
                raise ValueError("junk entry")
            return True
        res = sm.discover_sitemap("https://site.test/", http_get=self._http_get(),
                                  max_urls=100, accept=accept)
        assert len(res.urls) == len(self.PATHS) - 1
        assert all("/blog/3" not in u.url for u in res.urls)

    def test_the_index_file_limit_is_reported_only_when_a_file_was_left(self):
        """`index_truncated` 是**准确**的，不是 `capped` 那种「至少」：刚好读满
        `_MAX_SITEMAPS` 个文件时没有第 N+1 个可读，标志必须保持 False。"""
        def get_for(children):
            idx = ("<sitemapindex xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>"
                   + "".join(f"<sitemap><loc>https://site.test/sm{i}.xml</loc></sitemap>"
                             for i in range(children))
                   + "</sitemapindex>").encode()
            leaf = ("<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>"
                    "<url><loc>https://site.test/leaf</loc></url></urlset>").encode()

            def get(u):
                if u.endswith("/sitemap.xml"):
                    return (200, idx)
                if "/sm" in u:
                    return (200, leaf)
                return None
            return get

        # 索引自身占掉一个名额，所以「刚好读满」是 _MAX_SITEMAPS - 1 个子文件。
        exact = sm.discover_sitemap("https://site.test/",
                                    http_get=get_for(sm._MAX_SITEMAPS - 1))
        assert exact.index_truncated is False, "刚好读满不是截断 —— 没有下一个文件可读"
        over = sm.discover_sitemap("https://site.test/",
                                   http_get=get_for(sm._MAX_SITEMAPS))
        assert over.index_truncated is True


# ============================================================================
# 来源：test_cdp_attach_field_description_matches_the_audit_reality.py
# ============================================================================
# cdp_attached 描述声称「every attach attempt is audited」，而审计通道默认关闭。

def test_the_description_is_conditional_not_unconditional():
    desc = ResponseModel.model_fields["cdp_attached"].description or ""
    assert "DHOLE_USAGE_LOG" in desc, "描述要点名审计的开关，而不是声称无条件已审计"
    assert "every attach attempt is audited" not in desc


def test_with_the_channel_off_an_event_touches_nothing(monkeypatch):
    """关闭侧的现实：通道未设 → 早退，不碰文件系统。"""
    monkeypatch.setattr(env_registry, "value_of", lambda name: "")

    def _boom(*a, **k):
        raise AssertionError("audit touched the filesystem with the channel off")

    monkeypatch.setattr(audit.os, "makedirs", _boom)
    audit._audit_event("cdp_attach", outcome="refused")


def test_with_the_channel_on_an_event_is_recorded(tmp_path, monkeypatch):
    """开启侧的现实：通道指向文件 → 事件落一行（被拒的 attach 也记）。"""
    target = tmp_path / "usage.jsonl"
    monkeypatch.setattr(env_registry, "value_of", lambda name: str(target))
    audit._audit_event("cdp_attach", outcome="refused")
    lines = target.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1 and "cdp_attach" in lines[0] and "refused" in lines[0]
