"""`search=` 在 sitemap 那条分支上**不可能**生效 —— 而响应一个字不提（体验修复刀3-C2）。

`web_crawl` 的 `search` 过滤器站在整条路径的**最末尾**（`if search and result.pages:`），
而 sitemap 分支在那之前就用 `return sm_result` 整体返回了一份建好的响应。两条路不交汇，
所以

    web_crawl(url, sitemap="on", search="pricing")

发出去的是**未经筛的整张图**，`summary` 写着 "mapped 214 URL(s) ..."，`next_action` 还在
劝 "web_read the ones you need" —— 一次静默的参数丢弃，正是 `params.py` 不给合法用法的
第三种：钳必须点名、拒必须具名。

修法不是把过滤器再复制一份到两条路上（那是第二处会漂移的真相），而是把谓词**推进
`discover_sitemap` 的 `accept`** —— 那正是 `path_include` 已经在的位置，也已经有它记在
docstring 里的理由："filtering the already-capped list meant a subtree starting at entry
2000 returned nothing"。`search` 留在帽之后是**同一个缺陷的第二个实例**：一份 1,204 条的
sitemap，关键词在第 1,200 条，帽在 1,000 —— 筛完是空的，而调用方读到的是"这个站没有这个
词"。推进去之后帽花在**匹配的条目**上。

另一半也必须报：一条 sitemap 条目只带 `loc`（和 `lastmod`），**没有标题可比**。兑现 URL
那一半并点名少掉的那一半，是这份回执唯一的诚实形状。

靶站用注入的 `discover_sitemap` 替身，不打真 socket：这台机器上真回环那组用例本身依赖
套件顺序才绿（见 commit 记录），而 C2 判的是谓词在帽的哪一边、账怎么报 —— 替身把
`accept` 与 `max_urls` 的语义照 `sitemap.py` 的实现复刻（先记 seen 再判、够数即停），
足以判这两件事。
"""

from __future__ import annotations

import time

import pytest

from dhole_mcp import crawl
from dhole_mcp.crawl import SITEMAP_MAP_FLOOR
from dhole_mcp.sitemap import SitemapResult, SitemapURL

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
