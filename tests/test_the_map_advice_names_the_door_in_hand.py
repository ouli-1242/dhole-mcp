"""地图那一停的出路要点名**手上这扇门**的旋钮（R02-083 · P3）。

现场（真机读数在 `review/.runtime/p1/r82_discover.json` 与 `r83_knobs.json`，本机靶站）：
`web_discover` 被 `max_pages` 截断时，回执的 `next_action` 是

    crawl stopped early (max_pages); re-run web_crawl with a higher max_pages / deadline_ms

而被"没有 sitemap"挡住时是

    No sitemap found. Re-run web_crawl with sitemap=false (or omit it) …

两句都不是"传不进去"（R02-082 那一族管的是那种），而是**跨门指点 + 把这趟称成 crawl**：
实测这颗门自己就收 `max_pages`（C3 通过）与时钟 `timeout=`（C5 通过），`deadline_ms` 它**不收**
（C2 拒），那颗旋钮住在 crawl 门上（C4 通过）。所以照建议做的那一发会换一扇门、换一种成本
（discover 一发 0 正文，crawl 每页抓正文），而本门的旋钮一直没被提出来。

同文件里已经有做对了的先例：`_no_sitemap_next_action` 的 clock_cut 那一支（crawl.py:1539-1546）
自己分清"timeout= 归 web_discover、deadline_ms 归 web_crawl"，注释写着「对它说抬 deadline_ms
等于把调用方支到一个它够不着的旋钮上」。这一把不是发明规矩，是把同一支没照做的补齐。

判据的形状：前四格走**真代码路径**（`_FakeSite` + `discover_only=True`，先例
`test_a_crawl_that_stops_early_leaves_a_way_back_in.py`），最后一格把规矩做成一般式——
地图回执里点名的每一颗 `xxx=` 都必须在 `web_discover` 的 wire 面上。第五格守旧面不被误伤：
正文那一支的 `deadline_ms` 建议**不许被顺手改掉**（那一扇门上它是唯一正确的时钟）。
"""

from __future__ import annotations

import asyncio
import re

from dhole_mcp import crawl as crawl_mod
from dhole_mcp.crawl import _no_sitemap_next_action
from dhole_mcp.server import ResponseModel, _TOP_LEVEL_ARGS

BASE = "https://inhand.test"
SEED = BASE + "/"


def _links(names):
    return ("<html><body>" + "".join(f'<a href="{h}">l</a>' for h in names)
            + "</body></html>")


class _FakeSite:
    def __init__(self, pages, delay=0.0):
        self.pages = pages
        self.delay = delay

    async def web_read(self, url, **kwargs):
        if self.delay:
            await asyncio.sleep(self.delay)
        return ResponseModel(
            url=url, status=200, content=[self.pages.get(url, "<p>body.</p>")],
            content_type="text/html", fetcher_used="http", content_ok=True)


def _map(max_pages=None, delay=0.0, deadline_ms=None):
    async def _go():
        site = _FakeSite({SEED: _links([f"/p{i}" for i in range(8)])}, delay=delay)
        kw = {"max_depth": 1, "cache_ttl": 0}
        if max_pages is not None:
            kw["max_pages"] = max_pages
        if deadline_ms is not None:
            kw["deadline_ms"] = deadline_ms
        return await crawl_mod.web_crawl(site, SEED, discover_only=True, **kw)
    return asyncio.run(_go())


def _named_knobs(text: str) -> list:
    return re.findall(r"\b([a-z][a-z0-9_]{2,})\s*=", text)


class TestAMapStopsEarlyNamesItsOwnLever:

    def test_the_pages_cap_advice_names_max_pages_on_this_door(self):
        r = _map(max_pages=3)
        assert r.truncated_by_max_pages is True, (r.summary, r.next_action)
        na = r.next_action
        assert "max_pages" in na, na
        assert not na.lower().startswith("crawl stopped"), (
            f"地图那一停被称成 crawl：{na}")

    def test_the_pages_cap_advice_does_not_send_the_caller_to_the_other_door_first(self):
        """换门可以作为第二条出路，但第一条必须是本门就有的那颗旋钮。"""
        r = _map(max_pages=3)
        na = r.next_action
        cap = na.find("max_pages")
        other = na.find("web_crawl")
        assert cap >= 0, na
        assert other < 0 or other > cap, (
            f"建议先把调用方支到 web_crawl，本门的 max_pages 却排在后面：{na}")

    def test_the_clock_stop_advice_names_the_map_door_s_own_clock(self):
        r = _map(max_pages=20, delay=0.05, deadline_ms=120)
        assert r.truncated_by_time is True, (r.summary, r.next_action)
        na = r.next_action
        assert "timeout=" in na, f"地图被时钟砍时要点名本门的时钟 timeout=：{na}"
        assert "deadline_ms" not in na, (
            f"discover 不收 deadline_ms（C2 实测被拒），建议不许点它：{na}")

    def test_the_no_sitemap_answer_names_the_knob_not_another_door(self):
        na = _no_sitemap_next_action({})
        assert "sitemap=" in na, na
        assert "web_crawl" not in na, f"没有 sitemap 那一停不该把地图调用支到别的门：{na}"

    def test_every_knob_named_by_a_map_receipt_exists_on_the_map_door(self):
        """一般式：地图回执的出路里点名的每一颗 `xxx=`，都得是 web_discover 收下的名字。"""
        allowed = set(_TOP_LEVEL_ARGS["web_discover"])
        seen = set()
        for r in (_map(max_pages=3), _map(max_pages=20, delay=0.05, deadline_ms=120)):
            for knob in _named_knobs(r.next_action):
                seen.add(knob)
                assert knob in allowed, (
                    f"地图的出路点了 {knob}=，而 web_discover 的 wire 面不收它"
                    f"（清单 {sorted(allowed)}）：{r.next_action}")
        assert seen, "一格都没扫到名字，说明判据读的是空文本"


    def test_the_char_cap_is_not_the_lever_that_bits_a_map_yet(self):
        """字符帽那一停在地图上**今天**走不到：两种形状都没有截到字符。

        记这条不是为了护住旧文案，而是把"门序"钉成事实：`max_pages=1`（60 行）那一发被**页帽**截，
        `max_pages=40`（30 行）那一发整张图走完，两种形状的 `truncated_by_total_chars` 都是 False
        —— 地图的行价比正文便宜得多，帽的地板（1,000）在地图够得着的行数里花不完。所以那一支的
        crawl 措辞本把**没动**：给一条地图上走不到的句子改文案是投机代码（§12 不许）。哪天门序或
        行价变了、字符帽真的截到地图，同一颗规矩（点名的每颗旋钮要在本门的 wire 面上）要在那里
        补一次，而这一格会先把那种变化报成"到得了"。
        """
        async def _go(n_pages, links):
            site = _FakeSite({SEED: _links([f"/page-with-a-fairly-long-name-{i}"
                                           for i in range(links)])})
            return await crawl_mod.web_crawl(site, SEED, discover_only=True, max_depth=1,
                                             max_pages=n_pages, max_total_chars=1_000,
                                             cache_ttl=0)
        for shape in (asyncio.run(_go(1, 60)), asyncio.run(_go(40, 30))):
            assert shape.truncated_by_total_chars is False, shape.summary
        # 两种形状各自停在哪一位也一起钉住，免得这条判据其实是"两个 flag 都没看"：
        # max_pages=1 那一发被页帽截（60 行只扩展了一页），max_pages=40 那一发整张地图走完。
        assert asyncio.run(_go(1, 60)).truncated_by_max_pages is True
        assert asyncio.run(_go(40, 30)).truncated_by_max_pages is False


class TestTheCrawlDoorKeepsItsOwnAdvice:
    """不许顺手把正文那一支也改了：那一扇门上 `deadline_ms` 是它唯一的整轮时钟。"""

    def test_a_content_crawl_still_names_its_deadline(self):
        async def _go():
            site = _FakeSite({SEED: _links([f"/p{i}" for i in range(8)])}, delay=0.05)
            return await crawl_mod.web_crawl(site, SEED, max_pages=20, max_depth=1,
                                              deadline_ms=120, cache_ttl=0)
        out = asyncio.run(_go())
        assert out.truncated_by_time is True, (out.summary, out.next_action)
        assert "deadline_ms" in out.next_action, out.next_action

    def test_a_content_crawl_still_offers_its_per_page_lever(self):
        """正文那一支的 `max_content_chars_per` 是对的 —— 那一扇门上它收，也确实管每页正文。

        要撞得动字符帽就给足正文：每页 ~6KB、帽 1,000 ⇒ 第一批之后就没预算了。
        """
        big = "<p>" + ("filler text " * 500) + "</p>"

        async def _go():
            site = _FakeSite({SEED: _links([f"/p{i}" for i in range(8)]),
                              **{f"{SEED}p{i}": big for i in range(8)}})
            return await crawl_mod.web_crawl(site, SEED, max_pages=20, max_depth=1,
                                             max_total_chars=1_000, cache_ttl=0)
        out = asyncio.run(_go())
        assert out.truncated_by_total_chars is True, (out.summary, out.next_action)
        assert "max_content_chars_per" in out.next_action, out.next_action
        assert out.next_action.lower().startswith("crawl stopped"), out.next_action

    def test_the_guard_refused_no_sitemap_answer_still_points_at_the_waiver(self):
        na = _no_sitemap_next_action({"guard_refused": True})
        assert "allow_private" in na, na
        assert "sitemap=" in na, na


class TestTheScopeClauseIsAssembledInOnePlace:
    """`, {scope_hint}` 那一处装配要在**真代码路径**上被两扇门各走到一次。

    来路：本文件前九格与常驻靶站都到不了这一处 —— 靶站首页那 7 条链接分属 7 个一段路径，
    `_scope_hint` 在那里永远交回空串，于是连接词从来没在真机或桩里到场过（评审记在 B-40）。
    P5 的返工正是这里长出来的：既有守卫
    `test_a_scope_suggestion_must_select_something_the_caller_does_not_already_have.py::
    test_the_two_break_sites_share_one_connector` 量到装配点从 2 处变成 3 处（把 map / 正文两支
    各写了一遍连接词），修法是 `lead + 连接词 + tail` 并回一处装配，不是把守卫的 2 改成 3。
    下面几格把并回来的那一处按形状钉住：推荐句、事实句、什么都不接。
    """

    MAJORITY = [f"/deep/d{i}" for i in range(6)] + ["/other/o1", "/other/o2"]
    ONE_TREE = [f"/one/p{i}" for i in range(7)]
    NO_HINT = [f"/s{i}/x" for i in range(7)]

    def _run(self, links, discover_only=True, max_pages=2):
        async def _go():
            site = _FakeSite({SEED: _links(links)})
            return await crawl_mod.web_crawl(site, SEED, discover_only=discover_only,
                                             max_depth=1, max_pages=max_pages, cache_ttl=0)
        return asyncio.run(_go())

    def test_a_map_stop_with_a_minority_subtree_recommends_that_subtree(self):
        na = self._run(self.MAJORITY).next_action
        assert "map stopped early" in na, na
        assert ", or scope it with path_include=[" in na, na
        assert "holds 5 of the 7 URLs still queued" in na, na

    def test_the_content_crawl_with_the_same_shape_gets_the_same_clause(self):
        """同一处装配服务两扇门：正文那一支的句子头不同，连接词的接法必须相同。"""
        na = self._run(self.MAJORITY, discover_only=False).next_action
        assert "crawl stopped early" in na, na
        assert ", or scope it with path_include=[" in na, na

    def test_a_map_whose_whole_tail_is_one_subtree_gets_the_fact_clause(self):
        """整张尾巴在一棵子树里时不能推荐子树，只能陈述事实 —— 那句从前被接成过
        "or scope it with every URL still queued is…"（一道断句）。"""
        na = self._run(self.ONE_TREE).next_action
        assert ", and note that every URL still queued is under /one" in na, na
        assert "or scope it with every URL" not in na, na

    def test_no_shape_glues_the_connector_onto_a_period(self):
        for na in (self._run(self.MAJORITY).next_action,
                   self._run(self.MAJORITY, discover_only=False).next_action,
                   self._run(self.ONE_TREE).next_action,
                   self._run(self.NO_HINT).next_action):
            for artifact in (", .", ",  ", "  or scope", ", or scope it with every URL"):
                assert artifact not in na, f"装配伤口 {artifact!r}：{na}"

    def test_the_shape_with_no_minority_subtree_says_nothing_about_scoping(self):
        """没抓到多数派时什么都不许接（R02-008 那把的判据，这里只验装配为空不塌陷）。"""
        na = self._run(self.NO_HINT).next_action
        assert "map stopped early" in na, na
        assert "scope" not in na and "and note that" not in na, na
        assert "URL(s) were discovered but never sent" in na, na

