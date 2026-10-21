"""真人使用模拟、加固（凭据脱敏）与审查修复的回归

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import asyncio
import pytest
import dhole_mcp.crawl as crawl_mod
from dhole_mcp.crawl import CrawlPage, CrawlResponseModel, _classify_and_extract
from dhole_mcp.errors import classify_network_error
from dhole_mcp.robots import Verdict as RobotsVerdict
from dhole_mcp.search import _filter_irrelevant_results, _search_next_action
from dhole_mcp.search import SearchResult
from dhole_mcp.server import MasterFetchServer, ResponseModel, _agent_hints, _apply_envelope, _never_escalate, _validate_schema_selectors
import logging
from dhole_mcp.fetcher import HTTPSession
import json
from dhole_mcp import server as server_mod
from dhole_mcp.server import _detect_content_issue, _is_js_shell
from unittest.mock import AsyncMock, MagicMock, patch
from dhole_mcp.security import SecurityError, validate_url


# ============================================================================
# 来源：test_usage_simulation_regressions.py
# ============================================================================
# 第九轮实测：真实使用模拟（约 40 个调用）暴露的问题。

def _result(**kw):
    kw.setdefault("url", "https://x.test/p")
    kw.setdefault("status", 200)
    kw.setdefault("content", ["some extracted body"])
    return ResponseModel(**kw)


# ─── 1: 兜底必须比层级自己的 accounting 晚到 ──────────────────────────

class TestTheBackstopArrivesAfterTheTiers:

    @pytest.mark.asyncio
    async def test_a_tier_that_answers_late_still_answers_for_itself(self):
        """旧行为：兜底与内层用同一个数，却从更早的 started 起算，于是每次都由它
        回答 —— 而它不知道哪个层在跑、也不知道那个层已经查出了什么。"""
        server = MasterFetchServer()

        async def slow_but_fine():
            await asyncio.sleep(1.3)
            return _result(content=["the tier's own answer"])

        out = await server._within_call_budget(slow_but_fine(), "https://x.test/p",
                                               1000, "HTTP tier")
        assert out.error == "", f"兜底抢在层级之前回答了: {out.error}"
        assert out.content == ["the tier's own answer"]

    @pytest.mark.asyncio
    async def test_a_wedged_tier_still_names_the_stage(self):
        server = MasterFetchServer()

        async def hang():
            await asyncio.sleep(30)

        out = await server._within_call_budget(hang(), "https://x.test/p", 600,
                                               "forced http tier")
        assert out.error.startswith("timeout: the ")
        assert "forced http tier(timeout" in out.escalation_path, out.escalation_path
        # The number named is the caller's budget after the 1s floor - not
        # asked+grace, which would report a budget nobody asked for.
        assert "1000ms call budget" in out.error, out.error

    @pytest.mark.asyncio
    async def test_the_browser_timeout_carries_what_http_already_knew(self, monkeypatch):
        """实测：HTTP 层 5s 报出 os error 10054，默认路径烧满 30s 后只说"预算耗尽，
        请调大 timeout 或改用 force_fetcher='http'"——后者是对的，但它不是"建议"，
        而是"那样做你已经能拿到的答案"。"""
        server = MasterFetchServer()

        async def shell(url, **kwargs):        # empty 200 => JS shell => escalate
            return _result(content=[], content_type="text/html", fetcher_used="http")

        async def wedged(*args, **kwargs):
            await asyncio.sleep(5)
            return _result(content=[])

        async def fake_session(self, lock_wait=0.0):
            return "test-session"

        monkeypatch.setattr("dhole_mcp.fetcher.tcp_preflight",
                            lambda url, timeout=2.0: (True, ""))
        monkeypatch.setattr(MasterFetchServer, "_ensure_auto_session", fake_session)
        server.get = shell
        server.stealthy_fetch = wedged

        # 升级链那些参数按名字给：B5-13d 删掉的是时机那两位（`wait` 彻底没，
        # `network_idle` 变成浏览器层认出壳页才付的 5 秒，见 tests/test_render_settle_b5.py），
        # 照位置传的话这次会往后错两位，红成内部函数的形状而不是这条症状。
        out = await server._auto_escalate(
            "https://slow.test/page.html", extraction_type="markdown", css_selector=None,
            cache_ttl=0, offset=0, headless=True, real_chrome=False, proxy=None,
            timeout=2000, solve_cloudflare=False, block_webrtc=False, hide_canvas=False,
            extra_headers=None, useragent=None, cookies=None)
        assert out.error.startswith("timeout: the ")
        assert "status 200" in out.next_action, out.next_action
        assert "force_fetcher='http'" in out.next_action
        assert "http→stealthy(timeout)" == out.escalation_path


# ─── 2: 数据文档不进浏览器 ────────────────────────────────────────────

class TestADataDocumentIsNotAPage:

    @pytest.mark.parametrize("ct", ["text/xml", "application/xml",
                                    "application/rss+xml", "application/atom+xml",
                                    "application/json", "text/json"])
    def test_data_media_types_never_reach_a_render(self, ct):
        assert _never_escalate(_result(content_type=ct), "https://x.test/feed") is True

    @pytest.mark.parametrize("u", ["https://x.test/sitemap.xml",
                                   "https://x.test/api/v1/items.json",
                                   "https://x.test/blog/atom.xml",
                                   "https://x.test/news.rss"])
    def test_the_url_says_it_even_when_the_tier_did_not(self, u):
        """The tier worth refusing is usually the one that failed with no
        Content-Type header at all."""
        assert _never_escalate(_result(content_type="", content=[]), u) is True

    def test_html_still_escalates(self):
        assert _never_escalate(_result(content_type="text/html"),
                               "https://x.test/app") is False

    def test_xhtml_is_still_an_html_document(self):
        assert _never_escalate(_result(content_type="application/xhtml+xml"),
                               "https://x.test/doc") is False

    def test_svg_is_still_refused_as_an_image(self):
        assert _never_escalate(_result(content_type="image/svg+xml"),
                               "https://x.test/i.svg") is True


# ─── 3: URL 地图的行只留下有区别的东西 ────────────────────────────────

def _map_model(sitemap=True, pages=None):
    pages = pages or [CrawlPage(url=f"https://d.test/en/latest/mod{i}", depth=0,
                                status=0, content_ok=True, fetcher_used="sitemap",
                                page_type="sitemap", content=[], content_chars=0,
                                lastmod="2026-09-27", summary="sitemap entry")
                      for i in range(1000)]
    return CrawlResponseModel(start_url="https://d.test/", pages=pages,
                              pages_crawled=0, pages_discovered=len(pages),
                              discover_only=True, sitemap_used=sitemap)


class TestAMapIsNotOneThousandPageReports:

    def test_a_row_is_the_url_not_a_page_report(self):
        row = _map_model().model_dump()["pages"][0]
        assert row == {"url": "https://d.test/en/latest/mod0", "lastmod": "2026-09-27"}

    def test_the_map_no_longer_costs_five_times_its_payload(self):
        m = _map_model()
        wire = len(m.model_dump_json())
        urls = sum(len(p.url) for p in m.pages)
        assert wire / urls < 2.2, f"map overhead still {wire / urls:.1f}x"

    def test_a_dead_row_keeps_what_makes_it_interesting(self):
        """Compaction must not hide which URLs are broken."""
        m = _map_model(pages=[CrawlPage(url="https://d.test/gone", status=404,
                                        fetcher_used="http", content_ok=False,
                                        error="http_error_404")])
        row = m.model_dump()["pages"][0]
        assert row["status"] == 404 and row["error"] == "http_error_404"

    def test_a_bfs_map_row_does_not_restate_the_mode(self):
        """没有 sitemap 的站点，地图行同样不该重复 page_type。

        _classify 在 discover_only 下把每行的 page_type 写成 "discover_only"，
        其余赋值都在 `if not discover_only` 后面（crawl.py:1042），所以这一列
        从来区分不了任何两行，而顶层 discover_only:true 说的就是同一件事。
        sitemap 模式早就删了它，BFS 没删：实测 47 条的地图每行 204 字符、其中
        28 字符是这一列 —— 1000 条的站就是 28KB 的重复。
        """
        m = _map_model(sitemap=False, pages=[
            CrawlPage(url="https://d.test/a", status=200,
                      page_type="discover_only", fetcher_used="http",
                      content_ok=True, title="A",
                      summary="200 OK · 10.8KB html · http"),
            CrawlPage(url="https://d.test/b", status=404,
                      page_type="discover_only", fetcher_used="http",
                      error="http_error_404")])
        rows = m.model_dump()["pages"]
        assert all("page_type" not in r for r in rows), rows
        # 而"哪一行值得看"必须还在：状态与错误是判定，不属于被上收的列
        assert rows[1]["status"] == 404
        assert rows[1]["error"] == "http_error_404"
        assert m.model_dump()["discover_only"] is True

    def test_the_hoist_cannot_leak_into_content_mode(self):
        """地图之外 page_type 是真判定（article / list / js_shell / fallback）。"""
        out = CrawlResponseModel(start_url="https://d.test/", pages=[
            CrawlPage(url="https://d.test/a", status=200, page_type="list",
                      content=["x"], content_chars=1)], pages_crawled=1)
        assert out.model_dump()["pages"][0]["page_type"] == "list"

    def test_content_mode_is_untouched(self):
        out = CrawlResponseModel(start_url="https://d.test/",
                                 pages=[CrawlPage(url="https://d.test/a",
                                                  content=["hi"], content_chars=2)],
                                 pages_crawled=1)
        row = out.model_dump()["pages"][0]
        assert {"status", "is_truncated", "next_offset", "content"} <= set(row)


# ─── 3b: 体积闸第一次量到地图行（体验修复刀3-C3）────────────────────────

# docs.vllm.ai 那一形状：76 字符的 URL + 一颗 lastmod，序列化后 117 字符/行。
_LONG_PATHS = [f"/en/latest/api/vllm/entrypoints/openai/chatml_{i:05d}.html"
               for i in range(1, 1201)]


def _fake_walk(paths):
    """`discover_sitemap` 的离线替身，口径抄 `sitemap.py:301-320`。

    先记 seen 再过 accept、被拒的计入 filtered_out、够 max_urls 即停并置 capped ——
    这四件事任一件走样，"预算花在哪些行上"就与真机不同。不打真 socket：这一族
    回环用例本身依赖套件顺序才绿（台账 #41），而 C3 判的是账怎么算、怎么出口。
    """
    def _discover(start_url, *, http_get, max_urls, accept=None):
        from dhole_mcp.sitemap import SitemapResult, SitemapURL
        res = SitemapResult(sitemaps_used=[f"{start_url}/sitemap.xml"], via="direct")
        seen: set[str] = set()
        for path in paths:
            u = f"{start_url.rstrip('/')}{path}"
            if u in seen:
                continue
            seen.add(u)
            if accept is not None and not accept(u):
                res.filtered_out += 1
                continue
            res.urls.append(SitemapURL(url=u, lastmod="2026-09-27"))
            if len(res.urls) >= max_urls:
                res.capped = len(seen) < len(paths)
                break
        return res
    return _discover


@pytest.fixture
def big_sitemap(monkeypatch):
    monkeypatch.setattr("dhole_mcp.sitemap.discover_sitemap", _fake_walk(_LONG_PATHS))


class TestTheCharBudgetPricesMapRows:
    """`max_total_chars` 从前只累加 `content_md`，而地图行的 content 恒为空。

    现场（2026-10-09 真实使用模拟 · 剧本卡 3）：一次默认预算的 web_discover 交回
    116,173 字符（1,000 行 / ~32k token），信封里 `truncated_by_total_chars:false`，
    预算 60,000 —— 一道对某类输出**从不应战**的闸，比没有闸更坏，因为它让调用方
    以为自己设过上限。
    """

    @pytest.mark.asyncio
    async def test_the_default_budget_now_bounds_a_map(self, big_sitemap):
        r = await MasterFetchServer().web_crawl(
            "https://d.test", max_pages=10, sitemap="on", discover_only=True)
        shipped, total = len(r.pages), r.pages_discovered
        assert r.truncated_by_total_chars is True, r.summary
        assert total == 1000, "地图的大小不该被预算改变"
        assert shipped < total, "预算又躺平了"
        rows = sum(len(crawl_mod._map_row_dict(p, sitemap_used=True)) for p in r.pages)
        assert rows <= 60_000, f"序列化后的行超出了调用方设的预算：{rows:,}"
        # 四件事作为一个整体出口：旗子、总量、游标、散文里的数。缺一件就是静默截断。
        assert r.next_resume_token.endswith(f":{shipped}"), r.next_resume_token
        assert f"{shipped} of {total} map row(s) shipped" in r.summary, r.summary
        assert f"held {total - shipped} row(s) back" in r.summary, r.summary
        # 被砍的行不是丢了：计划里存的是整张图（前半 + 尾巴）。
        assert len(crawl_mod.plan_load(r.plan_id)) == total, (
            "游标指向的计划只存了交出去的那些行 —— 续页将无行可续")

    @pytest.mark.asyncio
    async def test_a_budget_of_zero_means_no_cap_and_says_no_lie(self, big_sitemap):
        r = await MasterFetchServer().web_crawl(
            "https://d.test", max_pages=10, sitemap="on", discover_only=True,
            max_total_chars=0)
        assert len(r.pages) == 1000, len(r.pages)
        assert r.truncated_by_total_chars is False
        assert "held" not in r.summary, r.summary
        # 0 是"这一轮不要帽"的合法写法（与 nested_max=0 同一拼法），所以 580KB 是
        # 调用方自己点的菜 —— 但这一句必须说得出重量，不然"不要帽"读起来像"没有多少"。
        assert "map capped at 1000 URLs" in r.summary, r.summary

    @pytest.mark.asyncio
    async def test_the_prose_slash_the_wire_still_fits_the_budget(self, big_sitemap):
        """预算数行，信封自己也要能被同一句话解释 —— 而不是"设了 60,000 收到 90,000"。"""
        r = await MasterFetchServer().web_crawl(
            "https://d.test", max_pages=10, sitemap="on", discover_only=True)
        whole = len(r.model_dump_json())
        assert whole <= 60_000 + 4_000, (
            f"整封回执 {whole:,} 字符，预算 60,000：信封的散文把自己挤出去了")


# ─── 4: crawl 的分页契约 ─────────────────────────────────────────────

def _article_html(chars: int) -> str:
    body = " ".join(f"sentence number {i} about testing extraction limits"
                    for i in range(chars // 48 + 1))
    return f"<html><head><title>T</title></head><body><article><p>{body}</p></article></body></html>"


class TestACutPageSaysItWasCut:

    def test_the_extractor_reports_the_cut_it_made(self):
        md, kind, ok, cut = _classify_and_extract(
            _article_html(4000), "https://d.test/a", "https://d.test/", None, 500)
        assert len(md) == 500 and cut is True

    def test_a_short_page_is_not_reported_as_cut(self):
        *_, cut = _classify_and_extract(_article_html(200), "https://d.test/a",
                                        "https://d.test/", None, 5000)
        assert cut is False

    @pytest.mark.asyncio
    async def test_a_capped_page_advertises_more(self, monkeypatch):
        """旧行为：is_truncated 恒为 false —— 抽取函数先裁到上限，外层再比长度就不
        可能成立，于是同一行 summary 写着 truncated 而分页字段说"没有更多了"。"""
        monkeypatch.setattr("dhole_mcp.crawl.check_robots",
                            lambda *a, **k: {"allowed": True, "crawl_delay": 0,
                                             "disallow": []}, raising=False)

        class _Srv:
            async def web_read(self, url, **kwargs):
                return _result(url=url, content=[_article_html(4000)],
                               content_type="text/html", fetcher_used="http",
                               content_ok=True, summary="200 OK · big · http")

        out = await crawl_mod.web_crawl(_Srv(), "https://d.test/", max_pages=1,
                                          max_depth=0, max_content_chars_per=500)
        page = out.pages[0]
        assert page.is_truncated is True
        assert page.next_offset == 500

    @pytest.mark.asyncio
    async def test_the_per_page_cap_clamp_is_named(self):
        class _Srv:
            async def web_read(self, url, **kwargs):
                return _result(url=url, content=["x" * 200], content_type="text/html",
                               fetcher_used="http", content_ok=True)

        out = await crawl_mod.web_crawl(_Srv(), "https://d.test/", max_pages=1,
                                          max_depth=0, max_content_chars_per=100)
        assert "clamped 100->500" in out.summary, out.summary

    @pytest.mark.asyncio
    async def test_an_http_error_page_is_not_called_a_js_shell(self):
        """实测：arxiv 对一个错误分类返回 400「Invalid archive or category」，
        crawl 的页却标 page_type=js_shell —— 那是把"URL 无效"说成"这站要 JS"。"""
        class _Srv:
            async def web_read(self, url, **kwargs):
                return _result(url=url, status=400, content=["<html><body>Invalid "
                                                             "archive or category</body></html>"],
                               content_ok=False, content_type="text/html",
                               error="http_error_400: server returned error status")

        out = await crawl_mod.web_crawl(_Srv(), "https://arxiv.org/list/cs.CO/recent",
                                          max_pages=1, max_depth=0)
        assert out.pages[0].page_type == "fallback", out.pages[0].page_type

    @pytest.mark.asyncio
    async def test_an_empty_success_is_still_called_a_js_shell(self):
        class _Srv:
            async def web_read(self, url, **kwargs):
                return _result(url=url, status=200, content=["<html><body></body></html>"],
                               content_ok=False, content_type="text/html",
                               error="js_shell_detected: x")

        out = await crawl_mod.web_crawl(_Srv(), "https://x.test/", max_pages=1,
                                          max_depth=0)
        assert out.pages[0].page_type == "js_shell"

    @pytest.mark.asyncio
    async def test_a_filter_that_ate_everything_says_so(self):
        """path_include 命中 0 条时，响应里没有任何地方承认有个过滤器运行过。"""
        class _Srv:
            async def web_read(self, url, **kwargs):
                html = ('<html><body><a href="/other/a">a</a>'
                        '<a href="/other/b">b</a></body></html>')
                return _result(url=url, content=[html], content_type="text/html",
                               fetcher_used="http", content_ok=True)

        out = await crawl_mod.web_crawl(_Srv(), "https://d.test/", max_pages=5,
                                          max_depth=1, path_include="/nowhere")
        assert len(out.pages) == 1
        assert "only the start page was kept" in out.summary, out.summary
        # 那句 blame 现在还带得上它声称的数：2 条候选、2 条被作用域挡下。
        assert "the 2 same-domain candidate link(s)" in out.summary, out.summary
        assert "(2 rejected" in out.summary, out.summary

    @pytest.mark.asyncio
    async def test_a_link_that_repeats_forty_times_is_one_rejection(self):
        """计数要说人话：一条导航链接在页里出现 40 次，被挡下的是**一个候选**，
        不是 40 个。写"40 条被作用域吃掉"的数字没人能拿来决策。"""
        class _Srv:
            async def web_read(self, url, **kwargs):
                html = '<html><body>' + '<a href="/other/x">x</a>' * 40 + '</body></html>'
                return _result(url=url, content=[html], content_type="text/html",
                               fetcher_used="http", content_ok=True)

        out = await crawl_mod.web_crawl(_Srv(), "https://d.test/", max_pages=5,
                                          max_depth=1, path_include="/nowhere")
        assert "the 1 same-domain candidate link(s)" in out.summary, out.summary
        assert "(1 rejected" in out.summary, out.summary


# ─── 10b: 甩锅的那句话要由计数选，不能由"参数在不在"选 ────────────────

class TestTheScopeBlameIsChosenByCounts:
    """体验修复刀1b-C1：`only the start page was kept - path_include 吃光了链接`
    这一句在三种"过滤器从未拿到过任何东西可吃"的形状里也照样开火，而它给出的解药
    （改模式）在这些形状上修不了任何东西。实测起点是我自己那次体验测试：
    `path_include=["/docs"]` 回来 `pages_discovered: 1` —— 那颗 1 已经证明没有链接
    可被过滤，响应却把原因端给了过滤器。

    反过来说，匹配器**没有** bug：`/docs` 保住的是它自己的子树，`path_allowed` 在
    两条作用域路径上是一致的（`:659`）。所以这一刀不动匹配器，只动那句 blame 的
    选题依据。
    """

    @staticmethod
    def _srv(html: str):
        class _Srv:
            async def web_read(self, url, **kwargs):
                return _result(url=url, content=[html], content_type="text/html",
                               fetcher_used="http", content_ok=True)
        return _Srv()

    @pytest.mark.asyncio
    async def test_a_page_with_no_same_domain_links_does_not_blame_the_filter(self):
        html = ('<html><body><a href="https://other.test/a">a</a>'
                '<a href="https://cdn.test/b">b</a></body></html>')
        out = await crawl_mod.web_crawl(self._srv(html), "https://d.test/docs/intro",
                                        max_pages=5, max_depth=1, path_include="/docs")
        assert len(out.pages) == 1
        assert "scoped out" not in out.summary, out.summary
        assert "0 same-domain candidate links" in out.summary, out.summary
        assert "is NOT the reason" in out.summary, out.summary

    @pytest.mark.asyncio
    async def test_max_depth_zero_does_not_blame_a_filter_that_never_ran(self):
        html = '<html><body><a href="/docs/a">a</a><a href="/docs/b">b</a></body></html>'
        out = await crawl_mod.web_crawl(self._srv(html), "https://d.test/docs/intro",
                                        max_pages=5, max_depth=0, path_include="/docs")
        assert len(out.pages) == 1
        assert "scoped out" not in out.summary, out.summary
        assert "max_depth=0 does no link discovery" in out.summary, out.summary

    @pytest.mark.asyncio
    async def test_links_the_scope_kept_are_not_blamed_on_the_scope(self):
        """候选全部通过作用域，却仍然只剩一页 —— 因为那条链接就是起始页自己。
        这时"过滤器吃光了链接"两头都不成立：既没吃，也没东西剩下可吃。"""
        html = '<html><body><a href="/">home</a></body></html>'
        out = await crawl_mod.web_crawl(self._srv(html), "https://d.test/",
                                        max_pages=5, max_depth=1, path_include="/")
        assert len(out.pages) == 1
        assert "scoped out" not in out.summary, out.summary
        assert "kept all 1 candidate link(s)" in out.summary, out.summary

    def test_the_counters_come_out_of_the_matcher_that_made_the_decision(self):
        """计数与判决必须同源：一句 blame 由另一套算法数出来，就会再次和它自己反悔。"""
        html = ('<html><body><a href="/docs/a">a</a><a href="/other/b">b</a>'
                '<a href="/docs/a">a again</a></body></html>')
        stats: dict = {}
        kept = crawl_mod.extract_same_domain_links(
            html, "https://d.test/x", "https://d.test/", ["docs"], None, stats=stats)
        assert [u for u, _t in kept] == ["https://d.test/docs/a"]
        assert stats == {"links_seen": 2, "links_rejected_by_scope": 1}, stats

    def test_a_caller_that_passes_no_stats_gets_the_same_links(self):
        """默认参数不许改变交付集合 —— 否则"加计数器"本身就是一次静默的行为改动。"""
        html = ('<html><body><a href="/docs/a">a</a><a href="/other/b">b</a>'
                '<a href="https://cdn.test/c.png">c</a></body></html>')
        plain = crawl_mod.extract_same_domain_links(
            html, "https://d.test/x", "https://d.test/", ["docs"])
        counted = crawl_mod.extract_same_domain_links(
            html, "https://d.test/x", "https://d.test/", ["docs"], None, stats={})
        assert plain == counted



# ─── 5 + 6: 空结果的两句话不能互相拆台 ───────────────────────────────

class TestAnEmptyRoundStopsContradictingItself:

    def test_the_off_topic_discard_does_not_tell_you_to_rephrase(self):
        err = ("Engines delivered 5 results, but all 5 matched no query term, so "
               "dhole dropped them rather than return noise.")
        # R02-004：那句出路读的是**本轮自己的**判定表（上线时 `_search_next_action` 一直
        # 拿到 `engine_detail`，这里补上同一形状）。没有一家答好时那句改成指向自检，
        # 那一支由 tests/test_a_way_out_that_names_a_key_the_caller_can_press.py 钉住。
        na = _search_next_action([], [], err, ["bing"],
                                 engine_detail=[{"name": "bing", "verdict": "healthy"}])
        assert "Rephrase" not in na, na
        assert "engines=[" in na

    def test_mode_neural_is_not_recommended_when_it_is_already_on(self):
        na = _search_next_action([], [], "", ["bing"], rerank_mode="neural")
        assert "try mode=neural" not in na, na
        assert "already the semantic matcher" in na

    def test_mode_neural_is_still_offered_when_it_is_off(self):
        assert "mode=neural" in _search_next_action([], [], "", ["bing"])

    def test_the_discarded_titles_are_shown_to_the_caller(self):
        """守卫本身是对的（本机网络确实会把 bing 的回答换成知乎首页），但它丢弃了
        什么必须可见，否则调用方只能选择信或不信。"""
        rows = [SearchResult(title="知乎 - 有问题，就会有答案",
                             url="https://www.zhihu.com/", snippet="", source="bing")]
        assert _filter_irrelevant_results(rows, "PagedAttention") == []
        assert _filter_irrelevant_results(
            [SearchResult(title="PagedAttention paper", url="https://arxiv.org/1",
                          snippet="", source="bing")], "PagedAttention") != []


# ─── 7 + 8 + 9: 错误的归因与不该出现的分数 ───────────────────────────

class TestAFailureBlamesTheRightThing:

    def test_a_file_that_was_found_is_not_a_missing_file(self):
        for err in ('.json file is not valid JSON: Expecting value: line 1 column 20',
                    '.yaml file is not valid YAML: mapping values are not allowed',
                    'PDF extraction failed: not_a_pdf: body does not start with %PDF',
                    "Unsupported file type '.exe'. Supported: .csv, .docx"):
            _, na, _ = _agent_hints(_result(status=0, content=[], error=err,
                                            fetcher_used="parse", content_ok=False))
            assert "places it tried" not in na, err
            assert "found and read" in na, err

    def test_a_file_that_was_not_found_still_gets_path_advice(self):
        """这一层只拿得到 message，拿不到形状 ⇒ 它不该自己写出路（R02-062）。

        原本这一格钉的是那句「Pass an absolute path, or pass cwd=...」在 next_action 里。
        那句现在由 server 按 `os.path.isabs` 写一次、进 `error`；这里改钉两件更硬的事：
        回执把读者送去 `error`（那里逐字列着被找过哪几处），以及这一层不许凭空发明一扇门
        —— 它看不到形状，替调用方决定「换成绝对形式」正是本条要修的错话。
        """
        _, na, _ = _agent_hints(_result(status=0, content=[],
                                        error="File not found: notes.md",
                                        fetcher_used="parse", content_ok=False))
        assert "places it tried" in na, na
        assert "error" in na.lower(), na
        assert "absolute path" not in na.lower(), na

    @pytest.mark.parametrize("probe,expected", [
        ("network_error: dns_failure (TCP preflight)", "dns_failure"),
        ("network_error: connection_refused (TCP preflight)", "connection_refused"),
        ("os error 10054 远程主机强迫关闭了一个现有的连接。", "connection_reset"),
        ("getaddrinfo failed", "dns_failure"),          # the old spellings still work
        ("Timeout 10000ms exceeded", "timeout"),
    ])
    def test_dholes_own_labels_are_recognised(self, probe, expected):
        assert classify_network_error(probe)[0] == expected

    def test_a_server_error_is_not_called_a_bot_block(self):
        _, na, _ = _agent_hints(_result(status=500, content=[],
                                        error="http_error_500: server returned error status",
                                        content_ok=False))
        assert "no parameter fixes it" in na, na
        assert "different parameters" not in na


class TestAnUnscoredExtractionSaysNull:

    def test_a_csv_parse_reports_no_quality_score(self):
        # quality_score's own field definition: "null for anything that is not a
        # PDF, because a 0.0 there reads as 'the extraction is maximally
        # garbled'". Every successful non-PDF parse was returning exactly that 0.0.
        _, payload = asyncio.run(MasterFetchServer()._dispatch(
            "doc_parse", {"file_path": "tests/gbk_sample.csv"}))
        assert payload["content_ok"] is True
        assert "quality_score" not in payload or payload["quality_score"] is None, \
            payload.get("quality_score")

    def test_a_local_pdf_keeps_its_real_score(self):
        _, payload = asyncio.run(MasterFetchServer()._dispatch(
            "doc_parse", {"file_path": "tests/background_checks.pdf"}))
        assert payload["quality_score"] is not None


# ─── 11: 错误页的链接是站点导航，不是本文引用 ─────────────────────────

class TestAnErrorPageHasNoCitationGraph:

    def test_links_are_dropped_with_the_page_they_came_from(self):
        r = _result(status=404, content=[], content_ok=False,
                    links={"citations": [{"url": "https://x.test/a", "text": "x"}],
                           "navigation": [{"url": "https://x.test/b", "text": "y"}],
                           "total_found": 53, "is_truncated": True})
        _apply_envelope(r)
        assert r.links == {}

    def test_a_good_page_keeps_its_links(self):
        r = _result(status=200, content=["real content" * 20], content_ok=True,
                    links={"citations": [{"url": "https://x.test/a", "text": "x"}]})
        _apply_envelope(r)
        assert r.links["citations"]


# ─── 13: 数组必须有可迭代的东西 ───────────────────────────────────────

class TestAnArrayFieldNeedsAContainer:

    def test_array_of_records_without_a_selector_is_rejected(self):
        with pytest.raises(ValueError, match="selector.*container"):
            _validate_schema_selectors({"quotes": {
                "type": "array",
                "properties": {"author": {"selector": "small.author"}}}})

    def test_the_same_shape_with_a_selector_is_the_documented_path(self):
        _validate_schema_selectors({"quotes": {
            "type": "array", "selector": "div.quote",
            "properties": {"tags": {"type": "array", "selector": "a.tag"}}}})

    def test_one_record_for_the_page_itself_stays_legal(self):
        _validate_schema_selectors({"page": {
            "properties": {"title": {"selector": "h1"}}}})

    def test_the_rule_follows_the_schema_into_its_children(self):
        with pytest.raises(ValueError, match="selector.*container"):
            _validate_schema_selectors({"outer": {
                "selector": "section",
                "properties": {"inner": {"type": "array",
                                         "properties": {"x": {"selector": "i"}}}}}})

    def test_a_rejected_schema_says_what_to_change(self):
        """重启实测抓到的收尾问题：这条响应的 next_action 是空串，而它恰恰是唯一
        一个修复完全在调用方自己手里、且确实一个请求都没发出去的错。"""
        _, payload = asyncio.run(MasterFetchServer()._dispatch(
            "web_read", {"url": "https://quotes.toscrape.com/", "schema": {
                "properties": {"quotes": {"type": "array",
                                          "properties": {"a": {"selector": "i"}}}}}}))
        assert payload["error"].startswith("schema validation error: ")
        assert "no request was made" in payload["next_action"], payload["next_action"]


# ─── 15: 读不到 robots.txt 不能被报成"合规通过"（去环境变量后实测）────

class TestRobotsLabelTellsTheTruth:

    @staticmethod
    def _fetch_with_verdict(monkeypatch, verdict):
        import dhole_mcp.server as srv_mod
        monkeypatch.setattr(srv_mod, "check_robots", lambda url, **kw: _already(verdict))
        monkeypatch.setattr(srv_mod, "robots_env_disabled", lambda: False)
        server = MasterFetchServer()

        async def fake_get(*a, **k):
            return _result(url="https://x.test/deny", content=["body"],
                           content_type="text/html", content_ok=True)
        server.get = fake_get
        return asyncio.run(server.web_read("https://x.test/deny", cache_ttl=0))

    def test_an_unreadable_file_is_not_reported_as_compliance(self, monkeypatch):
        out = self._fetch_with_verdict(
            monkeypatch, RobotsVerdict(True, "unavailable", "https://x.test/robots.txt"))
        label = out.metadata.get("robots", "")
        assert label == "not checked (robots.txt unreadable)", label
        assert "complied" not in label

    def test_a_read_file_that_allows_the_path_says_complied(self, monkeypatch):
        out = self._fetch_with_verdict(
            monkeypatch, RobotsVerdict(True, "allowed", "https://x.test/robots.txt"))
        assert out.metadata.get("robots") == "complied"

    def test_a_cached_receipt_cannot_state_the_present(self):
        """`robots` 说的是"这台机器现在怎样"，它是进程属性，不能被冻结在写入时刻。
        实测：去掉环境变量之后，命中缓存的页面仍标注 bypassed (DHOLE_IGNORE_ROBOTS=1,
        process-wide) —— 用它做合规审计的人会把一句过期陈述当成当前事实。"""
        from dhole_mcp.server import _ROBOTS_MODE

        stale = "bypassed (DHOLE_IGNORE_ROBOTS=1, process-wide)"
        r = _result(content=["body"], content_ok=True, metadata={"robots": stale})
        token = _ROBOTS_MODE.set("complied")
        try:
            out = asyncio.run(MasterFetchServer()._finalize_result(
                r, "https://x.test/p", "markdown", None, 0, 0, 5000))
        finally:
            _ROBOTS_MODE.reset(token)
        label = out.metadata["robots"]
        assert label.startswith("complied"), label
        assert stale in label, f"the fetch-time receipt was dropped: {label}"

    @pytest.mark.asyncio
    async def test_a_cache_hit_restates_the_present_mode(self, monkeypatch):
        """抓取路径会重算，缓存命中那条是直接 return、不经过 _finalize_result。
        实测：一份在 complied 下缓存的正文，被一次带 ignore_robots=true 的调用命中后
        仍然只写 complied —— 等于用一次过去的进程状态回答"现在这台机器怎样"。"""
        import dhole_mcp.server as srv_mod

        monkeypatch.setattr(srv_mod, "robots_env_disabled", lambda: False)
        monkeypatch.setattr(
            srv_mod, "check_robots",
            lambda url, **kw: _already(
                RobotsVerdict(True, "allowed", "https://receipt.test/robots.txt")))

        async def fake_get(self, url, **kwargs):
            return _result(url=url, content=["body text here"],
                           content_type="text/html", content_ok=True,
                           fetcher_used="http")

        monkeypatch.setattr(srv_mod.MasterFetchServer, "get", fake_get)
        server = srv_mod.MasterFetchServer()

        first = await server.web_read(url="https://receipt.test/p", cache_ttl=600)
        assert first.metadata.get("robots") == "complied", first.metadata
        second = await server.web_read(url="https://receipt.test/p", cache_ttl=600,
                                          ignore_robots=True)
        assert second.cached is True, "the second call must be the cache-hit path"
        label = second.metadata.get("robots")
        assert label.startswith("bypassed (ignore_robots=true)"), label
        assert "fetched under: complied" in label, label


async def _already(value):
    """check_robots is awaited; hand back a ready value."""
    return value


# ─── 14: 建议不能承诺管道拒绝做的事（重启实测新发现）──────────────────

class TestTheAdviceCannotPromiseWhatTheTierRefuses:

    def test_a_data_document_is_not_promised_a_render(self):
        r = _result(status=429, content=["Enable JavaScript and cookies to continue"],
                    content_type="text/html", content_ok=False, url="https://x.test/sitemap.xml",
                    error="js_shell_detected: page requires JavaScript rendering")
        _, na, _ = _agent_hints(r)
        assert "will NOT render" in na, na
        assert "auto-escalates" not in na

    def test_a_real_html_shell_is_still_promised_one(self):
        r = _result(status=200, content=[], content_type="text/html",
                    content_ok=False, url="https://x.test/app",
                    error="js_shell_detected: page requires JavaScript rendering")
        _, na, _ = _agent_hints(r)
        assert "auto-escalates to the stealthy browser" in na, na


# ============================================================================
# 来源：test_hardening_regressions.py
# ============================================================================
# 回归测试：日志与错误文本里的凭据脱敏 + 若干「开关其实没接线」的历史缺陷。

class _CredentialLeakingClient:
    """模拟 primp：异常文本里带出完整的带凭据代理 URL。

    两个入口都实现，因为 primp 本来就有两个：``get(url, ...)`` 是
    ``request("GET", url, ...)`` 的便捷写法，而抓取层统一走 ``request``（要发
    POST/PUT/HEAD）。只桩一个的话，测试会在与凭据无关的地方红。
    """

    def request(self, *args, **kwargs):
        raise RuntimeError(
            "connection failed via http://alice:s3cret@proxy.test:8080 "
            "(proxy rejected)"
        )

    def get(self, *args, **kwargs):
        return self.request(*args, **kwargs)


@pytest.mark.asyncio
async def test_retry_warning_redacts_proxy_credentials(caplog):
    """重试告警不得把 user:pass@ 形式的代理凭据写进日志。"""
    session = HTTPSession(stealthy_headers=False, retries=1, retry_delay=0)
    session._client = _CredentialLeakingClient()

    with caplog.at_level(logging.WARNING, logger="dhole_mcp.fetcher"):
        with pytest.raises(RuntimeError):
            await session.get("https://example.com/", retries=1)

    assert "s3cret" not in caplog.text, "代理密码泄漏到了日志"
    assert "alice:" not in caplog.text, "代理用户名密码片段泄漏到了日志"
    assert "CREDENTIALS_REDACTED" in caplog.text, "未走 redact_api_key 脱敏"


@pytest.mark.asyncio
async def test_retry_warning_still_mentions_the_url_and_attempt(caplog):
    """脱敏不能把告警本身抹掉——排障信息要保留。"""
    session = HTTPSession(stealthy_headers=False, retries=1, retry_delay=0)
    session._client = _CredentialLeakingClient()

    with caplog.at_level(logging.WARNING, logger="dhole_mcp.fetcher"):
        with pytest.raises(RuntimeError):
            await session.get("https://example.com/page", retries=1)

    assert "https://example.com/page" in caplog.text
    assert "attempt 1" in caplog.text


_KEYED_KEY = "tvly-dev-0123456789-SECRETDONOTLOG"

class TestKeyedEngineErrorBodyRedaction:
    """Keyed 引擎（Tavily/博查）的失败响应体不得把 key 原样写进日志。"""

    @pytest.mark.asyncio
    async def test_key_in_error_body_does_not_reach_log(self, monkeypatch, caplog):
        import httpx
        import logging

        from dhole_mcp import search_metasearch as m

        class _ServerError:
            status_code = 500
            text = f'{{"error":"unauthorized, key {_KEYED_KEY} rejected"}}'

        monkeypatch.setenv("DHOLE_TAVILY_API_KEY", _KEYED_KEY)
        monkeypatch.setattr(m, "_get_search_proxy", lambda: None)
        monkeypatch.setattr(httpx, "post", lambda *a, **k: _ServerError())

        with caplog.at_level(logging.DEBUG, logger="dhole_mcp.search_metasearch"):
            results, _status = await m.metasearch("q", 3, engines=["tavily"])

        assert results == []
        assert _KEYED_KEY not in caplog.text, "keyed 引擎的 key 泄漏到了日志"
        assert "500" in caplog.text, "脱敏不能把状态码这个排障线索一起抹掉"


# ---------------------------------------------------------------------------
# 缓存的作用域：共享状态不得跨请求上下文回放
# ---------------------------------------------------------------------------


class TestCacheContextIsolation:
    """缓存键必须覆盖「改变返回内容」的请求上下文。

    缓存是整机共享的（``~/.dhole/cache.db``），而键里只有
    URL + 抽取参数：带 cookies/auth 抓来的正文会被之后一次匿名抓取原样
    回放，且回放时 ``cached=True`` / ``content_ok=true``。同一份代码为
    ``actions`` 做了「绕过缓存」，却没为凭据做同样处理。
    """

    def test_plain_request_keeps_the_old_empty_fingerprint(self):
        """默认请求指纹为空 —— 修复不能把已有缓存一次性作废。"""
        from dhole_mcp.server import _cache_context

        assert _cache_context({}) == ""
        assert _cache_context({
            "cookies": None, "extra_headers": None, "useragent": None, "proxy": None,
            "include_media": False, "include_links": False,
        }) == ""

    def test_credentials_and_content_flags_change_the_fingerprint(self):
        from dhole_mcp.server import _cache_context

        base = _cache_context({})
        cookies = _cache_context({"cookies": [{"name": "session", "value": "abc"}]})
        headers = _cache_context({"extra_headers": {"Authorization": "Bearer x"}})
        media = _cache_context({"include_media": True})
        assert len({base, cookies, headers, media}) == 4
        # 同一份凭据 → 同一指纹，否则缓存永不命中
        assert cookies == _cache_context({"cookies": [{"name": "session", "value": "abc"}]})
        # header 顺序不该影响指纹
        assert _cache_context({"extra_headers": {"A": "1", "B": "2"}}) == \
            _cache_context({"extra_headers": {"B": "2", "A": "1"}})

    def test_cache_key_separates_contexts(self):
        from dhole_mcp.cache import _cache_key

        url = "https://example.com/private"
        assert _cache_key(url, "markdown") != _cache_key(url, "markdown", ctx="deadbeef")

    @pytest.mark.asyncio
    async def test_credentialed_body_is_not_served_to_an_anonymous_fetch(self, temp_dir):
        from dhole_mcp.cache import get_cached, set_cached

        url = "https://example.com/private"
        await set_cached(url, "markdown", ["private body"], 200,
                         cache_dir=temp_dir, ctx="A")
        # 匿名请求（ctx=""）看不到带凭据写入的正文
        assert await get_cached(url, "markdown", cache_dir=temp_dir) is None
        hit = await get_cached(url, "markdown", cache_dir=temp_dir, ctx="A")
        assert hit is not None and hit["content"] == ["private body"]

    def test_request_context_decorator_sets_the_fingerprint(self):
        """接线测试：web_read 的请求上下文装饰器负责把指纹放进 ContextVar。

        只看纯函数不够——指纹必须真的在调用期间生效，否则缓存键永远为空。
        """
        import asyncio

        from dhole_mcp import server as server_mod

        captured: dict = {}

        @server_mod._smart_fetch_request_context
        async def fake(self, url="", urls=None, extraction_type="markdown",
                       css_selector=None,
                       cache_ttl=3600, force_fetcher=None, headless=True,
                       real_chrome=False, wait=0, proxy=None, timeout=30000,
                       network_idle=False, solve_cloudflare=True, block_webrtc=True,
                       hide_canvas=True, extra_headers=None, useragent=None, cookies=None,
                       offset=0, max_content_chars=None, pages=None, password=None,
                       focus=None, actions=None, include_media=False, include_links=False,
                       max_links=None, ignore_robots=False, schema=None):
            captured["ctx"] = server_mod._CACHE_CTX.get()

        asyncio.run(fake(None, url="https://x/y", cookies=[{"name": "s", "value": "1"}]))
        credentialed = captured["ctx"]
        assert credentialed != ""

        asyncio.run(fake(None, url="https://x/y"))
        assert captured["ctx"] == "", "指纹必须随调用重置，不能泄漏到下一条请求"

        # 同一份 cookies → 同一个指纹（缓存仍能命中）
        asyncio.run(fake(None, url="https://x/y", cookies=[{"name": "s", "value": "1"}]))
        assert captured["ctx"] == credentialed

    @pytest.mark.asyncio
    async def test_finalize_result_passes_the_context_to_the_cache(self, monkeypatch):
        """写侧接线：_finalize_result 必须把当前指纹交给 set_cached。"""
        from dhole_mcp import server as server_mod

        seen: dict = {}

        async def fake_set_cached(url, extraction_type, content, status, css_selector,
                                  ttl, **kwargs):
            seen["ctx"] = kwargs.get("ctx")
            seen["url"] = url

        monkeypatch.setattr(server_mod, "set_cached", fake_set_cached)
        srv = server_mod.MasterFetchServer(cache_ttl=3600)
        result = server_mod._with_agent_hints(server_mod.ResponseModel(
            url="https://example.com/x", status=200, content=["hello"],
        ))
        token = server_mod._CACHE_CTX.set("ctx123")
        try:
            await srv._finalize_result(result, "https://example.com/x", "markdown",
                                       None, 3600)
        finally:
            server_mod._CACHE_CTX.reset(token)
        assert seen.get("url") == "https://example.com/x"
        assert seen.get("ctx") == "ctx123"

    # ─── PDF 口令维度（14.3 的凭据修复漏了它）────────────────────────

    def test_pdf_password_changes_the_fingerprint(self):
        """口令改变"能不能解出正文"，因此必须进缓存指纹。

        14.3 把 cookies/headers/UA/proxy 四维补进了键，password 却仍是裸的：
        用 password= 解出来的 PDF 正文与同一 URL 的匿名行撞同一个键。
        """
        from dhole_mcp.server import _cache_context

        plain = _cache_context({})
        pw = _cache_context({"password": "s3cret"})
        assert plain == ""
        assert pw and pw != plain
        # 同一口令 → 同一指纹，否则带口令的缓存永不命中
        assert pw == _cache_context({"password": "s3cret"})
        # 明文口令不得出现在指纹里（指纹本身就是缓存键的一部分）
        assert "s3cret" not in pw
        assert _cache_context({"password": ""}) == ""
        assert _cache_context({"password": None}) == ""

    @pytest.mark.asyncio
    async def test_two_passwords_do_not_share_a_row(self, temp_dir):
        from dhole_mcp.server import _cache_context
        from dhole_mcp.cache import get_cached, set_cached

        url = "https://example.com/doc.pdf"
        a = _cache_context({"password": "alpha"})
        b = _cache_context({"password": "bravo"})
        assert a != b
        await set_cached(url, "markdown", ["alpha body"], 200, cache_dir=temp_dir, ctx=a)
        assert await get_cached(url, "markdown", cache_dir=temp_dir, ctx=b) is None
        assert (await get_cached(url, "markdown", cache_dir=temp_dir, ctx=a))["content"] == ["alpha body"]

    @pytest.mark.asyncio
    async def test_decrypted_pdf_body_is_not_served_to_a_passwordless_refetch(self, temp_dir):
        """端到端那一格的缓存侧：匿名请求不能读到别人用口令解出来的正文。"""
        from dhole_mcp.server import _cache_context
        from dhole_mcp.cache import get_cached, set_cached

        url = "https://example.com/financials.pdf"
        ctx = _cache_context({"password": "board-only"})
        await set_cached(url, "markdown", ["DECRYPTED CONFIDENTIAL"], 200,
                         cache_dir=temp_dir, ctx=ctx)
        assert await get_cached(url, "markdown", cache_dir=temp_dir) is None,             "口令解出的正文被匿名请求回放了"

    def test_password_flows_through_the_request_context_decorator(self):
        """接线：password 是真参数，必须真的进指纹（纯函数测试看不到这点）。"""
        import asyncio

        from dhole_mcp import server as server_mod

        captured: dict = {}

        @server_mod._smart_fetch_request_context
        async def fake(self, url="", password=None, pages=None, focus=None,
                       include_media=False, include_links=False, max_links=None,
                       css_selector=None, cookies=None, extra_headers=None,
                       useragent=None, proxy=None):
            captured["ctx"] = server_mod._CACHE_CTX.get()

        asyncio.run(fake(None, url="https://x/a.pdf", password="s3cret"))
        with_pw = captured["ctx"]
        assert with_pw != ""
        asyncio.run(fake(None, url="https://x/a.pdf"))
        assert captured["ctx"] == "", "指纹必须随调用重置"
        asyncio.run(fake(None, url="https://x/a.pdf", password="s3cret"))
        assert captured["ctx"] == with_pw

    @pytest.mark.asyncio
    async def test_legacy_pdf_rows_are_purged_once_and_nothing_else_dies(self, temp_dir):
        """升级只清唯一可能受影响的子集，其余命中照旧。

        整体作废缓存对这个工具有实代价：某些网络上被拦的页面再也抓不回来 ——
        不是"重下一次就好"（重排模型坚持发布方 sha256 校验是同一个道理：重下还可能
        下到不一样的东西）。
        """
        import aiosqlite

        from dhole_mcp import cache as cache_mod
        from dhole_mcp.cache import get_cached, set_cached

        await set_cached("https://x/a.pdf", "markdown", ["decrypted secret"], 200,
                         cache_dir=temp_dir, css_selector=None, content_type="application/pdf")
        await set_cached("https://x/other.pdf?v=2", "markdown", ["second pdf"], 200,
                         cache_dir=temp_dir)
        await set_cached("https://x/page.html", "markdown", ["normal page"], 200,
                         cache_dir=temp_dir)
        await set_cached("https://x/judged.html", "markdown", ["judged page"], 200,
                         cache_dir=temp_dir, content_type="text/html; charset=utf-8",
                         envelope={"content_ok": False, "substance": "nav_only",
                                   "substance_basis": "12 lines", "visible_text_chars": 36})

        # 假装这是升级前写的库：键版本退回 1
        db = await aiosqlite.connect(temp_dir / "cache.db")
        await db.execute("PRAGMA user_version=1")
        await db.commit()
        await db.close()
        cache_mod._db_initialized.clear()
        await cache_mod._ensure_db(temp_dir)

        assert await get_cached("https://x/a.pdf", "markdown", cache_dir=temp_dir) is None
        assert await get_cached("https://x/other.pdf?v=2", "markdown",
                                cache_dir=temp_dir) is None
        # v3 之后 `page.html` 是**受影响的子集**，不是误伤：那是一份没有实质判决的 HTML，
        # 回放路径会从 status + content 重算出一个 `content_ok=true`，而那一发从来没被判过
        # —— 留在缓存里就是一张没有判据的绿灯（本机实测 134 行 fetch 里 132 行缺那颗读数）。
        assert await get_cached("https://x/page.html", "markdown",
                                cache_dir=temp_dir) is None, "没判决的 HTML 行不许继续作答"
        # 而"带着判决的 HTML 行"不许被牵连：它交回的正是当初那一份，一个字都没重取。
        judged = await get_cached("https://x/judged.html", "markdown", cache_dir=temp_dir)
        assert judged is not None and judged["content"] == ["judged page"], "误伤了带判决的行"
        assert (judged.get("envelope") or {}).get("substance") == "nav_only"

        # 幂等：版本已到位后，新写的 PDF 行不能再被清掉
        await set_cached("https://x/new.pdf", "markdown", ["fresh pdf"], 200, cache_dir=temp_dir)
        cache_mod._db_initialized.clear()
        await cache_mod._ensure_db(temp_dir)
        fresh = await get_cached("https://x/new.pdf", "markdown", cache_dir=temp_dir)
        assert fresh is not None and fresh["content"] == ["fresh pdf"]



# ---------------------------------------------------------------------------
# 隐式域名加权：默认关闭
# ---------------------------------------------------------------------------


class TestSearchFeedbackIsGone:
    """search feedback 域名偏好整块删除（17.0）：旋钮注销、文件不再存在，
    也没有任何代码路径能再写它。"""

    def test_the_machinery_is_gone(self):
        from dhole_mcp import search

        assert not hasattr(search, "_feedback_file")
        assert not hasattr(search, "record_search_feedback")
        assert not hasattr(search, "_feedback_enabled")

    def test_the_knob_changes_nothing(self, monkeypatch, tmp_path):
        from pathlib import Path as _Path
        monkeypatch.setenv("DHOLE_SEARCH_FEEDBACK", "1")
        # 哨兵：任何向 search_feedback.json 的写入都会当场炸掉测试
        sentinel = tmp_path / "search_feedback.json"
        real_write_text = _Path.write_text

        def guard(self, s, *a, **kw):
            if self == sentinel:
                raise AssertionError(f"unexpected write to {self}")
            return real_write_text(self, s, *a, **kw)
        monkeypatch.setattr(_Path, "write_text", guard)
        assert sentinel.exists() is False


class TestTheRepairKnobsMoveNothing:
    """17.0 B2-3 把自愈那次重装整个删了，于是 15.x 的三个旋钮控制的是一件**已经不存在
    的事**：设了它们不该有第二种行为 —— 入口点照旧只打印、照旧返回 1，而那两个
    改包名/改索引的取值更不该出现在任何一句文案里（它们曾经决定"重装谁"）。"""

    @pytest.mark.parametrize("knob,value", [
        ("DHOLE_UPDATE_PACKAGE", "my-fork-name"),
        ("DHOLE_UPDATE_INDEX_URL", "https://mirror.example/simple"),
        ("DHOLE_NO_AUTO_REPAIR", "1"),
    ])
    def test_the_entry_point_behaves_the_same_with_the_stale_knob(
            self, monkeypatch, capsys, knob, value):
        import builtins
        import sys

        from dhole_mcp import cli

        monkeypatch.setenv(knob, value)
        real_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if name == "dhole_mcp.server":
                raise ModuleNotFoundError("No module named 'dhole_mcp.server'",
                                          name="dhole_mcp.server")
            return real_import(name, *args, **kwargs)

        # ops 在别的用例里已经被导入过；不清缓存就不会再执行它那行
        # `from dhole_mcp.server import …`，于是测不到坏安装这条路。
        monkeypatch.delitem(sys.modules, "dhole_mcp.ops", raising=False)
        monkeypatch.setattr(builtins, "__import__", _fake_import)
        rc = cli.main()
        captured = capsys.readouterr()
        text = captured.out + captured.err
        assert rc == 1, "坏安装必须非零退出，否则宿主以为服务起来了"
        assert "pip install" in text, text
        assert not captured.out, "诊断不许写 stdout（那是 MCP 协议通道）"
        for leaked in ("my-fork-name", "mirror.example"):
            assert leaked not in text, f"{leaked} 已经不作数了，却还印在文案里"

# ---------------------------------------------------------------------------
# 面向 agent 的措辞：抓来的正文是数据，不是指令
# ---------------------------------------------------------------------------


def test_instructions_mark_page_text_as_untrusted():
    """抓取结果与服务器自写的 next_action/summary 走同一条信道，容易被模型当成
    同一类「可信指令」。指令里必须显式声明页面正文是不可信数据。"""
    from dhole_mcp.server import DHOLE_INSTRUCTIONS

    low = DHOLE_INSTRUCTIONS.lower()
    assert "untrusted" in low
    assert "never instructions" in low


def test_instructions_do_not_overclaim_officialness():
    """措辞必须与实现一致：is_official 只对 gov/edu/github 为真。"""
    from dhole_mcp.server import DHOLE_INSTRUCTIONS

    assert "gov/edu/github" in DHOLE_INSTRUCTIONS


# ---------------------------------------------------------------------------
# 模型下载源：HF 失败时回退镜像
# ---------------------------------------------------------------------------


class TestRerankerDownloadEndpoints:
    """huggingface.co 在很多国内桌面被 hosts 钉死（本机就是：
    `127.0.0.1 huggingface.co`）。下载源没有回退，模型就永远下不回来，
    神经排序永久静默降级，且没有报错。"""

    def test_default_order_is_hf_then_mirror(self, monkeypatch):
        from dhole_mcp import reranker

        monkeypatch.delenv("DHOLE_HF_ENDPOINT", raising=False)
        monkeypatch.delenv("HF_ENDPOINT", raising=False)
        assert reranker._hf_endpoints() == \
            ["https://huggingface.co", "https://hf-mirror.com"]

    def test_env_override_pins_one_endpoint(self, monkeypatch):
        from dhole_mcp import reranker

        monkeypatch.setenv("DHOLE_HF_ENDPOINT", "https://my-mirror.internal/")
        assert reranker._hf_endpoints() == ["https://my-mirror.internal"]

    def test_urls_keep_the_pinned_revision(self, monkeypatch):
        """换端点不换内容：revision 固定，任何来源的字节一致。"""
        from dhole_mcp import reranker

        for name, model in reranker.MODELS.items():
            urls = reranker._model_urls(model, "model.onnx")
            assert len(urls) >= 2, name
            assert all(model.rev in u for u in urls), name
            assert all(u.endswith("/" + model.relpaths["model.onnx"]) for u in urls), name
            assert all(f"/{model.repo}/" in u for u in urls), name

    def test_default_bge_zh_is_the_int8_bilingual_model(self):
        """450MB fp32 太重：默认必须落在小一号的中英双语 int8 上。"""
        from dhole_mcp import reranker

        default = reranker.MODELS[reranker.DEFAULT_MODEL]
        assert default.name == "bge-zh"
        assert default.relpaths["model.onnx"].endswith("int8.onnx")
        assert default.approx_bytes < 300_000_000
        assert "zh" in default.label or "bilingual" in default.label

    def test_download_model_file_falls_back_to_the_next_endpoint(self, monkeypatch):
        """第一个端点失败必须继续试下一个，而不是直接放弃。"""
        from pathlib import Path

        from dhole_mcp import reranker

        tried: list[str] = []

        def _fail(url, dest):
            tried.append(url.split("/")[2])
            return False

        monkeypatch.setattr(reranker, "_download_file", _fail)
        assert reranker._download_model_file("vocab.txt", Path("x")) is False
        assert tried == ["huggingface.co", "hf-mirror.com"]




class TestServerCacheTtlFlagIsLive:
    """`dhole --cache-ttl N` 必须是真旋钮。

    它此前是死的：web_read 的默认值在函数定义时就把模块常量 DEFAULT_TTL 焊进了
    签名，self._cache_ttl 赋值后没有任何读取点。用户唯一能表达"别存这么久"的开关
    不生效，而 README 还列着它。
    """

    class _Stop(Exception):
        """读到 ttl 之后立刻停住 —— 绝不放行到真正的抓取路径（测试必须零网络）。"""

    def _read_ttl(self, monkeypatch, srv, **kw):
        import asyncio

        from dhole_mcp import server as server_mod
        seen: dict = {}

        async def fake_get(url, extraction_type, css_selector, **kwargs):
            seen["read_ttl"] = kwargs.get("ttl")
            return None

        async def stop(*a, **kwargs):
            raise self._Stop()

        monkeypatch.setattr(server_mod, "get_cached", fake_get)
        monkeypatch.setattr(server_mod.MasterFetchServer, "_auto_escalate", stop)
        monkeypatch.setattr(server_mod.MasterFetchServer, "_force_fetch", stop)
        with pytest.raises(self._Stop):
            asyncio.run(srv.web_read(url="https://example.com/x", **kw))
        return seen

    def test_instance_default_reaches_the_cache_lookup(self, monkeypatch):
        from dhole_mcp import server as server_mod
        srv = server_mod.MasterFetchServer(cache_ttl=99)
        seen = self._read_ttl(monkeypatch, srv)
        assert seen.get("read_ttl") == 99, "读取用的 TTL 应来自实例，而非被焊死的 DEFAULT_TTL"

    def test_explicit_argument_still_wins(self, monkeypatch):
        from dhole_mcp import server as server_mod
        srv = server_mod.MasterFetchServer(cache_ttl=99)
        assert self._read_ttl(monkeypatch, srv, cache_ttl=5).get("read_ttl") == 5

    def test_unset_flag_keeps_the_documented_hour(self, monkeypatch):
        from dhole_mcp.cache import DEFAULT_TTL
        from dhole_mcp import server as server_mod
        srv = server_mod.MasterFetchServer()
        assert srv._cache_ttl == DEFAULT_TTL
        assert self._read_ttl(monkeypatch, srv).get("read_ttl") == DEFAULT_TTL

    def test_zero_still_skips_the_cache_lookup(self, monkeypatch):
        """cache_ttl=0 = 完全绕开缓存，这条既有语义不能被哨兵改动弄丢。"""
        from dhole_mcp import server as server_mod
        srv = server_mod.MasterFetchServer()
        assert "read_ttl" not in self._read_ttl(monkeypatch, srv, cache_ttl=0)


class TestPdfPasswordReachesTheExtractorAndTheCache:
    """口令链的接线：选项 → _PDF_PASSWORD → extract_pdf(password=…) → 写缓存用带口令的指纹。

    这条验的是**接线**（用打桩，能指出"口令没传下去"这类错）；pdfplumber 真解密那份
    行为由 tests/test_pdf_real.py 用真加密字节验（pypdf 现场生成样本）。两条合起来
    才闭合：一条证"传到了"，一条证"传到了就真解得开"。
    """

    def test_option_flows_to_the_extractor_and_the_cache_key(self, monkeypatch):
        import asyncio

        from dhole_mcp import pdf_extractor
        from dhole_mcp import server as sm

        seen: dict = {}

        def fake_extract_pdf(body, extraction_type="markdown", pages=None,
                             password=None, include_media=False):
            seen["password"] = password
            # 真实现解开口令后 encrypted 是 False（加密标志只在"打开失败/被拒"时立起，
            # server 用它来决定还该不该走 OCR）—— 桩要跟真行为一致，否则测试在教错事。
            return pdf_extractor.PdfResult(
                content=["DECRYPTED BODY"], encrypted=False, content_ok=True)

        monkeypatch.setattr(pdf_extractor, "extract_pdf", fake_extract_pdf)

        writes: dict = {}

        async def fake_set_cached(url, extraction_type, content, status, css_selector,
                                  ttl, **kwargs):
            writes["ctx"] = kwargs.get("ctx")
            writes["content"] = content

        monkeypatch.setattr(sm, "set_cached", fake_set_cached)

        srv = sm.MasterFetchServer(cache_ttl=3600)
        pw_token = sm._PDF_PASSWORD.set("s3cret")
        ctx_token = sm._CACHE_CTX.set(sm._cache_context({"password": "s3cret"}))
        try:
            res = sm._extract_pdf_response(b"%PDF-1.4 fake", "application/pdf", 13,
                                           "https://x/a.pdf", "markdown", "http", 5.0)
            assert seen["password"] == "s3cret", "口令没传到 extract_pdf"
            assert sm._cache_context({"password": "s3cret"}) != ""
            asyncio.run(srv._finalize_result(res, "https://x/a.pdf", "markdown", None, 3600))
        finally:
            sm._PDF_PASSWORD.reset(pw_token)
            sm._CACHE_CTX.reset(ctx_token)

        assert writes.get("content") == ["DECRYPTED BODY"], "解密正文没进写缓存路径"
        assert writes.get("ctx") == sm._cache_context({"password": "s3cret"}), \
            "带口令解出的正文必须以带口令的指纹写入，否则匿名请求可以复读"


# ============================================================================
# 来源：test_live_audit_regressions.py
# ============================================================================
# 描述改写后的多角度实测（2026-09-22）挖出的两个缺陷。
# 重名消歧：_result → _result__live_audit_regressions

# ~3.6KB of filler before the real markup: anything the tier caps below this
# and the selector below can never match. Real pages look like this too — head,
# styles and scripts come first.
_PADDED_HTML = (
    "<html><head><style>"
    + ("/* framework css */" * 200)
    + "</style></head><body><main><article>"
    + "<h1 class='late-title'>Late Title</h1>"
    + "</article></main></body></html>"
)


def _stub_tier(monkeypatch, html: str, captured: dict) -> None:
    """Stand in for the fetch tiers, faithfully: they truncate to `max_chars`.

    Keyword-splat on purpose: this stub is here to talk about `max_chars`, and a
    hand-copied copy of the tier's 18 internal names would make every unrelated
    signature change in `server.py` red here (B5-13a's ladder did exactly that).
    """
    async def fake(
        self, url, extraction_type,
        max_chars: int = server_mod.MAX_CONTENT_CHARS,
        **_hardening,
    ) -> ResponseModel:
        captured["max_chars"] = max_chars
        body = html[:max_chars]
        result = ResponseModel(
            url=url, status=200, content=[body], fetcher_used="http",
            extracted_type="html", content_type="text/html",
            total_size_bytes=len(html), total_extracted_chars=len(body),
        )
        return server_mod._apply_chunking(result, max_chars=max_chars)

    monkeypatch.setattr(server_mod.MasterFetchServer, "_auto_escalate", fake)


def _fetch_schema(schema: dict, **kwargs) -> ResponseModel:
    server = server_mod.MasterFetchServer()
    return asyncio.run(server.web_read(
        url="https://example.com/padded", schema=schema, **kwargs,
    ))


class TestSchemaExtractionSeesTheWholeDocument:

    def test_return_cap_does_not_starve_the_selector(self, monkeypatch):
        """`max_content_chars` 是返回给调用方的上限，不该决定抽取器看到多少 HTML。"""
        captured: dict = {}
        _stub_tier(monkeypatch, _PADDED_HTML, captured)

        out = _fetch_schema(
            {"properties": {"title": {"selector": "h1.late-title"}}},
            max_content_chars=500,
        )

        assert json.loads(out.content[0])["title"] == "Late Title", (
            "抽取器只拿到了被 max_content_chars 截断的 HTML，选择器必然全部落空"
        )

    def test_the_tier_is_not_asked_for_a_starved_document(self, monkeypatch):
        captured: dict = {}
        _stub_tier(monkeypatch, _PADDED_HTML, captured)

        _fetch_schema(
            {"properties": {"title": {"selector": "h1.late-title"}}},
            max_content_chars=500,
        )

        assert captured["max_chars"] >= len(_PADDED_HTML), (
            "抓 HTML 的那一层被喂了调用方的返回上限"
        )

    def test_envelope_describes_the_json_not_the_html(self, monkeypatch):
        """回包的信封必须描述真正返回的 JSON；否则 next_action 会让人去翻一个不存在的长页。"""
        captured: dict = {}
        _stub_tier(monkeypatch, _PADDED_HTML, captured)

        out = _fetch_schema(
            {"properties": {"title": {"selector": "h1.late-title"}}},
            max_content_chars=500,
        )

        assert out.is_truncated is False
        assert out.next_offset == 0
        assert out.total_extracted_chars == len(out.content[0])

    def test_focus_never_filters_the_structured_json(self, monkeypatch):
        """描述里写明「schema 生效时 focus 被忽略」，重排 chunking 顺序后也得成立。"""
        captured: dict = {}
        _stub_tier(monkeypatch, _PADDED_HTML, captured)

        out = _fetch_schema(
            {"properties": {"title": {"selector": "h1.late-title"}}},
            focus="something the json does not contain",
            max_content_chars=500,
        )

        assert json.loads(out.content[0])["title"] == "Late Title"

    def test_all_empty_fields_are_not_a_success(self, monkeypatch):
        """schema 一个字段都没抽到 = 没做到，不能同时报 content_ok: true。"""
        captured: dict = {}
        _stub_tier(monkeypatch, _PADDED_HTML, captured)

        out = _fetch_schema(
            {"properties": {"missing": {"selector": "h1.not-on-this-page"}}},
            max_content_chars=500,
        )

        assert out.content_ok is False
        assert out.error.startswith("schema_no_match"), out.error

    def test_partial_match_stays_ok(self, monkeypatch):
        """只要有一个字段命中，就还是成功——不能因为可选字段空着就全盘否定。"""
        captured: dict = {}
        _stub_tier(monkeypatch, _PADDED_HTML, captured)

        out = _fetch_schema(
            {"properties": {
                "title": {"selector": "h1.late-title"},
                "missing": {"selector": "h1.not-on-this-page"},
            }},
            max_content_chars=500,
        )

        payload = json.loads(out.content[0])
        assert payload["title"] == "Late Title"
        assert out.content_ok is True


def _result__live_audit_regressions(**kwargs) -> ResponseModel:
    defaults = dict(
        status=200, content=["Hello world"], url="https://example.com",
        fetcher_used="http", content_type="text/html",
        total_size_bytes=1000, extracted_type="markdown",
    )
    defaults.update(kwargs)
    return ResponseModel(**defaults)


class TestEmptyErrorPagesAreNotJsShells:

    def test_empty_404_body_is_not_a_js_shell(self):
        """4xx 的空 body 是 HTTP 错误，不是「需要 JS 渲染」。"""
        assert _is_js_shell(_result__live_audit_regressions(status=404, content=[])) is False

    def test_empty_500_body_is_not_a_js_shell(self):
        assert _is_js_shell(_result__live_audit_regressions(status=503, content=[])) is False

    def test_empty_200_body_is_still_a_js_shell(self):
        """2xx 空 body 仍然要触发浏览器升级——不能把升级路径一起修掉。"""
        assert _is_js_shell(_result__live_audit_regressions(status=200, content=[])) is True

    def test_404_reports_the_status_not_the_shell(self):
        issue = _detect_content_issue(_result__live_audit_regressions(status=404, content=[]))
        assert issue.startswith("http_error_404"), issue

    def test_404_does_not_recommend_a_browser_escalation(self):
        result = _result__live_audit_regressions(status=404, content=[])
        result.error = _detect_content_issue(result)
        _summary, next_action, content_ok = _agent_hints(result)
        assert content_ok is False
        assert "stealthy" not in next_action, (
            "404 空 body 没有可渲染的内容，把 agent 送去跑浏览器升级是纯浪费"
        )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))


# ============================================================================
# 来源：test_audit_fixes.py
# ============================================================================
# Dhole 审查修复回归测试。

# ---------------------------------------------------------------------------
# P0: feed_read 崩溃修复
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feed_fetch_returns_dict_structured(mock_logger=None):
    """feed_read 分支应返回 dict structured_content（list 会 -32603）。"""

    srv = MasterFetchServer(cache_ttl=0)
    args = {"urls": ["https://example.com/feed"], "max_items": 3}

    # 真实的 FeedResult，不是一份手抄的替身：这一条测的就是"投影与模型一致"，
    # 而替身会把模型的新字段挡在测试之外（替身少写一格，投影就少读一格，两边一起
    # 过期 —— 与本文件注释里 `note` 溜过去的正是同一种漏法）。
    from dhole_mcp.feed import FeedResult

    _FakeResp = lambda: FeedResult(source_url="https://example.com/feed",  # noqa: E731
                                   source_title="t")

    with patch("dhole_mcp.feed.fetch_feeds", new=AsyncMock(return_value=[_FakeResp()])):
        with patch("socket.getaddrinfo",
                   return_value=[(2, 1, 6, "", ("93.184.216.34", 0))]):
            result = await srv.feed_read(urls=args["urls"], max_items=3)

    assert isinstance(result, list)
    assert isinstance(result[0], dict)
    assert "source_url" in result[0]
    assert "items" in result[0]
    # G19: the projection has to carry the discovery hop, or a caller that passed
    # a site page sees a feed URL it never named and no explanation why.
    assert "discovered_from" in result[0]
    # G23, and the durable form of it: this tool returns a hand-built dict, so any
    # model field not projected here does not exist for the caller. Comparing the
    # projection against FeedResult's own fields makes that invisible: a field
    # added to the model fails here until it is projected (the hand-written list
    # of names is what let `note` slip through).
    assert set(result[0]) == set(FeedResult.model_fields), (
        "feed_read 的投影与 FeedResult 的字段已经不一致："
        f"少 {set(FeedResult.model_fields) - set(result[0])}，"
        f"多 {set(result[0]) - set(FeedResult.model_fields)}")


@pytest.mark.asyncio
async def test_feed_fetch_rejects_internal_url():
    """feed_read 传内网 URL 应报错（SSRF 防护）。"""
    srv = MasterFetchServer(cache_ttl=0)
    with pytest.raises(ValueError, match="校验失败"):
        await srv.feed_read(urls=["http://127.0.0.1:8080/x"], max_items=3)


# ---------------------------------------------------------------------------
# P1: 重定向逐跳 SSRF 校验
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redirect_to_internal_rejected():
    """公网 URL 重定向到内网应被 SecurityError 拒绝。"""
    session = HTTPSession(stealthy_headers=False, retries=0)
    session._client = MagicMock()

    def fake_get(method, url, headers=None, timeout=None, follow_redirects=False, **kw):
        resp = MagicMock()
        resp.status_code = 302
        resp.headers = {"location": "http://127.0.0.1:8080/internal.png"}
        resp.content = b""
        resp.reason = "Found"
        resp.cookies = []
        resp.url = url
        return resp

    session._client.request = MagicMock(side_effect=fake_get)
    with pytest.raises(SecurityError, match="internal/private IP"):
        await session.get("https://example.com/x.png", follow_redirects=True)


@pytest.mark.asyncio
async def test_max_redirects_bounded():
    """重定向次数受 max_redirects 限制（不会无限跟随）。"""
    session = HTTPSession(stealthy_headers=False, retries=0)
    session._client = MagicMock()
    calls = {"n": 0}

    def fake_get(method, url, headers=None, timeout=None, follow_redirects=False, **kw):
        calls["n"] += 1
        resp = MagicMock()
        resp.status_code = 302
        resp.headers = {"location": f"http://127.0.0.1:1/hop{calls['n']}"}
        resp.content = b""
        resp.reason = "Found"
        resp.cookies = []
        resp.url = url
        return resp

    session._client.request = MagicMock(side_effect=fake_get)
    # allow_internal=True 让重定向目标通过校验；max_redirects=2 应限制为
    # 初始 + 2 跳 = 3 次请求，第 4 次不再跟随（返回 302 而非继续）
    r = await session.get("http://127.0.0.1:9/x", follow_redirects=True,
                          max_redirects=2, allow_internal=True)
    assert calls["n"] == 3, f"max_redirects=2 应产生 3 次请求，实际 {calls['n']}"
    assert r.status == 302, "超过 max_redirects 后应返回 3xx 而非继续跟随"


# ---------------------------------------------------------------------------
# P2: DNS 复查（默认开启）
# ---------------------------------------------------------------------------


def test_dns_recheck_rejects_internal_resolution(monkeypatch):
    """域名解析到内网应拒绝（默认开启，不需要设任何环境变量）。"""
    monkeypatch.delenv("DHOLE_SSRF_DNS_RECHECK", raising=False)
    sp = __import__("dhole_mcp.security", fromlist=["_tunnel_exempt_engaged"])
    assert sp._tunnel_exempt_engaged() is False
    with patch("socket.getaddrinfo",
               return_value=[(2, 1, 6, "", ("192.168.1.10", 0))]):
        with pytest.raises(SecurityError, match="resolves to internal"):
            validate_url("https://mycorp.internal/x")


def test_dns_recheck_on_by_default(monkeypatch):
    """默认开启（fail-closed）：agent 抓来的 URL 不受信任，内网解析是主路径。"""
    monkeypatch.delenv("DHOLE_SSRF_DNS_RECHECK", raising=False)
    sp = __import__("dhole_mcp.security", fromlist=["_tunnel_exempt_engaged"])
    assert sp._tunnel_exempt_engaged() is False


def test_retired_dns_recheck_knob_moves_nothing(monkeypatch):
    """17.0：``DHOLE_SSRF_DNS_RECHECK`` 已注销 —— 设成什么都不改变判定。

    当年这个旋钮能让一个解析到内网的主机名放行，等于把一道 SSRF 守卫交给一个
    环境变量；现在唯一的放宽路径是「探测到 fake-ip」时的自动判定，且只放宽 DNS
    复检这一层（见 test_fake_ip_auto.py）。整段都待在假 getaddrinfo 里跑：判定
    既然只来自探测，测试就必须把探测的答案钉死，否则红的会是本机网络。
    """
    sp = __import__("dhole_mcp.security", fromlist=["_tunnel_exempt_engaged"])
    with patch("socket.getaddrinfo",
               return_value=[(2, 1, 6, "", ("192.168.1.10", 0))]):
        sp.reset_fake_ip_probe()
        sp._DNS_CHECK_CACHE.clear()
        for value in ("0", "false", "off", "no"):
            monkeypatch.setenv("DHOLE_SSRF_DNS_RECHECK", value)
            assert sp._tunnel_exempt_engaged() is False, value
        with pytest.raises(SecurityError, match="resolves to internal"):
            validate_url("https://mycorp.internal/x")
    sp.reset_fake_ip_probe()


def test_dns_recheck_accepts_public_resolution():
    """解析到公网 IP 应放行。"""
    with patch("socket.getaddrinfo",
               return_value=[(2, 1, 6, "", ("93.184.216.34", 0))]):
        assert validate_url("https://example.com/x") == "https://example.com/x"


def test_dns_recheck_honors_hosts_file_pin(monkeypatch):
    """hosts 文件里显式钉住的域名放行：那是本机用户的故意决定（阻断/mirror/
    分流），攻击者改不了 hosts 文件，所以不该被当成攻击者的 DNS 答案。

    这台机器就把 github.com / huggingface.co 钉到 127.0.0.1。
    """
    sp = __import__("dhole_mcp.security", fromlist=["_hosts_file_pin", "_resolves_to_internal"])
    monkeypatch.setattr(sp, "_hosts_file_pin", lambda host: "127.0.0.1")
    sp._DNS_CHECK_CACHE.clear()
    with patch("socket.getaddrinfo",
               return_value=[(2, 1, 6, "", ("127.0.0.1", 0))]):
        assert sp._resolves_to_internal("pinned.example") is None
        assert validate_url("https://pinned.example/x") == "https://pinned.example/x"


def test_hosts_file_parser_reads_real_file(tmp_path, monkeypatch):
    """解析器本身：注释/多主机名/大小写/尾点。"""
    hosts = tmp_path / "hosts"
    hosts.write_text(
        "# comment line\n"
        "127.0.0.1   pinned.example  SECOND.example.\n"
        "0.0.0.0 ad.example # trailing comment\n"
        "malformed-line\n",
        encoding="utf-8",
    )
    sp = __import__("dhole_mcp.security", fromlist=["_hosts_file_pin"])
    monkeypatch.setattr(sp, "_hosts_file_path", lambda: str(hosts))
    monkeypatch.setattr(sp, "_hosts_cache", None)
    assert sp._hosts_file_pin("pinned.example") == "127.0.0.1"
    assert sp._hosts_file_pin("second.example") == "127.0.0.1"
    assert sp._hosts_file_pin("AD.EXAMPLE") == "0.0.0.0"
    assert sp._hosts_file_pin("not-pinned.example") is None

# ---------------------------------------------------------------------------
# P1: sitemap SSRF（深挖发现）
# ---------------------------------------------------------------------------


def test_robots_sitemap_internal_rejected(monkeypatch):
    """robots.txt 声明的内网 Sitemap 地址应被过滤。"""
    from dhole_mcp.sitemap import _robots_sitemaps

    robots_body = (
        b"User-agent: *\n"
        b"Sitemap: http://169.254.169.254/latest/meta-data/\n"
        b"Sitemap: https://example.com/real-sitemap.xml\n"
    )

    def fake_get(url):
        return (200, robots_body)

    result, checked = _robots_sitemaps("https://example.com/", fake_get)
    assert checked is True
    assert result == ["https://example.com/real-sitemap.xml"], \
        f"内网 sitemap 应被过滤，实际: {result}"


def test_robots_sitemap_accepts_public(monkeypatch):
    """公网 Sitemap 地址应保留。"""
    from dhole_mcp.sitemap import _robots_sitemaps

    robots_body = b"User-agent: *\nSitemap: https://example.com/sitemap.xml\n"

    def fake_get(url):
        return (200, robots_body)

    result, checked = _robots_sitemaps("https://example.com/", fake_get)
    assert result == ["https://example.com/sitemap.xml"]
