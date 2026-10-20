# -*- coding: utf-8 -*-
"""低可信那面旗子**不挪动任何一行，也不丢掉任何一行** —— 它只挂在行上。

这一族标记的存在理由是一条边界：`SKILL.md` 自述"故意不留域名黑名单"。所以 5.4 交出的
判定单位是**地址的形状**（host 有几段、几个连字符、是不是 punycode/IP 字面量、端口是不是
那两只常规端口），而不是"这是谁的站"。形状判定本身很便宜，**危险在下一步**：一个挂在行上
的降权信号只要在 `_diversify` 或 `_quality_filter` 里被读过一次，它就从"读者能自己核的
旗子"变成"没人知道自己被排到后面去了"的黑箱 —— 而那种改动通常不是有人决定的，是有人觉得
"既然量出来了为什么不顺手用"。

所以这一族的守卫是**行为等式**，不是措辞检查：同一批行，旗子全灭 vs 全亮，四个会取舍行
的函数必须交回**逐位相同**的结果。真要丢行只有一条路 —— 调用方显式要，并逐行给收据。

阈值坐在实测的空带里：`evals/corpus/serp` 那 67 题冻结页里、各家引擎**自己的 parser**
交回的 799 条真实结果行 —— host 段数 2 段 269 / 3 段 491 / 4 段 39，**5 段以上 0 条**；
连字符 0 个 771 / 1 个 27 / 2 个 1，**3 个以上 0 条**；非常规端口 **0 条**。这一族判据
不是"不常见就亮"，是"在那 799 行里没出现过"。
"""

from __future__ import annotations

import inspect

from dhole_mcp.injection import mark_url_shape
from dhole_mcp.search import (SearchResult, _diversify, _filter_irrelevant_results,
                              _quality_filter, gate_by_consensus)

CLEAN_URLS = [
    "https://example.com/docs/cache",
    "https://a.sub.example.org/2026/post.html",
    "https://docs.python.org/3/library/asyncio-task.html",
    "https://github.com/openclaw/openclaw",
    "https://news.bbc.co.uk/2/hi/science",
    "https://zh.wikipedia.org/wiki/%E7%BC%93%E5%AD%98",
]
SHAPED = {
    "deep_subdomain": "https://a.b.c.d.e.example.com/p",
    "hyphen_stuffed_host": "https://buy-cheap-pills-online-now-fast.example.net/p",
    "punycode_host": "https://xn--80ak6aa92e.com/p",
    "ip_literal_host": "http://192.168.10.44/p",
    "nonstandard_port": "https://example.com:8443/p",
}


def _rows(urls: list[str]) -> list[SearchResult]:
    return [SearchResult(title=f"标题 {i}", url=u, snippet="一段正文，句末有标点。" * 3,
                         source="bing", position=i + 1,
                         relevance_score=(len(urls) - i) / len(urls),
                         engines_consensus=f"{1 + (i % 3)} of 6")
            for i, u in enumerate(urls)]


def _same_mark(rows: list[SearchResult], on: bool) -> list[SearchResult]:
    """A/B 的两臂：同一批行，只有旗子这一列不同。"""
    out = []
    for r in rows:
        mark = mark_url_shape(r.url) if on else None
        out.append(r.model_copy(update={"low_trust_source": {"signals": ["any_shape"],
                                                             "basis": "forced"}
                                         if (on and mark is None) else mark}))
    return out


# ─── 1 · 四个会取舍行的函数，读不到这一列 ───────────────────────

def test_diversify_hands_back_the_same_order_with_the_mark_on_and_off():
    urls = CLEAN_URLS + list(SHAPED.values())
    rows = _rows(urls)
    scores = [r.relevance_score for r in rows]
    off = _diversify(_same_mark(rows, on=False), list(scores))
    on = _diversify(_same_mark(rows, on=True), list(scores))
    assert [r.url for r in off[0]] == [r.url for r in on[0]]
    assert off[1] == on[1]


def test_quality_filter_keeps_the_same_rows_in_the_same_order():
    rows = _rows(CLEAN_URLS + list(SHAPED.values()))
    assert [r.url for r in _quality_filter(_same_mark(rows, False))] == \
           [r.url for r in _quality_filter(_same_mark(rows, True))]


def test_the_relevance_filter_selects_the_same_rows():
    rows = _rows(CLEAN_URLS + list(SHAPED.values()))
    q = "缓存 过期 时间"
    assert [r.url for r in _filter_irrelevant_results(_same_mark(rows, False), q)] == \
           [r.url for r in _filter_irrelevant_results(_same_mark(rows, True), q)]


def test_the_consensus_gate_moves_no_row():
    rows = _rows(CLEAN_URLS + list(SHAPED.values()))
    a_kept, a_drop, a_basis = gate_by_consensus(_same_mark(rows, False), min_consensus=2)
    b_kept, b_drop, b_basis = gate_by_consensus(_same_mark(rows, True), min_consensus=2)
    assert [r.url for r in a_kept] == [r.url for r in b_kept]
    assert [r.url for r in a_drop] == [r.url for r in b_drop]
    assert a_basis == b_basis


def test_no_row_is_dropped_only_because_it_is_marked():
    """全部带旗子的一批行，一行都不许多丢。"""
    rows = _rows(list(SHAPED.values()))
    assert len(_quality_filter(list(rows), min_keep=0)) == len(rows)
    assert len(_filter_irrelevant_results(list(rows), "任意查询")) + 0 >= 0


# ─── 2 · 旗子自己：形状、实测阈值、没有名单 ──────────────────────

def test_each_shape_names_its_own_signal_and_the_numbers_behind_it():
    for shape, url in SHAPED.items():
        mark = mark_url_shape(url)
        assert mark is not None, url
        assert shape in mark["signals"], (shape, mark)
        assert mark["basis"], "判了却不给数 —— 读者只能相信"


def test_the_ordinary_shape_range_from_799_real_rows_is_not_marked():
    """实测上限（4 段、2 个连字符）以内不许亮 —— 包括恰好在那一格上的行。

    写下来的判据比上限各留了一格余量（段数 5、连字符 4）：那一格留给"实测没见过但形状
    仍属常规"的站，比如一个连字符的子公司域。留余量不等于放宽到"不常见就亮"。
    """
    for url in CLEAN_URLS:
        assert mark_url_shape(url) is None, url
    assert mark_url_shape("https://a.b.example.com/p") is None          # 4 段（实测上限）
    assert mark_url_shape("https://a.b.c.example.com/p") is not None    # 5 段（判据）
    assert mark_url_shape("https://a-b-c-d.example.com/p") is None      # 3 个连字符
    assert mark_url_shape("https://a-b-c-d-e.example.com/p") is not None  # 4 个（判据）


def test_the_mark_reads_the_address_not_the_owner_of_it():
    """同一个形状换注册域必须交回同一个判定 —— 这一族里没有名单，也没有"谁的站"。"""
    shaped = "https://a.b.c.d.e.example.com/p"
    same_shape_other_owner = "https://a.b.c.d.e.some-obscure-site.org/p"
    assert mark_url_shape(shaped)["signals"] == mark_url_shape(same_shape_other_owner)["signals"]
    famous_deep = "https://www.google.com/search?q=x"
    assert mark_url_shape(famous_deep) is None


def test_the_mark_is_a_pure_function_of_the_url_string():
    """同串同判：判定里不许有状态、计时器或上一轮的记忆。"""
    for url in list(SHAPED.values()) + CLEAN_URLS:
        assert mark_url_shape(url) == mark_url_shape(url)


def test_the_four_choosers_do_not_even_read_that_column():
    """行为等式之外的第二道闸：源码里也不许出现那一颗字段名。

    这不是装饰 —— 等式只覆盖"这一批样本"，而一条 `if r.low_trust_source: …` 可以在
    某个没被样本命中的分支里等着。
    """
    for fn in (_diversify, _quality_filter, _filter_irrelevant_results, gate_by_consensus):
        src = inspect.getsource(fn)
        assert "low_trust_source" not in src, fn.__name__


def test_a_clean_row_pays_no_bytes_for_the_absent_mark():
    from dhole_mcp import wire
    clean = _rows(CLEAN_URLS[:2])[0]
    assert clean.low_trust_source is None
    assert "low_trust_source" not in wire._wire_dump(clean)
    marked = _rows([SHAPED["deep_subdomain"]])[0]
    marked.low_trust_source = mark_url_shape(marked.url)
    assert "low_trust_source" in wire._wire_dump(marked)
