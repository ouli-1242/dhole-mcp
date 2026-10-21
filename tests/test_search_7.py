"""test_search_7.py —— 由 7 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import inspect
from dhole_mcp.search import SearchResult, _filter_irrelevant_results, _query_terms, _title_relevance, _url_relevance, compute_fetch_hint, _drop_causes
import re
from dhole_mcp import search
from dhole_mcp.search import _engine_detail_rows, _search_next_action, _way_out_for_a_dead_end
from dhole_mcp.search_engines import EngineReport
from unittest.mock import AsyncMock
import pytest
from dhole_mcp import search as search_mod
from dhole_mcp.search import _drop_the_source_page, _same_page
from dhole_mcp.search_engines import RawResult
from dhole_mcp.server import MasterFetchServer
from dhole_mcp.search import _follow_up_cause, _related_queries
from dhole_mcp.search import _tier
from dhole_mcp.search import _quality_filter
from dhole_mcp.wire import _wire_dump
from dhole_mcp.search import _RAW_HIGH_AT


# ============================================================================
# 来源：test_a_chinese_query_gets_the_same_word_face_leg_as_latin.py
# ============================================================================
# 词面互证这条腿必须对中文也长出来：今天 `_query_terms` 只认拉丁词形，于是中文查询的三处读者一起 no-op。

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


# ============================================================================
# 来源：test_a_way_out_that_names_a_key_the_caller_can_press.py
# ============================================================================
# R02-004 · 零结果那条「死路」必须给出一条按得下去的路，而且名字要来自这一发自己。

def _detail(*pairs) -> list[dict]:
    return [{"name": n, "verdict": v} for n, v in pairs]


HEALTHY_TWO = _detail(("bing", "healthy"), ("yandex", "healthy"),
                      ("brave", "blocked"), ("so360", "timeout"))


def test_the_way_out_names_the_engines_that_answered_this_round():
    line = _way_out_for_a_dead_end(HEALTHY_TWO)
    assert 'engines=["bing", "yandex"]' in line, line
    assert "engines=[...]" not in line, "空格不许留在有名字可给的那一发里"


def test_a_single_answering_engine_is_named_the_same_way():
    line = _way_out_for_a_dead_end(_detail(("sogou", "healthy"), ("brave", "blocked")))
    assert 'engines=["sogou"]' in line, line
    assert "engines=[...]" not in line


def test_the_name_list_comes_from_this_rounds_own_verdicts_not_a_registry():
    """只有 verdict=healthy 才算数：答了但空、没插探针、被撤下的都不许当名字端出去。"""
    line = _way_out_for_a_dead_end(_detail(
        ("bing", "healthy"), ("sogou", "upstream_empty"), ("brave", "not_instrumented"),
        ("duckduckgo", "preempted"), ("yandex", "stale"), ("mojeek", "parser_drift")))
    assert 'engines=["bing"]' in line, line
    for name in ("sogou", "brave", "duckduckgo", "yandex", "mojeek"):
        assert name not in line.split("engines=")[1], (name, line)


def test_nothing_answered_healthy_points_at_the_self_check_that_exists():
    line = _way_out_for_a_dead_end(_detail(("brave", "blocked"), ("baidu", "timeout")))
    assert 'sys_state(show=["engine_state"])' in line, line
    assert "engines=" not in line, "没人答的时候不许编一个名字"


def test_an_empty_table_also_points_at_the_self_check():
    """一家都没被问到（detail 为空）也不许留下空白：那把自检仍然答得出谁在被拦。"""
    line = _way_out_for_a_dead_end([])
    assert 'sys_state(show=["engine_state"])' in line, line
    assert "engines=" not in line


def test_the_sentence_is_not_written_as_a_promise():
    line = _way_out_for_a_dead_end(HEALTHY_TWO)
    assert "not a promise" in line, line


def test_the_discard_branch_carries_the_named_way_out():
    """题面那一发（引擎都答了、全被词面闸门丢光）：出路里要有名字，不要再是空参数。"""
    na = _search_next_action([], ["brave", "so360"],
                            "Engines delivered 6 results, but none came back - dhole dropped "
                            "them rather than return noise: 6 matched no query term.",
                            engines_used=["bing", "yandex"],
                            engine_detail=HEALTHY_TWO)
    assert 'engines=["bing", "yandex"]' in na, na
    assert "engines=[...]" not in na, na
    # 解释仍在（这一刀换掉的是空洞，不是那句诊断）
    assert "rephrasing cannot fix it" in na, na


def test_the_blocked_branch_still_says_the_seconds_and_adds_the_self_check():
    na = _search_next_action([], ["brave"], "No results (engines rate-limited).",
                             engines_used=[],
                             engine_detail=_detail(("brave", "blocked"),
                                                   ("so360", "timeout")))
    assert 'sys_state(show=["engine_state"])' in na, na
    assert "cooling" in na or "Retry" in na, na


def test_a_round_that_returned_rows_never_sells_a_way_out():
    """有行的时候这不是死路，那句出路一个字都不该出现（沉默锁）。"""
    from dhole_mcp.search import SearchResult
    rows = [SearchResult(title="年度报告", url="https://a.example/x", snippet="一段正文",
                         source="bing", position=1, relevance_score=0.9,
                         fetch_relevance="high")]
    na = _search_next_action(rows, [], "", engines_used=["bing"],
                             engine_detail=_detail(("bing", "healthy")))
    assert "sys_state" not in na, na
    assert "engines=[" not in na, na


def test_error_and_next_action_carry_the_same_way_out_word_for_word():
    """两处读作一条指示，所以不许一处给名字、另一处留给空参数。"""
    line = _way_out_for_a_dead_end(HEALTHY_TWO)
    err = ("Engines delivered 6 results, but none came back - dhole dropped them rather "
           "than return noise: 6 matched no query term. " + line)
    na = _search_next_action([], ["brave"], "…matched no query term…",
                             engines_used=["bing", "yandex"], engine_detail=HEALTHY_TWO)
    assert line in na, na
    assert "engines=[...]" not in err and "engines=[...]" not in na


def test_both_reply_authors_call_the_one_author_and_nobody_re_writes_it():
    src = inspect.getsource(search)
    # 定义 1 + `_search_next_action` 的五条出路里 4 条 + `web_search` 侧 2 条
    # （`_way_out` 那份共用底稿挂在四张 error 脸上，以及词面丢光那一支）
    assert src.count("_way_out_for_a_dead_end(") == 7, src.count("_way_out_for_a_dead_end(")
    flat = re.sub(r"\s+", "", inspect.getsource(search.web_search))
    assert flat.count("+_way_out)") == 4, "error 那四张脸必须都走同一句出路"
    for fn in (search._search_next_action, search.web_search):
        body = inspect.getsource(fn)
        assert "retry with engines=[...]" not in body, (
            "空参数还留在交回执的那一处 = 同一条死路有第二个作者")
    assert inspect.getsource(search._search_next_action).count(
        "_way_out_for_a_dead_end(") == 4


def test_the_detail_rows_are_built_by_the_engine_that_mints_them():
    """名字表用的就是上线那一份 `engine_detail` 的作者，不在别处再分类一次。"""
    reports = [EngineReport(name="bing", ok=True, status="ok", yield_verdict="healthy"),
               EngineReport(name="brave", blocked=True, status="blocked",
                            yield_verdict="blocked")]
    names = lambda line: line.split("engines=")[1].split(" -")[0]  # noqa: E731
    assert names(_way_out_for_a_dead_end(_engine_detail_rows(reports))) == \
        names(_way_out_for_a_dead_end(_detail(("bing", "healthy"))))


def test_the_manual_says_which_key_the_way_out_presses():
    from dhole_mcp import guide
    corpus = "\n".join(p.read_text(encoding="utf-8") for p in guide._ROOT.rglob("*.md"))
    assert re.search(r"(?<![\w.])engine_state(?![\w])", corpus), (
        "手册没写这条出路指的是哪一把自检")
    assert "answered this round" in corpus, (
        "手册没写这条出路点的是**本轮答过的**引擎，读者会以为是个静态名单")


# ============================================================================
# 来源：test_find_similar_does_not_return_the_page_you_asked_about.py
# ============================================================================
# 「与这页相似的页」的第一条不许是这页自己（2026-10-08 真实使用模拟 · 刀10）
# 重名消歧：_row → _row__find_similar_does_not_re_565d

SOURCE = "https://clig.dev/"


def _row__find_similar_does_not_re_565d(url: str, pos: int) -> RawResult:
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
        rows = [_row__find_similar_does_not_re_565d("https://clig.dev/", 1), _row__find_similar_does_not_re_565d("https://other.test/a", 2),
                _row__find_similar_does_not_re_565d("http://www.clig.dev/?utm=1", 3), _row__find_similar_does_not_re_565d("https://other.test/b", 4)]
        kept, dropped = _drop_the_source_page(rows, SOURCE)
        assert [r.url for r in kept] == ["https://other.test/a", "https://other.test/b"]
        assert dropped == 2, "去掉几行要能报出数，不能只报'有东西被去掉'"

    def test_a_list_without_the_source_is_untouched(self):
        rows = [_row__find_similar_does_not_re_565d("https://other.test/a", 1), _row__find_similar_does_not_re_565d("https://clig.dev/faq", 2)]
        kept, dropped = _drop_the_source_page(rows, SOURCE)
        assert [r.url for r in kept] == [r.url for r in rows]
        assert dropped == 0, "没去掉却说去了 = 又一次谎报"


def _stub_engines(monkeypatch, urls, *, reranker_scores=None):
    """引擎与取源都不发真请求；检测器（若给了分数）按位置打高分。"""
    rows = [_row__find_similar_does_not_re_565d(u, i) for i, u in enumerate(urls, 1)]

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


# ============================================================================
# 来源：test_a_round_with_no_pairable_words_says_so.py
# ============================================================================
# 空着的建议词要说得出「这一轮的行根本配不出词对」—— 用结构量，不用新阈值（R02-071）。

# 「储能 电芯 出口 数据」那一发的三行标题，逐字沿用 R02-F26 已经钉在本仓库里的真现场
CHU_NENG_QUERY = "储能 电芯 出口 数据"
CHU_NENG_ROWS = [
    SearchResult(title="ACG猎奇【重口】图片大全-ACG猎奇【重口】高清好看的图片-花瓣桃奈小娘子的画板",
                 url="https://a.example.org/1", position=1, snippet=""),
    SearchResult(title="暗网猎奇 -- 序 - 知乎", url="https://b.example.net/2", position=2, snippet=""),
    SearchResult(title="百测网-分析测试_论文润色_学术绘图_科研培训_试剂耗材_实验仪器_科研服务",
                 url="https://c.example.com/3", position=3, snippet=""),
]


def _rows(titles_urls):
    return [SearchResult(title=t, url=u, position=i + 1, snippet=s,
                         relevance_score=1.0 - i / 10, relevance_raw=0.9, fetch_relevance="high")
            for i, (t, u, s) in enumerate(titles_urls)]


# ─── 1 · 这一格该开口（改前是沉默 ⇒ 本文件第一次跑就是红的） ─────────────────

def test_a_round_with_no_pairable_words_speaks():
    st: dict = {}
    got = _related_queries(CHU_NENG_QUERY, CHU_NENG_ROWS, stats=st)
    assert got == [], got
    assert st["spaced_pairs"] == 0, st
    assert st["words_total"] > 0, st          # 切得出词，但一个相邻对都配不出来
    cause = _follow_up_cause(st, got)
    assert cause, "这一格今天什么都不说"
    assert str(st["words_total"]) in cause, cause   # 句子带着真实个数，不许说「一个词都没有」
    assert "rewording the query cannot change" in cause, cause


def test_the_zero_word_round_still_speaks():
    """老现场不许退化：一个词都切不出来的那一发仍然要说，而且说的一样是「换问法没用」。"""
    rows = _rows([("乡村阅读公益项目年度报告服务人次", "https://a.example.org/4", ""),
                  ("公益项目年度报告服务人次乡村阅读", "https://b.example.net/5", "")])
    st: dict = {}
    got = _related_queries("乡村阅读 公益项目 年度报告 服务人次 2024 2025", rows, stats=st)
    assert got == [], got
    assert st["words_total"] == 0, st
    cause = _follow_up_cause(st, got)
    assert cause and "rewording the query cannot change" in cause, cause


def test_a_latin_query_whose_rows_are_all_address_runs_speaks_too():
    """行的正文全是地址串：切得出十几个词，却没有一对只隔空白（`代工厂…` 3 行那发的形状）。"""
    rows = _rows([("new.qq.com/rain/a/20230216A0739K00", "https://new.qq.com/rain/a/x1", "代工厂供货说明"),
                  ("news.qq.com/rain/a/20260505A068KK00", "https://news.qq.com/rain/a/x2", "供应商爬坡期"),
                  ("x.cn/rain/a/202401010A000001", "https://x.cn/rain/a/202401010A000001", "品牌公告")])
    st: dict = {}
    got = _related_queries("代工厂 换供应商 爬坡期 品牌", rows, stats=st)
    assert st["spaced_pairs"] == 0, (st, got)
    assert _follow_up_cause(st, got), st


# ─── 2 · 反面锁：造得出词对的轮子继续闭嘴 ──────────────────────────────────

def test_a_round_with_one_pairable_word_stays_silent():
    """`乡村阅读 公益项目 年度报告 服务人次` 8 行那发：words=6 但空白词对 = 1 ⇒ 还是②的真沉默。"""
    rows = _rows([("年度报告 服务人次 report 2024 annual", "https://a.example.org/6", ""),
                  ("公益项目 report 乡村阅读 annual", "https://b.example.net/7", ""),
                  ("乡村阅读 service 人次 report", "https://c.example.com/8", ""),
                  ("年度 annual 服务 report", "https://d.example.org/9", ""),
                  ("项目人次 report overview", "https://e.cn/10", ""),
                  ("服务 annual 乡村 report", "https://f.cn/11", ""),
                  ("阅读 report 年度 annual", "https://g.cn/12", ""),
                  ("乡村 overview 项目 report", "https://h.cn/13", "")])
    st: dict = {}
    got = _related_queries("乡村阅读 公益项目 年度报告 服务人次", rows, stats=st)
    assert st["spaced_pairs"] > 0, st
    assert _follow_up_cause(st, got) == "", (st, got)


def test_a_healthy_latin_round_with_no_repetition_stays_silent():
    """真正的「证据不足」：词对多得是，只是没有跨两家 —— ②按 docstring 该沉默。"""
    rows = _rows([("GPT scoring notes", "https://one.example/1",
                   "tokenizer overhead dominates inference latency in serving stacks today"),
                  ("LLM notes", "https://two.example/2",
                   "inference latency grows when tokenizer overhead dominates serving")])
    st: dict = {}
    got = _related_queries("large language models", rows, stats=st)
    assert st["spaced_pairs"] > 5, st
    assert _follow_up_cause(st, got) == "", (st, got)


def test_a_round_that_ships_phrases_never_says_the_emptiness_line():
    rows = _rows([("open access policy notes", "https://a.example.org/1",
                   "repository rules, open access mandate worldwide"),
                  ("open access policy notes", "https://b.example.net/2",
                   "repository rules, open access mandate worldwide")])
    st: dict = {}
    got = _related_queries("repository licensing", rows, stats=st)
    assert "open access" in got, got
    assert _follow_up_cause(st, got) == "", got


# ─── 3 · 结构锁：判据是同一个函数已经算过的形状，不是第二把尺 ────────────────

def test_the_emptiness_line_reads_the_same_adjacency_shape_as_the_miner():
    src = inspect.getsource(_related_queries)
    assert "spaced_pairs" in src, "词对的数量必须由挖词那位自己数出来（同一把尺）"
    st = {"words_total": 12, "recurring": 0, "shipped": 0, "spaced_pairs": 0}
    assert _follow_up_cause(st, [])
    st2 = dict(st, spaced_pairs=1)
    assert _follow_up_cause(st2, []) == ""     # 不再看 words_total 是不是 0
    # 早退那一条（没有任何行）也要把新数字填上，否则「没矿」会被读成「有矿但没话」
    st3: dict = {}
    assert _related_queries("q", [], stats=st3) == []
    assert "spaced_pairs" in st3, st3


def test_the_sentence_never_claims_zero_words_when_there_are_words():
    st = {"words_total": 12, "recurring": 0, "shipped": 0, "spaced_pairs": 0}
    cause = _follow_up_cause(st, [])
    assert "no whitespace-separated words" not in cause, cause
    assert "12 whitespace-separated word" in cause, cause


# ============================================================================
# 来源：test_a_round_that_kept_some_off_topic_rows_says_it_kept_them.py
# ============================================================================
# 同一把尺子走了两条路时，回执要说得出这一次走的是哪条：几行不含查询词、为什么还留着。
# 重名消歧：_row → _row__a_round_that_kept_some_o_eecd

MIXED_QUERY = "small appliance brand switched OEM supplier 2025 announcement"
ON_TOPIC = "Brand X confirms it switched OEM supplier for small appliances in 2025"
OFF_TOPICS = [
    "Doppler radar forecast for tomorrow across the region",
    "How to bake a sourdough loaf at home",
    "Celebrity gossip: the summer party roundup",
    "Car insurance quotes compared side by side",
]


def _row__a_round_that_kept_some_o_eecd(pos: int, title: str, url: str = "https://a.example.org/p",
         snippet: str = "", raw: float = 0.9) -> SearchResult:
    return SearchResult(title=title, url=url, position=pos, snippet=snippet,
                        relevance_score=1.0 - pos / 10, relevance_raw=raw,
                        fetch_relevance=_tier(raw, pos, 6, raw))


MIXED = ([_row__a_round_that_kept_some_o_eecd(1, ON_TOPIC)]
         + [_row__a_round_that_kept_some_o_eecd(i + 2, t, raw=0.03) for i, t in enumerate(OFF_TOPICS)]
         + [_row__a_round_that_kept_some_o_eecd(6, "Supplier statement on the same announcement", raw=0.8)])


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
    rows = [_row__a_round_that_kept_some_o_eecd(1, ON_TOPIC), _row__a_round_that_kept_some_o_eecd(2, "Supplier statement on the same announcement")]
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
    rows = [_row__a_round_that_kept_some_o_eecd(1, "2025年中国储能电池出口数据发布", snippet="电芯出口同比增长"),
            _row__a_round_that_kept_some_o_eecd(2, "ACG猎奇【重口】图片大全", raw=0.03),
            _row__a_round_that_kept_some_o_eecd(3, "暗网猎奇 -- 序 - 知乎", raw=0.03)]
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


# ============================================================================
# 来源：test_the_top_result_is_not_high_because_it_is_first.py
# ============================================================================
# 第一名不是第一名，所以它不配一个"值得抓"（体验修复刀2-S2/S3）。
# 重名消歧：_row → _row__the_top_result_is_not_hi_fd0a

def _row__the_top_result_is_not_hi_fd0a(score: float, rank: int, total: int, raw=None, *, url=None) -> SearchResult:
    return SearchResult(
        title=f"row {rank}", url=url or f"https://s.test/{rank}",
        snippet="x", source="bing", position=rank,
        relevance_score=round(score, 4), relevance_raw=raw,
        fetch_relevance=_tier(score, rank, total, raw),
        engines_consensus="2 of 6")


OLD_RULE_HIGH = lambda s, r, t: s >= 0.5 or r == 1  # noqa: E731  前态，用来钉"只准降级"


class TestTheBadgeIsMintedFromTheEvidence:

    @pytest.mark.parametrize("raw,expected", [
        (0.9996, "high"), (0.5, "high"),          # 在题群（实测 p50 0.92、p90 0.9985）
        (0.49, "med"), (0.2, "med"), (0.05, "med"),
        (0.049, "low"), (0.0001, "low"),           # 离题群（p10 0.00013）
    ])
    def test_with_a_verdict_the_tier_follows_it_not_the_seat(self, raw, expected):
        """rank=1、归一分=1.0 的**同一个座位**，可以领到三种档位 —— 档位跟着模型的
        判决走。体验测试里那颗 `.cc` 黑料站就是这个形状：第 1 位、1.0、high。"""
        assert _tier(1.0, 1, 8, raw) == expected

    def test_the_row_carries_the_number_its_badge_was_minted_from(self):
        """badge 的原料必须和 badge 一起端出去：只给结论的调用方只能信或不信，
        给原料的调用方能自己复核"0.5 这条线对我够不够"。"""
        row = _row__the_top_result_is_not_hi_fd0a(1.0, 1, 8, 0.00012)
        assert row.relevance_raw == 0.00012
        assert row.relevance_score == 1.0
        assert row.fetch_relevance == "low"

    def test_no_row_is_high_because_it_is_first(self):
        row = _row__the_top_result_is_not_hi_fd0a(1.0, 1, 8, None)
        assert row.fetch_relevance != "high"


class TestWithoutAVerdictTheRulerMayOnlyDemote:

    @pytest.mark.parametrize("score,rank,total", [
        (1.0, 1, 20), (0.9, 2, 20), (0.6, 5, 20), (0.4, 6, 20), (0.2, 10, 20),
        (0.16, 14, 20), (0.1, 15, 20), (0.02, 19, 20), (0.0, 20, 20),
        (1.0, 1, 3), (0.0, 1, 1),
    ])
    def test_the_none_case_is_the_old_rule_capped_at_med(self, score, rank, total):
        """`raw is None` 那一支必须是**单调降档**：high→med，med/low 一字节不动。
        这条不变量就是"改档位规则不会把交付清空或缩窄"的证明 —— `_quality_filter`
        只看 low，所以 low 集合不动 = 交付不动（B0 实测：merge 臂 low 36→36）。"""
        old = "high" if OLD_RULE_HIGH(score, rank, total) else (
            "med" if (score >= 0.15 or rank <= max(2, total // 3)) else "low")
        new = _tier(score, rank, total, None)
        if old == "low":
            assert new == "low"
        elif old == "med":
            assert new == "med"
        else:
            assert new == "med", (score, rank, total)


class TestTheHintSaysWhyThereIsNoHigh:

    def test_a_round_with_no_verdict_names_the_missing_ruler(self):
        rows = [_row__the_top_result_is_not_hi_fd0a(1.0 - i / 6, i + 1, 6, None) for i in range(6)]
        hint = compute_fetch_hint(rows)
        assert "no reranker" in hint, hint
        assert "nothing is claimed 'high'" in hint, hint
        assert "0 high" in hint, hint

    def test_a_round_with_a_verdict_does_not_claim_to_have_no_reranker(self):
        """反向：有判决时不许把这句话端出来 —— 那是一次自我否证的假谦虚。"""
        rows = [_row__the_top_result_is_not_hi_fd0a(1.0, 1, 3, 0.97), _row__the_top_result_is_not_hi_fd0a(0.5, 2, 3, 0.2),
                _row__the_top_result_is_not_hi_fd0a(0.0, 3, 3, 0.0002)]
        hint = compute_fetch_hint(rows)
        assert "no reranker" not in hint, hint
        assert "1 high" in hint and "1 low" in hint, hint


class TestARelevanceRuleMustNotManufactureAnEmptyRound:

    def test_a_round_the_model_rejects_wholesale_still_delivers(self):
        """这一颗守着 S2 唯一真正可怕的方向：阈值把整轮判成 low，然后过滤器把人
        类问的 10 条变成 0 条。`min_keep` 就是为这个存在的，档位规则改的是它的输入，
        所以它必须被显式钉住。"""
        rows = [_row__the_top_result_is_not_hi_fd0a(1.0 - i / 10, i + 1, 10, 0.0001) for i in range(10)]
        assert all(r.fetch_relevance == "low" for r in rows)
        kept = _quality_filter(rows)
        assert len(kept) == 10, kept
        assert kept, "问 10 得 0 不是诚实，是故障"

    def test_a_mixed_round_drops_only_the_rows_the_model_rejected(self):
        rows = [_row__the_top_result_is_not_hi_fd0a(1.0, 1, 8, 0.99), _row__the_top_result_is_not_hi_fd0a(0.8, 2, 8, 0.6), _row__the_top_result_is_not_hi_fd0a(0.5, 3, 8, 0.2),
                _row__the_top_result_is_not_hi_fd0a(0.1, 4, 8, 0.0004), _row__the_top_result_is_not_hi_fd0a(0.05, 5, 8, 0.0002),
                _row__the_top_result_is_not_hi_fd0a(0.02, 6, 8, 0.0001), _row__the_top_result_is_not_hi_fd0a(0.01, 7, 8, 0.00005),
                _row__the_top_result_is_not_hi_fd0a(0.0, 8, 8, 0.00001)]
        kept = _quality_filter(rows)
        assert [r.relevance_raw for r in kept] == [0.99, 0.6, 0.2]


class TestTheWireOmitsWhatWasNeverMeasured:

    def test_an_unmeasured_row_dumps_no_relevance_raw_key(self):
        dumped = _wire_dump(_row__the_top_result_is_not_hi_fd0a(1.0, 1, 6, None))
        assert "relevance_raw" not in dumped, dumped

    def test_a_measured_row_puts_the_number_on_the_wire(self):
        dumped = _wire_dump(_row__the_top_result_is_not_hi_fd0a(1.0, 1, 6, 0.00013))
        assert dumped["relevance_raw"] == 0.00013, dumped

    def test_a_measured_zero_is_not_omitted_as_if_it_was_never_measured(self):
        """缺席必须只说"没量过"。模型给出 0.0 是一条**判决**（判为不相关），把它
        按默认值省掉，读侧就只剩"这一行没数据" —— 而这一行的数据恰恰是否定的那条。"""
        dumped = _wire_dump(_row__the_top_result_is_not_hi_fd0a(1.0, 1, 6, 0.0))
        assert "relevance_raw" in dumped, dumped
        assert dumped["relevance_raw"] == 0.0, dumped


# ============================================================================
# 来源：test_a_seat_number_is_not_a_percentage_and_a_tier_names_what_it_rests_on.py
# ============================================================================
# 一颗 1.0 与一档 high 必须由同一句话说清是谁给的：座位号不是百分数，档位要说它靠什么站着。
# 重名消歧：_row → _row__a_seat_number_is_not_a_p_d78c

def _row__a_seat_number_is_not_a_p_d78c(score: float, rank: int, total: int, raw) -> SearchResult:
    r = SearchResult(title=f"t{rank}", url=f"https://e{rank}.example/x{rank}",
                    position=rank, relevance_score=score)
    r.relevance_raw = raw
    # 档位由产品自己的那位作者铸 —— 测的是那句话怎么说，不是手搓一个 tier 字符串。
    r.fetch_relevance = _tier(score, rank, total, raw)
    return r


def _round(raws: list[float], query: str) -> str:
    total = len(raws)
    rows = [_row__a_seat_number_is_not_a_p_d78c(1.0 - i / total, i + 1, total, raws[i]) for i in range(total)]
    return compute_fetch_hint(rows, query=query)


# ─── 1 · 两把尺要各说各的（R02-011）───

def test_a_seat_number_is_not_left_reading_as_a_percentage():
    hint = _round([0.0295, 0.0273, 0.0193], "ceftriaxone diazepam compatibility chart pdf")
    assert "relevance_raw" in hint, hint
    assert "seat" in hint.lower(), hint
    assert "Ranked by relevance_score" not in hint, hint


def test_a_round_nothing_cleared_names_its_own_best_number():
    """0 high 那一轮不能只留一颗 1.0：本轮最高的 raw 要点出来，数从证据里来。"""
    hint = _round([0.0295, 0.0273, 0.0067], "ceftriaxone diazepam compatibility chart pdf")
    assert "0.03" in hint, hint          # 本轮真实的最高 raw
    assert "0 high, 0 med, 3 low" in hint, hint


def test_the_line_in_the_sentence_is_the_one_the_ruler_uses():
    """阈值一位作者：句子里的 high 线就是 `_RAW_HIGH_AT`，不是措辞里抄来的第二个数。"""
    hint = _round([0.0295, 0.01], "ceftriaxone diazepam compatibility chart pdf")
    assert f"{_RAW_HIGH_AT:g}" in hint, (_RAW_HIGH_AT, hint)


# ─── 2 · 档位靠什么站着要说出来（R02-030）───

def test_a_round_with_no_term_to_check_against_says_it_rests_on_one_post():
    """`那个` 现场：数不出查询词的那一轮，要点明词面互证这一维今天没有。"""
    hint = _round([0.9956, 0.9886, 0.6379], "那个")
    assert "no" in hint and ("term" in hint or "tokeni" in hint), hint
    assert "2" in hint, hint            # 查询实际交出的字符数，是量出来的不是形容词
    assert "model" in hint.lower(), hint


def test_a_pronoun_followup_is_measured_the_same_way():
    """第二现场（11 个汉字的代词追问）走的是同一条判据 —— 不靠长度阈值。"""
    hint = _round([0.9658, 0.2422, 0.233], "那几本里有开放获取的吗")
    assert "term" in hint or "tokeni" in hint, hint


def test_the_model_is_not_claimed_to_be_wrong_only_because_the_query_is_short():
    """不许把「这一问没有指代对象」当成产品的判决端出来 —— 产品不知道。"""
    hint = _round([0.9956, 0.9886], "那个")
    assert "irrelevant" not in hint.lower(), hint
    assert "not a topic verdict" in hint.lower() or "single" in hint.lower(), hint


# ─── 3 · 反面锁：噪声不许扩散 ───

def test_a_round_that_can_check_terms_does_not_confess_to_not_checking():
    """数得出查询词的那些轮（拉丁文真查询）不许出现那一句自白。"""
    hint = _round([0.9995, 0.9982, 0.6], "python asyncio semaphore concurrency limit")
    assert "tokeni" not in hint and "no query term" not in hint, hint


def test_a_round_without_a_verdict_keeps_its_own_sentence_untouched():
    """上一把刀立的契约一字不动：`raw is None` 那一句必须还是那一句。"""
    rows = [_row__a_seat_number_is_not_a_p_d78c(1.0 - i / 6, i + 1, 6, None) for i in range(6)]
    hint = compute_fetch_hint(rows, query="python asyncio semaphore")
    assert "no reranker" in hint, hint
    assert "nothing is claimed 'high'" in hint, hint
    assert "0.03" not in hint, hint


def test_a_round_with_a_verdict_never_claims_to_have_no_reranker():
    hint = _round([0.97, 0.2, 0.0002], "python asyncio semaphore concurrency limit")
    assert "no reranker" not in hint, hint
    assert "1 high" in hint and "1 low" in hint, hint


def test_the_actionable_half_survives():
    """改的是「这封响应说的是什么」，不是把人支走：那一半 advice 必须在。"""
    hint = _round([0.91, 0.6, 0.01], "python asyncio semaphore concurrency limit")
    assert "web_read" in hint, hint
    assert "lower tier can be the right call" in hint, hint
