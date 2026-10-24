"""web_recall 的钉子：本机已读的页，一次检索就回来——指针不是正文，过期不是消失。

被钉的行为（每条对应一次 2026-10-24 的实测或一次真库形状）：
  * 中文页以 \\uXXXX 转义存库（json.dumps ensure_ascii=True 的既成事实）——查询词
    必须翻成同一形状才打得中（真库实测：不翻 0 行、翻了 3 行）；
  * 过期行照样召回、如实标 is_stale——TTL 过期只作废正文，不作废「读过哪儿」；
  * search 行（scope='search'）永不召回——那是别的查询的结果行，不是页面；
  * 全程零网络（socket 一响就炸）——这是这颗门 openWorldHint=False 的合同；
  * 神经档在场才用、缺席降级 BM25、跑题行剪掉并计数——每一条都如实出现在回执里。

所有用例都走自建的临时 cache.db（set_cached 是生产写入路径），与真库零接触；
reranker 一律打桩——conftest 每用例重置单例，真模型在本机盘上，不打桩的话用例
就依赖「这台机器下载过 119MB」这件与代码无关的事。
"""

import asyncio
import json
import time

import pytest
import pytest_asyncio

from dhole_mcp.cache import set_cached
from dhole_mcp.recall import recall_pages


@pytest_asyncio.fixture
async def cache_dir(tmp_path):
    return tmp_path


async def _seed(cache_dir, url, text, *, extraction_type="markdown", ttl=3600,
                scope="fetch", envelope=None):
    await set_cached(url, extraction_type, [text], 200, ttl=ttl,
                     cache_dir=cache_dir, scope=scope, envelope=envelope)


def _no_network(monkeypatch):
    """一次出网就是一次违约：recall 的合同是「零网络」，用 socket 闸钉住。"""
    import socket

    def _explode(*a, **k):
        raise AssertionError("web_recall touched the network - it must not")

    monkeypatch.setattr(socket, "create_connection", _explode)
    monkeypatch.setattr(socket.socket, "connect", _explode)


class TestRecallFindsWhatWasRead:

    @pytest.mark.asyncio
    async def test_a_chinese_page_hides_behind_json_escapes_until_the_query_wears_them(
            self, cache_dir, monkeypatch):
        """存储层把中文写成 \\uXXXX —— 查询词不翻成那个形状就永远打不中。

        真库实测（2026-10-24）：'注意力机制' 原文 LIKE 0 行，翻成转义 3 行。
        这条钉住的是「翻」这个动作本身：哪天有人把 set_cached 改成 ensure_ascii=False
        或换了存储编码，这条用例会用失败说出「预筛与存储层的合同要重新对齐」。
        """
        _no_network(monkeypatch)
        await _seed(cache_dir, "https://a.example/survey",
                    "深度学习模型中的注意力机制研究综述，覆盖各类注意力模型的演进。")
        await _seed(cache_dir, "https://a.example/other", "完全无关的一页内容。")
        out = await recall_pages("注意力机制", cache_dir=cache_dir)
        urls = [r["url"] for r in out["results"]]
        assert "https://a.example/survey" in urls
        assert "https://a.example/other" not in urls
        assert out["terms_found"], out

    @pytest.mark.asyncio
    async def test_an_english_query_finds_the_page_without_escaping(
            self, cache_dir, monkeypatch):
        _no_network(monkeypatch)
        await _seed(cache_dir, "https://a.example/asyncio",
                    "asyncio is a library to write concurrent code with async/await.")
        out = await recall_pages("asyncio timeout", cache_dir=cache_dir)
        assert [r["url"] for r in out["results"]] == ["https://a.example/asyncio"]
        assert "asyncio" in out["terms_found"]

    @pytest.mark.asyncio
    async def test_the_reply_is_pointers_not_the_page_itself(
            self, cache_dir, monkeypatch):
        _no_network(monkeypatch)
        body = "注意力机制的综述正文。每一段都在讲注意力。" * 20
        env = {"metadata": {"title": "注意力机制综述"},
               "quality_score": 0.9}
        await _seed(cache_dir, "https://a.example/survey", body, envelope=env)
        out = await recall_pages("注意力机制", cache_dir=cache_dir)
        row = out["results"][0]
        assert row["url"] == "https://a.example/survey"
        assert row["title"] == "注意力机制综述"
        # 摘录是窗口，不是正文：240 字帽 + 省略号语义
        assert len(row["snippet"]) <= 260
        assert "注意力" in row["snippet"]
        # 没有正文字段——正文要走 web_read
        for key in ("content", "markdown", "text", "body"):
            assert key not in row
        assert "web_read" in out["note"]


class TestTheFreshnessVerdict:

    @pytest.mark.asyncio
    async def test_an_expired_page_still_recalls_and_says_so(
            self, cache_dir, monkeypatch):
        """TTL 过期 = 「这页该重抓」，不是「没读过」。召回 + is_stale=true 同行交付。"""
        _no_network(monkeypatch)
        await _seed(cache_dir, "https://a.example/old", "注意力机制的老综述。", ttl=60)
        # 把这一行拨回两天前（直接 SQL，set_cached 的 fetched_at 是写入时刻）
        import aiosqlite
        async with aiosqlite.connect(cache_dir / "cache.db") as db:
            await db.execute("UPDATE cache SET fetched_at = ? WHERE url = ?",
                             (time.time() - 2 * 86_400, "https://a.example/old"))
            await db.commit()
        out = await recall_pages("注意力机制", cache_dir=cache_dir)
        row = out["results"][0]
        assert row["is_stale"] is True
        assert row["age_days"] >= 1.9

    @pytest.mark.asyncio
    async def test_a_fresh_page_recalls_with_stale_false(
            self, cache_dir, monkeypatch):
        _no_network(monkeypatch)
        await _seed(cache_dir, "https://a.example/new", "注意力机制的新综述。", ttl=3600)
        out = await recall_pages("注意力机制", cache_dir=cache_dir)
        assert out["results"][0]["is_stale"] is False


class TestTheBoundaries:

    @pytest.mark.asyncio
    async def test_search_rows_are_not_pages_and_never_recall(
            self, cache_dir, monkeypatch):
        _no_network(monkeypatch)
        await _seed(cache_dir, "注意力机制 综述",
                    json.dumps([{"url": "https://hit.example/1", "title": "hit"}]),
                    scope="search")
        out = await recall_pages("注意力机制", cache_dir=cache_dir)
        assert out["results"] == []
        assert out["scanned_rows"] == 0

    @pytest.mark.asyncio
    async def test_an_empty_cache_answers_zero_without_error(
            self, cache_dir, monkeypatch):
        _no_network(monkeypatch)
        out = await recall_pages("注意力机制", cache_dir=cache_dir)
        assert out["results"] == []
        assert out["scanned_rows"] == 0
        assert out["terms_absent"]

    @pytest.mark.asyncio
    async def test_a_query_of_only_punctuation_is_refused_by_name(
            self, cache_dir, monkeypatch):
        _no_network(monkeypatch)
        with pytest.raises(ValueError, match="searchable word"):
            await recall_pages("！？…、", cache_dir=cache_dir)

    @pytest.mark.asyncio
    async def test_like_metacharacters_in_a_query_are_escaped_not_executed(
            self, cache_dir, monkeypatch):
        """% _ 与引号进了 LIKE 要么按字面匹配、要么不中——绝不能变成通配或注入。"""
        _no_network(monkeypatch)
        await _seed(cache_dir, "https://a.example/plain", "普通的一页。")
        out = await recall_pages("100%_of'%\"things", cache_dir=cache_dir)
        # 这个查询分词后的词没有一个命中种子页——空结果且没有炸
        assert out["results"] == []


class TestTheNeuralTier:

    @pytest.mark.asyncio
    async def test_without_the_model_the_bm25_order_stands_and_says_so(
            self, cache_dir, monkeypatch):
        _no_network(monkeypatch)
        # download=False 的合同：模型缺席就缺席，绝不触发下载
        async def _absent(*, download: bool = True):
            assert download is False, "recall must never ask for a download"
            return None
        monkeypatch.setattr("dhole_mcp.reranker.ensure_reranker", _absent)
        await _seed(cache_dir, "https://a.example/survey", "注意力机制综述。")
        out = await recall_pages("注意力机制", cache_dir=cache_dir)
        assert out["engine"] == "bm25"
        assert all("relevance" not in r for r in out["results"])

    @pytest.mark.asyncio
    async def test_the_neural_floor_cuts_the_offtopic_band_and_counts_it(
            self, cache_dir, monkeypatch):
        """raw_med 是模型自己量出的跑题线（对题 0.93+ / 跑题 ~1e-4）——线下剪，线上留。"""
        _no_network(monkeypatch)

        class _FakeRR:
            def score(self, query, docs):
                # 两个候选：第一个对题，第二个是词碰上但内容跑题
                return [0.97, 0.0001]

        async def _warm(*, download: bool = True):
            return _FakeRR()

        monkeypatch.setattr("dhole_mcp.reranker.ensure_reranker", _warm)
        monkeypatch.setattr("dhole_mcp.reranker.raw_thresholds",
                            lambda: (0.5, 0.05))
        await _seed(cache_dir, "https://a.example/on", "注意力机制的综述。")
        await _seed(cache_dir, "https://a.example/off", "注意力这个词出现在无关段落里。")
        out = await recall_pages("注意力机制", cache_dir=cache_dir)
        assert out["engine"] == "neural"
        urls = [r["url"] for r in out["results"]]
        assert urls == ["https://a.example/on"]
        assert out["results"][0]["relevance"] == 0.97
        assert out["dropped_low_relevance"] == 1


class TestTheClamps:

    @pytest.mark.asyncio
    async def test_limit_clamps_and_rows_respect_it(self, cache_dir, monkeypatch):
        _no_network(monkeypatch)
        for i in range(6):
            await _seed(cache_dir, f"https://a.example/p{i}",
                        f"注意力机制第 {i} 篇。")
        out = await recall_pages("注意力机制", limit=3, cache_dir=cache_dir)
        assert len(out["results"]) == 3

    @pytest.mark.asyncio
    async def test_an_html_row_recalls_with_a_readable_snippet(
            self, cache_dir, monkeypatch):
        """html 行的正文是原始标记——摘录必须褪成可见文本，不是标签汤。"""
        _no_network(monkeypatch)
        await _seed(cache_dir, "https://a.example/page",
                    "<html><head><style>body{color:red}</style></head>"
                    "<body><h1>注意力机制</h1><p>综述的正文段落。</p></body></html>",
                    extraction_type="html")
        out = await recall_pages("注意力机制", cache_dir=cache_dir)
        snip = out["results"][0]["snippet"]
        assert "<" not in snip and "body{color:red}" not in snip
        assert "注意力机制" in snip


class TestTheWireFace:

    def test_the_tool_is_registered_local_only(self):
        """第 13 颗：required=query、开闭世界诚实、两颗参数。"""
        from dhole_mcp.server import MasterFetchServer
        td = next(t for t in MasterFetchServer._TOOL_DEFS
                  if t["name"] == "web_recall")
        assert td["inputSchema"]["required"] == ["query"]
        assert set(td["inputSchema"]["properties"]) == {"query", "limit"}
        assert td["annotations"]["readOnlyHint"] is True
        # 12 颗兄弟全都要出去，唯独这一颗不需要：它一比特外网流量都不发
        assert td["annotations"]["openWorldHint"] is False

    def test_the_dispatch_door_knows_the_query_is_required(self):
        import dhole_mcp.server as srv
        assert "query" in srv._DISPATCH_REQUIRED["web_recall"]
        assert srv._TOP_LEVEL_ARGS["web_recall"] == frozenset({"query", "limit"})
