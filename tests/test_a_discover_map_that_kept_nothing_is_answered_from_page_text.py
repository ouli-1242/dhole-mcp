"""F5：`web_discover(search=)` 空手而归时，这一刀要替调用方把"内容级"那一问回答了。

缺陷的原文是一句人的话："0 条？那我 Ctrl+F 去了"（2026-10-11 全真模拟 S7）。
`search` 比的是 **URL 路径 + 已读到的标题**（wire 上 `web_crawl.search` 那颗描述就是这么
写的），而地图行只有这两样；手里攥着"timeout"这个词的人，问的其实是"哪一页讲超时"。
docs.python-requests.org 实测：地图几十条 URL，`search='timeout'` 一条不留，答案站在
Quickstart / Advanced 两页正文的锚点里 —— 没有任何一条 URL 拼得出那个词。返工那一轮
自己抬到 10 页 / depth 3，又被 29.7s 的时间帽掐断。

所以修法在**工具边界层**（`server.py::_discover_search_fallback`）：地图确有 URL 却一行
没留下时，用这一次调用**剩下**的时钟补一轮 `focus` 内容扫描。三条边界各钉一组：

* 空地图不补 —— 无物可读，"这站没链出来"那一句由 crawl 自己解释（`pages_discovered`
  正是"地图多大"，与"筛掉多少"是两件事，别替它编一次扫描）；
* 剩下的秒数不够走完一轮就不补，并把"没补"连同两个数一起说出口 —— 交回一份被同一口
  时钟砍过、读起来像"站点慢"的爬取，比空手更糟；
* 接管与否都要出声：内容行带着 `content`，那不是这颗工具承诺的东西，`summary` 必须
  自己承认这一点（`test_every_clamp_is_announced.py` 认的正是这一格）。

靶站是注入的 `web_crawl` 替身 + 一枚可控的钟（`dhole_mcp.server.now`）：这一刀判的是
**边界层的调度与记账** —— 何时补、补几页、花在多少秒上、结果怎么标。crawl 内部的筛法由
`test_a_sitemap_search_matches_urls_and_says_what_it_could_not.py` 那组钉着，不在这里。
"""

from __future__ import annotations

import pytest

from dhole_mcp.crawl import CrawlPage, CrawlResponseModel
from dhole_mcp.server import (DISCOVER_FALLBACK_MIN_MS, DISCOVER_FALLBACK_PAGES,
                              MasterFetchServer)

BASE = "https://requests.readthedocs.io"
MAP_ROWS = 42
HIT = f"{BASE}/en/latest/user/advanced/"
MISS = f"{BASE}/en/latest/community/"
API = f"{BASE}/en/latest/api/"


class _Clock:
    """可推进的钟：`web_discover` 用 `server.now()` 量"地图那一轮花了多少秒"。"""

    def __init__(self) -> None:
        self.t = 1_700_000_000.0

    def __call__(self) -> float:
        return self.t


def _row(url: str, *, content: str = "", error: str = "") -> CrawlPage:
    return CrawlPage(url=url, title=url.rsplit("/", 1)[-1],
                     content=[content] if content else [],
                     content_ok=not error, error=error,
                     status=-1 if error else 200,
                     page_type="" if error else "article")


def _map(rows: int = MAP_ROWS, **kw) -> CrawlResponseModel:
    """那一轮返回的形状：地图有 `rows` 条 URL，`search` 一行没留下。"""
    kw.setdefault("summary", f"search='timeout' filtered to 0 URL(s); crawled 0 page(s) "
                             f"at {BASE} (depth <= 2)")
    kw.setdefault("next_action", "search='timeout' matched none of the crawled pages. "
                                 "It matches URL path + title only - widen it, drop it, "
                                 "or pass focus= to prioritize relevant links.")
    return CrawlResponseModel(start_url=BASE, pages=[], pages_crawled=0,
                              pages_discovered=rows, discover_only=True, **kw)


def _scan(*items: tuple) -> CrawlResponseModel:
    pages = [_row(i[0], content=i[1], error=(i[2] if len(i) > 2 else ""))
             for i in items]
    return CrawlResponseModel(start_url=BASE, pages=pages, pages_crawled=len(pages),
                              pages_discovered=len(pages) + 5, discover_only=False,
                              summary=f"crawled {len(pages)} page(s) at {BASE} "
                                      f"(depth <= 2), {len(pages)} content_ok")


@pytest.fixture
def stub(monkeypatch):
    """`web_crawl` 的替身：按队列交出结果、记下参数，并让钟按配置往前走。

    `advance` 以秒计，`"map"` 是地图那一轮的开销 —— 剩下多少时钟由它决定，所以"够不够
    补一轮扫描"这条边界是量出来的，不是碰巧的。
    """
    box: dict = {"results": [], "calls": [], "clock": _Clock(),
                 "advance": {"map": 0.0, "scan": 0.0}}
    monkeypatch.setattr("dhole_mcp.server.now", box["clock"])

    async def _fake(self, url, **kw):
        box["calls"].append(kw)
        side = "map" if kw.get("discover_only") else "scan"
        box["clock"].t += box["advance"].get(side, 0.0)
        return box["results"].pop(0)

    monkeypatch.setattr(MasterFetchServer, "web_crawl", _fake)
    return box


def _sent(box: dict, n: int = -1) -> dict:
    return box["calls"][n]


class TestTheScanRunsWhenTheMapHadUrls:

    @pytest.mark.asyncio
    async def test_page_text_answers_the_question_the_map_could_not(self, stub):
        stub["results"] = [_map(), _scan((HIT, "… set timeout=(5, 300) …"),
                                         (MISS, "Contributing and code of conduct"),
                                         (API, "Requests API, reference only"))]
        r = await MasterFetchServer().web_discover(BASE, search="timeout")
        assert _sent(stub, -1)["discover_only"] is False
        assert _sent(stub, -1)["focus"] == "timeout"
        assert _sent(stub, -1)["max_pages"] == DISCOVER_FALLBACK_PAGES
        # 只留带词的那一行，而"读了几页、其中几页带词"两个数都在句子里
        assert [p.url for p in r.pages] == [HIT], [p.url for p in r.pages]
        assert "3 page(s) read, 1 of them carrying the word" in r.summary, r.summary

    @pytest.mark.asyncio
    async def test_the_downgrade_admits_itself_in_the_summary(self, stub):
        stub["results"] = [_map(), _scan((HIT, "the timeout parameter"))]
        r = await MasterFetchServer().web_discover(BASE, search="timeout")
        assert "answered by CONTENT, not by the map" in r.summary, r.summary
        assert "URL path and the page title" in r.summary, r.summary
        # 地图那一轮的账要用**数**活下来（"筛掉 0 行"这句话唯一的用处就是那几个数），
        # 而扫描自己那行统计不许被替身抹掉 —— 接管是追加说明，不是重写两轮的账。
        assert f"kept none of the {MAP_ROWS} URL(s) the map found" in r.summary, r.summary
        assert "crawled 1 page(s) at" in r.summary, r.summary

    @pytest.mark.asyncio
    async def test_the_adopted_rows_are_page_rows_not_map_rows(self, stub):
        stub["results"] = [_map(), _scan((HIT, "connect timeout"))]
        r = await MasterFetchServer().web_discover(BASE, search="timeout")
        assert r.discover_only is False, r.discover_only
        assert r.pages[0].content, "内容行不该被剥成正文为空"
        assert r.pages_crawled == 1

    @pytest.mark.asyncio
    async def test_a_content_answer_is_not_stored_as_a_map_plan(self, stub):
        """接管的是正文，不是清单：给它一个 plan_id 等于承诺一次没人要的续传。"""
        stub["results"] = [_map(), _scan((HIT, "read timeout"))]
        r = await MasterFetchServer().web_discover(BASE, search="timeout")
        assert r.plan_id == "", r.plan_id
        assert "Plan " not in (r.next_action or ""), r.next_action

    @pytest.mark.asyncio
    async def test_the_next_action_names_a_lever_the_caller_can_reach(self, stub):
        stub["results"] = [_map(), _scan((HIT, "timeout=(5, 300)"))]
        r = await MasterFetchServer().web_discover(BASE, search="timeout")
        assert "web_read(url=" in r.next_action, r.next_action
        assert "focus='timeout'" in r.next_action, r.next_action


class TestTheScanSpendsOnlyWhatTheClockLeft:

    @pytest.mark.asyncio
    async def test_the_remaining_clock_bounds_the_scan(self, stub):
        stub["advance"]["map"] = 5.0
        stub["results"] = [_map(), _scan((HIT, "timeout"))]
        await MasterFetchServer().web_discover(BASE, search="timeout", timeout=25000)
        sent = _sent(stub)
        assert sent["deadline_ms"] == 20000, sent
        assert sent["timeout"] == sent["deadline_ms"], "每页预算与整轮帽必须是同一口钟"
        assert sent["max_total_chars"] == 60000

    @pytest.mark.asyncio
    async def test_too_little_clock_left_runs_no_scan_and_says_so(self, stub):
        """地图把时钟花光了：宁可空手回来讲清楚，也不交回一份被同一口钟砍过的爬取。"""
        stub["advance"]["map"] = 22.0
        stub["results"] = [_map()]
        r = await MasterFetchServer().web_discover(BASE, search="timeout", timeout=25000)
        assert len(stub["calls"]) == 1, "剩下 3000ms 低于门槛还补扫描"
        assert "No content scan was run" in r.summary, r.summary
        assert "3000ms of its 25000ms clock" in r.summary, r.summary
        assert f"{DISCOVER_FALLBACK_MIN_MS}ms floor" in r.summary, r.summary
        assert "web_search" in (r.next_action or ""), r.next_action

    @pytest.mark.asyncio
    async def test_the_map_answer_survives_when_nothing_was_scanned(self, stub):
        stub["advance"]["map"] = 22.0
        stub["results"] = [_map()]
        r = await MasterFetchServer().web_discover(BASE, search="timeout", timeout=25000)
        assert r.pages == []
        assert r.pages_discovered == MAP_ROWS, "地图多大这件事不能被降级说明改掉"
        assert r.discover_only is True


class TestAnEmptyMapIsNotGivenAnInventedScan:

    @pytest.mark.asyncio
    async def test_a_map_that_held_nothing_stops_at_the_explanation_it_has(self, stub):
        """`pages_discovered` 是地图的大小。它是 0 就是"这站没链出来"，与筛掉无关。"""
        stub["results"] = [_map(rows=0, summary=f"no links found at {BASE}")]
        r = await MasterFetchServer().web_discover(BASE, search="timeout")
        assert len(stub["calls"]) == 1, stub["calls"]
        assert "answered by CONTENT" not in (r.summary or ""), r.summary
        assert "No content scan was run" not in (r.summary or ""), r.summary
        assert r.summary == f"no links found at {BASE}", "这一支一个字都不许多写"

    @pytest.mark.asyncio
    async def test_an_errored_call_does_not_start_a_second_fetch(self, stub):
        stub["results"] = [_map(rows=9, error="blocked by the site",
                                summary="crawl failed: blocked by the site")]
        r = await MasterFetchServer().web_discover(BASE, search="timeout")
        assert len(stub["calls"]) == 1, stub["calls"]
        assert r.error == "blocked by the site"

    @pytest.mark.asyncio
    async def test_a_map_that_kept_rows_is_left_alone(self, stub):
        """有行就没有降级这回事 —— 反向半边：不传 search 时一个字不许多写。"""
        stub["results"] = [CrawlResponseModel(
            start_url=BASE, pages=[_row(f"{BASE}/a/")], pages_crawled=1,
            pages_discovered=MAP_ROWS, discover_only=True, summary="mapped 1 URL(s)")]
        r = await MasterFetchServer().web_discover(BASE)
        assert len(stub["calls"]) == 1
        assert "search" not in (r.summary or "").lower(), r.summary
        assert r.plan_id, "地图照旧进计划"

    @pytest.mark.asyncio
    async def test_no_search_means_no_second_call_at_all(self, stub):
        stub["results"] = [_map()]
        await MasterFetchServer().web_discover(BASE)
        assert len(stub["calls"]) == 1, "没有关键词就没人问内容"


class TestAScanThatFindsNothingIsReportedAsAScan:

    @pytest.mark.asyncio
    async def test_no_content_hit_keeps_the_map_answer_and_adds_the_look(self, stub):
        stub["results"] = [_map(), _scan((MISS, "Contributing and code of conduct"),
                                         (API, "Requests API, reference only"))]
        r = await MasterFetchServer().web_discover(BASE, search="timeout")
        assert r.pages == [], "扫描没命中却把行交回来，等于替它们作证"
        assert r.discover_only is True
        assert "found none of it either" in r.summary, r.summary
        assert "A content scan of 2 page(s)" in r.summary, r.summary
        assert "web_search" in (r.next_action or ""), r.next_action
        assert r.plan_id == ""

    @pytest.mark.asyncio
    async def test_the_scan_own_error_is_quoted_not_swallowed(self, stub):
        stub["results"] = [_map(), CrawlResponseModel(
            start_url=BASE, pages=[], pages_crawled=0, pages_discovered=0,
            discover_only=False, error="robots.txt disallows every link",
            summary="crawled 0 page(s)")]
        r = await MasterFetchServer().web_discover(BASE, search="timeout")
        assert "robots.txt disallows every link" in r.summary, r.summary

    @pytest.mark.asyncio
    async def test_a_row_that_failed_to_read_is_not_counted_as_scanned(self, stub):
        stub["results"] = [_map(), _scan((MISS, "", "network_error: timeout"))]
        r = await MasterFetchServer().web_discover(BASE, search="timeout")
        assert "content scan of 0 page(s)" in r.summary, r.summary
