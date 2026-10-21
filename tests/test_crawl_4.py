"""test_crawl_4.py —— 由 3 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import pytest
from dhole_mcp import crawl as crawl_mod
from dhole_mcp.crawl import MIRROR_VARIANTS_KEPT, _order_map_entries
from dhole_mcp.server import MasterFetchServer
from dhole_mcp.sitemap import SitemapResult, SitemapURL
import asyncio
from dhole_mcp import server as SM
from dhole_mcp.crawl import CrawlPage, CrawlResponseModel
import re
from dhole_mcp import guide
from dhole_mcp.feed import FeedItem, FeedResult
from dhole_mcp.models import BulkResponseModel, ResponseModel
from dhole_mcp.search import SearchResponseModel, SearchResult


# ============================================================================
# 来源：test_a_mirror_heavy_sitemap_used_to_ship_zero_originals.py
# ============================================================================
# 地图交回的是原文还是它的译本 —— 文档序不该决定谁在这一页里（体验修复刀3-C4）。

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


# ============================================================================
# 来源：test_the_clock_a_fallback_returned_is_the_clock_the_call_used.py
# ============================================================================
# R01-039 · 回退那一路交回的钟，必须是这一次调用真用掉的钟（P09 邓佳 S3b）。

URL = "https://site.test/docs"
WORD = "grpc_rest"
MAP_MS = 44_371.0
SCAN_MS = 269.0
CLOCK_MS = 90_000


def _map_rows(n: int) -> list[CrawlPage]:
    return [CrawlPage(url=f"{URL}/page-{i}", title=f"Page {i}", depth=1) for i in range(n)]


def _hit_pages(n: int = 3) -> list[CrawlPage]:
    return [CrawlPage(url=f"{URL}/doc-{i}", title=f"Doc {i}", depth=1, content_ok=True,
                      content=[f"prose about {WORD} on page {i}"], error="")
              for i in range(n)]


@pytest.fixture
def stub(monkeypatch):
    """把两次 web_crawl（地图 / 补考）钉成可编程的替身，并把 `now` 换成假钟。

    假钟必须自己钉：这一刀审的就是数，数不许来自真实流逝的时间（那会造出一条今天绿、
    明天红的用例）。两次读数 = 地图开始、地图结束，于是 spent_ms = MAP_MS。
    """
    state = {"map_rows": 0, "discovered": 270, "pages": _hit_pages(),
             "map_error": "", "scan_error": ""}
    reads = [0.0, MAP_MS / 1000]
    monkeypatch.setattr(SM, "now", lambda: reads.pop(0) if reads else reads[-1])

    async def fake(self, url, **kw):
        if kw.get("discover_only"):
            return CrawlResponseModel(
                start_url=url, pages=_map_rows(state["map_rows"]),
                pages_discovered=state["discovered"], discover_only=True,
                duration_ms=MAP_MS, error=state["map_error"],
                summary="map of 270 URL(s)", next_action="")
        return CrawlResponseModel(
            start_url=url, pages=state["pages"], pages_crawled=len(state["pages"]),
            pages_discovered=state["discovered"], duration_ms=SCAN_MS,
            error=state["scan_error"], summary="crawled 3 page(s)", next_action="")

    monkeypatch.setattr(MasterFetchServer, "web_crawl", fake)
    return state


def _call(monkeypatch, **kw):
    """跑一次真调度，拿线上的那份载荷（`_dispatch` 交回的是 wire dict）。"""
    args = {"url": URL, "max_pages": 30, "search": WORD, "timeout": CLOCK_MS}
    args.update(kw)
    out = asyncio.run(MasterFetchServer()._dispatch("web_discover", args))
    if isinstance(out, tuple) and len(out) == 2:
        out = out[1]
    assert isinstance(out, dict), type(out)
    return out


# ─── 1 · 那一条读数 ─────────────────────────────────────────────

def test_the_clock_returned_is_the_clock_the_call_used(stub, monkeypatch):
    out = _call(monkeypatch)
    assert out["pages"], "这一发走的必须是补考接管那一路"
    assert out["duration_ms"] == pytest.approx(MAP_MS + SCAN_MS), out["duration_ms"]


def test_the_map_phase_is_not_dropped_when_its_result_is_replaced(stub, monkeypatch):
    """字段不许再说 0.27 秒：地图用了 44.4 秒这一件事必须在总数里。"""
    out = _call(monkeypatch)
    assert out["duration_ms"] > MAP_MS, out["duration_ms"]


def test_the_prose_and_the_field_name_the_same_total(stub, monkeypatch):
    """一句话与一颗字段不许各说一套：把句子里的数加起来必须等于字段。"""
    out = _call(monkeypatch)
    text = out.get("summary") or ""
    assert f"{MAP_MS:,.0f}ms" in text, text
    assert f"{SCAN_MS:,.0f}ms" in text, text
    assert out["duration_ms"] == pytest.approx(MAP_MS + SCAN_MS), (out["duration_ms"], text)


def test_the_budget_left_is_not_reported_as_time_spent(stub, monkeypatch):
    """`left` 是余额不是花费：这一发的读页只用了 269ms，不是那 45,629ms。"""
    out = _call(monkeypatch)
    text = out.get("summary") or ""
    assert f"{int(CLOCK_MS - MAP_MS)}ms this call had left went into" not in text, text
    assert "left" in text.lower(), text   # 余额仍可说，但要作为余额说


# ─── 2 · 其余三支：不许有的多算、不许有的漏算 ───────────────────

def test_the_no_hit_branch_counts_both_phases(stub, monkeypatch):
    """补考跑了但一个词都没命中：交回的是地图那份模型，两段的账同样都要在。"""
    stub["pages"] = [CrawlPage(url=f"{URL}/doc-{i}", title=f"Doc {i}",
                               content=["unrelated prose"], error="") for i in range(3)]
    out = _call(monkeypatch)
    assert out["pages"] == [], out["pages"]
    assert out["duration_ms"] == pytest.approx(MAP_MS + SCAN_MS), out["duration_ms"]
    assert f"{SCAN_MS:,.0f}ms" in (out.get("summary") or ""), out.summary


def test_the_branch_that_ran_no_scan_counts_the_map_only(stub, monkeypatch):
    """没跑补考就不许凭空多出一段耗时：那一支用的钟就是地图自己。"""
    stub["discovered"] = 270
    out = _call(monkeypatch, timeout=45_000)   # 45,000 - 44,371 < 4,000ms 门槛
    assert out["duration_ms"] == pytest.approx(MAP_MS), out["duration_ms"]
    assert "No content scan was run" in (out.get("summary") or ""), out.summary


def test_a_map_that_answered_keeps_its_own_clock(stub, monkeypatch):
    """正向不许被连带：地图筛得出行时压根没有第二次读，钟就是地图的。"""
    stub["map_rows"] = 5
    out = _call(monkeypatch)
    assert len(out["pages"]) == 5, out["pages"]
    assert out["duration_ms"] == pytest.approx(MAP_MS), out["duration_ms"]


# ============================================================================
# 来源：test_every_response_field_is_in_the_manual.py
# ============================================================================
# 响应字段在全语料里查无此人 = 这个字段对只读手册的 agent 不存在。

def _mentions(text: str, key: str) -> bool:
    """按词边界找字段名：`page` 不该被 `pages=1-5` 里的一段算成已解释。"""
    return re.search(rf"(?<![\w.]){re.escape(key)}(?![\w])", text) is not None


def _corpus() -> str:
    return "\n".join(p.read_text(encoding="utf-8")
                     for p in guide._ROOT.rglob("*.md"))


# 每个信封都过一遍。PrivateAttr 不在 model_fields 里，天然排除 —— 它们按设计
# 就不上线，要求手册解释一个不上线的内部读数，只会逼人往手册里塞噪声。
_MODELS = {
    "ResponseModel": ResponseModel,
    "BulkResponseModel": BulkResponseModel,
    "CrawlPage": CrawlPage,
    "CrawlResponseModel": CrawlResponseModel,
    "SearchResponseModel": SearchResponseModel,
    "SearchResult": SearchResult,
    "FeedResult": FeedResult,
    "FeedItem": FeedItem,
}


@pytest.mark.parametrize("name", sorted(_MODELS))
def test_every_response_field_is_named_in_the_manual(name):
    model = _MODELS[name]
    corpus = _corpus()
    missing = sorted(k for k in model.model_fields
                     if not _mentions(corpus, k))
    assert not missing, (
        f"{name} 的这些响应字段在全语料查无此人 —— 只读手册的 agent 不知道它们"
        f"存在，更不知道该怎么信它: {missing}。补进 references/fields.md"
        "（写清这个字段在什么条件下出现、读者该拿它做什么判断），"
        "或把字段从信封上撤下来。")


def test_the_response_field_coverage_surface_is_the_corpus_alone():
    """机制自检：表面若混进 wire 文本，覆盖率永远假绿；语料空了同理。

    请求侧那位（test_every_wire_parameter_is_findable_in_the_manual）撞的就是这个
    坑，这里同法自证。
    """
    corpus = _corpus()
    assert len(corpus) > 20_000, "语料空了：字段覆盖率会伪装成通过"
    # 一个确实上线、也确实写在手册里的字段必须能找到
    assert _mentions(corpus, "content_ok"), "content_ok 都不在语料里，说明表面取错了"
    # 而词边界必须真的在挡事：子串命中不算数
    assert not _mentions("escalation_pathway to retry", "escalation_path")
