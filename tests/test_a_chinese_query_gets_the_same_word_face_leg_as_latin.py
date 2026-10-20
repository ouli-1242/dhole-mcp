r"""词面互证这条腿必须对中文也长出来：今天 `_query_terms` 只认拉丁词形，于是中文查询的三处读者一起 no-op。

现场（P4 写 R02-F23 时量到 · R02-046 · P2 · 组 E；不是画像现场）：

    产品自己的 67 条人工标注真查询（evals/corpus/serp/*.meta.json 的 query 字段）上直接调
    `_query_terms()`：min=0、median=3、**零集占比 21%**（14/67，全是中文/日文那几条）。

`_query_terms` 的词形来自 `_WORD_RE`（拉丁字母起头的串）再要 `len >= 3`，CJK 没有空格分词 → 空集 →
以它为原料的三处**同时**不动：`_filter_irrelevant_results` 的整轮清空、`_title_relevance` /
`_url_relevance` 两颗加成、丢弃账里那句「matched no query term」。R02-F23 已经把这件事**说出来**
（`fetch_hint` 在数不出词的那一轮点明档位只站一根桩），但「说出来」不等于「有」。

动手前的测量（`review/.runtime/p4/f26_measure_cjk.py`，两条腿各量一样东西）：
  * 查询侧：给 CJK 连段产二元组之后，67 条真查询的零集从 14 条变成 **0 条**（36 条有 CJK 连段，
    其余 31 条本来就有拉丁词）；词集中位数 2、最大 10。
  * 行侧：本轮已入账的 40 发真 SERP 里，「只有 CJK 词、拉丁为空」的轮有 9 发 —— 装上这条腿之后
    **7 发保留**（命中 1–6 个二元组）、**2 发会被清空**。那 2 发的标题就是清空的理由：
    「储能 电芯 出口 数据」换来的是 ACG猎奇图片/暗网猎奇/百测网论文润色，「光伏 硅料 价格 周度」
    换来的是 wxy88.top 的 uptime 查询站 —— 引擎给的确实是噪声，而今天这一族对中文是 no-op，
    它们就那么站在响应里。同一趟里 `那个` / `这个呢` 两发都**命中并保留**，所以这条腿不会把
    代词追问那族误清（也不设长度线：F23 已经量过任何长度线都会误伤正常的短中文问法）。

契约（本文件钉住）：
  * `_query_terms` 是**同一位作者**长出第二条腿：拉丁词形一条不变，CJK 连段产相邻二元组，
    连段长度为 1 时交出自己；跨空格/标点不接缝（「河南省 家电」不许产生「省家」）。
    三处读者一行都不改 —— 它们改不动是因为本来就只读这一个函数（反面锁：不许出现第二个分词器）。
  * 三处读者一起动起来：整轮清空对中文第一次真的能做事（实测那 2 发），命中时一行不丢；
    `_title_relevance` 对中文第一次给得出分（仍封顶 0.10）；`_url_relevance` 对 CJK 查询
    在 ASCII 路径上仍是 0 —— **不许把 URL 百分号编码当成命中来造分**。
  * 丢弃账那句「matched no query term」第一次有中文的读者（清空的轮数带着这句话交出去）。
  * 拉丁那一族一字不动（反面锁）：`MSI 80 主板` 这类混排查询里拉丁词形照旧、纯拉丁离题轮照旧会清。
  * F23 那句话随这条腿一起收窄：中文轮不再是「数不出词」那一格，`fetch_hint` 不该再说它只站一根桩；
    而符号那族（`???`）仍然数不出词，那句话必须留着。
"""

from __future__ import annotations

import inspect

from dhole_mcp.search import (SearchResult, _filter_irrelevant_results, _query_terms,
                              _title_relevance, _url_relevance, compute_fetch_hint, _drop_causes)

CHINESE_QUERY = "储能 电芯 出口 数据"
#: 实测那一发的三行标题原文（f26_measure_cjk.py 的 B 腿，本轮入账的真 SERP）
GARBAGE_TITLES = ["ACG猎奇【重口】图片大全-ACG猎奇【重口】高清好看的图片-花瓣桃奈小娘子的画板",
                  "暗网猎奇 -- 序 - 知乎",
                  "百测网-分析测试_论文润色_学术绘图_科研培训_试剂耗材_实验仪器_科研服务"]


def _row(pos: int, title: str, url: str = "https://a.example.org/p",
         snippet: str = "", raw: float | None = None) -> SearchResult:
    return SearchResult(title=title, url=url, position=pos, snippet=snippet,
                        relevance_score=1.0 - pos / 10, relevance_raw=raw,
                        fetch_relevance="high" if raw and raw >= 0.5 else "low")


# ─── 1 · 词面这条腿本身 ───

def test_a_chinese_query_now_yields_terms_that_can_be_checked():
    t = _query_terms(CHINESE_QUERY)
    assert {"储能", "电芯", "出口", "数据"} <= t, t


def test_a_mixed_query_keeps_the_latin_leg_and_adds_the_cjk_one():
    t = _query_terms("python asyncio 并发限制 semaphore")
    assert {"python", "asyncio", "semaphore"} <= t, t
    assert {"并发", "发限", "限制"} <= t, t


def test_bigrams_do_not_stitch_across_a_space_or_a_mark():
    """连段是被空格/标点切开的：接缝会造出页面里根本不存在的词。"""
    t = _query_terms("河南省 家电")
    assert "省家" not in t, t
    assert {"河南", "南省", "家电"} <= t, t


def test_a_lone_cjk_character_is_a_term_and_a_symbol_only_query_still_is_not():
    assert _query_terms("嗯") == {"嗯"}
    assert _query_terms("??? !!!") == set()


def test_the_real_corpus_queries_all_reach_a_nonempty_term_set():
    """27% 的零集是这条腿的题面 —— 装好之后，产品自己的真查询里一条都不该剩空集。"""
    real = ["储能 电芯 出口 数据", "光伏 硅料 价格 周度", "那个", "这个呢",
            "河南省 2025 家电以旧换新 征集公告 申报条件", "代工厂 换供应商 爬坡期 品牌",
            "就那种做乡村阅读的，他们自己发了数据的那种", "那几本里有开放获取的吗",
            "python asyncio 并发限制 semaphore 用法", "MDN fetch API"]
    empty = [q for q in real if not _query_terms(q)]
    assert empty == [], empty


# ─── 2 · 三处读者一起动起来 ───

def test_the_off_topic_round_of_garbage_is_dropped_for_a_chinese_query():
    """实测那两发之一：中文查询第一次真的有人管得住这一轮（今天它是 no-op）。"""
    rows = [_row(i + 1, t) for i, t in enumerate(GARBAGE_TITLES)]
    assert _filter_irrelevant_results(rows, CHINESE_QUERY) == []


def test_a_round_that_carries_one_of_the_terms_keeps_every_row():
    rows = [_row(1, "2025年中国储能电池出口数据发布", snippet="电芯出口同比增长"),
            _row(2, "行业报告：光伏硅料价格周度走势"),
            _row(3, "某论坛：讨论一个无关话题")]
    assert len(_filter_irrelevant_results(rows, "储能 电芯 出口 数据")) == 3


def test_the_latin_family_is_untouched():
    """反面锁：拉丁那一族的清空照旧，且中文二元组不许混进拉丁词集去放宽它。"""
    rows = [_row(1, "SpaceX launch schedule", url="https://space.com/x")]
    assert _filter_irrelevant_results(rows, "kettle bell recipe") == []
    assert _query_terms("MSI 80 主板 缺货") >= {"msi"}


def test_the_title_boost_is_available_to_chinese_for_the_first_time():
    q = "河南省 征集公告"
    hit = _title_relevance(q, "河南省2025年家电产品以旧换新品牌厂家征集公告")
    miss = _title_relevance(q, "今日菜谱：红烧肉的做法")
    assert hit > 0.0, hit
    assert hit <= 0.10, hit
    assert miss == 0.0, miss


def test_the_url_boost_does_not_fabricate_a_cjk_hit():
    """ASCII 路径里没有中文 —— 这颗加成对中文就该是 0，不许解码百分号去凑分。"""
    assert _url_relevance("储能 电芯 出口 数据", "https://www.gov.cn/news/20250106/a.html") == 0.0


def test_the_drop_account_names_the_term_leg_for_a_chinese_round():
    """丢弃账那句话第一次有中文的读者 —— 清空一轮的人必须说得出是哪一因。"""
    sentence = _drop_causes(rank_dropped=0, low_dropped=0, off_topic=3,
                            floors=[], verdict_ran=True)
    assert "3 matched no query term" in sentence, sentence


# ─── 3 · F23 那句话随这条腿收窄（两侧都要） ───

def test_a_chinese_round_is_no_longer_claimed_to_have_no_query_terms():
    rows = [_row(1, "2025年中国储能电池出口数据发布", raw=0.9)]
    hint = compute_fetch_hint(rows, query=CHINESE_QUERY)
    assert "tokenized to nothing" not in hint, hint


def test_a_symbol_only_query_still_says_it_can_be_checked_by_nobody():
    rows = [_row(1, "SpaceX launch schedule", raw=0.9)]
    hint = compute_fetch_hint(rows, query="??? !!!")
    assert "tokenized to nothing" in hint, hint


# ─── 4 · 作者唯一 ───

def test_the_tokenizer_has_one_author():
    from dhole_mcp import search as search_mod

    src = inspect.getsource(search_mod)
    assert src.count("_CJK_TERM_RE = re.compile") == 1, "分词器出现第二个作者 = 三处读者会分叉"
    for fn in ("_filter_irrelevant_results", "_title_relevance", "_url_relevance"):
        body = inspect.getsource(getattr(search_mod, fn))
        assert "findall" not in body, fn + " 自己切词 = 第二条腿"
