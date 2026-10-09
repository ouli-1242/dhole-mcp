"""一张被体积闸砍过的地图要能一行一行读回来（体验修复刀3-C3）。

C3 给地图上了预算，于是出现了一种新形状：**地图比这一页大**。这件事只有两种诚实
的收法 —— 要么交回一个真能续上的游标，要么别说自己被砍了。游标指向空无处比没有
游标更坏：调用方按它再问一次，拿到"计划到头了"，而那 440 行从来没有存在过。

所以这一族用例钉四件事，缺任一件即红：

1. 续页拿到的正是上一页没交出的那些行，顺序不变、不重叠、不重付（同一张地图上
   的每一行只出现一次 —— 这条与 `test_a_continuation_page_does_not_repay_the_whole_map.py`
   是同一个论点在不同器官上：翻页不许重新买已经读过的东西）。
2. 游标在 **MCP 表面**上够得着。`discover_only` 不在 web_crawl 的 wire 面上（17.0 §4
   把它变成了 `web_discover`），所以"用 web_crawl 加 discover_only=true 续图"是一句
   只在内层成立的话 —— 门外的调用方照着做会被 `_reject_unknown_args` 拒掉。C3 因此
   把 `resume_token` 与 `max_total_chars` 放上 web_discover 这颗工具，而不是留一句
   走不通的建议。**上一版这里踩空过一次**：只改了 crawl 的建议文案，全绿。
3. 续页的行只带 URL。每一列都在文档默认值上，所以地图序列化器把它们删了；
   `content_ok` 留在 false 是**真话**（这一页没打开过任何 URL），而不是像从前地图模式
   那样硬写成 true 来冒充"这 1,000 页都有内容"。lastmod 不在计划里，要说出口。
4. 谓词（`search` / `path_include` / `path_exclude`）在游标这一支**兑现**而不是陪跑；
   在抓取正文那一支则**具名拒绝** —— params.py 只许可这两种说法，没有第三种。

离线跑：`discover_sitemap` 用替身，口径抄 `sitemap.py:301-320`（先记 seen 再过 accept、
够 max_urls 即停）。不打真 socket，理由与 `test_usage_simulation_regressions.py` 同一份
（回环那组用例依赖套件顺序才绿，台账 #41）。
"""

from __future__ import annotations

import json

import pytest

from dhole_mcp import crawl as crawl_mod
from dhole_mcp.server import MasterFetchServer
from dhole_mcp.sitemap import SitemapResult, SitemapURL

BASE = "https://d.test"
_LONG = [f"/en/latest/api/vllm/entrypoints/openai/chatml_{i:05d}.html"
         for i in range(1, 1201)]
# /docs 子树与带关键词的尾巴放在帽（1,000）的那一边：C2 已经证明"筛在帽之前"，
# C3 要的是同一条形状在**续页**上也成立。
#
# 混一点：`/blog` 与 `/docs` 插在地图中间（都在帽之内），所以"续页上的谓词只兑现一半"
# 与"一条都不剩"两种形状都数得出来 —— 一整条 `/en/latest` 的尾巴会让 path_include
# 什么都不删，那条用例就分不清"筛过了"与"忘了筛"。
_PATHS = (_LONG[:100] + [f"/blog/post/{i}" for i in range(96)] + _LONG[100:400]
          + ["/docs/api/auth", "/docs/api/tokens", "/pricing", "/plans/compare"]
          + _LONG[400:1200])


def _fake_walk(paths, delay: float = 0.0):
    def _discover(start_url, *, http_get, max_urls, accept=None):
        import time as _t
        if delay:
            _t.sleep(delay)   # 让"走图之后时限已经过了"成为确定事件而不是运气
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
def site(monkeypatch):
    monkeypatch.setattr("dhole_mcp.sitemap.discover_sitemap", _fake_walk(_PATHS))


@pytest.fixture
def slow_site(monkeypatch):
    """走图本身把整轮时限花光：地图的每一行都"在手上"，但一行都没来得及交。"""
    monkeypatch.setattr("dhole_mcp.sitemap.discover_sitemap",
                        _fake_walk(_PATHS, delay=0.05))


def _urls(r):
    return [p.url for p in r.pages]


def _dump(r, limit: int = 400):
    """失败时只看该看的三格：断言一旦红了，读者要能当场读出响应说了什么。"""
    return json.dumps({"summary": r.summary, "next_action": r.next_action,
                       "error": r.error}, ensure_ascii=False)[:limit]


class TestTheCursorWalksTheHeldTail:
    """游标交回的必须是上一页没交出的那些行 —— 一行不多，一行不少。"""

    @pytest.mark.asyncio
    async def test_two_pages_are_the_map_with_no_overlap(self, site):
        srv = MasterFetchServer()
        p1 = await srv.web_discover(BASE, sitemap=True)
        assert p1.truncated_by_total_chars is True, p1.summary
        p2 = await srv.web_discover(BASE, resume_token=p1.next_resume_token)

        first = _urls(p1)
        second = _urls(p2)
        assert not (set(first) & set(second)), "续页重付了已经交出的行"
        assert second[0] != first[-1], "游标差一格：上一页的最后一行被数了两次"
        assert p2.next_resume_token == "", "地图到头了还发游标，下一次问会得到空页"
        assert "Map exhausted" in p2.next_action, p2.next_action
        assert len(first) + len(second) == p1.pages_discovered == 1000, (
            f"{len(first)}+{len(second)} 不等于地图的 {p1.pages_discovered}")

    @pytest.mark.asyncio
    async def test_a_tiny_budget_pages_the_whole_map_in_many_pages(self, site):
        """预算越小页越多，但**行数守恒**：这是"清单可续"的全部含义。

        每一页都带同一个预算 —— 预算是**按次**的（与 crawl 的其他旋钮同一口径），
        所以"第一页 3,000、后面几页默认 60,000"是两次不同的请求，不是一条续链。
        """
        srv = MasterFetchServer()
        seen = []
        pages = 0
        r = await srv.web_discover(BASE, sitemap=True, max_total_chars=3000)
        seen += _urls(r)
        cursor = r.next_resume_token
        while cursor:
            r = await srv.web_discover(BASE, resume_token=cursor,
                                       max_total_chars=3000)
            pages += 1
            assert not (set(seen) & set(_urls(r))), "重叠的一行"
            priced = sum(crawl_mod._map_row_cost(p, sitemap_used=False)
                         for p in r.pages)
            assert priced <= 3000, f"这一页 {priced:,} 字符，预算写的不是 3,000"
            seen += _urls(r)
            cursor = r.next_resume_token
        assert len(seen) == 1000, f"{len(seen)} 行到手，地图有 {r.pages_discovered} 行"
        assert pages >= 12, f"只翻了 {pages} 页：游标在半路掉了几行"

    @pytest.mark.asyncio
    async def test_a_continuation_row_carries_the_url_and_says_what_it_lost(self, site):
        srv = MasterFetchServer()
        p1 = await srv.web_discover(BASE, sitemap=True, max_total_chars=1500)
        p2 = await srv.web_discover(BASE, resume_token=p1.next_resume_token)
        row = p2.model_dump()["pages"][0]
        assert list(row) == ["url"], f"续页的行多说了它没查过的东西：{row}"
        assert p2.discover_only is True and p2.pages_crawled == 0
        assert "lastmod is not held in the plan" in p2.summary, p2.summary

    @pytest.mark.asyncio
    async def test_the_budget_the_caller_sets_is_the_budget_that_binds(self, site):
        """`max_total_chars` 在 web_discover 上从前根本不参与（转发时不传）。"""
        srv = MasterFetchServer()
        small = await srv.web_discover(BASE, sitemap=True, max_total_chars=1000)
        big = await srv.web_discover(BASE, sitemap=True, max_total_chars=40_000)
        assert len(small.pages) < len(big.pages), (
            f"{len(small.pages)} 行 / {len(big.pages)} 行：这颗旋钮在地图上还是死的")
        priced = sum(len(json.dumps(crawl_mod._map_row_dict(p, sitemap_used=True),
                                    separators=(",", ":"))) + 1
                     for p in big.pages)
        assert priced <= 40_000, f"调用方设了 40,000，这一页付了 {priced:,}"


class TestTheCursorIsReachableFromOutsideTheProcess:
    """游标在 MCP 表面上必须走得通 —— 只有内层能走的路不算一条路。

    `discover_only` 不在 web_crawl 的 wire 面上（17.0 §4 把它变成了 `web_discover`），
    所以"web_crawl(resume_token=…, discover_only=true)"这句建议在真机上只会撞到
    `_reject_unknown_args`。C2 在这一格踩过一次同样的空：只改了内层，全绿。
    """

    def _props(self):
        defs = {d["name"]: d for d in MasterFetchServer._TOOL_DEFS}
        return defs["web_discover"]["inputSchema"]["properties"]

    def test_the_map_door_carries_both_size_knobs(self):
        props = self._props()
        assert {"max_total_chars", "resume_token"} <= set(props), sorted(props)
        for key in ("max_total_chars", "resume_token"):
            assert props[key]["description"], f"{key} 上了门却没带一句说明"

    def test_the_wire_face_and_the_dispatch_face_are_one_table(self):
        from dhole_mcp.server import _TOP_LEVEL_ARGS
        assert set(self._props()) == set(_TOP_LEVEL_ARGS["web_discover"]), (
            "schema 上有的键 dispatch 会拒，或 dispatch 允许的键 schema 没承诺")

    @pytest.mark.asyncio
    async def test_an_outside_call_can_actually_page_the_map(self, site):
        """真门是 `_dispatch`：方法体收得到参数不等于门外够得着。

        `_reject_unknown_args` 站在 dispatch 之前，不在 `web_discover` 的方法签名上 ——
        只测方法调用的话，把 `resume_token` 从 `_TOP_LEVEL_ARGS` 里删掉这条用例照样绿，
        而真机上它会变成一句拒绝。C2 在这一格踩过一次同样的空（只测内层，全绿）。
        """
        srv = MasterFetchServer()
        p1 = await srv.web_discover(BASE, sitemap=True, max_total_chars=1500)
        out = await srv._dispatch("web_discover", {
            "url": BASE, "resume_token": p1.next_resume_token,
            "max_total_chars": 1500})
        payload = json.loads(out[0][0].text)
        assert not payload.get("error"), payload.get("error")
        assert payload["pages"], "门外这一次续页一行都没拿到"
        assert payload["discover_only"] is True
        first_from_wire = list(payload["pages"][0])
        assert first_from_wire == ["url"], f"门外的行形状与门内不同：{first_from_wire}"

    def test_the_advice_names_the_door_that_works(self):
        """建议文案不许指一条走不通的路（真人照做会撞墙，那面墙就是本次模拟的现场）。"""
        import inspect
        src = inspect.getsource(crawl_mod._sitemap_map)
        assert "web_discover(url=" in src, "游标建议又写回 web_crawl+discover_only 了"
        assert "discover_only=true) " not in src, "地图上那句 discover_only 在门外走不通"

    @pytest.mark.asyncio
    async def test_the_deadline_holds_rows_the_same_way(self, slow_site):
        """时间到点交出零行时，"少了多少"同样不能是个谜，而手上有的一行也不许丢。

        这一支从前是 `break` 之后一句不提，再往前是把两个原因塞进一个括号（"a
        repeat after normalization, **or** the deadline"）。`held` 这个数把它们分开：
        0 行交出、1,000 行在手上、游标指向第 0 行。
        """
        srv = MasterFetchServer()
        r = await srv.web_crawl(BASE, sitemap="on", discover_only=True,
                                deadline_ms=1)
        assert r.pages == [], _dump(r)
        assert r.truncated_by_time is True, _dump(r)
        assert "the deadline came before the first one" in r.summary, _dump(r)
        assert "normalization" not in r.summary, r.summary
        assert r.next_resume_token.endswith(":0"), r.next_resume_token
        assert r.pages_discovered == 1000, r.summary
        page = await srv.web_discover(BASE, resume_token=r.next_resume_token,
                                      max_total_chars=0)
        assert len(page.pages) == 1000, f"在手的行没被续页接住：{len(page.pages)}"
        assert page.next_resume_token == "", page.summary



class TestThePredicatesSurviveTheCursor:
    """谓词在续页上是 URL 谓词，所以必须兑现；在正文支上没作用对象，所以必须点名。"""

    @pytest.mark.asyncio
    async def test_search_on_a_map_page_keeps_only_the_rows_that_carry_it(self, site):
        srv = MasterFetchServer()
        p1 = await srv.web_discover(BASE, sitemap=True, max_total_chars=6000)
        page = await srv.web_discover(BASE, resume_token=p1.next_resume_token,
                                      path_include=["/en/latest"],
                                      max_total_chars=60_000)
        assert page.pages and all("/en/latest/" in u for u in _urls(page)), _dump(page)
        # 尾巴里确实有 /blog：谓词必须**删掉过**东西，否则这条用例分不清"筛过"与"忘了筛"
        assert "kept" in page.summary and "matched none" in page.summary, page.summary
        kept = _urls(page)
        again = await srv.web_discover(BASE, resume_token=p1.next_resume_token,
                                       search="chatml_0004", max_total_chars=60_000)
        assert all("chatml_0004" in u for u in _urls(again)), _dump(again)
        assert len(_urls(again)) < len(kept), "关键词没删掉任何一行：它在续页上没运行"

    @pytest.mark.asyncio
    async def test_a_filter_that_keeps_nothing_says_which_it_was(self, site):
        srv = MasterFetchServer()
        p1 = await srv.web_discover(BASE, sitemap=True, max_total_chars=1500)
        page = await srv.web_discover(BASE, resume_token=p1.next_resume_token,
                                      search="zzzznothing")
        assert page.pages == []
        assert "matched none" in page.summary, page.summary
        # "地图到头了"与"你的词在这段里一个都没有"是两句话，必须都能读出来
        assert "kept none of the" in page.next_action, page.next_action

    @pytest.mark.asyncio
    async def test_the_content_branch_refuses_them_by_name(self, site):
        srv = MasterFetchServer()
        p1 = await srv.web_discover(BASE, sitemap=True, max_total_chars=1500)
        r = await srv.web_crawl(BASE, resume_token=p1.next_resume_token,
                                search="chatml")
        assert r.error.startswith("invalid_request: search"), r.error
        assert r.pages == [], "拒绝的同时还把页交了出去"
        assert "web_discover" in r.next_action, r.next_action

    @pytest.mark.asyncio
    async def test_sitemap_on_a_resumed_map_is_refused_not_ignored(self, site):
        """续页读的是已存下的地图：这里没有第二张图可走，`sitemap=` 没有作用对象。"""
        srv = MasterFetchServer()
        p1 = await srv.web_discover(BASE, sitemap=True, max_total_chars=1500)
        r = await srv.web_discover(BASE, resume_token=p1.next_resume_token,
                                   sitemap=True)
        assert r.error.startswith("invalid_request: resume_token"), r.error
        assert r.pages == []


