"""验收刀9-3（F1）：搜「最近很火的 AI 产品」不该端上一屏黄暴 SEO 农场。

报告里的实测有两层。第一层是结果本身：前 7 条全是 yandex 系的农场页。第二层更糟——
**账面替它们作了证**：`relevance_score` 是本轮内的 min-max（`reranker.py:543-549`，
第一名恒 1.0，全分相同则全体 1.0），于是垃圾站在归一尺子上拿了 1.0。模型真正说过
"这一条不相关"的那一份数并没有丢，它一直带在 `relevance_raw` 上（实测那些片段 ~1e-4）。

所以这一刀不新造判决，只把已有的两份证据合起来用，并且**只丢模型已经认为弱的行**：

* 判据是形状，不是名单。`search.py` 里删掉手工域名信誉表那一次留下的理由（测不出差异、
  名单要人养）同样适用在这里 —— 这一族问的是"这串字符像不像人起的名字"和"这个标题
  是不是农场的话术"，两样都不随站点改版腐烂。
* 阈值坐在实测的空带里。语料是 `evals/corpus/serp` 那 67 题冻结页里各家引擎自己的
  parser 交回的 **799 条真实结果行**（与 `injection.py` 那五类形状阈值同一份语料）：
  两条形状判据各命中 **0 条**，词形硬档命中 **0 条**。曾经写进过一档"盗播话术"
  （在线观看 / 免费观看 / 高清完整版），量下来单颗命中 11 条、两颗同现仍命中 3 条，
  全是 `tv.sohu.com` / `m.ixigua.com` 这类**正版视频平台自己的标题** —— 那一档因此被
  撤掉了，而不是调低了阈值：一个会天天误伤答案的形状，教出来的是读者跳过整个字段。
* 丢了多少、为什么丢，必须进账。`_drop_causes` 里新增一因，与 `min_relevance` /
  "matched no query term" 并列 —— 三个因各有各的解药，合并成一个总数就谁也不指向。

`DHOLE_SEARCH_ROW_FILTER=off` 整族关掉；过滤发生在写缓存之前，所以回放不会复活垃圾。
"""

from __future__ import annotations

import pytest

from dhole_mcp import search as S
from dhole_mcp.search import (SearchResult, _drop_causes, _reputation_filter,
                              _row_is_junk, _row_text_is_junk,
                              _row_url_is_farm_shaped)
from dhole_mcp.search_engines import EngineReport, RawResult

FARM_ONE = "http://d3gbed2ley04jq.cloudfront.net/player.html"
FARM_TWO = "https://1za7kjca.aass341.sbs/v/9f3.html"
# 实测里"字母+数字"混排却**是**人名/品牌名的三家（799 条语料里唯一带混排标签的三行）。
BRANDS = ["https://regex101.com/page", "https://eathealthy365.com/a",
          "https://www.heyuan110.com/a", "https://www.360doc.com/",
          "https://m.kuaibo88.com/dp/a.html"]


def _row(url, *, raw=None, title="Normal title", snippet=""):
    return SearchResult(title=title, url=url, snippet=snippet, relevance_raw=raw)


# ─────────────── 形状：两条判据各自抓谁、都不抓谁 ───────────────

def test_each_shape_rule_catches_the_host_it_was_measured_on():
    """报告里那两例分别落在两条不同的形状判据上（各自 0/799 命中真结果行）。"""
    assert _row_url_is_farm_shaped(FARM_ONE).startswith("leftmost host label")
    assert "2 letter+digit labels" in _row_url_is_farm_shaped(FARM_TWO)


@pytest.mark.parametrize("url", BRANDS)
def test_a_host_that_is_a_word_with_digits_at_the_end_is_not_a_shape(url):
    """`regex101` / `heyuan110` 是"一个词 + 尾巴缀数字"，那是人起的名字。

    数字夹在中间才是机器摇的那一形。这一条不是假设：这三家就是 799 条真实结果行里
    唯一带字母+数字混排标签的主机，而阈值把它们全留在门外（量到 0 命中才敢定这条线）。
    """
    assert _row_url_is_farm_shaped(url) == "", url


# ─────────────── 词形：只认硬档，正版视频页不在这一族 ───────────────

@pytest.mark.parametrize("title,snippet", [
    ("某剧 全集 在线观看", "免费观看高清完整版"),
    ("某剧 免费播放 在线观看", "高清"),
])
def test_a_legit_video_platform_title_is_not_spam_wording(title, snippet):
    """这一档被**撤掉**了，不是没想到：799 条真实结果行里"在线观看/免费观看"命中 11 条，
    两颗同现仍有 3 条，全是 `tv.sohu.com` / `m.ixigua.com` 自己的标题。

    一个会天天误伤答案的形状留着，教出来的是读者跳过整个字段（`injection.py` 给那五类
    形状定阈值时写的是同一条理由）。盗播那一族因此只由主机形状管。
    """
    assert _row_text_is_junk(_row("https://tv.sohu.com/v/abc.html",
                                  title=title, snippet=snippet)) == ""


def test_a_hard_wording_alone_convicts():
    for title in ("棋牌注册送体验金", "真人视讯官方平台", "六合彩开奖结果投注计划",
                  "无删减版资源"):
        basis = _row_text_is_junk(_row("https://a.example.com/x", title=title))
        assert basis, title


# ─────────────── eligibility：只丢模型已经认为弱的那一行 ───────────────

def test_a_row_the_model_called_relevant_is_never_dropped():
    """农场形状 + raw=0.9 也留着。宁可漏滤，不误杀一条真相关。"""
    assert _row_is_junk(_row(FARM_ONE, raw=0.9)) == ""


def test_a_weak_row_with_only_a_shape_is_dropped_and_says_which_shape():
    basis = _row_is_junk(_row(FARM_TWO, raw=0.0001))
    assert "letter+digit labels" in basis, basis
    assert "relevance_raw=0.0001" in basis, basis


def test_with_no_verdict_a_shape_alone_drops_nothing():
    """`relevance_raw` 是 null 表示这一轮没有模型判断过 —— 缺席不是 0 分。

    那一轮里丢弃必须两份证据同现（形状 + 词形），否则"没人量过"会被读成"量出来是 0"。
    """
    assert _row_is_junk(_row(FARM_ONE, raw=None)) == ""
    both = _row(FARM_ONE, raw=None, title="无删减版 真人视讯入口")
    assert "no reranker verdict" in _row_is_junk(both), _row_is_junk(both)


def test_the_filter_drops_the_farm_rows_and_keeps_the_rest():
    rows = [_row(FARM_ONE, raw=1e-4, title="AI video tool"),
            _row(FARM_TWO, raw=2e-4, title="hot AI product"),
            _row("https://goenhance.com/blog/hotel-lobby", raw=0.97,
                 title="Hotel Lobby explained"),
            _row("https://tv.sohu.com/v/abc.html", raw=0.3, title="某剧 在线观看")]
    kept = [r.url for r in _reputation_filter(rows)]
    assert kept == ["https://goenhance.com/blog/hotel-lobby",
                    "https://tv.sohu.com/v/abc.html"], kept


def test_the_knob_switches_the_whole_family_off(monkeypatch):
    monkeypatch.setenv("DHOLE_SEARCH_ROW_FILTER", "off")
    rows = [_row(FARM_ONE, raw=1e-4), _row(FARM_TWO, raw=1e-4)]
    assert _reputation_filter(rows) == rows
    monkeypatch.delenv("DHOLE_SEARCH_ROW_FILTER")
    assert _reputation_filter(rows) == [], "默认必须是开着的"


# ─────────────── 账：丢了几个、为什么，必须进那一份逐因清单 ───────────────

def test_the_drop_lands_in_the_per_cause_ledger_next_to_the_other_three():
    reasons = _drop_causes(rank_dropped=0, low_dropped=0, off_topic=0,
                           farm_dropped=2, floors=[], verdict_ran=True)
    assert reasons.startswith("2 dropped as SEO-farm rows"), reasons
    assert "DHOLE_SEARCH_ROW_FILTER=off" in reasons, reasons
    # 四因并列时顺序与总数不许互相吞：合并成一句话就谁也不指向哪个旋钮。
    all_four = _drop_causes(rank_dropped=1, low_dropped=1, off_topic=1,
                            farm_dropped=1, floors=["min_relevance=0.2"],
                            verdict_ran=True)
    assert all(s in all_four for s in ("1 below min_relevance=0.2", "1 labelled low",
                                        "1 matched no query term",
                                        "1 dropped as SEO-farm rows")), all_four


# ─────────────── 走一次真流水线：账要说出口，缓存里不许留垃圾 ───────────────

def _patch(monkeypatch, rows, *, raws=None):
    """引擎与缓存两处桩；`_rank` 不桩，让 raw 沿着 `neural_raw` 走完整条链。"""
    rows = list(rows)
    for r, v in zip(rows, raws or []):
        r.neural_raw = v

    async def fake_multi(query, max_results, **kwargs):
        return rows, [EngineReport(name="bing", ok=True, status="ok")]

    async def no_cache(*a, **k):
        return None

    stored: dict = {}

    async def capture(url, extraction_type, content, *a, **k):
        stored["payload"] = content

    async def pass_through(*a, **k):
        return None

    monkeypatch.setattr(S, "multi_search", fake_multi)
    monkeypatch.setattr(S, "get_cached", no_cache)
    monkeypatch.setattr(S, "set_cached", capture)
    monkeypatch.setattr(S, "ensure_reranker", pass_through)
    return S, stored


def _rr(title, url, snippet=""):
    return RawResult(title=title, url=url, snippet=snippet, source="bing",
                     position=1, consensus=1, sources=("bing",))


@pytest.mark.asyncio
async def test_the_live_round_drops_them_and_says_how_many(monkeypatch):
    """报告里那一次的原形状：一条中文查询，两行农场 + 一行真答案。

    `_quality_filter` 在 3 行以下不动手（不许把一轮清到少于 min_keep），而
    `_filter_irrelevant_results` 对中文是 no-op（`_query_terms` 切不出 >=3 字符的拉丁
    词）—— 所以这一屏垃圾在这一刀之前**两道门都不拦**，走到这里才第一次有人管。
    """
    srv, _stored = _patch(
        monkeypatch,
        [_rr("AI视频 生成工具 爆火", FARM_ONE),
         _rr("热门AI产品 在线观看", FARM_TWO),
         _rr("Hotel Lobby explained", "https://goenhance.com/blog/hotel-lobby")],
        raws=[1e-4, 2e-4, 0.97])
    out = await srv.web_search(None, "最近很火的 AI 产品", 5, cache_ttl=0)
    assert [r.url for r in out.results] == ["https://goenhance.com/blog/hotel-lobby"], \
        out.results
    assert "2 dropped as SEO-farm rows" in (out.fetch_hint or ""), out.fetch_hint


@pytest.mark.asyncio
async def test_the_cache_holds_the_filtered_rows_not_the_farm(monkeypatch):
    """过滤必须在 `set_cached` 之前：否则第二次问同一句，垃圾从缓存里复活。"""
    srv, stored = _patch(
        monkeypatch,
        [_rr("AI视频 生成工具", FARM_ONE),
         _rr("Hotel Lobby explained", "https://goenhance.com/blog/hotel-lobby")],
        raws=[1e-4, 0.97])
    await srv.web_search(None, "最近很火的 AI 产品", 5, cache_ttl=60)
    assert FARM_ONE not in str(stored.get("payload")), stored.get("payload")


@pytest.mark.asyncio
async def test_the_knob_off_leaves_the_farm_in_the_round(monkeypatch):
    monkeypatch.setenv("DHOLE_SEARCH_ROW_FILTER", "off")
    srv, _stored = _patch(
        monkeypatch, [_rr("AI视频 生成工具", FARM_ONE),
                      _rr("Hotel Lobby explained",
                          "https://goenhance.com/blog/hotel-lobby")],
        raws=[1e-4, 0.97])
    out = await srv.web_search(None, "最近很火的 AI 产品", 5, cache_ttl=0)
    assert FARM_ONE in [r.url for r in out.results], out.results
    assert "SEO-farm" not in (out.fetch_hint or ""), out.fetch_hint
