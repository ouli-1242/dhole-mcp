"""第九轮实测：真实使用模拟（约 40 个调用）暴露的问题。

这一轮不是外部报告，是我按"用户会怎么用"去敲这个 MCP 时量出来的。每条都附了
实测证据，因为其中两条我最初的判断是错的，是测量纠正了我：

* 我怀疑 bing 的字面词守卫误杀了正常结果 —— 直接探引擎原始返回后确认守卫是对的
  （本机网络把 "PagedAttention" 劫持成了知乎首页）。守卫不动，只修它自相矛盾的
  next_action，并把被丢弃的标题透出来让调用方自己能判断。
* 我怀疑并发调用在抢浏览器锁 —— 实测 8.3s 浏览器 + 2.1s HTTP 并行互不影响，假设
  作废，没有改任何东西。

修好的东西：

1. 预算耗尽时，外层兜底比内层早几微秒开火，把内层已经拿到的诊断整个丢掉：同一个
   URL，force_fetcher='http' 5.0s 给出 "connection reset (os error 10054)"，默认
   路径烧满 30.0s 只留下 "timeout: 请调大 timeout"。兜底必须**迟到**。
   浏览器层超时还要带上 HTTP 层已经说过的那句话。
2. XML/JSON 升级到浏览器层后被 Chromium 的 XML viewer 覆盖：docs.vllm.ai/
   sitemap.xml 在 HTTP 层是 428KB 原文 / 0.6s，在浏览器层是 2,335,300 字符的
   viewer 外壳 / 5.4s，而 content_ok=true、page_type=xml 一切"正常"。
3. discover_only+sitemap 一次给出 151k token：1000 行 × 14 字段 = 353KB JSON 装
   70KB 的 URL（5.0 倍开销），且 max_pages=2 根本不参与 map 的上限。
4. crawl 页的 is_truncated 永远是 false（抽取函数已经先裁到上限，外层再比长度就不
   可能成立），而同一行的 summary 自己写着 truncated；max_content_chars_per 的钳
   制也不像 smart_fetch 那样说出来。
5. 空结果时 error 说"改措辞没用"，next_action 说"请改措辞或试 mode=neural"——后
   者在这次调用里已经开着。
7. 本地解析里"文件找到了但内容解析不了"（坏 JSON/坏 YAML/假 PDF/不支持扩展名）全
   都收到"检查你试过的路径"，把用户支去查一条已经解析成功的路径。
8. quality_score 的字段定义明写"不是 PDF 就是 null，因为 0.0 会被读成'抽取结果最大
   程度地乱码'"，而每个 .docx/.xlsx/.csv 成功解析都返回 0.0。
9. dns_failure / connection_reset 是 dhole 自己写的 snake_case 标签，分类器的正则
   只认识浏览器/OS 的拼写，于是它们落到 "unknown" 的通用建议；5xx 同样没有专属分支。
10. path_include 把所有链接过滤光时，响应里没有任何地方说有个过滤器运行过。
11. 404 页配 include_links 吐出 53 条站点导航链接，primary_source 指向一个搜索页。
12. discover_only/focus 等只在顶层、不在 options，而描述的写法引人放错地方（我就
    踩了）——这是有意的通道设计（test_options_are_canonical_g22 钉着），所以改描
    述而不是改管道。
13. 嵌套 properties + type:"array" 而漏写父 selector 时，静默产出"标量取首个 + 数
    组全页摊平"的混合体（作者只有第一条、tags 却是十条）。带 selector 时分组完全
    正确，所以这里缺的是一个报错，不是一种新行为。
"""

import asyncio

import pytest

import dhole_mcp.crawl as crawl_mod
from dhole_mcp.crawl import CrawlPage, CrawlResponseModel, _classify_and_extract
from dhole_mcp.errors import classify_network_error
from dhole_mcp.robots import Verdict as RobotsVerdict
from dhole_mcp.search import _filter_irrelevant_results, _search_next_action
from dhole_mcp.search import SearchResult
from dhole_mcp.server import (MasterFetchServer, ResponseModel, _agent_hints,
                              _apply_envelope, _never_escalate,
                              _validate_schema_selectors)


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

        out = await server._auto_escalate(
            "https://slow.test/page.html", "markdown", None, True, True, 0, 0,
            True, False, 0, None, 2000, False, False, False, False,
            None, None, None)
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
            async def smart_fetch(self, url, **kwargs):
                return _result(url=url, content=[_article_html(4000)],
                               content_type="text/html", fetcher_used="http",
                               content_ok=True, summary="200 OK · big · http")

        out = await crawl_mod.smart_crawl(_Srv(), "https://d.test/", max_pages=1,
                                          max_depth=0, max_content_chars_per=500)
        page = out.pages[0]
        assert page.is_truncated is True
        assert page.next_offset == 500

    @pytest.mark.asyncio
    async def test_the_per_page_cap_clamp_is_named(self):
        class _Srv:
            async def smart_fetch(self, url, **kwargs):
                return _result(url=url, content=["x" * 200], content_type="text/html",
                               fetcher_used="http", content_ok=True)

        out = await crawl_mod.smart_crawl(_Srv(), "https://d.test/", max_pages=1,
                                          max_depth=0, max_content_chars_per=100)
        assert "clamped 100->500" in out.summary, out.summary

    @pytest.mark.asyncio
    async def test_an_http_error_page_is_not_called_a_js_shell(self):
        """实测：arxiv 对一个错误分类返回 400「Invalid archive or category」，
        crawl 的页却标 page_type=js_shell —— 那是把"URL 无效"说成"这站要 JS"。"""
        class _Srv:
            async def smart_fetch(self, url, **kwargs):
                return _result(url=url, status=400, content=["<html><body>Invalid "
                                                             "archive or category</body></html>"],
                               content_ok=False, content_type="text/html",
                               error="http_error_400: server returned error status")

        out = await crawl_mod.smart_crawl(_Srv(), "https://arxiv.org/list/cs.CO/recent",
                                          max_pages=1, max_depth=0)
        assert out.pages[0].page_type == "fallback", out.pages[0].page_type

    @pytest.mark.asyncio
    async def test_an_empty_success_is_still_called_a_js_shell(self):
        class _Srv:
            async def smart_fetch(self, url, **kwargs):
                return _result(url=url, status=200, content=["<html><body></body></html>"],
                               content_ok=False, content_type="text/html",
                               error="js_shell_detected: x")

        out = await crawl_mod.smart_crawl(_Srv(), "https://x.test/", max_pages=1,
                                          max_depth=0)
        assert out.pages[0].page_type == "js_shell"

    @pytest.mark.asyncio
    async def test_a_filter_that_ate_everything_says_so(self):
        """path_include 命中 0 条时，响应里没有任何地方承认有个过滤器运行过。"""
        class _Srv:
            async def smart_fetch(self, url, **kwargs):
                html = ('<html><body><a href="/other/a">a</a>'
                        '<a href="/other/b">b</a></body></html>')
                return _result(url=url, content=[html], content_type="text/html",
                               fetcher_used="http", content_ok=True)

        out = await crawl_mod.smart_crawl(_Srv(), "https://d.test/", max_pages=5,
                                          max_depth=1, path_include="/nowhere")
        assert len(out.pages) == 1
        assert "only the start page was kept" in out.summary, out.summary


# ─── 5 + 6: 空结果的两句话不能互相拆台 ───────────────────────────────

class TestAnEmptyRoundStopsContradictingItself:

    def test_the_off_topic_discard_does_not_tell_you_to_rephrase(self):
        err = ("Engines delivered 5 results, but all 5 matched no query term, so "
               "dhole dropped them rather than return noise.")
        na = _search_next_action([], [], err, ["bing"])
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
            assert "paths that were tried" not in na, err
            assert "found and read" in na, err

    def test_a_file_that_was_not_found_still_gets_path_advice(self):
        _, na, _ = _agent_hints(_result(status=0, content=[],
                                        error="File not found: notes.md",
                                        fetcher_used="parse", content_ok=False))
        assert "paths that were tried" in na

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
            "parse", {"file_path": "tests/gbk_sample.csv"}))
        assert payload["content_ok"] is True
        assert "quality_score" not in payload or payload["quality_score"] is None, \
            payload.get("quality_score")

    def test_a_local_pdf_keeps_its_real_score(self):
        _, payload = asyncio.run(MasterFetchServer()._dispatch(
            "parse", {"file_path": "tests/background_checks.pdf"}))
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
            "smart_fetch", {"url": "https://quotes.toscrape.com/", "schema": {
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
        return asyncio.run(server.smart_fetch("https://x.test/deny", cache_ttl=0))

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

        first = await server.smart_fetch(url="https://receipt.test/p", cache_ttl=600)
        assert first.metadata.get("robots") == "complied", first.metadata
        second = await server.smart_fetch(url="https://receipt.test/p", cache_ttl=600,
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
