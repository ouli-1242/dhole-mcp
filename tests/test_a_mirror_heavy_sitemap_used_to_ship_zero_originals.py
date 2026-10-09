"""地图交回的是原文还是它的译本 —— 文档序不该决定谁在这一页里（体验修复刀3-C4）。

现场（2026-10-09 真实使用模拟 · 剧本卡 3）：一次 `web_discover(sitemap=true)` 交回
626 行 / 59,936 字符，其中**原文 0 篇** —— 站点把自己 100 篇文档的 9 种语言副本排在
前面，副本吃光了整轮预算，而我要的那一页在清单第 997 条：在地图里、在这一页之外。
回执读起来像"这个站只有这些页"。

`evals/sitemap_map_baseline.py` 量的就是这个（零网络；真 `parse_sitemap` +
真 `discover_sitemap` + 真 `_order_map_entries` + 真 `_map_row_cost`）：

    metric                  文档序 = C4 前   折叠+重排 = C4 后
    target_in_rows                 False            True
    originals_shipped                  0              100
    mirrors_demoted                    0              700
    rows_moved                         0              300

注意"覆盖了多少份不同内容"这一格**看不丢**：626 行译本折叠后仍是同一批内容 key。
所以判据不是覆盖数，而是"交回的是原文还是译本" —— 本文件数的是原文篇数。

契约（本文件钉住）：
  * **重排，不删行**。被折到后面的副本仍然是地图的行：`pages_discovered` 照数、游标
    照得到、计划里照存。重排可逆，删行不可逆，而"这一页其实存在"是不可收回的谎。
  * 折与移都要**数出来**再出口（`mirrors_demoted` / `moved` 进 summary），不是断言。
  * 阈值：同一份内容的前 MIRROR_VARIANTS_KEPT 个变体留在前面 —— 一个真用 /ja/ 做主
    语言的站不许被折掉。
  * 代表由组内选，且**不带语言段的那条优先**：先到先得会把原文折到后面，那是本缺陷
    的镜像而不是修复。
  * 没有副本的地图一寸不动，也就不许多说一句"我重排过"。
"""

from __future__ import annotations

import pytest

from dhole_mcp import crawl as crawl_mod
from dhole_mcp.crawl import MIRROR_VARIANTS_KEPT, _order_map_entries
from dhole_mcp.server import MasterFetchServer
from dhole_mcp.sitemap import SitemapResult, SitemapURL

BASE = "https://docs.test"
LOCALES = ["bn", "es", "ja", "de", "fr", "it", "pt", "ru", "zh"]
ORIGINALS = [f"/docs/{g}/{i}/index.html"
             for g in ("inference-generation", "quantization", "serving",
                       "features", "api") for i in range(20)]
TARGET = "/docs/api/11/index.html"


def _mirror_heavy() -> list[str]:
    """900 条语言副本打头（100 篇 × 9 语言），100 篇原文排在后面 —— 与基线同一形状。"""
    return [f"/{loc}{p}" for p in ORIGINALS for loc in LOCALES] + ORIGINALS


def _fake_walk(paths: list[str]):
    """`discover_sitemap` 的替身，口径抄 `sitemap.py:301-320`（先记 seen 再过 accept，
    被拒的计入 filtered_out，够 max_urls 即停）。不打真 socket：回环那组用例本身依赖
    套件顺序才绿（台账 #41），而 C4 判的是谁先走、账怎么报。"""
    def _discover(start_url, *, http_get, max_urls, accept=None):
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
def crowded(monkeypatch):
    monkeypatch.setattr("dhole_mcp.sitemap.discover_sitemap",
                        _fake_walk(_mirror_heavy()))


@pytest.fixture
def plain(monkeypatch):
    monkeypatch.setattr("dhole_mcp.sitemap.discover_sitemap", _fake_walk(ORIGINALS))


def _paths(r) -> list[str]:
    return [crawl_mod.urlparse(p.url).path for p in r.pages]


def _originals_in(r) -> int:
    return sum(1 for p in _paths(r) if p in ORIGINALS)


class TestTheOriginalsShip:

    @pytest.mark.asyncio
    async def test_a_mirror_heavy_sitemap_used_to_ship_zero_originals(self, crowded):
        r = await MasterFetchServer().web_discover(BASE, sitemap=True)
        assert TARGET in _paths(r), _originals_in(r)
        assert _originals_in(r) == len(ORIGINALS), (
            f"100 篇原文只交回 {_originals_in(r)} 篇")

    @pytest.mark.asyncio
    async def test_the_fold_is_counted_not_asserted(self, crowded):
        r = await MasterFetchServer().web_discover(BASE, sitemap=True)
        # 每份内容 10 个成员（9 副本 + 1 原文），留 3 折 7
        demoted = len(ORIGINALS) * (len(LOCALES) + 1 - MIRROR_VARIANTS_KEPT)
        assert f"{demoted} locale variant(s)" in r.summary, r.summary
        assert f"of {len(ORIGINALS)} page(s)" in r.summary, r.summary

    @pytest.mark.asyncio
    async def test_folding_moves_rows_it_does_not_delete_them(self, crowded):
        """折叠改变的只有顺序：地图的大小与尾巴的可得性都不许因此变少。"""
        srv = MasterFetchServer()
        r = await srv.web_discover(BASE, sitemap=True)
        assert r.truncated_by_total_chars is True, r.summary
        assert r.pages_discovered == len(_mirror_heavy()), r.pages_discovered
        shipped = set(_paths(r))
        tail = await srv.web_discover(BASE, resume_token=r.next_resume_token,
                                      max_total_chars=0)
        assert _originals_in(tail) == 0, "尾巴里还有原文：原文被折到后面去了"
        new = {p for p in _paths(tail) if p not in shipped}
        assert new, "续页一行都没有：被折掉的副本其实不存在"
        assert len(shipped) + len(tail.pages) == len(_mirror_heavy()), (
            "两页加起来不等于地图：有行在折叠里消失了")

    @pytest.mark.asyncio
    async def test_a_map_with_nothing_to_fold_says_nothing_about_folding(self, plain):
        """没有副本就不提折叠：一条永远为真的"我折了 N 个副本"与没有这条一样坏。"""
        r = await MasterFetchServer().web_discover(BASE, sitemap=True)
        assert "locale variant" not in r.summary, r.summary
        assert _originals_in(r) == len(ORIGINALS), _paths(r)
        assert r.pages_discovered == len(ORIGINALS), r.pages_discovered

    @pytest.mark.asyncio
    async def test_a_content_prior_that_actually_moved_something_says_so(self, plain):
        """`/docs/api/*` 在这一族里分更高，所以它确实前移了，而 moved 数的是这件事。

        这一条同时是"没有噪声重排"的反证：若 `_order_map_entries` 什么都不动，
        `moved` 必须是 0 且那句条款必须不出现 —— 两个方向都要能测出来。
        """
        r = await MasterFetchServer().web_discover(BASE, sitemap=True)
        paths = _paths(r)
        api_first = [p for p in paths if "/api/" in p]
        assert api_first and paths.index(api_first[0]) == 0, paths[:3]
        assert "moved)" in r.summary, r.summary


class TestTheRepresentativeIsChosenByTheGroup:
    """单趟"先到先得"会把原文折到后面 —— 那与本缺陷同形，不是修复。"""

    def test_the_locale_free_path_stays_up_front_with_the_variants_after_it(self):
        entries = [SitemapURL(url=f"https://d.test/{loc}/docs/a") for loc in LOCALES]
        entries.append(SitemapURL(url="https://d.test/docs/a"))
        order, stats = _order_map_entries(entries, query="")
        paths = [crawl_mod.urlparse(e.url).path for e in order]
        # 留在前面的三个是"原文 + 文档序最前的两个变体"。原文不必排在第一位 —— 同分
        # 时按文档序，而这个站点把原文列在自己的译本之后 —— 要紧的是它**在**最前那三个
        # 里，以及被折的是文档序第 4 到第 10 个变体，不是按语言码字母序挑一个来折。
        assert paths[:MIRROR_VARIANTS_KEPT] == ["/bn/docs/a", "/es/docs/a", "/docs/a"], paths
        assert stats["mirrors_demoted"] == len(LOCALES) + 1 - MIRROR_VARIANTS_KEPT
        assert paths[-1] != "/docs/a", "原文被自己的译本挤到了最后"

    def test_three_variants_of_a_page_are_left_alone(self):
        entries = [SitemapURL(url=f"https://d.test/{loc}/guide")
                   for loc in ("ja", "en-uk", "de")]
        entries.append(SitemapURL(url="https://d.test/docs/other"))
        order, stats = _order_map_entries(entries, query="")
        assert stats["mirrors_demoted"] == 0, stats
        assert [crawl_mod.urlparse(e.url).path for e in order] == [
            crawl_mod.urlparse(e.url).path for e in entries]

    def test_the_fourth_variant_is_the_one_that_moves(self):
        entries = [SitemapURL(url=f"https://d.test/{loc}/guide")
                   for loc in ("ja", "de", "fr", "es")]
        entries.append(SitemapURL(url="https://d.test/docs/other"))
        order, stats = _order_map_entries(entries, query="")
        assert stats["mirrors_demoted"] == 1, stats
        assert crawl_mod.urlparse(order[-1].url).path == "/es/guide", order
        assert stats["paths_total"] == 2, stats

    def test_a_two_letter_path_that_is_not_a_locale_never_forms_a_group(self):
        """孤立的 `/id/1234` 自成一键（组大小 1），在阈值那一关就被放行。

        钉的是**孤立**放行，不是"看起来像语言码" —— 首段像语言码但摘掉之后没有第二个
        成员与它同 key，就没有任何东西可折。
        """
        entries = [SitemapURL(url=f"https://d.test/id/{i}") for i in range(5)]
        order, stats = _order_map_entries(entries, query="")
        assert [e.url for e in order] == [e.url for e in entries]
        assert stats["mirrors_demoted"] == 0

    def test_the_order_is_stable_within_a_score(self):
        """同分保持文档序：重排不许在毫无信息时给站点自己的顺序加噪声。"""
        entries = [SitemapURL(url=f"https://d.test/docs/page{i}") for i in range(6)]
        order, stats = _order_map_entries(entries, query="")
        assert [e.url for e in order] == [e.url for e in entries]
        assert stats["moved"] == 0
