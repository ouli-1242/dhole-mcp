"""「与这页相似的页」的第一条不许是这页自己（2026-10-08 真实使用模拟 · 刀10）

现场（本机真引擎，`.scratch/knife_p3_find_similar2.py`）：

    web_search(mode="find_similar", url="https://clig.dev/", max_results=8)
      1. https://clig.dev/                 <-- 问的就是它
      2. https://docs.oracle.com/.../cliconcepts.htm
      ...
    web_search(mode="find_similar", url="https://docs.python.org/3/library/os.path.html")
      4. http://docs.python.org/3/library/os.path.html?highlight=abspath
                                           <-- 同一页的另一副面孔（协议 + 查询串之别）

"给我与这页相似的页"，第一条答案是这页自己 —— 而它恰恰是调用方手上已经有的那一份，
名额还被它占掉一个。机制也是注定的：候选来自"拿原页标题去搜"，交叉编码器打的又是
(src_text, 候选) 这一对，原页与自己的相似度当然是全场最高。

现在这条路上，原页以及它的同页变体（协议 / www. / 尾斜杠 / 查询串 / 锚点之别）不进
结果，名额让给真正相似的一页。**去掉要说**：一个查询词都不变的列表少了一行，读者
只能猜是索引里没有 —— 与"每一处钳制都要 announce"是同一条纪律。

反向锁同样要紧：这只发生在 find_similar（普通搜索里那页是合法结果），以及
"相似"判据不比 URL —— 同 host 不同 path（`/docs/` 与 `/docs/intro`）必须留下。
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from dhole_mcp import search as search_mod
from dhole_mcp.search import _drop_the_source_page, _same_page
from dhole_mcp.search_engines import EngineReport, RawResult
from dhole_mcp.server import MasterFetchServer

SOURCE = "https://clig.dev/"


def _row(url: str, pos: int) -> RawResult:
    return RawResult(title=f"page at {url}", url=url, snippet="command line interface guidelines",
                     source="brave", position=pos)


class TestTheSamePageQuestion:
    """什么算"同一页"：引擎爱发的写法之别算，内容之别不算。"""

    @pytest.mark.parametrize("other", [
        "http://clig.dev/",                    # 协议
        "https://clig.dev",                    # 尾斜杠
        "https://www.clig.dev/",               # www.
        "https://clig.dev/?utm_source=x",      # 查询串
        "https://clig.dev/#top",               # 锚点
        "https://CLIG.dev/",                   # 主机大小写
    ], ids=["scheme", "slash", "www", "query", "fragment", "case"])
    def test_a_spelling_difference_is_the_same_page(self, other):
        assert _same_page(SOURCE, other), other

    @pytest.mark.parametrize("other", [
        "https://clig.dev/faq",                # 同站不同页
        "https://blog.clig.dev/",              # 子站
        "https://clig.dev.evil.test/",         # 后缀冒充
        "https://example.com/",                # 别的页
        "",                                    # 空
    ], ids=["path", "subdomain", "suffix-spoof", "other-host", "empty"])
    def test_a_content_difference_is_a_different_page(self, other):
        assert not _same_page(SOURCE, other), other


class TestTheDroppedRows:

    def test_the_source_and_its_twins_leave_the_list(self):
        rows = [_row("https://clig.dev/", 1), _row("https://other.test/a", 2),
                _row("http://www.clig.dev/?utm=1", 3), _row("https://other.test/b", 4)]
        kept, dropped = _drop_the_source_page(rows, SOURCE)
        assert [r.url for r in kept] == ["https://other.test/a", "https://other.test/b"]
        assert dropped == 2, "去掉几行要能报出数，不能只报'有东西被去掉'"

    def test_a_list_without_the_source_is_untouched(self):
        rows = [_row("https://other.test/a", 1), _row("https://clig.dev/faq", 2)]
        kept, dropped = _drop_the_source_page(rows, SOURCE)
        assert [r.url for r in kept] == [r.url for r in rows]
        assert dropped == 0, "没去掉却说去了 = 又一次谎报"


def _stub_engines(monkeypatch, urls, *, reranker_scores=None):
    """引擎与取源都不发真请求；检测器（若给了分数）按位置打高分。"""
    rows = [_row(u, i) for i, u in enumerate(urls, 1)]

    async def fake_multi_search(query, max_results, **kwargs):
        return rows, [EngineReport(name="brave", ok=True)]

    monkeypatch.setattr(search_mod, "multi_search", fake_multi_search)
    monkeypatch.setattr(search_mod, "ensure_reranker", AsyncMock(return_value=None))
    monkeypatch.setattr(search_mod, "fetch_source_for_similar",
                        AsyncMock(return_value={
                            "title": "The Command Line Interface Guidelines",
                            "body": "an open-source guide to help you write better "
                                    "command-line interfaces",
                            "status": 200, "reason": "", "detail": ""}))
    if reranker_scores is not None:

        class _Rer:
            def score(self, _a, docs):
                return list(reranker_scores)

            def is_available(self):
                return True

        monkeypatch.setattr(search_mod, "get_reranker", lambda: _Rer())


@pytest.mark.asyncio
async def test_the_source_page_is_not_a_similar_page(monkeypatch):
    """全链路（web_search 本体）：原页与它的 twin 都不许出现在结果里。"""
    urls = ["https://clig.dev/", "https://other.test/a", "http://www.clig.dev/?utm=1",
            "https://other.test/b", "https://clig.dev/faq"]
    # 交叉编码器给原页最高分 —— 正是"它必然排第一"的那个机制，判据要顶得住。
    _stub_engines(monkeypatch, urls, reranker_scores=[0.99, 0.4, 0.98, 0.3, 0.2])
    r = await MasterFetchServer().web_search(mode="find_similar", url=SOURCE,
                                             max_results=5, cache_ttl=0)
    got = [row.url for row in r.results]
    assert "https://clig.dev/" not in got, got
    assert "http://www.clig.dev/?utm=1" not in got, got
    assert "https://other.test/b" in got, f"名额没让给真相似的一页: {got}"
    assert "https://clig.dev/faq" in got, f"同站不同页被当成原页去掉了: {got}"


@pytest.mark.asyncio
async def test_the_removal_is_said_out_loud(monkeypatch):
    _stub_engines(monkeypatch, ["https://clig.dev/", "https://other.test/a"],
                  reranker_scores=[0.99, 0.4])
    r = await MasterFetchServer().web_search(mode="find_similar", url=SOURCE,
                                             max_results=5, cache_ttl=0)
    text = r.fetch_hint
    assert "1 row(s)" in text, f"去掉几行要报得出数: {text}"
    assert "page you asked about" in text, f"少了行却不说是哪一行：{text}"


@pytest.mark.asyncio
async def test_no_note_when_the_index_never_returned_the_source(monkeypatch):
    """索引本来就没给原页 —— 没有东西可 announce。"""
    _stub_engines(monkeypatch, ["https://other.test/a", "https://other.test/b"],
                  reranker_scores=[0.5, 0.4])
    r = await MasterFetchServer().web_search(mode="find_similar", url=SOURCE,
                                             max_results=5, cache_ttl=0)
    assert "row(s)" not in r.fetch_hint, r.fetch_hint


@pytest.mark.asyncio
async def test_an_ordinary_search_still_returns_that_page(monkeypatch):
    """反向锁：只在 find_similar 去重 —— 普通搜索里那一页是合法结果。"""
    _stub_engines(monkeypatch, ["https://clig.dev/", "https://other.test/a"],
                  reranker_scores=[0.99, 0.4])
    r = await MasterFetchServer().web_search(query="command line guidelines",
                                             max_results=5, cache_ttl=0)
    assert "https://clig.dev/" in [row.url for row in r.results], [
        row.url for row in r.results]
