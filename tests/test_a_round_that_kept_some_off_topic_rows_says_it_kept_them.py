r"""同一把尺子走了两条路时，回执要说得出这一次走的是哪条：几行不含查询词、为什么还留着。

现场（P02-01 祝野 S3/S4 相邻两发 · R02-002 · P2 · 组 E）：

    web_search('small appliance brand switched OEM supplier 2025 announcement')
      → 6 行里 4 行与查询词**零重合**，全部逐行保留（档位低，但没被丢），error=""
    web_search('小家电代工 上市公司 2024 年度报告 pdf 供应商 变化')（同会话下一发）
      → 6 行**一条都不含查询词** → 整轮弃发，并且解释得很清楚（"matched no query term" + 交出被丢的标题）

第二封是对的。第一封也不违反判据 —— `_filter_irrelevant_results` 只在**全轮一条都不命中**时动手。
坏在**没有一维说得出这次走的是哪条路**：同一会话里两种噪声处理，读者无法预测下一次拿到哪一种，
也无法判断眼前这几行离题行是"产品认为它们相关"还是"产品的闸只管整轮"。
（L1 临床画像 P02-03 第二现场同一形状：`ceftriaxone diazepam Y-site compatibility chart pdf`
交回 6 行 0 high/0 med/6 low，其中 5 行与查询词零重合——全是多普勒天气页。）

动手前量的形状（`review/.runtime/p4/f26_measure_cjk.py` 的 B 腿 + `f30_measure_zero_rows.py`，
都跑在产品自己的 16 发已入账真 SERP / 86 行上）：**14 行零重合**，
其中 3 轮是「一部分行零重合」（这一条抱怨的那一族）、2 轮全轮零重合（今天会被弃发的那一族）、11 轮干净。
—— 所以缺的不是判据，是可见性。台账写下了两条出路（统一，或把判据名字说出口），
本刀走第二条：**说出口**，因为"全轮都不命中才是噪声信号"这条判据本身是对的，
把它拉平到逐行去丢就会开始删用户能自己判断的行（§12 不弱化门禁）。

契约（本文件钉住）：
  * `fetch_hint` 交出这一轮里**几行与查询词零重合**（`N of M rows`），并说清为什么留着：
    只有**全轮**一条都不命中才是弃发的信号；
  * 数零重合的作者唯一：用的就是词面互证那一位 `_query_terms`（R02-046 之后它对中文也长得出腿），
    不许再写第二个分词器，也不许给某一族站点/语言开洞；
  * **反面锁一**：一轮里每一行都含查询词时，这一句一个字节都不许多出（没事实就不占线）；
  * **反面锁二**：这一句不许变成判决 —— 不许写 "irrelevant / off-topic"，也不许改任何行的档位、
    顺序、去留（交付一位不动：本刀只加一维读数）；
  * **反面锁三**：整轮被弃发时（`results=[]`）那句 `matched no query term` 的丢弃账照旧在，
    而这一句不出现（没有留下的行可数）；
  * 中文那一族照数（R02-046 的那条腿落地之后，这一维对两种文一致）。
"""

from __future__ import annotations

import inspect

from dhole_mcp.search import SearchResult, _query_terms, _tier, compute_fetch_hint

MIXED_QUERY = "small appliance brand switched OEM supplier 2025 announcement"
ON_TOPIC = "Brand X confirms it switched OEM supplier for small appliances in 2025"
OFF_TOPICS = [
    "Doppler radar forecast for tomorrow across the region",
    "How to bake a sourdough loaf at home",
    "Celebrity gossip: the summer party roundup",
    "Car insurance quotes compared side by side",
]


def _row(pos: int, title: str, url: str = "https://a.example.org/p",
         snippet: str = "", raw: float = 0.9) -> SearchResult:
    return SearchResult(title=title, url=url, position=pos, snippet=snippet,
                        relevance_score=1.0 - pos / 10, relevance_raw=raw,
                        fetch_relevance=_tier(raw, pos, 6, raw))


MIXED = ([_row(1, ON_TOPIC)]
         + [_row(i + 2, t, raw=0.03) for i, t in enumerate(OFF_TOPICS)]
         + [_row(6, "Supplier statement on the same announcement", raw=0.8)])


def test_the_hint_counts_the_rows_that_carry_no_query_term():
    hint = compute_fetch_hint(MIXED, query=MIXED_QUERY)
    assert "4 of 6 rows" in hint, hint


def test_the_hint_names_the_rule_that_kept_them():
    """说得出"为什么留着"：全轮都不命中才是弃发的信号。"""
    hint = compute_fetch_hint(MIXED, query=MIXED_QUERY)
    low = hint.lower()
    assert "dropped" in low or "kept" in low, hint
    assert "only when no row" in low or "whole round" in low, hint


# ─── 反面锁 ───

def test_a_round_where_every_row_carries_a_term_pays_no_extra_line():
    rows = [_row(1, ON_TOPIC), _row(2, "Supplier statement on the same announcement")]
    hint = compute_fetch_hint(rows, query=MIXED_QUERY)
    assert "rows carry no query term" not in hint, hint


def test_the_sentence_is_not_a_verdict():
    """不许把"零重合"说成"离题判决"—— 那是模型那一把尺的话，这把尺说不出。"""
    hint = compute_fetch_hint(MIXED, query=MIXED_QUERY)
    assert "irrelevant" not in hint.lower(), hint
    assert "off-topic" not in hint.lower(), hint


def test_the_rows_themselves_are_untouched():
    """交付一位不动：本刀只加一维读数，档位/顺序/去留一概不变。"""
    tiers_before = [r.fetch_relevance for r in MIXED]
    scores_before = [r.relevance_score for r in MIXED]
    compute_fetch_hint(MIXED, query=MIXED_QUERY)
    assert [r.fetch_relevance for r in MIXED] == tiers_before
    assert [r.relevance_score for r in MIXED] == scores_before


def test_an_emptied_round_still_keeps_the_drop_account_and_says_nothing_here():
    """整轮弃发那一支（反面锁三）：没有留下的行可数，那句话就不该出现。"""
    hint = compute_fetch_hint([], query=MIXED_QUERY)
    assert "rows carry no query term" not in hint, repr(hint)


def test_the_cjk_family_is_counted_by_the_same_leg():
    """R02-046 的那条腿之后，这一维对中文一致地数（不给某一族语言开洞）。"""
    rows = [_row(1, "2025年中国储能电池出口数据发布", snippet="电芯出口同比增长"),
            _row(2, "ACG猎奇【重口】图片大全", raw=0.03),
            _row(3, "暗网猎奇 -- 序 - 知乎", raw=0.03)]
    hint = compute_fetch_hint(rows, query="储能 电芯 出口 数据")
    assert "2 of 3 rows" in hint, hint


def test_a_round_with_no_reranker_reads_it_the_same_way():
    """这一维与档位无关：冷启动那一轮（没有 raw）也一样要说得出几行零重合。"""
    rows = [SearchResult(title=ON_TOPIC, url="https://a.example.org/1", position=1,
                         relevance_score=0.9, fetch_relevance="low"),
            SearchResult(title=OFF_TOPICS[0], url="https://a.example.org/2", position=2,
                         relevance_score=0.8, fetch_relevance="low")]
    hint = compute_fetch_hint(rows, query=MIXED_QUERY)
    assert "no reranker on this round" in hint, hint
    assert "1 of 2 rows carry no query term" in hint, hint


def test_the_number_it_prints_is_the_products_own_term_author():
    """句子里那颗数必须由 `_query_terms` 那位作者算出来 —— 测试自己也不另数一遍。"""
    terms = _query_terms(MIXED_QUERY)
    expected = sum(1 for r in MIXED
                   if not any(t in f"{r.title} {r.snippet} {r.url}".lower() for t in terms))
    hint = compute_fetch_hint(MIXED, query=MIXED_QUERY)
    assert expected == 4, expected          # 现场那一发的形状（六行里四行零重合）
    assert f"{expected} of {len(MIXED)} rows" in hint, hint


def test_the_counter_has_one_author():
    from dhole_mcp import search as search_mod

    src = inspect.getsource(search_mod.compute_fetch_hint)
    assert "_query_terms" in src, "零重合要数在词面互证那一位作者上"
    assert "findall" not in src, "fetch_hint 里自己切词 = 第二个作者"
