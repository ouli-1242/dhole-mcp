"""G28 回归：`crawl_urls` 的清单要能对得上账。

调用方交出 40 个 URL、拿回 32 页，必须能区分两件事：「这个站只有 32 页」和
「我丢了你 8 个」。后者意味着他要的那一页不在结果里，而结果里没有任何东西替他
说明这件事 —— 这一族的前例是 G1（链接被截断却不披露），静默丢数据比丢数据本身
更难查，因为它把「没找到」伪装成「本来就没有」。

去重发生在**规范化之后**：`/a` 与 `/a/` 是同一页，`#frag` 与 `?utm_source=` 也是。
所以「重复」的判定比字符串比较宽，报出来的数字也才是真的重复。
"""

from __future__ import annotations

import pytest

from dhole_mcp import crawl as crawl_mod
from dhole_mcp.server import ResponseModel, MasterFetchServer

SEED = "https://ledger.example.com/"
LEAVES = [f"https://ledger.example.com/p{i}" for i in range(4)]


class _LedgerServer:
    """每页秒回，只记录被请求到的 URL。"""

    def __init__(self):
        self.fetched: list[str] = []

    async def smart_fetch(self, url, **kwargs):
        self.fetched.append(url)
        return ResponseModel(url=url, status=200, content=["<p>body</p>"],
                             content_type="text/html", fetcher_used="http")


async def _crawl(server, urls, **kwargs):
    kwargs.setdefault("max_pages", 10)
    kwargs.setdefault("cache_ttl", 0)
    return await crawl_mod.smart_crawl(server, SEED, crawl_urls=urls, **kwargs)


class TestTheTally:

    @pytest.mark.asyncio
    async def test_duplicates_after_normalization_are_counted(self):
        server = _LedgerServer()

        out = await _crawl(server, [
            LEAVES[0],
            LEAVES[0] + "/",              # 同一个页面：尾部斜杠被规范化吃掉
            LEAVES[0] + "#section",        # 同一个页面：片段不参与去重
            LEAVES[1],
        ])

        assert out.urls_supplied == 4
        assert out.urls_deduped == 2
        assert out.pages_crawled == 2
        from dhole_mcp.crawl import normalize_url
        assert sorted(normalize_url(u) for u in server.fetched) == sorted(
            [normalize_url(LEAVES[0]), normalize_url(LEAVES[1])])
        assert "2 already in the list" in out.summary

    @pytest.mark.asyncio
    async def test_off_domain_entries_are_counted_not_swallowed(self):
        server = _LedgerServer()

        out = await _crawl(server, [LEAVES[0], "https://other.example.com/x"])

        assert out.urls_dropped_off_domain == 1
        assert out.pages_crawled == 1
        assert "1 off-domain" in out.summary

    @pytest.mark.asyncio
    async def test_a_list_longer_than_max_pages_reports_the_remainder(self):
        """max_pages 也管着调用方自己给的清单：超出的条目是「没去拿」，不是「站内没有」。"""
        server = _LedgerServer()
        urls = [f"https://ledger.example.com/p{i}" for i in range(12)]

        out = await _crawl(server, urls, max_pages=3)

        assert out.urls_supplied == 12
        assert out.urls_dropped_over_max_pages == 9
        assert out.truncated_by_max_pages is True
        assert "9 over max_pages=3" in out.summary
        assert "split them across calls" in out.next_action
        # 账要平：给进去的 = 抓到的 + 三类被丢的
        assert (out.urls_supplied == out.pages_crawled + out.urls_deduped
                + out.urls_dropped_off_domain + out.urls_dropped_over_max_pages)

    @pytest.mark.asyncio
    async def test_a_clean_list_reports_nothing_extra(self):
        server = _LedgerServer()

        out = await _crawl(server, LEAVES[:2])

        assert (out.urls_supplied, out.urls_deduped, out.urls_dropped_off_domain,
                out.urls_dropped_over_max_pages) == (2, 0, 0, 0)
        assert "crawl_urls:" not in out.summary

    @pytest.mark.asyncio
    async def test_a_bfs_crawl_leaves_the_fields_at_zero(self):
        """这几个字段说的是调用方的清单，自动发现模式下没有清单可报。"""
        server = _LedgerServer()

        out = await crawl_mod.smart_crawl(server, SEED, max_pages=2, max_depth=1,
                                          cache_ttl=0)

        assert out.urls_supplied == 0
        assert "crawl_urls:" not in out.summary


class TestTheEmptyResult:

    @pytest.mark.asyncio
    async def test_a_list_of_only_off_domain_urls_says_why(self):
        server = _LedgerServer()

        out = await _crawl(server, ["https://other.example.com/a",
                                    "https://other.example.com/b"])

        assert out.pages == []
        assert out.urls_dropped_off_domain == 2
        assert "nothing to crawl" in out.next_action
        assert "different host" in out.next_action

    @pytest.mark.asyncio
    async def test_a_repeated_url_is_crawled_once_and_says_so(self):
        """三条一样的 URL 不是「空结果」，是「一条 + 两条被去重」——账要报对这一边。"""
        server = _LedgerServer()

        out = await _crawl(server, [LEAVES[2]] * 3)

        assert out.pages_crawled == 1
        assert out.urls_deduped == 2
        assert len(server.fetched) == 1
        assert "2 already in the list" in out.summary
        assert "nothing to crawl" not in out.next_action


class TestTheWireText:

    def test_crawl_wire_names_the_accounting_fields(self):
        """新字段不进 wire 文本，调用方就永远不知道可以拿它对账。"""
        tools = {t["name"]: t for t in MasterFetchServer._TOOL_DEFS}
        text = tools["smart_crawl"]["description"]
        for field in ("urls_supplied", "urls_deduped", "urls_dropped_off_domain",
                      "urls_dropped_over_max_pages"):
            assert field in text, field

    def test_response_model_documents_the_drop_reasons(self):
        from dhole_mcp.crawl import CrawlResponseModel
        f = CrawlResponseModel.model_fields
        assert "max_pages" in f["urls_dropped_over_max_pages"].description
        assert "normalization" in f["urls_deduped"].description
        assert "host" in f["urls_dropped_off_domain"].description
