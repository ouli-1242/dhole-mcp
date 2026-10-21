"""test_crawl_3.py —— 由 5 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import time
import pytest
from dhole_mcp import crawl
from dhole_mcp.crawl import SITEMAP_MAP_FLOOR
from dhole_mcp.sitemap import SitemapResult, SitemapURL
import asyncio
import re
from dhole_mcp import crawl as crawl_mod
from dhole_mcp.crawl import _no_sitemap_next_action
from dhole_mcp.server import ResponseModel, _TOP_LEVEL_ARGS
from dhole_mcp.crawl import plan_load, plan_parse
from dhole_mcp.server import MasterFetchServer
import inspect
import json
from dhole_mcp import server as server_mod
from dhole_mcp.crawl import plan_store


# ============================================================================
# 来源：test_a_sitemap_search_matches_urls_and_says_what_it_could_not.py
# ============================================================================
# `search=` 在 sitemap 那条分支上**不可能**生效 —— 而响应一个字不提（体验修复刀3-C2）。

BASE = "https://site.test"
# 1,200 条普通页 + 4 条带关键词的 + 3 条 docs 子树。`/pricing` 故意排在第 1,200 位之后：
# 帽是 1,000，所以"关键词在帽的那一边"是这个 fixture 的核心，不是偶然。
PATHS = ([f"/p/{i}" for i in range(1, 1201)]
         + ["/pricing", "/plans/compare", "/docs/index", "/docs/api/auth"])


def _fake_walk(state: dict, paths: list[str] | None = None):
    """`discover_sitemap` 的替身：复刻真实现的 accept/max_urls 语义。

    真实现（`sitemap.py:301-320`）：每条先查 seen（重复条目不重复计数），再过
    `accept`，被拒的计入 `filtered_out`，够 `max_urls` 就停并把 `capped` 置真。
    替身若在这四点上任一点走样，"推进帽之前"与"留在帽之后"就分不出来了。
    """
    wanted = paths if paths is not None else PATHS

    def _discover(start_url, *, http_get, max_urls, accept=None):
        res = SitemapResult(sitemaps_used=[f"{start_url}/sitemap.xml"], via="robots",
                            robots_checked=True, robots_had_sitemap=True)
        seen: set[str] = set()
        for path in wanted:
            url = f"{start_url.rstrip('/')}{path}"
            if url in seen:
                continue
            seen.add(url)
            state.setdefault("asked", []).append(url)
            if accept is not None and not accept(url):
                res.filtered_out += 1
                continue
            res.urls.append(SitemapURL(url=url, lastmod="2026-01-0T00:00:00Z"))
            if len(res.urls) >= max_urls:
                res.capped = len(seen) < len(wanted)
                break
        state["capped_at"] = max_urls
        return res

    return _discover


@pytest.fixture
def walk(monkeypatch):
    state: dict = {}
    monkeypatch.setattr("dhole_mcp.sitemap.discover_sitemap", _fake_walk(state))
    # 帽由 max_pages 推导（max(1000, max_pages*10)），这里默认走 10 → 帽 1000
    return state


async def _map(**kw):
    return await crawl._sitemap_map(BASE, kw.pop("path_include", None),
                                    kw.pop("path_exclude", None),
                                    max_pages=kw.pop("max_pages", 10),
                                    deadline_t=time.time() + 30, search=kw.pop("search", None),
                                    **kw)


def _tail(result) -> list[str]:
    return [p.url.replace(BASE, "") for p in (result.pages if result else [])]


class TestTheWiringCarriesTheKeyword:
    """C2 的缺陷**长在接线上**（`web_crawl` 忘了把 search 传给 `_sitemap_map`），
    所以判据也必须从接线那头进。

    这一组不是"再测一遍过滤器"：直接调 `_sitemap_map` 的用例全绿，而把调用点那一处
    `search=search` 摘掉，它们一个都不会红 —— 实测过（见 commit）。同一个形状本批在
    S9 已经认过一次账（"两个调用点里有没有人忘了传 basis"）。
    """

    @pytest.mark.asyncio
    async def test_web_crawl_passes_it_to_the_map(self, walk):
        from dhole_mcp.server import MasterFetchServer

        r = await MasterFetchServer().web_crawl(BASE, max_pages=10, max_depth=1,
                                                sitemap="on", discover_only=True,
                                                search="pricing")
        assert r.sitemap_used is True, r.summary
        assert _tail(r) == ["/pricing"], _tail(r)

    @pytest.mark.asyncio
    async def test_web_discover_passes_the_same_keyword_through_the_same_hole(self, walk):
        """两颗工具共用同一个洞：`web_discover` 只是 `web_crawl` 换了一层入口。"""
        from dhole_mcp.server import MasterFetchServer

        r = await MasterFetchServer().web_discover(BASE, sitemap=True, search="pricing")
        assert _tail(r) == ["/pricing"], (r.summary, _tail(r))

    @pytest.mark.asyncio
    async def test_the_same_entry_without_a_keyword_is_unchanged(self, walk):
        """没有关键词时，**地图**一寸没变 —— 变的只是这一页交出多少行（C3 之后）。

        这条用例原本断的是 `len(r.pages) == SITEMAP_MAP_FLOOR`。C3 让整轮字符预算第一
        次量到地图（它从前只累加 `content_md`，而地图行的 content 恒为空），60,000 字符
        在 910 行就花完了 —— 于是"多少行到手"不再是站点决定的数，而"地图有多大"仍然只
        由关键词决定。所以这里读 `pages_discovered`：那才是地图。少掉的行带着游标，
        不是丢了。
        """
        from dhole_mcp.server import MasterFetchServer

        r = await MasterFetchServer().web_crawl(BASE, max_pages=10, max_depth=1,
                                                sitemap="on", discover_only=True)
        assert r.pages_discovered == SITEMAP_MAP_FLOOR, r.pages_discovered
        assert len(r.pages) < SITEMAP_MAP_FLOOR, "预算没咬：C3 那道闸又躺平了"
        assert r.truncated_by_total_chars is True, r.summary
        assert r.next_resume_token.endswith(f":{len(r.pages)}"), r.next_resume_token
        assert "search=" not in (r.summary or ""), r.summary


class TestTheFilterActuallyRuns:

    @pytest.mark.asyncio
    async def test_a_keyword_keeps_only_the_urls_that_carry_it(self, walk):
        r = await _map(search="pricing")
        assert _tail(r) == ["/pricing"], _tail(r)
        assert r.pages_discovered == 1, r.pages_discovered

    @pytest.mark.asyncio
    async def test_it_is_decided_before_the_cap_not_after(self, walk):
        """C2 的核心：`/pricing` 是第 1,201 条，帽是 1,000。

        留在帽之后筛 → 前 1,000 条里没有它 → 空图 + "mapped 0"，读起来像"这个站没有
        这个词"。推进 `accept` → 帽只花在匹配的条目上。
        """
        assert SITEMAP_MAP_FLOOR == 1000, "这条用例的算术依赖帽的值"
        assert walk is not None
        r = await _map(search="pricing")
        assert r is not None and _tail(r) == ["/pricing"], (r.summary if r else None)
        assert r.sitemap_truncated is False, "匹配只有 1 条，帽根本没碰到"

    @pytest.mark.asyncio
    async def test_the_map_reports_both_numbers_it_filtered_on(self, walk):
        r = await _map(search="pricing")
        summary = r.summary or ""
        assert "search='pricing'" in summary, summary
        assert "kept 1 of 1204" in summary, summary
        assert "1203 carried none of the terms" in summary, summary

    @pytest.mark.asyncio
    async def test_a_keyword_that_matches_everything_changes_nothing_but_still_accounts(
            self, walk):
        """一个不误伤的关键词：筛除数为 0，行数与不传时一致，而帽那一半照旧自报。

        `kept 1000 of 1000` 的两个数都是**帽所见**——走查在 1,000 条就停了，后面的条目
        accept 根本没被调用。紧跟着那句 "map capped at 1000 URLs (the sitemap holds at
        least this many)" 就是为了让这个 1000 不被读成"整站 1,000 条"。
        """
        r = await _map(search="site.test")
        plain = await _map()
        summary = r.summary or ""
        assert "0 carried none of the terms" in summary, summary
        assert "map capped at 1000 URLs" in summary, summary
        assert len(r.pages) == len(plain.pages) == SITEMAP_MAP_FLOOR, len(r.pages)

    @pytest.mark.asyncio
    async def test_the_terms_actually_reached_the_walk(self, walk):
        """判据不是"响应里写了 search"，是谓词真的被逐条调用过。"""
        await _map(search="pricing")
        assert len(walk["asked"]) == len(PATHS), len(walk["asked"])
        assert walk["asked"][0] == f"{BASE}/p/1"


class TestWhatItCannotMatchIsNamed:
    """一条 sitemap 条目只有 `loc`：标题那一半兑现不了，就必须被说出来。"""

    @pytest.mark.asyncio
    async def test_a_title_only_word_reports_the_reason_not_an_empty_site(self, walk):
        """/plans/compare 页面上写着 "Enterprise Plan"，而 loc 里没有 "enterprise"。

        这里挑的词刻意不与任何 URL 片段撞车（"plan" 会撞 /plans/compare，那测的是另一
        件事）—— 要钉的是"标题那一半兑现不了，必须被点名"，不是"这个筛子有多宽松"。
        """
        r = await _map(search="Enterprise")
        assert r is not None and r.pages == [], _tail(r)
        summary = r.summary or ""
        assert "carried none of the terms" in summary, summary
        assert "no title" in summary.lower(), summary
        assert "web_search" in (r.next_action or ""), r.next_action
        # 作用域这次根本没参与判断，不许把空图推给它
        assert "path_include" not in summary, summary

    @pytest.mark.asyncio
    async def test_two_filters_that_ate_everything_are_two_counts(self, walk):
        """scope 吃掉一部分、search 吃掉其余 —— 合成一句就等于让人放宽错的那个旋钮。"""
        r = await _map(search="pricing", path_include=["/docs"])
        summary = r.summary or ""
        assert "fell outside the scope" in summary, summary
        assert "carried none of the terms" in summary, summary
        assert "1202 fell outside the scope" in summary, summary
        assert "2 carried none of the terms" in summary, summary


class TestNoSearchLeavesTheOldShapeAlone:
    """反向半边：不传 search 时一个字不许多写 —— 否则这条修复自己成了新的噪声源。"""

    @pytest.mark.asyncio
    async def test_an_unfiltered_map_says_nothing_about_keywords(self, walk):
        r = await _map()
        assert "search=" not in (r.summary or ""), r.summary
        assert len(r.pages) == SITEMAP_MAP_FLOOR, len(r.pages)

    @pytest.mark.asyncio
    async def test_scope_only_rejections_keep_the_existing_sentence(self, walk):
        r = await _map(path_include=["/nowhere"])
        assert "path_include=[\'/nowhere\']" in (r.summary or ""), r.summary
        assert "carried none of the terms" not in (r.summary or ""), r.summary


# ============================================================================
# 来源：test_the_map_advice_names_the_door_in_hand.py
# ============================================================================
# 地图那一停的出路要点名**手上这扇门**的旋钮（R02-083 · P3）。
# 重名消歧：BASE → BASE__the_map_advice_names_the_89e7、_map → _map__the_map_advice_names_the_89e7

BASE__the_map_advice_names_the_89e7 = "https://inhand.test"
SEED = BASE__the_map_advice_names_the_89e7 + "/"


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


def _map__the_map_advice_names_the_89e7(max_pages=None, delay=0.0, deadline_ms=None):
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
        r = _map__the_map_advice_names_the_89e7(max_pages=3)
        assert r.truncated_by_max_pages is True, (r.summary, r.next_action)
        na = r.next_action
        assert "max_pages" in na, na
        assert not na.lower().startswith("crawl stopped"), (
            f"地图那一停被称成 crawl：{na}")

    def test_the_pages_cap_advice_does_not_send_the_caller_to_the_other_door_first(self):
        """换门可以作为第二条出路，但第一条必须是本门就有的那颗旋钮。"""
        r = _map__the_map_advice_names_the_89e7(max_pages=3)
        na = r.next_action
        cap = na.find("max_pages")
        other = na.find("web_crawl")
        assert cap >= 0, na
        assert other < 0 or other > cap, (
            f"建议先把调用方支到 web_crawl，本门的 max_pages 却排在后面：{na}")

    def test_the_clock_stop_advice_names_the_map_door_s_own_clock(self):
        r = _map__the_map_advice_names_the_89e7(max_pages=20, delay=0.05, deadline_ms=120)
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
        for r in (_map__the_map_advice_names_the_89e7(max_pages=3), _map__the_map_advice_names_the_89e7(max_pages=20, delay=0.05, deadline_ms=120)):
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


# ============================================================================
# 来源：test_a_link_walk_map_holds_its_own_tail.py
# ============================================================================
# R02-057 · 走链接的地图早停时，尾巴要连着已交出的那几行一起存下来。
# 重名消歧：BASE → BASE__a_link_walk_map_holds_it_aaf9、SEED → SEED__a_link_walk_map_holds_it_aaf9、_links → _links__a_link_walk_map_holds_it_aaf9

BASE__a_link_walk_map_holds_it_aaf9 = "https://tail.test"
SEED__a_link_walk_map_holds_it_aaf9 = BASE__a_link_walk_map_holds_it_aaf9 + "/"
N_LINKS = 6
# 帽=3 ⇒ 交出 seed、p0、p1，队列里剩 p2..p5（一次都没发过）。
TAIL = [f"{SEED__a_link_walk_map_holds_it_aaf9}p{i}" for i in range(2, N_LINKS)]


def _links__a_link_walk_map_holds_it_aaf9(names):
    return ("<html><body>" + "".join(f'<a href="{h}">l</a>' for h in names)
            + "</body></html>")


class _Counter:
    """数得清"这一发付了几次请求"的替身站点：续页那几发必须是 0。"""

    def __init__(self, pages):
        self.fetched = []
        self.pages = pages

    async def web_read(self, url, **kwargs):
        self.fetched.append(url)
        return ResponseModel(url=url, status=200,
                             content=[self.pages.get(url, "<p>plain body.</p>")],
                             content_type="text/html", fetcher_used="http",
                             content_ok=True)


def _rows(r):
    return [p.url for p in r.pages]


def _plan(r):
    return list(plan_load(r.plan_id) or []) if r.plan_id else []


def _door():
    srv = MasterFetchServer()
    counter = _Counter({SEED__a_link_walk_map_holds_it_aaf9: _links__a_link_walk_map_holds_it_aaf9([f"/p{i}" for i in range(N_LINKS)])})
    srv.web_read = counter.web_read
    return srv, counter


@pytest.fixture
def map_stop():
    async def _go():
        srv, counter = _door()
        r = await srv.web_discover(SEED__a_link_walk_map_holds_it_aaf9, max_pages=3)
        return srv, counter, r
    return asyncio.run(_go())


def _taught_tokens(text: str) -> list[str]:
    return re.findall(r"resume_token='([^']*)'", text)


class TestThePlanHoldsTheWholeMap:
    def test_the_tail_rows_are_held_after_the_rows_that_shipped(self, map_stop):
        _srv, _counter, r = map_stop
        plan = _plan(r)
        assert plan[:len(r.pages)] == _rows(r), "计划的前半就是这一发交出的那几行"
        assert plan[len(r.pages):] == TAIL, (
            f"尾巴必须是这一发本会接着发出的那几行，按同一顺序：{plan}")

    def test_the_plan_size_is_the_map_size(self, map_stop):
        _srv, _counter, r = map_stop
        assert len(_plan(r)) == r.pages_discovered, (
            f"地图有 {r.pages_discovered} 行，计划里只有 {len(_plan(r))} 行")

    def test_the_held_tail_is_exactly_what_the_reply_called_never_sent(self, map_stop):
        _srv, _counter, r = map_stop
        quoted = int(re.search(r"([\d,]+) URL\(s\) were discovered but never sent",
                               r.next_action).group(1).replace(",", ""))
        assert len(_plan(r)) - len(r.pages) == quoted, r.next_action


class TestTheCursorLandsOnTheFirstHeldRow:
    def test_the_cursor_counts_the_rows_that_shipped(self, map_stop):
        _srv, _counter, r = map_stop
        assert r.next_resume_token == f"{r.plan_id}:{len(r.pages)}", r.next_resume_token
        assert plan_parse(r.next_resume_token) == (r.plan_id, len(r.pages))

    def test_the_way_out_teaches_both_cursors_by_name(self, map_stop):
        _srv, _counter, r = map_stop
        na = r.next_action
        tokens = _taught_tokens(na)
        assert f"{r.plan_id}:{len(r.pages)}" in tokens, na
        assert f"{r.plan_id}:0" in tokens, na
        assert f"url='{SEED__a_link_walk_map_holds_it_aaf9}'" in na, f"教的这一句少了 url= 就照抄不跑：{na}"


class TestTheTaughtCursorsDoWhatTheySay:
    @pytest.mark.asyncio
    async def test_the_tail_cursor_lists_the_held_rows_for_free(self):
        srv, counter = _door()
        r = await srv.web_discover(SEED__a_link_walk_map_holds_it_aaf9, max_pages=3)
        paid = len(counter.fetched)
        page = await srv.web_discover(SEED__a_link_walk_map_holds_it_aaf9, resume_token=r.next_resume_token)
        assert _rows(page) == TAIL, _rows(page)
        assert len(counter.fetched) == paid, "读尾巴那一发一个请求都不该付"
        assert page.pages_crawled == 0
        assert page.next_resume_token == "", page.summary
        assert "Map exhausted" in page.next_action, page.next_action

    @pytest.mark.asyncio
    async def test_the_zero_cursor_lists_the_whole_map_from_row_one(self):
        srv, counter = _door()
        r = await srv.web_discover(SEED__a_link_walk_map_holds_it_aaf9, max_pages=3)
        paid = len(counter.fetched)
        page = await srv.web_discover(SEED__a_link_walk_map_holds_it_aaf9, resume_token=f"{r.plan_id}:0")
        assert _rows(page) == _plan(r), "游标 0 读回的是整张地图，不是只读尾巴"
        assert len(counter.fetched) == paid, "重读地图也不该付请求"
        assert page.pages_discovered == r.pages_discovered

    @pytest.mark.asyncio
    async def test_every_token_the_reply_teaches_runs_from_outside_the_process(self):
        """`_dispatch` 才是真门：建议文案里每一个 resume_token 都要能在门外照抄就跑。"""
        import json
        srv, _counter = _door()
        r = await srv._dispatch("web_discover", {"url": SEED__a_link_walk_map_holds_it_aaf9, "max_pages": 3})
        payload = json.loads(r[0][0].text)
        assert payload["next_resume_token"]
        for token in _taught_tokens(payload["next_action"]):
            out = await srv._dispatch("web_discover", {"url": SEED__a_link_walk_map_holds_it_aaf9,
                                                       "resume_token": token})
            got = json.loads(out[0][0].text)
            assert not got.get("error"), f"{token} 照抄被拒：{got.get('error')}"
            assert got["pages"], f"{token} 教了一条走不通的路"


class TestTheOldShapeIsNotDisturbed:
    @pytest.mark.asyncio
    async def test_a_content_stop_still_holds_only_the_never_sent_urls(self):
        """正文那一支不许把已交出的行存进计划：那里的游标每走一步都付一次请求。"""
        srv, counter = _door()
        r = await srv.web_crawl(SEED__a_link_walk_map_holds_it_aaf9, max_pages=3)
        plan = _plan(r)
        assert plan, r.plan_id
        assert not (set(plan) & {p.url for p in r.pages}), plan
        assert r.next_resume_token == f"{r.plan_id}:0"

    def test_a_map_that_shipped_every_row_publishes_no_plan_of_its_own(self):
        async def _go():
            counter = _Counter({SEED__a_link_walk_map_holds_it_aaf9: _links__a_link_walk_map_holds_it_aaf9([f"/p{i}" for i in range(2)])})
            return await crawl_mod.web_crawl(counter, SEED__a_link_walk_map_holds_it_aaf9, max_pages=30,
                                             max_depth=1, discover_only=True,
                                             cache_ttl=0)
        r = asyncio.run(_go())
        assert r.truncated_by_max_pages is not True
        assert not r.plan_id, "队列见底就不该为不存在的东西留一份状态"
        assert not r.next_resume_token


class TestOneAuthorForBothMapKinds:
    def test_the_tail_is_held_by_the_same_helper_a_sitemap_map_uses(self, monkeypatch):
        seen: list[tuple[list, list]] = []
        real = crawl_mod._hold_map_tail

        def spy(pages, held):
            seen.append(([p.url for p in pages], list(held)))
            return real(pages, held)

        monkeypatch.setattr(crawl_mod, "_hold_map_tail", spy)

        async def _go():
            counter = _Counter({SEED__a_link_walk_map_holds_it_aaf9: _links__a_link_walk_map_holds_it_aaf9([f"/p{i}" for i in range(N_LINKS)])})
            return await crawl_mod.web_crawl(counter, SEED__a_link_walk_map_holds_it_aaf9, max_pages=3,
                                             max_depth=1, discover_only=True,
                                             cache_ttl=0)
        r = asyncio.run(_go())
        assert seen == [([p.url for p in r.pages], TAIL)], seen

    def test_the_door_stores_one_plan_for_a_truncated_map(self, monkeypatch):
        """两处各存一份会把游标指向一个没有尾巴的新计划 —— 存储只许发生一次。"""
        calls: list[list[str]] = []
        real = crawl_mod.plan_store

        def spy(urls):
            calls.append(list(urls))
            return real(urls)

        monkeypatch.setattr(crawl_mod, "plan_store", spy)

        async def _go():
            srv, counter = _door()
            return await srv.web_discover(SEED__a_link_walk_map_holds_it_aaf9, max_pages=3)
        r = asyncio.run(_go())
        assert len(calls) == 1, f"计划被写了 {len(calls)} 次：{calls}"
        assert calls[0] == _plan(r)


class TestTheManualCarriesTheNewShape:
    def test_the_field_cell_names_both_halves_of_a_map_plan(self):
        from dhole_mcp import guide
        text = (guide._ROOT / "references" / "fields.md").read_text(encoding="utf-8")
        line = next(r for r in text.splitlines() if r.startswith("| `plan_id`"))
        assert "never sent" in line, line
        assert "map ROWS it shipped" not in line, (
            "这一格还在说 discover 只存已交出的行 —— 地图的尾巴今天也在计划里")


# ============================================================================
# 来源：test_the_read_door_never_borrows_a_verb_from_another.py
# ============================================================================
# 读门上不许有第二套动词，也不许有没人装配的格子（评审 R02-092 · R02-094 · R02-095）。

READ_CLAUSE = "Nothing was fetched."
DEAD = "http://example.invalid/"
BORROWED = re.compile(r"nothing was read", re.I)
# 全表那一支尺要**只数句子结尾**：`server.py` 的 `_agent_hints` 里有两处长句
# （`… so nothing was fetched and nothing was read -` 与 `nothing was read - the name you
# gave is not where this call looked`）说的是**另一件事**（CDP 没应答的复合成因、陌生参数名的
# 指引），本轮没有一条回执证明它们害到谁 —— 按 §12 / ㉘ 不在这一把改文案，尺子也不许把它们
# 算成战果。三处实测的句子都以句号收尾，就按那个形状数。
SCAN_BORROWED = re.compile(r"nothing was read( from the plan)?\s*\.", re.I)
ANY_CLAUSE = re.compile(r"[Nn]othing was [a-zA-Z ,\-]+?\.", re.I)


def _discover(**kwargs):
    out = asyncio.run(MasterFetchServer().web_discover(**kwargs))
    assert out.error, f"这一形状本该被拒，却回了没有 error 的包: {out}"
    return out


def _texts(out) -> str:
    return f"{out.error or ''} {out.next_action or ''}"


async def _wire(tool: str, args: dict) -> dict:
    """走真接线（`InMemoryTransport`）：类型归一与它那道门只发生在分发那一步。

    直接调方法会绕过 `_coerce_arg_types`，拿到的是引擎内部的 `invalid literal for int()` ——
    那是 R02-091 的半径，不是这一格的判据（同一个理由，F82 那件也用这条通道）。
    """
    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport
    async with InMemoryTransport(MasterFetchServer()._mcp_server()) as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            res = await session.call_tool(tool, args)
            text = "".join(getattr(b, "text", "") for b in res.content)
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        return {"_not_json": text[:300]}


class TestTheReadDoorSpeaksOneVerb:
    """四颗实测的拒绝都要说本门那句话，且要说一遍。"""

    @pytest.mark.parametrize("kwargs", [
        pytest.param({"url": DEAD, "resume_token": "no-colon", "sitemap": True},
                     id="R3 · 形状不对 + sitemap"),
        pytest.param({"url": DEAD, "resume_token": "deadbeef:0", "sitemap": True},
                     id="R5 · 形状对 + sitemap"),
        pytest.param({"url": DEAD, "timeout": "one"}, id="C1 · timeout 写成字符串"),
        pytest.param({"url": DEAD, "timeout": 999999}, id="C2 · timeout 超帽"),
    ])
    def test_a_refused_discovery_declares_it_the_doors_way(self, kwargs):
        texts = _texts(_discover(**kwargs))
        assert READ_CLAUSE in texts, texts
        assert not BORROWED.search(texts), f"借了别的门的动词: {texts}"

    @pytest.mark.parametrize("kwargs", [
        pytest.param({"url": DEAD, "resume_token": "no-colon", "sitemap": True},
                     id="R3 · 形状不对 + sitemap"),
        pytest.param({"url": DEAD, "timeout": "one"}, id="C1 · timeout 写成字符串"),
    ])
    def test_the_declaration_is_spoken_once(self, kwargs):
        """两遍等于两位作者：拼接处一旦重复，读的人要猜哪一遍是真的。"""
        texts = _texts(_discover(**kwargs))
        assert texts.count(READ_CLAUSE) == 1, texts

    def test_a_bad_path_filter_on_a_real_cursor_speaks_the_doors_verb(self):
        """R6：这一颗要有**真游标**才走得到（`_plan_map_page` 在计划读回之后才校路径式）。

        靶：`r107b_plan_family.json` 的 R6（`6cab92a06242:0` + `path_include=['[']`）交回
        `Nothing was read from the plan.`，同批 R7/R8 交回 `Nothing was fetched.`。
        对照组也钉住：同一颗游标原样再问要**读得到行**，否则这一格只是"根本没计划"。
        """
        plan_id = plan_store([f"{DEAD}a/1", f"{DEAD}a/2"])
        bad = _discover(url=DEAD, resume_token=f"{plan_id}:0", path_include=["["])
        texts = _texts(bad)
        assert READ_CLAUSE in texts, texts
        assert not BORROWED.search(texts), f"借了别的门的动词: {texts}"
        # 对照组：同一颗游标原样再问要**读得到行**（不走 `_discover`，它的前提是"这一发该被拒"）
        good = asyncio.run(MasterFetchServer().web_discover(
            url=DEAD, resume_token=f"{plan_id}:0"))
        assert not good.error and good.pages, f"同一颗游标本该读得到行: {good.error}"


class TestTheCoercionRefusalHasItsOwnDoor:
    """`web_read` 那条只属于自己的支路：出路要带上门话，而既有那句 no request was made 不动。"""

    @pytest.mark.parametrize("bad", [
        pytest.param({"urls": "one"}, id="D1 · 数组写成裸字符串"),
        pytest.param({"offset": "one"}, id="D3 · 整数写成字符串"),
        pytest.param({"timeout": "one"}, id="D4 · 数字写成字符串"),
    ])
    def test_web_read_says_its_own_door(self, bad):
        payload = asyncio.run(_wire("web_read", {"url": DEAD} | bad))
        way = payload.get("next_action") or ""
        texts = f"{payload.get('error')} {way}"
        assert str(payload.get("error", "")).startswith("invalid_request"), texts
        assert READ_CLAUSE in way, f"出路那格没有门话: {texts}"
        assert way.startswith(READ_CLAUSE), f"门话要在行首: {way}"
        assert not BORROWED.search(texts), texts
        # 既有契约（test_bug_report2_regressions 钉的是 max_content_chars 那一颗，同一条支路）
        assert "no request was made" in way.lower(), way

    def test_the_sister_door_already_said_it(self):
        """对照不许被修成"两边各写一遍"：`web_crawl` 同一层早就是装配好的。"""
        payload = asyncio.run(_wire("web_crawl", {"url": DEAD, "max_pages": "one"}))
        texts = f"{payload.get('error')} {payload.get('next_action')}"
        assert str(payload.get("error", "")).startswith("invalid_request"), texts
        assert READ_CLAUSE in texts, texts
        assert not BORROWED.search(texts), texts


class TestEveryRefusalFillsItsOwnCell:
    """空着的 `next_action` 也是一位缺席的作者（R02-095）。"""

    @pytest.mark.parametrize("tool", ["web_crawl", "web_discover"])
    def test_a_malformed_cursor_still_offers_a_way_back(self, tool):
        out = asyncio.run(getattr(MasterFetchServer(), tool)(
            url=DEAD, resume_token="no-colon"))
        texts = _texts(out)
        assert out.error, out
        assert (out.next_action or "").strip(), (
            f"{tool} 这颗拒绝的出路那格是空串 —— 申报写在 error 里不等于出路有人写过")
        # 这一格的申报只在 error 里出现一遍：补的出路只教「怎么拿回一颗真游标」，不重复签一次字
        assert texts.count(READ_CLAUSE) == 1, texts
        assert "next_resume_token" in (out.next_action or ""), out.next_action


class TestOneAuthorPerClause:
    """这三句在源码里只许有一位作者：读门的拒绝不许再抄 `read`。"""

    def _literals(self, code, doc=None) -> list:
        """常量池要**连着嵌套的代码对象**一起数：内联的 lambda/闭包也写得出句子。"""
        found = []
        for const in code.co_consts:
            if isinstance(const, str):
                if const == doc:
                    continue
                found.append(const)
            elif hasattr(const, "co_consts"):
                found.extend(self._literals(const))
        return found

    def test_no_read_door_refusal_authors_a_read_verb(self):
        """按函数的常量池数，不按文件文本数 —— 注释与文档里引用它不算写它（F79 的同一把尺）。

        尺子本身要先能红：这三处全在 `MasterFetchServer` 的**方法**里，所以只数模块级函数
        的那一支从一开始就是永远绿的（本把这趟就是它自己露馅的，改之前先看过它数到 0 处）。
        """
        from dhole_mcp import crawl as crawl_mod
        from dhole_mcp import params as params_mod

        def callables_in(mod):
            for _, member in inspect.getmembers(mod, inspect.isfunction):
                yield member
            for _, klass in inspect.getmembers(mod, inspect.isclass):
                for _, meth in inspect.getmembers(klass, inspect.isfunction):
                    yield meth

        offenders = []
        for mod in (server_mod, params_mod, crawl_mod):
            for fn in callables_in(mod):
                for const in self._literals(fn.__code__, fn.__doc__):
                    if SCAN_BORROWED.search(const):
                        offenders.append((mod.__name__, fn.__qualname__, const[:80]))
            for name, val in vars(mod).items():
                if isinstance(val, str) and SCAN_BORROWED.search(val):
                    offenders.append((mod.__name__, name, val[:80]))
        assert not offenders, f"读门上还有 {len(offenders)} 处自己写 read: {offenders}"

    def test_the_ruler_itself_can_go_red(self):
        """反证：同一把尺拿**修前那三句原文**去量，必须各数到一处（永远绿的断言不算判据）。"""
        for sample in ("… sitemap= would decide nothing here. Nothing was read.",
                       "Nothing was read from the plan. Fix the pattern.",
                       "invalid_request: timeout= Timeout must be a number - nothing was read."):
            assert SCAN_BORROWED.search(sample), sample

    def test_the_words_still_come_from_one_author(self):
        """装配位不动：门话仍然只由 `context.nothing_went_out` 写出来。"""
        from dhole_mcp import context as context_mod
        holders = [n for n, fn in inspect.getmembers(context_mod, inspect.isfunction)
                   if any(READ_CLAUSE in c for c in self._literals(fn.__code__, fn.__doc__))]
        assert holders == ["nothing_went_out"], holders

    def test_the_payload_shape_survives(self):
        """遮蔽与序列化不许被这次改动打坏：拒绝包要能原样变成 wire JSON，且只申报一次。"""
        out = _discover(url=DEAD, timeout="one")
        payload = json.loads(json.dumps(out.model_dump(exclude_none=True), ensure_ascii=False))
        texts = f"{payload.get('error')} {payload.get('next_action')}"
        assert READ_CLAUSE in texts, texts
        assert {c.rstrip(".") for c in ANY_CLAUSE.findall(texts)} == {"Nothing was fetched"}, texts


# ============================================================================
# 来源：test_tool_split_b5.py
# ============================================================================
# B5 拆分的守卫：一张工具表一次只搬一件事。

URL = "https://example.com/"


def _defs() -> dict:
    return {td["name"]: td for td in MasterFetchServer._TOOL_DEFS}


def _run(coro):
    return asyncio.run(coro)


# ─── web_discover：从 web_crawl 搬出「只出 URL 地图」──────────────────

# §4 搬出来的那七颗之外，C3 又加了两颗 —— 它们不是"还没搬走的爬行旋钮"，是地图
# 独有的大小旋钮：一张地图的行数由站点决定，所以预算与游标必须在这扇门上够得着
# （`max_total_chars` 在 web_crawl 上有、web_discover 上没有的那段时间里，被预算
# 砍掉的行只剩"重付前半张地图"一条路，而游标指向空无处比没有游标更坏）。
DISCOVER_PARAMS = {"url", "sitemap", "path_include", "path_exclude",
                   "max_pages", "search", "timeout",
                   "max_total_chars", "resume_token"}


def test_web_discover_is_registered_with_its_own_param_set():
    defs = _defs()
    assert "web_discover" in defs, sorted(defs)
    props = set(defs["web_discover"]["inputSchema"]["properties"])
    assert props == DISCOVER_PARAMS, (
        f"§4 给 web_discover 的是这 7 颗，C3 给它的地图大小是这 2 颗，"
        f"多出来的才是还没搬走的旧旋钮：{props}")
    assert props == set(server_mod._TOP_LEVEL_ARGS["web_discover"])


def test_web_discover_says_what_it_returns_and_what_it_does_not():
    desc = _defs()["web_discover"]["description"].lower()
    assert "no page" in desc and "content" in desc, desc
    # 拆出去的工具必须自己说清下一步是谁，否则 agent 拿着地图不知道找谁读正文
    assert "web_read" in desc, desc


def test_web_crawl_no_longer_accepts_discover_only():
    assert "discover_only" not in server_mod._TOP_LEVEL_ARGS["web_crawl"], (
        "地图搬走了，开关还留在爬取那颗上：同一个能力两个入口")
    assert "discover_only" not in _defs()["web_crawl"]["inputSchema"]["properties"]
    with pytest.raises(ValueError) as exc:
        _run(MasterFetchServer()._dispatch("web_crawl",
                                           {"url": URL, "discover_only": True}))
    assert "Unsupported argument" in str(exc.value)


def test_a_map_call_over_the_new_name_asks_the_engine_for_a_map(monkeypatch):
    """行为账：入口换成 web_discover 之后，引擎收到的仍然是「只出地图」那档。

    断的是接线而不是地图内容 —— 引擎本身（crawl.web_crawl 的 discover_only 模式）
    已由爬取那批测试逐条钉过，这里重钉一遍只是把同一件事写两处。
    """
    from dhole_mcp.crawl import CrawlResponseModel

    seen: dict = {}

    async def fake_engine(self, url, **kwargs):
        seen.update(kwargs)
        seen["url"] = url
        return CrawlResponseModel(start_url=url, summary="map",
                                  pages=[], pages_crawled=0,
                                  pages_discovered=0, discover_only=True)

    monkeypatch.setattr(MasterFetchServer, "web_crawl", fake_engine)
    _run(MasterFetchServer()._dispatch(
        "web_discover", {"url": URL, "max_pages": 5, "sitemap": True}))
    assert seen.get("discover_only") is True, \
        "新入口没把地图模式带上：它其实还在抓正文"
    assert seen.get("max_pages") == 5 and seen.get("sitemap") is True


# ─── sys_state：把 cache_clear 与 close_session 合成一颗 ───────────────
#
# 两颗旧工具做的是同一件事的两半：「本地还存着什么」和「把它抹掉」。分开摆的代价
# 是踩过的：cache_clear(all=true) 连 cookie jar 一起抹，而它自己的描述里没这句；
# close_session 看不见缓存。合成一颗之后 token 词表把「抹哪一类」说明白，
# §3-32 的闸（抹掉后用户不会立刻知道的那一类要先 confirm）才有地方落。

STATE_PARAMS = {"show", "clear", "confirm"}
VOCAB = {"cache", "engine_state", "sessions", "cred_defaults", "all"}

ME = "https://shop.example.com/account"
SECRET = "session-value-DO-NOT-LEAK"


def _defs_by_name() -> dict:
    return {td["name"]: td for td in MasterFetchServer._TOOL_DEFS}


def test_sys_state_is_registered_with_three_params():
    defs = _defs_by_name()
    assert "sys_state" in defs, sorted(defs)
    props = set(defs["sys_state"]["inputSchema"]["properties"])
    assert props == STATE_PARAMS, (
        f"§4 给 sys_state 的是这 3 个，多出的是没搬干净的旧旋钮：{props}")
    assert props == set(server_mod._TOP_LEVEL_ARGS["sys_state"])


def test_the_two_housekeeping_tools_are_gone():
    """合并不能留两个家：旧名字必须在三个注册面上都消失。"""
    for old in ("cache_clear", "close_session"):
        assert old not in _defs_by_name(), f"{old} 还在连接期的表里"
        assert old not in server_mod._TOP_LEVEL_ARGS, f"{old} 仍可被分发"
        with pytest.raises(ValueError, match="Unknown tool") as exc:
            _run(MasterFetchServer()._dispatch(old, {}))
        # 报错会回显调用方打的那个名字，那是对的；不能有的部分是"建议名单"——
        # 服务端不许把一个不存在的名字推荐给人。
        assert old not in str(exc.value).split("Registered names:")[1], \
            "报错的建议名单里还留着旧名字"


def test_a_bare_call_shows_the_census_and_changes_nothing():
    from dhole_mcp import sessions
    from dhole_mcp.cache import set_cached

    sessions.store("alpha", ME, {"sid": SECRET})
    _run(set_cached(URL, "markdown", ["body"], 200))
    _content, structured = _run(MasterFetchServer()._dispatch("sys_state", {}))
    assert [r["session_id"] for r in structured["sessions"]] == ["alpha"]
    assert structured["open_count"] == 1
    assert structured["cache"]["entries"] >= 1
    assert sessions.header_for("alpha", ME) == {"sid": SECRET}, "看一看不该有任何副作用"
    assert SECRET not in json.dumps(structured, ensure_ascii=False)


def test_clearing_every_session_waits_for_a_confirm():
    """§3-32：抹掉之后用户不会立刻知道自己失去了什么 —— 登录态正是这一类。"""
    from dhole_mcp import sessions

    sessions.store("alpha", ME, {"sid": SECRET})
    _content, paused = _run(MasterFetchServer()._dispatch(
        "sys_state", {"clear": ["sessions"]}))
    assert paused["pending_approval"] is True, paused
    assert sessions.header_for("alpha", ME) == {"sid": SECRET}, \
        "没批准就不能动手"
    assert "confirm" in paused["next_action"]

    _content, ok = _run(MasterFetchServer()._dispatch(
        "sys_state", {"clear": ["sessions"], "confirm": True}))
    assert sessions.header_for("alpha", ME) == {}
    assert ok["cleared"] == ["sessions"] and ok["cookies_forgotten"] == 1


def test_naming_one_session_forgets_it_without_a_confirm():
    """点名关是调用方自己写出来的意图，闸只拦「你没点名的那些也一起没了」。"""
    from dhole_mcp import sessions

    sessions.store("target", ME, {"sid": SECRET})
    sessions.store("untouched", "https://elsewhere.example.org/x", {"sid": SECRET})
    _content, structured = _run(MasterFetchServer()._dispatch(
        "sys_state", {"clear": ["target"]}))
    assert sessions.header_for("target", ME) == {}
    assert sessions.header_for("untouched", "https://elsewhere.example.org/x") == \
        {"sid": SECRET}
    assert structured["session_id"] == "target"


def test_clear_cache_does_not_touch_the_jar():
    """旧 cache_clear(all=true) 顺手抹掉 cookie，是这条合并要修的谎。
    现在缓存与凭据是两个 token，谁都不再是另一个的隐藏副作用。"""
    from dhole_mcp import sessions
    from dhole_mcp.cache import get_cached, set_cached

    sessions.store("alpha", ME, {"sid": SECRET})
    _run(set_cached(URL, "markdown", ["body"], 200))
    _content, structured = _run(MasterFetchServer()._dispatch(
        "sys_state", {"clear": ["cache"]}))
    assert _run(get_cached(URL, "markdown")) is None, structured
    assert structured["purged"] >= 1
    assert sessions.header_for("alpha", ME) == {"sid": SECRET}, \
        "清缓存不该顺手把人登出"


def test_an_unknown_token_names_the_vocabulary_and_its_neighbour():
    """词表打错不能静悄悄：既不落会话分支，也不能只回一句 not found。"""
    _content, structured = _run(MasterFetchServer()._dispatch(
        "sys_state", {"clear": ["chache"]}))
    assert structured["error"], structured
    for token in VOCAB:
        assert token in structured["error"] or token in structured["next_action"], token
    assert structured.get("cleared", []) == []
