"""test_search_6.py —— 由 6 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

from unittest.mock import AsyncMock
import pytest
from dhole_mcp import search as search_mod
from dhole_mcp.search import _similarity_missed_note
from dhole_mcp.search_engines import EngineReport
from dhole_mcp.server import MasterFetchServer
from dhole_mcp.search import SearchResult, _same_document_groups, compute_fetch_hint
import inspect
import re
from dhole_mcp.search import _related_queries
from dhole_mcp.search import _follow_up_cause
from dhole_mcp import search
from dhole_mcp.search import RawResult
from pathlib import Path
from dhole_mcp import search_metasearch as ms
from dhole_mcp import serp_dates, wire
from dhole_mcp.search import SearchResponseModel
from datetime import date
from dhole_mcp.search import recount_row_dates
from dhole_mcp.server import ResponseModel


# ============================================================================
# 来源：test_a_degraded_find_similar_says_what_ranked_the_rows.py
# ============================================================================
# find_similar 排不了"相似"的时候，必须说清这次是谁排的（2026-10-08 验收 · D-19）

SOURCE = "https://clig.dev/"
NOTE_MARK = "did NOT run"


def _row(url: str, pos: int) -> RawResult:
    return RawResult(title=f"page at {url}", url=url,
                     snippet="command line interface guidelines",
                     source="brave", position=pos)


class _Reranker:
    """A warm cross-encoder: `score` either answers or raises, as the test wants."""

    def __init__(self, scores=None, raises=None):
        self._scores = scores
        self._raises = raises

    def score(self, _a, docs):
        if self._raises is not None:
            raise self._raises
        return list(self._scores)[:len(docs)]


def _stub(monkeypatch, urls, *, reranker=None, neural=None):
    """引擎与取源都不发真请求。`reranker=None` = 这台机器上没有暖着的重排器。"""
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
    monkeypatch.setattr(search_mod, "get_reranker", lambda: reranker)
    monkeypatch.setattr(search_mod, "unavailable_reason",
                        lambda: "reranker model download failed (offline?)")
    if neural is not None:
        monkeypatch.setattr(search_mod, "neural_rerank", neural)


async def _find_similar(monkeypatch, *, urls, reranker=None, neural=None):
    _stub(monkeypatch, urls, reranker=reranker, neural=neural)
    return await MasterFetchServer().web_search(mode="find_similar", url=SOURCE,
                                                max_results=5, cache_ttl=0)


def _neural_pairs(query, ranked, **kwargs):
    """The cross-encoder DOES run here - against the derived query words."""
    return [(r, 1.0 - i / 10) for i, r in enumerate(ranked)]


class TestTheFieldNamesTheRerankThatRan:

    @pytest.mark.asyncio
    async def test_a_failed_similarity_stage_stops_claiming_the_mode(self, monkeypatch):
        """症状本体：`score` 抛了，`rerank_mode` 却仍写 find_similar。"""
        r = await _find_similar(
            monkeypatch,
            urls=["https://other.test/a", "https://other.test/b"],
            reranker=_Reranker(raises=RuntimeError("onnx session refused this pair")),
            neural=_neural_pairs)
        assert r.rerank_mode != "find_similar", r.rerank_mode
        assert r.rerank_mode == "neural", f"字段要说这次实际跑了谁: {r.rerank_mode}"

    @pytest.mark.asyncio
    async def test_the_summary_line_agrees_with_the_field(self, monkeypatch):
        r = await _find_similar(
            monkeypatch,
            urls=["https://other.test/a", "https://other.test/b"],
            reranker=_Reranker(raises=TimeoutError("encoder timed out")),
            neural=_neural_pairs)
        assert f"rerank={r.rerank_mode}" in r.summary, r.summary
        assert "rerank=find_similar" not in r.summary, r.summary

    @pytest.mark.asyncio
    async def test_no_reranker_at_all_reports_the_order_it_used(self, monkeypatch):
        r = await _find_similar(
            monkeypatch,
            urls=["https://other.test/a", "https://other.test/b"],
            reranker=None,
            neural=lambda *a, **k: None)
        assert r.rerank_mode == "merge", r.rerank_mode


class TestTheProseNamesTheBasis:

    @pytest.mark.asyncio
    async def test_the_note_says_both_halves_when_the_encoder_still_ran(self, monkeypatch):
        r = await _find_similar(
            monkeypatch,
            urls=["https://other.test/a", "https://other.test/b"],
            reranker=_Reranker(raises=RuntimeError("onnx session refused this pair")),
            neural=_neural_pairs)
        hint = r.fetch_hint
        assert NOTE_MARK in hint, f"相似度这步没跑却没说: {hint}"
        assert "topical match, not page similarity" in hint, hint
        assert "Command Line Interface Guidelines" in hint, \
            f"要说出分数是拿什么比的: {hint}"
        assert "RuntimeError" in hint, f"为什么没跑也要给: {hint}"

    @pytest.mark.asyncio
    async def test_the_note_says_nothing_was_scored_when_no_model_ran(self, monkeypatch):
        r = await _find_similar(
            monkeypatch,
            urls=["https://other.test/a", "https://other.test/b"],
            reranker=None,
            neural=lambda *a, **k: None)
        hint = r.fetch_hint
        assert NOTE_MARK in hint, hint
        assert "no model scored anything" in hint, hint
        assert "reranker model download failed (offline?)" in hint, hint

    @pytest.mark.asyncio
    async def test_a_note_never_invents_an_exception(self, monkeypatch):
        """重排器缺席那条路的起因是"没有模型"，不是"抛了异常"。"""
        r = await _find_similar(
            monkeypatch,
            urls=["https://other.test/a", "https://other.test/b"],
            reranker=None,
            neural=lambda *a, **k: None)
        assert "raised" not in r.fetch_hint, r.fetch_hint


class TestASuccessfulRoundStaysQuiet:
    """反向锁：相似度真跑成了，这刀一个字都不许多说。"""

    @pytest.mark.asyncio
    async def test_the_mode_still_names_find_similar_when_it_ran(self, monkeypatch):
        r = await _find_similar(
            monkeypatch,
            urls=["https://other.test/a", "https://other.test/b"],
            reranker=_Reranker(scores=[0.9, 0.2]))
        assert r.rerank_mode == "find_similar", r.rerank_mode

    @pytest.mark.asyncio
    async def test_a_clean_round_carries_no_degradation_note(self, monkeypatch):
        r = await _find_similar(
            monkeypatch,
            urls=["https://other.test/a", "https://other.test/b"],
            reranker=_Reranker(scores=[0.9, 0.2]))
        assert NOTE_MARK not in r.fetch_hint, r.fetch_hint


class TestTheFieldAndTheProseAnswerTogether:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("reranker,neural,wanted", [
        (_Reranker(scores=[0.9, 0.2]), None, "find_similar"),
        (_Reranker(raises=RuntimeError("boom")), _neural_pairs, "neural"),
        (None, lambda *a, **k: None, "merge"),
    ], ids=["similarity-ran", "similarity-failed-encoder-ran", "no-model"])
    async def test_the_field_and_the_note_are_neither_both_silent_nor_both_loud(
            self, monkeypatch, reranker, neural, wanted):
        r = await _find_similar(monkeypatch,
                                urls=["https://other.test/a", "https://other.test/b"],
                                reranker=reranker, neural=neural)
        assert r.rerank_mode == wanted, r.rerank_mode
        assert (NOTE_MARK in r.fetch_hint) == (wanted != "find_similar"), r.fetch_hint
        # 字段在 wire 上"等于默认就省略"（merge 是默认值），省略不等于说谎：
        # 那一句必须替字段把降级说到。
        if wanted == "merge":
            assert NOTE_MARK in r.fetch_hint

    def test_a_long_reason_does_not_fold_into_a_pair_of_parentheses(self):
        """`_rerank_absent_reason()` 自己带括号与句号 —— 拼进去不能变成括号套括号。"""
        note = _similarity_missed_note("merge", "Some Title",
                                       "no reason recorded - the reranker has not "
                                       "run in this process yet (the first search "
                                       "may still be loading it).")
        assert "((" not in note and "))" not in note, note
        assert "Reason: no reason recorded" in note, note
        assert note.endswith("loading it)."), note


# ============================================================================
# 来源：test_four_copies_of_one_announcement_are_not_four_sources.py
# ============================================================================
# 同一份公告被四个站点交回时，响应必须说得出「这是一份文件，不是四个来源」。
# 重名消歧：_row → _row__four_copies_of_one_annou_dfb6

def _row__four_copies_of_one_annou_dfb6(pos: int, title: str, url: str, raw: float = 0.9,
         snippet: str = "") -> SearchResult:
    return SearchResult(title=title, url=url, position=pos, snippet=snippet,
                        relevance_score=1.0 - pos / 10,
                        relevance_raw=raw, fetch_relevance="high")


ANNOUNCE = "河南省2025年家电产品以旧换新品牌厂家征集的补充公告"
#: 重发的站点带着同一份通知的正文 —— 这条共享的 literal 是同文判据的第二条腿
#: （产品名相同的四个不同页面没有它，实测那一发就被这条腿切开了）。
COPY_SNIPPET = ("具体可参照《河南省2025年家电及3C产品以旧换新品牌厂家征集公告》"
                "要求,进行厂家报名。咨询电话 0371-65897")

COPY_ROWS = [
    _row__four_copies_of_one_annou_dfb6(1, ANNOUNCE + "- 开封本地宝", "http://kf.bendibao.com/news/202519/20495.shtm",
         snippet=COPY_SNIPPET),
    _row__four_copies_of_one_annou_dfb6(2, "2025年第五批家电以旧换新企业公示名单", "http://swj.zhuzhou.gov.cn/c16568/x.html",
         snippet="2025年第五批家电以旧换新企业公示名单，共 132 家"),
    _row__four_copies_of_one_annou_dfb6(3, ANNOUNCE + " | 中国采购与招标网", "https://www.chinamae.com/a/3108794.html",
         snippet=COPY_SNIPPET),
    _row__four_copies_of_one_annou_dfb6(4, "河南省商务厅-" + ANNOUNCE, "https://hnsswt.henan.gov.cn/2025/01-06/3108794.html",
         snippet=COPY_SNIPPET),
    _row__four_copies_of_one_annou_dfb6(5, ANNOUNCE + "– 本地宝", "http://zz.bendibao.com/news/20496.shtm",
         snippet=COPY_SNIPPET),
]


# ─── 1 · 同一份文件要说得出是哪几行 ───

def test_the_copies_of_one_announcement_form_one_group():
    groups = _same_document_groups(COPY_ROWS)
    assert len(groups) == 1, groups
    g = groups[0]
    assert sorted(g["positions"]) == [1, 3, 4, 5], g
    assert g["hosts"] >= 3, g          # bendibao（两个子站）/ chinamae / henan.gov.cn
    assert ANNOUNCE in g["title"], g    # 标题原文端出来，不是编号


def test_the_hint_says_one_document_not_four_sources():
    rows = COPY_ROWS
    hint = compute_fetch_hint(rows, query="小家电 品牌 代工厂 公告",
                              same_document_groups=_same_document_groups(rows))
    low = hint.lower()
    assert "document" in low and "republished" in low, hint
    assert "independent sources" in low, hint
    assert "1, 3, 4, 5" in hint, hint


def test_a_site_that_prepends_its_own_name_is_the_same_document():
    """官方那份把站名写在**前面**（"河南省商务厅-…"）：只按相等会漏掉它。

    473 行真语料上加上"包含"这一条多出的配对是 1，而它就是现场那一份（跨 3 个 host）——
    漏掉官方那一份的组，正是调用方最需要看见的那一个。
    """
    rows = [_row__four_copies_of_one_annou_dfb6(1, ANNOUNCE, "https://kf.bendibao.com/a", snippet=COPY_SNIPPET),
            _row__four_copies_of_one_annou_dfb6(2, "河南省商务厅-" + ANNOUNCE, "https://hnsswt.henan.gov.cn/b",
                 snippet=COPY_SNIPPET)]
    g = _same_document_groups(rows)
    assert len(g) == 1 and g[0]["positions"] == [1, 2], g


def test_a_containment_below_the_length_floor_does_not_merge():
    """包含不许把短标题吸进长标题：两侧都要过那道闸（实测的 'Community' 一族长这样）。"""
    shared = "同一句共享的正文，用来把第二条腿（snippet 重合）排除在外"
    rows = [_row__four_copies_of_one_annou_dfb6(1, "教程", "https://a.example.org/x", snippet=shared),
            _row__four_copies_of_one_annou_dfb6(2, "Python 异步教程 第三章 并发限制与信号量", "https://b.example.net/y",
                 snippet=shared)]
    assert _same_document_groups(rows) == []


# ─── 2 · 反面锁：实测的假合并形状 ───

def test_a_site_title_repeated_on_every_page_of_one_host_is_not_a_group():
    """Milvus 那一族的形状：文档站每页同一个 <title>，31 行同 host —— 那不是同文。"""
    shared = "同一句共享的正文：挡住这一格的必须是 host 那条腿，不是 snippet 那条"
    rows = [_row__four_copies_of_one_annou_dfb6(i, "Milvus Vector Database Documentation", f"https://milvus.io/docs/p{i}.md",
                 snippet=shared) for i in range(1, 8)]
    assert _same_document_groups(rows) == [], "同 host 的同名行被并成了份数"


def test_a_generic_word_is_not_a_document_name():
    """'Community' / 'GitHub' 这种通用词跨 host 也不成组：它载不下一份文件的身份。"""
    shared = "同一句共享的正文：挡住这一格的必须是标题长度那道闸"
    rows = [_row__four_copies_of_one_annou_dfb6(1, "Community", "https://forum.example.org/t/1", snippet=shared),
            _row__four_copies_of_one_annou_dfb6(2, "Community", "https://chat.example.net/rooms", snippet=shared),
            _row__four_copies_of_one_annou_dfb6(3, "GitHub", "https://github.com/a", snippet=shared),
            _row__four_copies_of_one_annou_dfb6(4, "GitHub", "https://gitlab.com/b", snippet=shared)]
    assert _same_document_groups(rows) == []


def test_the_same_head_on_different_pages_is_not_one_document():
    """**实测到的假合并本体**（sessions/P02-R5-F24-post.jsonl 的 C 格，真跑出来的那一发）：
    四个不同页面把产品名 "Milvus Vector Database" 当作标题的头，剥掉站点后缀一模一样、
    host 还各不同。产品名会相同，一份文件的正文不会 —— 第二条腿（共享的一段 literal）
    就是为这一格加的：实测真同文那一对共享 34 个字，而这一族的页面各有各的正文。
    """
    rows = [_row__four_copies_of_one_annou_dfb6(1, "Milvus vector database documentation | Siddesh V R",
                 "https://www.linkedin.com/posts/x",
                 snippet="I built a retrieval pipeline last week and here is what I learned "
                         "about collection schemas and shard counts"),
            _row__four_copies_of_one_annou_dfb6(2, "Milvus Vector Database - photos and vectors", "https://dishcuss.com/v",
                 snippet="Photo search experiments with clip embeddings running entirely on a "
                         "laptop gpu and a local index"),
            _row__four_copies_of_one_annou_dfb6(4, "Milvus Vector Database - KodeKloud", "https://notes.kodekloud.com/n",
                 snippet="Course notes covering installation, cluster topology and the "
                         "partition strategy used in production")]
    assert _same_document_groups(rows) == []


def test_a_round_of_distinct_documents_adds_nothing():
    rows = [_row__four_copies_of_one_annou_dfb6(1, "河南省商务厅-河南省2025年家电及3C产品以旧换新品牌厂家征集公告",
                 "https://hnsswt.henan.gov.cn/a.html"),
            _row__four_copies_of_one_annou_dfb6(2, "2025年第五批家电以旧换新企业公示名单", "https://swj.zhuzhou.gov.cn/b.html")]
    assert _same_document_groups(rows) == []
    hint = compute_fetch_hint(rows, query="家电 以旧换新 公告",
                              same_document_groups=_same_document_groups(rows))
    assert "republished" not in hint.lower(), hint


def test_grouping_never_moves_or_retiers_a_row():
    """这一刀只加一维读数：行序、position、档位都不许因为成组而变。"""
    before = [(r.position, r.url, r.fetch_relevance) for r in COPY_ROWS]
    _same_document_groups(COPY_ROWS)
    after = [(r.position, r.url, r.fetch_relevance) for r in COPY_ROWS]
    assert before == after


def test_a_shared_product_name_in_the_middle_is_not_the_same_document():
    """两侧复验真撞上的那一次假合并：共同的产品名含在**中间**，不是同一份文件。

    现场（post 第一趟的 C 格，query='milvus vector database documentation'）：
    「Milvus Vector Store | Nodes | n8n Docs」与一篇更长的教程标题共享中间那颗产品名，
    未锚定的"包含"把两行并成了一组 —— 于是规则只认贴在两端的站名装饰。
    """
    rows = [_row__four_copies_of_one_annou_dfb6(4, "Milvus Vector Store | Nodes | n8n Docs", "https://docs.n8n.io/milvus",
                 snippet="同一句共享的正文：挡住这一格的必须是「只认两端贴合」这条，不是 snippet"),
            _row__four_copies_of_one_annou_dfb6(8, "使用 NVIDIA RAG 与 Milvus Vector Store 搭建智能检索系统",
                 "https://juejin.cn/post/1",
                 snippet="同一句共享的正文：挡住这一格的必须是「只认两端贴合」这条，不是 snippet")]
    assert _same_document_groups(rows) == []


def test_the_host_count_has_one_author():
    """组里的 host 由既有的 `_registrable_domain` 数出来，判定里没有第二套域名规则。"""
    import inspect

    from dhole_mcp import search as search_mod
    src = inspect.getsource(search_mod._same_document_groups)
    assert "_registrable_domain" in src, src
    assert "tldextract" not in src and "PUBLIC_SUFFIX" not in src, src


# ============================================================================
# 来源：test_a_suggestion_is_two_words_that_stand_next_to_each_other.py
# ============================================================================
# 建议词得是行里**真出现过的相邻两词**：两个词之间只许站着空白。
# 重名消歧：_row → _row__a_suggestion_is_two_word_6b4d

def _row__a_suggestion_is_two_word_6b4d(pos: int, title: str, url: str, snippet: str = "") -> SearchResult:
    return SearchResult(title=title, url=url, position=pos, snippet=snippet,
                        relevance_score=1.0 - pos / 10, relevance_raw=0.9,
                        fetch_relevance="high")


# ─── 现场原文（逐字取自本轮真 SERP，未改写） ───────────────────────────────
QQ_ADDRESS_ROWS = [
    _row__a_suggestion_is_two_word_6b4d(3, "new.qq.com/rain/a/20230216A0739K00", "https://new.qq.com/rain/a/20230216A0739K00",
         "原因在于赫比是 iRobot 的代工厂,是小米供应链里唯一有做扫地机经验的厂商。"),
    _row__a_suggestion_is_two_word_6b4d(4, "news.qq.com/rain/a/20260505A068KK00", "https://news.qq.com/rain/a/20260505A068KK00",
         "(1)人形机器人:马斯克发声表明Optimus项目仍稳步推进中,后续产品发布、量产爬坡值得重点关注。"),
]
WXY88_ROWS = [
    _row__a_suggestion_is_two_word_6b4d(0, "wxy88.top - 文献云图书馆", "http://wxy88.top/",
         "文献云图书馆 学术论文下载,https://lib.wenx.top/ 论文下载 知网论文 维普期刊 万方论文 "
         "读秀下载 英文期刊 中文文 …"),
    _row__a_suggestion_is_two_word_6b4d(2, "wxy88.top Uptime, Downtime and Server Information - Who.is",
         "https://who.is/uptime/wxy88.top",
         "View uptime status, downtime, and server information for wxy88.top."),
]
COMMUNITY_QUERY = "community self-organization urban studies open access monograph"
COMMUNITY_ROWS = [
    _row__a_suggestion_is_two_word_6b4d(1, "Precipitation Regionalization Using Self-Organizing Maps for Mumbai ...",
         "https://www.scirp.org/JOURNAL/papercitationdetails?paperid=87616&journalid=46",
         "analysis using self-organizing maps (SOM). The cluster analysis results show six clusters "
         "... (urban area) and mountain and hills areas (Sanjay Gandhi National Park located in "
         "the …"),
    _row__a_suggestion_is_two_word_6b4d(3, "COMMUNITY definition and meaning | Collins English Dictionary",
         "https://www.collinsdictionary.com/dictionary/english/community",
         "Community is friendship between different people or groups, and a sense of having "
         "something in common. The retirement home …"),
    _row__a_suggestion_is_two_word_6b4d(5, "COMMUNITY Definition & Meaning - Merriam-Webster",
         "https://www.merriam-webster.com/dictionary/community",
         "5 days ago · The meaning of COMMUNITY is a unified body of individuals. "
         "How to use community in a sentence."),
    _row__a_suggestion_is_two_word_6b4d(8, "Performance analysis of partial self organizing maps for radar signal ...",
         "https://m.zhangqiaokeyan.com/academic-conference-foreign_meeting-206928_thesis/020514217923.html",
         "自组织图和模糊C均值方法进行战略分组的性能 2. Evaluating the Performance of 16 Egyptian "
         "Wheat Varieties Using Self-Organizing Map (SOM) and Cluster Analysis [J] . O.M. …"),
]


def _gap_of(rows, phrase: str) -> list[str]:
    """把某一建议词组在行原文里实际用的那段间隔抓出来 —— 判据自己说的不算，读原文。"""
    from dhole_mcp import search
    out = []
    a, b = phrase.split(" ", 1)
    for r in rows:
        for field in (r.title or "", r.snippet or ""):
            text = field.lower()
            hits = [m for m in search._WORD_RE.finditer(text)
                    if m.group() not in search._STOPWORDS]
            for prev, cur in zip(hits, hits[1:]):
                if prev.group() == a and cur.group() == b:
                    out.append(text[prev.end():cur.start()])
    return out


# ─── 1 · 三格都不是词组 ─────────────────────────────────────────────────────

def test_an_address_path_segment_is_not_a_phrase():
    got = _related_queries("代工厂 换供应商 爬坡期 品牌", QQ_ADDRESS_ROWS)
    assert "com rain" not in got, (got, _gap_of(QQ_ADDRESS_ROWS, "com rain"))
    assert got == [], got


def test_a_domain_label_split_by_a_dot_is_not_a_phrase():
    got = _related_queries("光伏 硅料 价格 周度", WXY88_ROWS)
    assert "wxy88 top" not in got, (got, _gap_of(WXY88_ROWS, "wxy88 top"))


def test_a_span_that_skips_a_word_is_not_a_phrase():
    """`COMMUNITY Definition & Meaning` 跳过 `&` 配出来的东西，行里从来没有这个说法。"""
    got = _related_queries(COMMUNITY_QUERY, COMMUNITY_ROWS)
    assert "definition meaning" not in got, (got, _gap_of(COMMUNITY_ROWS, "definition meaning"))


def test_a_pair_across_a_sentence_break_is_not_a_phrase():
    """gap 是 `). the ` / `) and ` —— 句子已经换掉了。"""
    got = _related_queries(COMMUNITY_QUERY, COMMUNITY_ROWS)
    assert "som cluster" not in got, (got, _gap_of(COMMUNITY_ROWS, "som cluster"))


# ─── 2 · 反面锁：gap 全是空白的那一族一条不少 ───────────────────────────────

def test_the_whitespace_bigrams_in_the_same_rows_still_ship():
    got = _related_queries(COMMUNITY_QUERY, COMMUNITY_ROWS, n=12)
    assert {"community definition", "cluster analysis"} <= set(got), got


def test_the_clean_family_from_other_rounds_is_untouched():
    rows = [
        _row__a_suggestion_is_two_word_6b4d(0, "open access policy notes", "https://a.example.org/1",
             "repository rules, open access mandate, and cluster analysis"),
        _row__a_suggestion_is_two_word_6b4d(1, "open access policy notes", "https://b.example.net/2",
             "repository rules, open access mandate, and cluster analysis"),
        _row__a_suggestion_is_two_word_6b4d(2, "async web scraping guide", "https://c.example.com/3",
             "async web scraping tips: limit concurrent requests"),
        _row__a_suggestion_is_two_word_6b4d(3, "async web scraping guide", "https://d.example.org/4",
             "async web scraping tips: limit concurrent requests"),
    ]
    got = _related_queries("web scraping concurrency", rows, n=12)
    assert {"open access", "cluster analysis", "async web", "limit concurrent"} <= set(got), got


def test_unspaced_prose_is_still_not_a_phrase():
    """R02-F52 那一格被这条判据吃成特例：中间站着中文同样不是空白。"""
    rows = [
        _row__a_suggestion_is_two_word_6b4d(0, "开放获取政策", "https://a.example.org/1", "先说 open access 的，on 发布 per 要求"),
        _row__a_suggestion_is_two_word_6b4d(1, "开放获取声明", "https://b.example.net/2", "先说 open access 的，on 发布 per 要求"),
    ]
    got = _related_queries("repository licensing", rows)
    assert "on per" not in got, got
    assert "open access" in got, got


# ─── 3 · 计数与判决同一个口径（R02-070 量完之后必须成立） ───────────────────

def test_the_recurring_count_uses_the_same_adjacency_rule_as_the_decision():
    st: dict = {}
    got = _related_queries("代工厂 换供应商 爬坡期 品牌", QQ_ADDRESS_ROWS, stats=st)
    assert got == [], got
    assert st["words_total"] > 0, st           # 行里确实切得出词
    assert st["recurring"] == 0, st            # 但没有一个按同一条相邻判据算数


def test_only_one_adjacency_rule_is_left_in_the_miner():
    src = inspect.getsource(_related_queries)
    body = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    assert "_CJK_TERM_RE" not in body, "相邻判据不许留两条：空白判据已经把它吃成特例"
    assert re.search(r"gap\.strip\(\)", body), "必须看得见那条「中间只许空白」的判据"


# ─── 4 · 交付面的终态锁：交出的每一条都能在某一行的原文里逐字找到 ───────────

def test_every_shipped_phrase_appears_verbatim_in_a_row():
    for q, rows in (("代工厂 换供应商 爬坡期 品牌", QQ_ADDRESS_ROWS),
                    ("光伏 硅料 价格 周度", WXY88_ROWS),
                    (COMMUNITY_QUERY, COMMUNITY_ROWS)):
        for phrase in _related_queries(q, rows, n=12):
            texts = [(f.title + " " + f.snippet).lower() for f in rows]
            assert any(phrase in t for t in texts), (q, phrase, _gap_of(rows, phrase))


# ============================================================================
# 来源：test_an_empty_follow_up_field_says_which_kind_of_empty.py
# ============================================================================
# 空的 `related_queries` 必须说得出是哪一种空。

def rows(*pairs):
    return [SearchResult(title=t, url=u, snippet=s) for t, u, s in pairs]


ZH_ONLY = rows(
    ("征集公告 申报条件 已发布", "https://a.example.cn/1", "各单位请注意 申报条件如下 征集开始"),
    ("申报条件 与征集范围", "https://b.example.net/2", "征集范围说明 申报条件见附件"),
)
NO_RECURRENCE = rows(
    ("ceftriaxone diazepam compatibility chart", "https://a.example.org/3", "pdf download table"),
    ("kingguide wall charts for infusions", "https://b.example.net/4", "nursing reference binder"),
)
ALL_DROPPED = rows(
    ("Notes before joining the guild", "https://a.example.org/5", "before joining you must read this"),
    ("Tips before joining the camp", "https://b.example.net/6", "before joining pack light"),
)
SHIPPED = rows(
    ("open access policy notes", "https://a.example.org/7", "open access mandate and cluster analysis"),
    ("open access policy notes", "https://b.example.net/8", "open access mandate and cluster analysis"),
)


def mine(rs, q):
    st = {}
    got = _related_queries(q, rs, stats=st)
    return st, got, _follow_up_cause(st, got)


class TestTheThreeKindsOfEmpty:

    def test_nothing_cuttable_says_so(self):
        """①一行里连一个可配的词都切不出来：出路不是换个问法。"""
        st, got, cause = mine(ZH_ONLY, "征集公告 申报条件")
        assert st["words_total"] == 0, st
        assert got == [], got
        assert "contain no such pair" in cause, cause
        assert "rewording the query cannot change that answer" in cause, cause

    def test_the_same_shape_in_japanese_gets_the_same_sentence(self):
        """判据看形状不看语言：换成日文行，同一句自述。"""
        jp = rows(("公募 の概要", "https://a.example.jp/9", "申請 手続 に ついて"),
                  ("公募 の資料", "https://b.example.jp/10", "提出 手続 の 案内"))
        st, got, cause = mine(jp, "公募 手続")
        assert st["words_total"] == 0, st
        assert "contain no such pair" in cause, cause

    def test_no_repetition_stays_silent(self):
        """②切得出词、只是没有任何词组跨两个来源重复：保持今天的诚实沉默。"""
        st, got, cause = mine(NO_RECURRENCE, "ceftriaxone diazepam Y-site compatibility chart pdf")
        assert st["words_total"] > 0, st
        assert st["recurring"] == 0, st
        assert got == [], got
        assert cause == "", cause

    def test_recurred_but_dropped_names_the_count(self):
        """③确实有跨源重复的词组，只是全被形状门挡掉：要说得出个数。"""
        st, got, cause = mine(ALL_DROPPED, "guild membership rules")
        assert st["recurring"] >= 1, st
        assert got == [], got
        assert "recurred across 2+ independent sources" in cause, cause
        assert "sentence fragment" in cause, cause

    def test_a_round_that_ships_says_nothing_extra(self):
        st, got, cause = mine(SHIPPED, "论文 存储库 政策")
        assert "open access" in got, got
        assert cause == "", cause


class TestTheSentencesThemselves:

    def test_no_maintainer_history_in_a_shipped_sentence(self):
        """这几句会走到线上：不许带版本号 / 日期 / 「某个版本改了什么的」叙事（同 R02-F49 那条规矩）。"""
        for _, rs, q in (("a", ZH_ONLY, "征集"), ("b", ALL_DROPPED, "guild"),
                         ("c", NO_RECURRENCE, "chart")):
            _cause = mine(rs, q)[2]
            assert not re.search(r"1[0-9]\.[0-9x]+", _cause), _cause
            assert not re.search(r"20[0-9]{2}-[0-9]{2}-[0-9]{2}", _cause), _cause

    def test_the_cause_never_invents_a_number(self):
        """沉默的那格不许有话，有话的那格不许编数：③的句子必须与 stats 的数一致。"""
        st, _got, cause = mine(ALL_DROPPED, "guild membership rules")
        assert f"{st['recurring']} word pair" in cause, (st, cause)

    def test_both_readers_of_the_field_are_wired(self):
        """主路径与 find_similar 两条路都读同一位作者；不许只接一条。"""
        src = open("src/dhole_mcp/search.py", encoding="utf-8").read()
        assert src.count("_follow_up_cause(") >= 3, src.count("_follow_up_cause(")
        assert "_related_queries(derived_query, results_list, stats=_sim_stats)" in src
        assert "_related_queries(query, results_list, stats=_main_stats)" in src

    def test_the_hint_helper_is_still_the_only_assembler(self):
        """接进 fetch_hint 用既有的 `_hint_with`，不开第三份拼接写法（同 R02-F23 那条约束）。"""
        src = open("src/dhole_mcp/search.py", encoding="utf-8").read()
        seg = src[src.index("sim_related = _related_queries") - 60:
                  src.index("sim_related = _related_queries") + 400]
        assert "_hint_with(fetch_hint, _sim_cause)" in seg, seg[:200]
        assert 'fetch_hint + " | "' not in seg.split("_floor_note")[0][:400], seg[:300]


def test_the_miner_still_ships_what_it_shipped_for_a_latin_round():
    """反面锁：本刀不改挖词的任何判据 —— 有交回的那一轮，建议词照旧、`fetch_hint`
    那句档位自述照旧，且**不许多出**这句空的原因。"""
    st, got, cause = mine(SHIPPED, "论文 存储库 政策")
    assert "open access" in got and "cluster analysis" in got, got
    assert cause == ""
    hint = compute_fetch_hint(SHIPPED, query="论文 存储库 政策")
    assert "high" in hint and "med" in hint and "low" in hint, hint[:160]
    assert "contain no such pair" not in hint, hint[:160]


# ─── 走一遍真的响应装配：句子必须上线，不只是作者层对 ───────────────────────



def _stub_round(monkeypatch, rows):
    """照 `tests/test_search.py` 的先例：只换掉引擎与重排器，响应装配走真代码。"""
    async def fake_multi(*a, **kw):
        return ([RawResult(title=r.title, url=r.url, snippet=r.snippet,
                           source="brave", position=i + 1)
                 for i, r in enumerate(rows)],
                [EngineReport(name="brave", ok=True)])

    async def fake_ensure_reranker():
        return None

    monkeypatch.setattr(search, "multi_search", fake_multi)
    monkeypatch.setattr(search, "ensure_reranker", fake_ensure_reranker)
    async def fake_get_cached(*a, **k):
        return None

    async def fake_set_cached(*a, **k):
        return None

    monkeypatch.setattr(search, "get_cached", fake_get_cached)
    monkeypatch.setattr(search, "set_cached", fake_set_cached)
    monkeypatch.setattr(
        search, "_rank",
        lambda query, ranked, mode, **kw: (ranked, [1.0] * len(ranked), "merge", ""))


class TestTheSentenceReachesTheResponse:
    """`related_queries` 空列表在 wire 上是**省略**的（默认值被扣下），所以这句话唯一的
    落点就是 `fetch_hint`。这两格锁的是"接上了"，不是"作者层算对了"。"""

    @pytest.mark.asyncio
    async def test_a_round_with_nothing_cuttable_speaks_on_the_hint(self, monkeypatch):
        from dhole_mcp import search as _s
        _stub_round(monkeypatch, ZH_ONLY)
        resp = await _s.web_search(None, "乡村阅读 公益项目 年度报告")
        assert resp.related_queries == [], resp.related_queries
        assert "contain no such pair" in resp.fetch_hint, resp.fetch_hint

    @pytest.mark.asyncio
    async def test_a_round_that_ships_phrases_stays_clean(self, monkeypatch):
        """有交回的那一轮不许多说一个字 —— 反面锁也要走真装配，不只是作者层。

        问法用行里真有的词（否则整轮会被「没有任何一行含查询词」那道门丢掉，
        那是 R02-046 的判据，不是本刀的）；`open access` 这一对会因「与查询自己的词重合」
        被丢掉，留下的 `cluster analysis` 才是"有交回"的那格。
        """
        from dhole_mcp import search as _s
        _stub_round(monkeypatch, SHIPPED)
        resp = await _s.web_search(None, "open access policy notes repository rules")
        assert resp.results, [r.title for r in resp.results]
        assert resp.related_queries, resp.related_queries
        assert "contain no such pair" not in resp.fetch_hint, resp.fetch_hint


# ============================================================================
# 来源：test_the_measured_engines_stamp_their_own_publish_date.py
# ============================================================================
# 期4 的行级日期只许在**量过的引擎**上出现，而且出现时带着它读出来的那个精度。

FIXTURE_DIR = Path(__file__).parent / "engine_fixtures"

DATE_KEYS = ("published", "published_precision", "published_basis")


def _rows(engine: str, fixture: str) -> list:
    """那一家自己的解析器在冻结页面上交出的行（走 `extract_kept`，生产那一条链）。"""
    text = (FIXTURE_DIR / fixture).read_text(encoding="utf-8")
    return ms._TEXT_ENGINES[engine](proxy=None, timeout=5, verify=True).extract_kept(text)


def _dated(rows: list) -> list:
    return [r for r in rows if r.published]


class TestSogouStampsItsRows:
    """sogou 是量过的那一家：值、精度、原文三颗必须一起到位。"""

    def test_dated_rows_carry_a_day_precision_value(self):
        rows = _rows("sogou", "sogou_web.html")
        assert len(rows) == 8, len(rows)
        dated = _dated(rows)
        assert len(dated) == 4, [r.published for r in rows]
        assert all(r.published_precision == "day" for r in dated), \
            [(r.published, r.published_precision) for r in dated]

    def test_the_basis_quotes_the_literal_the_page_wrote(self):
        """`published_basis` 不是"解析成功"的同义反复：它得是版面上那几个字。"""
        for r in _dated(_rows("sogou", "sogou_web.html")):
            assert r.published_basis == r.published, (r.published, r.published_basis)
            assert len(r.published) == 10, r.published

    def test_the_undated_rows_say_so_by_saying_nothing(self):
        for r in _rows("sogou", "sogou_web.html"):
            if not r.published:
                assert r.published_precision == "" and r.published_basis == "", \
                    (r.title, r.published_precision, r.published_basis)


class TestBaiduReadsBothLayoutsAndRefusesWhatHasNoYear:

    def test_both_baidu_layouts_put_a_date_on_some_rows(self):
        a = _dated(_rows("baidu", "baidu_variant_a.html"))
        b = _dated(_rows("baidu", "baidu_variant_b.html"))
        assert len(a) == 2 and len(b) == 2, (len(a), len(b))
        assert all(r.published_precision == "day" for r in a + b)

    def test_a_month_and_day_without_a_year_is_not_invented_into_a_date(self):
        """B 版有 3 格日期形状的字，却只有 2 行拿到日期 —— 少的那行是 "4月23日"。

        它没有年份，叶子就不判（`serp_dates.read_row("4月23日").present is False`），
        而补一个年份出来的那一行会跟着 `before=` 被判掉：丢一行是不可逆的动作，读者在
        别处看不见它，也无从知道它是被哪一天判掉的。
        """
        rows = _rows("baidu", "baidu_variant_b.html")
        assert len(rows) == 6 and len(_dated(rows)) == 2, \
            [(r.published, r.published_basis) for r in rows]
        assert serp_dates.read_row("4月23日").value is None
        for r in rows:
            if r.published:
                assert len(r.published) == 10 and r.published_precision == "day", \
                    (r.published, r.published_precision)


class TestTheEnginesThatWereNotMeasuredPutNothingOnTheWire:

    def test_yandex_rows_carry_no_date_at_all(self):
        rows = _rows("yandex", "yandex.html")
        assert rows and not _dated(rows), [(r.title, r.published) for r in rows]

    def test_an_undated_row_costs_exactly_zero_bytes(self):
        """缺席就是它的含义：一行没有日期时那三颗不许以 `""` 的形态上线。"""
        sr = SearchResult(title="t", url="https://x.test/a", source="yandex")
        dumped = wire._wire_dump(sr)
        for key in DATE_KEYS:
            assert key not in dumped, dumped

    def test_an_undated_round_does_not_pay_for_an_empty_ledger(self):
        resp = SearchResponseModel(query="q", results=[])
        assert "date_coverage" not in wire._wire_dump(resp)

    def test_a_dated_row_pays_only_for_what_it_states(self):
        sr = SearchResult(title="t", url="https://x.test/a", source="sogou",
                          published="2020-08-31", published_precision="day",
                          published_basis="2020-08-31")
        dumped = wire._wire_dump(sr)
        assert dumped["published"] == "2020-08-31"
        assert dumped["published_precision"] == "day"
        assert dumped["published_basis"] == "2020-08-31"


class TestBingGlobalRelativeRowIsNeverABareDay:
    """国际版那一格写的是 "1 day ago"：精度必须跟着值走，否则 4.6 会按假日期丢行。"""

    def test_the_relative_row_is_stamped_relative(self):
        rows = _rows("bing_global", "bing_global.html")
        dated = _dated(rows)
        assert len(dated) == 1, [r.published for r in rows]
        row = dated[0]
        assert row.published_precision == "relative", (row.published, row.published_basis)
        assert row.published_basis == "1 day ago", row.published_basis
        assert len(row.published) == 10, row.published

    def test_the_cn_layouts_absolute_dates_come_through_as_day(self):
        """实测修正：cn 版两套版面**是**带 `span.news_dt` 的（4/4 与 1/4，绝对到日）。"""
        a = _dated(_rows("bing", "bing_variant_a.html"))
        assert len(a) == 4, [r.published for r in _rows("bing", "bing_variant_a.html")]
        assert all(r.published_precision == "day" for r in a)


class TestSo360KeepsTheRowItCannotRead:

    def test_the_one_date_shaped_cell_is_not_turned_into_a_guessed_day(self):
        """so360：7 个结果容器里 1 个带日期形状的字，交出去的 0 行有日期。

        两个数都要写出来，因为它们说的是两件不同的事：`7 → 2` 是"只有 2 个容器读得出
        真链"（href 的账，16.x 就钉住了），`2 → 0` 才是日期的账。那一格里躺着的是
        "1条评论 &nbsp55个收藏 &nbsp发贴时间：2026年5月8日" —— 一整段混着写坏实体的版面
        文本，叶子读不出一个干净的日期，于是这一行按"没有日期"入账，而不是被补成
        2026-05-08 再跟着 `before=` 判掉。
        """
        eng = ms._TEXT_ENGINES["so360"](proxy=None, timeout=5, verify=True)
        text = (FIXTURE_DIR / "so360.html").read_text(encoding="utf-8")
        rows = eng.extract_kept(text)
        assert eng.last_extract[0] == 7, eng.last_extract   # 结果容器数
        assert len(rows) == 2, len(rows)                    # 交得出的行数
        assert not _dated(rows), [r.published for r in rows]
        # 那一格躺着的是 "1条评论 &nbsp55个收藏 &nbsp发贴时间：2026年5月8日" —— 一整段
        # 混着写坏实体的版面文本，叶子读不出干净日期，于是这一行按"没有日期"入账。


class TestTheStamperIsTheOnlyAuthor:
    """盖日期这一步在链上只有一个调用点，读日期的函数也只有一个。"""

    def test_there_is_exactly_one_call_site_that_reads_the_row_date(self):
        src = inspect.getsource(ms)
        # 1 = `_stamp_row_date` 的定义，1 = 它自己的函数体里那句 `serp_dates.read_row`，
        # 1 = `extract_kept` 里唯一那处推导式调用。多出一处就是第二个调用点。
        assert src.count("_stamp_row_date(") == 2, src.count("_stamp_row_date(")
        assert src.count("serp_dates.read_row(") == 1, src.count("serp_dates.read_row(")

    def test_the_stamper_hands_the_judgement_to_the_leaf(self):
        """解析不出 → 三颗空串，而不是把版面上那句原文留在 `published` 里上线。"""
        row = ms.TextResult(title="t", href="https://x.test/1", body="b",
                            published="See 1984 by Orwell")
        ms._stamp_row_date(row)
        assert (row.published, row.published_precision, row.published_basis) == ("", "", "")
        assert serp_dates.read_row("2020-08-31").precision == "day"

    def test_no_engine_class_grew_a_date_regex(self):
        """日期词表只有一份，住在 `envelope`；引擎那一层只许交 xpath。"""
        src = inspect.getsource(ms)
        for pattern in (r"\\d{4}", r"months? ago", r"天前"):
            assert pattern.replace("\\\\", "\\") not in src, pattern


# ============================================================================
# 来源：test_a_fetched_page_states_its_own_date_on_the_row.py
# ============================================================================
# 抓回来的那一页自己写了日期，这一行就要把源站的说法与引擎的说法并排放着。
# 重名消歧：_rows → _rows__a_fetched_page_states_it_db54

# ─── 1 · 只读页面自述的那一颗 ────────────────────────────────────

def test_the_pages_own_published_time_is_read_with_the_same_floor_as_published():
    assert serp_dates.page_stated_date(
        {"published_time": "2024-11-22T08:00:00+08:00"}) == "2024-11-22"
    assert serp_dates.page_stated_date({"published_time": "2024-11-22"}) == "2024-11-22"
    # 页面只写到月：值落 1 号。那是**帽**不是声明，而两颗日期字段共用同一个约定 ——
    # 各写各的精度口径，读者就得先猜它们是不是同一把尺。
    assert serp_dates.page_stated_date({"published_time": "2024-11"}) == "2024-11-01"


def test_the_two_date_fields_state_one_floor_convention():
    """两颗字段的说明必须说同一套精度口径（这一条钉的是文案，不是算术）。"""
    for name in ("published", "published_verified"):
        desc = SearchResult.model_fields[name].description.lower()
        assert "floor" in desc and "1st" in desc, f"{name} 的说明没写帽这一约定"


def test_a_page_that_states_nothing_verifies_nothing():
    for meta in ({}, {"published_time": ""}, {"published_time": "not a date"},
                 {"published_time": 1732233600}, None):
        assert serp_dates.page_stated_date(meta) == "", meta


def test_the_creation_date_and_the_modified_time_are_not_offered_as_publish_dates():
    """一张 wiki 的建页日期、一篇改过无数次的 `modified_time`，都不回答"这页什么时候
    发表"。把它们当核验，就是把"页面上有个日期"说成"源站声明了发表日"。"""
    assert serp_dates.page_stated_date({"creation_date": "2019-03-01"}) == ""
    assert serp_dates.page_stated_date({"modified_time": "2026-01-05"}) == ""


# ─── 2 · 账本跟着重算，而判决仍只有一位作者 ───────────────────────

def _rows__a_fetched_page_states_it_db54(n: int = 3) -> list[SearchResult]:
    return [SearchResult(title=f"t{i}", url=f"https://a{i}.test/x") for i in range(n)]


def test_an_empty_ledger_gains_the_whole_account_when_a_source_date_appears():
    rows = _rows__a_fetched_page_states_it_db54()
    rows[0].published = "2024-11-22"
    rows[0].published_verified = "2024-11-22"
    led = recount_row_dates(rows, {})
    assert led["rows_total"] == 3
    assert led["rows_with_date"] == 1 and led["rows_no_date"] == 2
    assert led["rows_verified_by_fetch"] == 1


def test_the_before_partition_stands_and_only_the_verified_column_moves():
    """问过 `before=` 的那一轮：丢了几行是**已经发生过的判决**，重算不许改历史。"""
    cutoff = date(2024, 1, 1)
    rows = _rows__a_fetched_page_states_it_db54()
    rows[0].published, rows[0].published_precision = "2023-05-05", "day"
    rows[1].published, rows[1].published_precision = "2024-06-06", "day"
    rows[2].published_precision = ""
    _kept, ledger = serp_dates.partition_for_before(
        search_mod._date_rows_dates(rows), cutoff)
    assert ledger["rows_dropped_by_before"] == 1, ledger
    assert ledger["rows_kept"] == 2, ledger

    rows[2].published_verified = "2024-06-06"
    after = recount_row_dates(rows, ledger)
    assert after["rows_verified_by_fetch"] == 1
    for key in ("rows_dropped_by_before", "rows_kept", "rows_total", "rows_no_date"):
        assert after[key] == ledger[key], f"重算动了历史那一格: {key}"


def test_a_verified_date_is_never_an_input_to_the_before_verdict():
    """核验是"这一行的说法有了出处"，不是"这一行更该被留下"。丢行只读 `published`
    那一族（4.6 的判据），所以给一行加上源站日期不许改变它的去向：一条证明晚于截止的
    行照旧被丢，哪怕它的源站自己报了日期。"""
    cutoff = date(2024, 1, 1)
    plain = SearchResult(title="t", url="https://a.test/x", published="2025-05-05",
                         published_precision="day")
    verified = SearchResult(title="t", url="https://a.test/x", published="2025-05-05",
                            published_precision="day", published_verified="2025-05-05")
    assert serp_dates.before_verdict(search_mod._row_date(plain), cutoff) == "drop"
    assert serp_dates.before_verdict(search_mod._row_date(verified), cutoff) == "drop"


# ─── 3 · 线上那一条路：真的走完 server.web_search 的扇出 ──────────

def _fake_search(dates: list[str], engines=("brave", "bing", "baidu", "so360")):
    async def fake_multi_search(query, max_results, **kwargs):
        rows = [RawResult(title=f"固态电池 量产 {i}",
                          url=f"https://a{i}.example.test/doc",
                          snippet="固态电池 量产 公告", source="bing", position=i + 1,
                          consensus=2, published=dates[i],
                          published_precision="day" if dates[i] else "")
                for i in range(len(dates))]
        return rows, [EngineReport(name=e, ok=True) for e in engines]
    return fake_multi_search


def _round(monkeypatch, metadata: dict, dates: list[str] | None = None):
    monkeypatch.setattr(search_mod, "multi_search", _fake_search(dates or ["2024-11-20"]))
    monkeypatch.setattr(search_mod, "ensure_reranker", AsyncMock(return_value=None))
    server = MasterFetchServer()
    server.web_read = AsyncMock(return_value=ResponseModel(
        url="u", status=200, content=["固态电池量产公告的正文一页。"], content_ok=True,
        metadata=metadata))
    return server


@pytest.mark.asyncio
async def test_a_fetched_page_writes_its_own_date_onto_its_row(monkeypatch):
    out = await _round(monkeypatch, {"published_time": "2024-11-22T08:00:00Z"}
                       ).web_search("固态电池 量产", fetch_content=True, cache_ttl=0)

    assert out.results[0].published_verified == "2024-11-22"
    assert out.date_coverage["rows_verified_by_fetch"] == 1
    assert out.date_coverage["rows_total"] == 1


@pytest.mark.asyncio
async def test_verification_never_overwrites_what_the_engine_said(monkeypatch):
    """两说不同时，行上必须两个都在 —— 把源站的日期写进 `published` 就是替引擎改口。"""
    out = await _round(monkeypatch, {"published_time": "2024-11-22"}
                       ).web_search("固态电池 量产", fetch_content=True, cache_ttl=0)

    assert out.results[0].published == "2024-11-20"
    assert out.results[0].published_verified == "2024-11-22"


@pytest.mark.asyncio
async def test_a_fetched_page_without_a_stated_date_verifies_nothing(monkeypatch):
    out = await _round(monkeypatch, {}).web_search("固态电池 量产",
                                                   fetch_content=True, cache_ttl=0)
    assert out.results[0].published_verified == ""
    assert out.date_coverage["rows_verified_by_fetch"] == 0, "账本不跟着动就是假账"


@pytest.mark.asyncio
async def test_a_round_that_fetched_nothing_writes_no_date_ledger(monkeypatch):
    """形状 5：`fetch_content` 没开时，那一格维持它本来的样子（这里没有别的日期可说，
    所以账本缺席）—— 凭空写一份全零的账就是替读者宣布"判过了"。"""
    monkeypatch.setattr(search_mod, "multi_search", _fake_search([""]))
    monkeypatch.setattr(search_mod, "ensure_reranker", AsyncMock(return_value=None))
    out = await MasterFetchServer().web_search("固态电池 量产", cache_ttl=0)

    assert out.results[0].published_verified == ""
    assert out.date_coverage == {}
    assert "date_coverage" not in wire._wire_dump(out)
    assert "published_verified" not in wire._wire_dump(out.results[0]), "干净行要零字节"
