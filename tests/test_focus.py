"""test_focus.py —— 由 3 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import pytest
from dhole_mcp import server as server_mod
from dhole_mcp.focus import _NEURAL_MAX_CHARS, _neural_block_scores, _split_for_encoder, focus_content
from dhole_mcp.server import MasterFetchServer, ResponseModel, _apply_chunking, _focus_intent_hint
import asyncio
from typing import get_args
from dhole_mcp import params, search
from dhole_mcp.focus import ENGINES as FOCUS_ENGINES
import re
from dhole_mcp.focus import _split_blocks, _tokens, _is_heading, _is_table, _is_code


# ============================================================================
# 来源：test_neural_focus_m1.py
# ============================================================================
# M 轴（M-1/M-2/M-3）：神经 focus、答案块定位、意图命中提示。

class FakeReranker:
    """score() 同形假打分器：query/doc -> 0..1。"""

    def __init__(self, table=None, zero=False):
        # table: {doc_substring: score}；未命中的文档给 0.001（跑题带）
        self.table = table or {}
        self.zero = zero
        self.calls: list[tuple[str, list[str]]] = []

    def score(self, query, docs):
        self.calls.append((query, list(docs)))
        if self.zero:
            return [0.0] * len(docs)
        out = []
        for d in docs:
            hit = [v for k, v in self.table.items() if k in d]
            out.append(hit[0] if hit else 0.001)
        return out


LONG_DOC = "\n\n".join([
    "# Pricing",
    "The Starter plan costs $9 per month and includes 3 projects. " * 6,
    "We also offer enterprise onboarding with SLA guarantees. " * 6,
    "Unrelated biography section about a 19th century botanist.",
])


# ─── M-1：编码器窗口与打分 ──────────────────────────────────────────────────


class TestEncoderWindow:

    def test_a_short_block_stays_whole(self):
        assert _split_for_encoder("short block") == ["short block"]

    def test_a_long_block_splits_under_the_cap(self):
        block = "word " * 300  # ~1500 chars
        pieces = _split_for_encoder(block)
        assert len(pieces) > 1
        assert all(len(p) <= _NEURAL_MAX_CHARS for p in pieces)
        assert " ".join(pieces).replace("\n", " ") == block.replace("\n", " ") \
            or "".join(pieces).replace("\n", "") == block.replace("\n", "")

    def test_a_minified_single_line_hard_wraps(self):
        block = "x" * 950
        pieces = _split_for_encoder(block)
        assert all(len(p) <= _NEURAL_MAX_CHARS for p in pieces)
        assert "".join(pieces) == block

    def test_block_score_is_the_max_over_its_chunks(self):
        rer = FakeReranker(table={"enterprise": 0.95})
        blocks = [LONG_DOC]
        scores = _neural_block_scores(rer, "enterprise SLA", blocks)
        assert scores is not None
        assert scores[0] == 0.95, "块的分数 = 最相关片段的分数"

    def test_an_all_zero_answer_reads_as_a_broken_scorer(self):
        scores = _neural_block_scores(FakeReranker(zero=True), "q", ["a", "b"])
        assert scores is None, "score() 对推理故障回 0；这里必须译成'打分器坏了'"

    def test_a_raising_scorer_maps_to_none(self):
        class Boom:
            def score(self, q, docs):
                raise RuntimeError("onnx exploded")
        assert _neural_block_scores(Boom(), "q", ["a"]) is None


# ─── M-1：focus_content 的神经路径与回退 ────────────────────────────────────


class TestNeuralFocusContent:

    def test_neural_scorer_names_itself_in_the_header(self):
        out = focus_content(LONG_DOC, "enterprise SLA",
                            reranker=FakeReranker(table={"enterprise": 0.95}))
        assert "neural relevance" in out
        assert "by BM25" not in out

    def test_neural_keeps_the_answer_and_drops_the_botanist(self):
        rer = FakeReranker(table={"enterprise": 0.95, "Starter": 0.9})
        out = focus_content(LONG_DOC, "enterprise SLA", reranker=rer)
        assert "enterprise" in out
        assert "botanist" not in out

    def test_a_broken_scorer_falls_back_to_bm25(self):
        out = focus_content(LONG_DOC, "enterprise SLA",
                            reranker=FakeReranker(zero=True))
        assert "by BM25 relevance" in out, "中途失败回 BM25，页不能被清空"

    def test_flag_off_leaves_the_bm25_path_untouched(self):
        out = focus_content(LONG_DOC, "enterprise SLA")
        assert "by BM25 relevance" in out

    def test_neural_ignores_the_bm25_absolute_threshold(self):
        """sigmoid 尺度与 BM25 分数没有共同零点：threshold=1.0 不得清空神经结果。"""
        rer = FakeReranker(table={"enterprise": 0.95})
        out = focus_content(LONG_DOC, "enterprise SLA", reranker=rer,
                            threshold=1.0)
        assert "enterprise" in out


# ─── M-2：best 模式 ─────────────────────────────────────────────────────────


class TestBestMode:

    DOC = "\n\n".join([
        "# Guide",
        "Intro paragraph about setup.",
        "## Pricing",
        "The price is $9 per month.",
        "## Support",
        "Email support@example.com for help.",
    ])

    def test_best_returns_the_answer_block_and_its_heading(self):
        rer = FakeReranker(table={"price": 0.97, "support": 0.9, "Guide": 0.1})
        out = focus_content(self.DOC, "what is the price", reranker=rer,
                            mode="best")
        assert "$9 per month" in out
        assert "## Pricing" in out, "答案块前的标题要保留"
        assert "support@example.com" not in out

    def test_best_works_over_bm25_too(self):
        out = focus_content(self.DOC, "what is the price", mode="best")
        assert "$9 per month" in out
        assert "support@example.com" not in out

    def test_best_header_says_what_it_is(self):
        out = focus_content(self.DOC, "price", reranker=FakeReranker(
            table={"price": 0.97}), mode="best")
        assert "Focus best block" in out


# ─── M-1 闸：flag 开着模型不在 → 显式报错 ───────────────────────────────────


class TestTheEntryGate:

    @pytest.mark.asyncio
    async def test_smart_fetch_refuses_without_the_model(self, monkeypatch):
        import dhole_mcp.reranker as rr

        async def no_model(*a, **kw):
            return None
        monkeypatch.setattr(rr, "get_reranker", lambda: None)
        monkeypatch.setattr(rr, "ensure_reranker", no_model)

        async def no_fetch(*a, **kw):
            raise AssertionError("model missing must stop the call before a fetch")
        monkeypatch.setattr(MasterFetchServer, "_force_fetch", no_fetch)
        monkeypatch.setattr(MasterFetchServer, "_auto_escalate", no_fetch)

        out = await MasterFetchServer().web_read(
            url="https://example.com/", focus="topic", focus_engine="neural")
        assert "neural_focus_model_missing" in out.error
        assert out.status == 0

    @pytest.mark.asyncio
    async def test_smart_fetch_without_focus_needs_no_model(self, monkeypatch):
        import dhole_mcp.reranker as rr

        async def no_model(*a, **kw):
            raise AssertionError("ensure_reranker ran without a focus query")
        monkeypatch.setattr(rr, "get_reranker", lambda: None)
        monkeypatch.setattr(rr, "ensure_reranker", no_model)

        result = ResponseModel(url="https://example.com/", status=200,
                               content=["body"], fetcher_used="http")
        monkeypatch.setattr(MasterFetchServer, "_auto_escalate", no_fetch_ok(result))
        out = await MasterFetchServer().web_read(url="https://example.com/")
        assert out.content == ["body"]

    @pytest.mark.asyncio
    async def test_unnamed_engine_never_touches_the_reranker(self, monkeypatch):
        import dhole_mcp.reranker as rr

        def boom():
            raise AssertionError("no focus_engine named but the reranker was consulted")
        monkeypatch.setattr(rr, "get_reranker", boom)

        result = ResponseModel(url="https://example.com/", status=200,
                               content=["body"], fetcher_used="http")
        monkeypatch.setattr(MasterFetchServer, "_auto_escalate", no_fetch_ok(result))
        out = await MasterFetchServer().web_read(url="https://example.com/",
                                                    focus="topic")
        assert out.content == ["body"]  # 原样通过；stub 不经 _apply_chunking

    @pytest.mark.asyncio
    async def test_smart_crawl_refuses_without_the_model(self, monkeypatch):
        import dhole_mcp.reranker as rr

        async def no_model(*a, **kw):
            return None
        monkeypatch.setattr(rr, "get_reranker", lambda: None)
        monkeypatch.setattr(rr, "ensure_reranker", no_model)

        out = await MasterFetchServer().web_crawl(
            "https://example.com/", focus="topic", max_pages=1,
            focus_engine="neural")
        assert out.error.startswith("neural_focus_model_missing")
        assert out.pages == []


def no_fetch_ok(result):
    async def _inner(self, *a, **kw):
        return result
    return _inner


# ─── M-3：意图命中提示 ──────────────────────────────────────────────────────


class TestIntentHint:

    @staticmethod
    def _result():
        return ResponseModel(url="https://example.com/", status=200,
                             content=["body"], fetcher_used="http",
                             content_ok=True)

    def test_a_low_neural_score_appends_the_hint(self):
        r = _focus_intent_hint(self._result(), "how to fly",
                               {"scorer": "neural", "best_score": 0.002})
        assert "focus match is weak" in r.next_action
        assert "0.002" in r.next_action
        assert r.content_ok is True, "内容是真的，弱的是命中——不翻转 content_ok"

    def test_a_strong_neural_score_stays_silent(self):
        r = _focus_intent_hint(self._result(), "pricing",
                               {"scorer": "neural", "best_score": 0.95})
        assert r.next_action == ""

    def test_bm25_has_no_calibrated_reading(self):
        r = _focus_intent_hint(self._result(), "pricing",
                               {"scorer": "bm25", "best_score": 0.001})
        assert r.next_action == ""

    def test_no_stats_no_hint(self):
        r = _focus_intent_hint(self._result(), "pricing", None)
        assert r.next_action == ""

    @pytest.mark.asyncio
    async def test_end_to_end_hint_on_a_neural_fetch(self, monkeypatch):
        """_apply_chunking 全链路：点名 neural + 假打分器全低分 → 提示出现在响应里。"""
        import dhole_mcp.reranker as rr
        monkeypatch.setattr(rr, "get_reranker", lambda: FakeReranker(zero=False))
        tokens = (server_mod._FOCUS_ENGINE.set("neural"),)

        multi_block = "\n\n".join([
            "# Intro", "Setup instructions for the tutorial.",
            "## Details", "More unrelated prose about installation.",
        ])
        result = ResponseModel(url="https://example.com/", status=200,
                               content=[multi_block], fetcher_used="http")
        tokens = (server_mod._FOCUS.set("how to fly to the moon cheaply"),
                  *tokens)
        try:
            out = _apply_chunking(result)
        finally:
            for tok in reversed(tokens):
                tok.var.reset(tok)
        assert "focus match is weak" in out.next_action


class TestNoPerPagePrescreen:
    """17.0 §6 删掉爬前相关性预筛（原 `_page_relevant`），这里钉的是删完之后仍然
    成立的那个形状：**取回几页就报几页**。

    那一档做的是"整页在进结果之前被打一次分，落在跑题带就整页丢弃"，省下的是跑题页
    的 token，代价是页面取回来了却不在结果里 —— 而"我要找的那页正好被判成跑题"这种
    失败是不可见的。B8 会带着新的打分后端重做这个能力（§3 第 7 条：现在删是因为它
    绑的就是 B8 要换掉的那套东西），在那之前不许有静默丢页。
    断言写在真爬取循环上（假 server 对象），不是"某个函数不存在"。
    """

    SEED = "https://prescreen.test/"
    LEAVES = [f"https://prescreen.test/p{i}" for i in range(3)]

    class _Server:
        """每一 leaf 都是一页与 `focus` 无关的东西；seed 只负责把链摊开。"""

        async def web_read(self, url, **kwargs):
            if url.rstrip("/") == TestNoPerPagePrescreen.SEED.rstrip("/"):
                body = ('<html><body>'
                        + "".join(f'<a href="/p{i}">p{i}</a>' for i in range(3))
                        + '</body></html>')
            else:
                body = "<p>bread and butter</p>"
            return ResponseModel(url=url, status=200, content=[body],
                                 content_type="text/html", fetcher_used="http")

    @pytest.mark.asyncio
    async def test_a_named_neural_crawl_keeps_every_page_it_fetched(self, monkeypatch):
        import dhole_mcp.reranker as rr
        from dhole_mcp import crawl as crawl_mod
        from dhole_mcp.focus import FOCUS_ENGINE

        # 假打分器对没点名 table 的文档一律给 0.001 —— 正是旧过滤器会丢掉的那一档。
        monkeypatch.setattr(rr, "get_reranker", FakeReranker)
        token = FOCUS_ENGINE.set("neural")
        try:
            out = await crawl_mod.web_crawl(
                self._Server(), self.SEED, max_pages=4, max_depth=1,
                focus="asyncio", cache_ttl=0)
        finally:
            FOCUS_ENGINE.reset(token)

        urls = {p.url.rstrip("/") for p in out.pages}
        assert urls >= {u.rstrip("/") for u in self.LEAVES}, \
            f"取回的页面没有全部进结果：{sorted(urls)}"
        assert "dropped" not in out.summary.lower(), out.summary


# ─── W15：按次选打分器（flag = 许可，focus_engine = 意愿）────────────────────


def _never_consulted():
    def boom(*a, **kw):
        raise AssertionError("this path must not consult the reranker at all")
    return boom


async def _never_ensured(*a, **kw):
    raise AssertionError("this path must not load (or download) the reranker")


class TestThePerCallScorerChoice:
    """M-1 的开关语义 17.0 收敛成一件事：``options.focus_engine`` 就是全部。

    钉住的三条：不点名 = BM25（12.5s/页的推理不该默认发生）；``bm25`` 永远
    不碰模型（点名便宜路的人不想为语义分付费）；``neural`` 缺模型时是**拒绝**
    而不是降级或下载（一次静默降级会把"神经/BM25 的差别"变成不可解释的玄学）。
    """

    @pytest.mark.parametrize("engine,expected", [
        ("", False), ("bm25", False), ("neural", True),
    ])
    def test_effective_choice_is_the_parameter_alone(self, engine, expected):
        from dhole_mcp import focus as _f
        token = _f.FOCUS_ENGINE.set(engine)
        try:
            assert _f.engine_wants_neural() is expected
        finally:
            _f.FOCUS_ENGINE.reset(token)

    def test_the_permission_api_is_gone(self):
        from dhole_mcp import focus as _f
        assert not hasattr(_f, "neural_for_call"), (
            "许可/意愿两段式已删 —— 留着它就是留着第二份语义")

    @pytest.mark.asyncio
    async def test_bm25_never_touches_the_model(self, monkeypatch):
        import dhole_mcp.reranker as rr
        monkeypatch.setattr(rr, "get_reranker", _never_consulted())
        monkeypatch.setattr(rr, "ensure_reranker", _never_ensured)

        result = ResponseModel(url="https://example.com/", status=200,
                               content=["body"], fetcher_used="http")
        monkeypatch.setattr(MasterFetchServer, "_auto_escalate", no_fetch_ok(result))
        out = await MasterFetchServer().web_read(url="https://example.com/",
                                                    focus="topic",
                                                    focus_engine="bm25")
        assert out.content == ["body"]

    @pytest.mark.asyncio
    async def test_neural_without_the_model_is_refused_not_downgraded(self, monkeypatch):
        import dhole_mcp.reranker as rr

        async def no_model(*a, **kw):
            return None
        monkeypatch.setattr(rr, "get_reranker", lambda: None)
        monkeypatch.setattr(rr, "ensure_reranker", no_model)

        async def no_fetch(*a, **kw):
            raise AssertionError("a refused call must not reach the network")
        monkeypatch.setattr(MasterFetchServer, "_auto_escalate", no_fetch)

        out = await MasterFetchServer().web_read(url="https://example.com/",
                                                    focus="topic",
                                                    focus_engine="neural")
        # 拒绝走的是所有参数级拒绝的同一条通道，而不是一个只有这个开关会
        # 产生的新形状；错误里没有"已用 BM25"的假成功。
        assert "neural_focus_model_missing" in out.error
        assert out.status == 0, "不是伪装成成功的 BM25 结果"
        assert "focus_engine_not_permitted" not in str(out.error)

    @pytest.mark.asyncio
    async def test_the_model_gate_stands(self, monkeypatch):
        """点名 neural 而模型缺失，仍是 neural_focus_model_missing。"""
        import dhole_mcp.reranker as rr

        async def no_model(*a, **kw):
            return None
        monkeypatch.setattr(rr, "get_reranker", lambda: None)
        monkeypatch.setattr(rr, "ensure_reranker", no_model)

        async def no_fetch(*a, **kw):
            raise AssertionError("missing model must stop the call before a fetch")
        monkeypatch.setattr(MasterFetchServer, "_auto_escalate", no_fetch)

        out = await MasterFetchServer().web_read(url="https://example.com/",
                                                    focus="topic",
                                                    focus_engine="neural")
        assert "neural_focus_model_missing" in out.error

    def test_the_chunker_obeys_the_per_call_choice(self, monkeypatch):
        """点名要活到打分那一刻，不只是活到闸里——两条路的表头就是证据。"""
        import dhole_mcp.reranker as rr
        monkeypatch.setattr(rr, "get_reranker",
                            lambda: FakeReranker(table={"enterprise": 0.95}))

        def run(engine):
            tokens = (server_mod._FOCUS.set("enterprise SLA"),
                      server_mod._FOCUS_ENGINE.set(engine))
            try:
                result = ResponseModel(url="https://example.com/", status=200,
                                       content=[LONG_DOC], fetcher_used="http")
                return _apply_chunking(result).content[0]
            finally:
                for token in reversed(tokens):
                    token.var.reset(token)

        assert "by BM25 relevance" in run("bm25")
        assert "neural relevance" in run("neural")
        assert "by BM25 relevance" in run("")  # 不传 = BM25（17.0：无默认跟随）


class TestTheCrawlFollowsTheSameRule:
    """爬取比抓取更需要这句实话：12.5s/页 × max_pages。"""

    @pytest.mark.asyncio
    async def test_crawl_refuses_neural_without_the_model(self, monkeypatch):
        import dhole_mcp.reranker as rr

        async def no_model(*a, **kw):
            return None
        monkeypatch.setattr(rr, "get_reranker", lambda: None)
        monkeypatch.setattr(rr, "ensure_reranker", no_model)

        # 拒绝必须发生在爬取本体之前，所以本体被换成一个记过的 stub：真爬取不仅
        # 慢，还会把这个单元测试变成对 example.com 的一次真实拜访。
        ran: list[bool] = []

        async def fake_crawl(*a, **kw):
            ran.append(True)
            from dhole_mcp.crawl import CrawlResponseModel as _CRM
            return _CRM(start_url="https://example.com/", pages=[])
        monkeypatch.setattr("dhole_mcp.crawl.web_crawl", fake_crawl)

        out = await MasterFetchServer().web_crawl("https://example.com/",
                                                    focus="topic",
                                                    focus_engine="neural")
        assert "neural_focus_model_missing" in out.error
        assert out.pages == []
        assert not ran, "the crawl body ran despite the refusal"

    @pytest.mark.asyncio
    async def test_crawl_with_bm25_needs_no_model(self, monkeypatch):
        """focus_engine='bm25'（或不点名）根本不查模型。"""
        import dhole_mcp.reranker as rr
        monkeypatch.setattr(rr, "get_reranker", _never_consulted())
        monkeypatch.setattr(rr, "ensure_reranker", _never_ensured)

        from dhole_mcp.crawl import CrawlResponseModel as _CRM

        async def fake_crawl(*a, **kw):
            return _CRM(start_url="https://example.com/", pages=[])
        monkeypatch.setattr("dhole_mcp.crawl.web_crawl", fake_crawl)

        out = await MasterFetchServer().web_crawl("https://example.com/",
                                                   focus="topic",
                                                   focus_engine="bm25")
        assert not out.error, "走到了爬取本体（stub 无页），闸没有拦住"

    def test_crawl_reads_the_same_per_call_var(self):
        from dhole_mcp import crawl as _crawl
        token = server_mod._FOCUS_ENGINE.set("bm25")
        try:
            assert _crawl._neural_for_this_call() is False
        finally:
            server_mod._FOCUS_ENGINE.reset(token)
        token2 = server_mod._FOCUS_ENGINE.set("")
        try:
            assert _crawl._neural_for_this_call() is False
        finally:
            server_mod._FOCUS_ENGINE.reset(token2)
        token3 = server_mod._FOCUS_ENGINE.set("neural")
        try:
            assert _crawl._neural_for_this_call() is True
        finally:
            server_mod._FOCUS_ENGINE.reset(token3)


# ============================================================================
# 来源：test_the_enum_argument_values_are_checked_by_dhole.py
# ============================================================================
# 批次 6c：枚举参数的合法值由 **dhole** 报，不由宿主的 JSON-Schema 门报。

URL = "https://example.com/"


def _defs():
    return {d["name"]: d for d in MasterFetchServer._TOOL_DEFS}


def _walk(node, path="root"):
    """Yield (path, value) for every `enum` key anywhere in a schema tree."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "enum":
                yield path, value
            else:
                yield from _walk(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, item in enumerate(node):
            yield from _walk(item, f"{path}[{i}]")


# ─── 1 · wire 上不再有任何 enum 数组 ─────────────────────────────

def test_no_tool_schema_carries_an_enum_array_anywhere():
    hits = []
    for name, tool in _defs().items():
        hits += [(f"{name}{path}", values) for path, values
                 in _walk(tool.get("inputSchema", {}), "")]
    assert not hits, f"这些 enum 数组回来了，宿主会先于 dhole 拒值：{hits}"


def test_the_enum_values_are_still_stated_where_a_reader_looks():
    """摘掉数组不等于藏起词表：描述必须自己点得出每一颗合法值。

    这一条是"删除 enum 会不会让调用方看不见词表"的反问 —— 会，如果只删不写。
    所以每颗曾经带 enum 的参数，其描述里必须出现全部合法值（`freshness` 当年只写
    "only these four"，那正是摘掉数组之后最不能留下的一句）。
    """
    props = _defs()
    checked = [
        ("web_read", "extraction_type"), ("web_act", "extraction_type"),
        ("web_request", "extraction_type"), ("web_read", "focus_mode"),
        ("web_read", "focus_engine"), ("web_crawl", "focus_engine"),
        ("web_read", "force_fetcher"), ("web_crawl", "force_fetcher"),
        ("web_read", "anti_detect"), ("web_act", "anti_detect"),
        ("web_search", "mode"), ("web_search", "freshness"),
    ]
    for tool, arg in checked:
        spec = props[tool]["inputSchema"]["properties"][arg]
        text = spec["description"]
        if arg == "anti_detect":
            values = list(server_mod._ANTI_DETECT)
        elif arg == "force_fetcher":
            values = sorted(params._FORCE_FETCHER_VALUES)
        elif arg == "mode":
            values = list(search._IMPLEMENTED_MODES)
        elif arg == "freshness":
            values = ["day", "week", "month", "year"]
        elif arg == "focus_mode":
            values = list(params._FOCUS_MODES)
        elif arg == "focus_engine":
            values = list(FOCUS_ENGINES)
        elif tool == "web_read":
            values = list(params._READ_EXTRACTION_TYPES)
        else:
            values = list(params._EXTRACTION_TYPES)
        missing = [v for v in values if v not in text]
        if missing:
            # 唯一允许的"没点全"是一句有落点的指针：档位表只住 web_read 一处，是 B5
            # 阶段5 砍掉的 83 字（账写在棘轮注释里），摘掉 enum 数组之后那笔账仍成立。
            # 但指针必须指向**这颗参数自己**的完整名单 —— "only these four" 那种既不
            # 说值、也不说去哪看的写法（当年 freshness 就是这句）不许过这一关。
            assert f"per web_read.{arg}" in text, (
                f"{tool}.{arg} 没点出 {missing}，也没有指向点全了的那份描述：{text}")


def test_the_source_of_truth_for_a_refused_value_is_dholes_own_table():
    """源码形状守卫：这一批不再往 server.py 的 _TOOL_DEFS 里写 enum 字面量。

    手法照 tests/test_ensure_chromium.py 读 `StealthyBrowser.start` 的那条 ——
    读源码而不是读行为，因为行为可以碰巧对，而"enum 又被人加回来了"必须当场红。
    `_TOOL_DEFS` 是类体里的一个 list 字面量（不是方法），所以读的是整个类。
    """
    import inspect

    source = inspect.getsource(MasterFetchServer)
    assert '"enum"' not in source, "线格式里又出现了 enum 数组"
    # 而且合法值的那份表必须真的住在 params：删了数组又没补校验器，就是让非法值
    # 第一次落到按字面量写的内部代码（本批次明令禁止的顺序错误）。
    assert set(params._ENUM_ARGS) == {"web_read", "web_act", "web_request",
                                      "web_crawl"}, sorted(params._ENUM_ARGS)


# ─── 2 · 每一颗非法值都由 dhole 报出，并列出全部合法值 ──────────

@pytest.mark.parametrize("tool,arg,values", [
    ("web_read", "extraction_type", params._READ_EXTRACTION_TYPES),
    ("web_act", "extraction_type", params._EXTRACTION_TYPES),
    ("web_request", "extraction_type", params._EXTRACTION_TYPES),
    ("web_read", "focus_mode", params._FOCUS_MODES),
    ("web_read", "focus_engine", FOCUS_ENGINES),
    ("web_crawl", "focus_engine", FOCUS_ENGINES),
])
def test_a_bad_enum_value_is_refused_with_the_whole_value_list(tool, arg, values):
    with pytest.raises(ValueError) as e:
        params._validate_tool_args(tool, {"url": URL, arg: "bogus"})
    msg = str(e.value)
    assert msg.startswith(arg), f"报错第一个词必须是那颗参数名：{msg}"
    assert "bogus" in msg, msg
    for value in values:
        assert value in msg, f"拒本没列全，{value} 不在：{msg}"
    assert "No request was made" in msg, "非法值不许已经发过一次请求"


@pytest.mark.parametrize("arg", ["force_fetcher", "anti_detect"])
def test_the_two_enum_params_that_already_had_a_validator_still_refuse(arg):
    for tool in ("web_read", "web_act" if arg == "anti_detect" else "web_crawl"):
        with pytest.raises(ValueError) as e:
            params._validate_tool_args(tool, {"url": URL, arg: "bogus"})
        assert arg in str(e.value)


def test_search_refuses_its_two_enums_before_its_first_packet():
    """mode / freshness 的校验器住在 search.py，而且在那一次网络之前。

    它们不进 `params._ENUM_ARGS` 不是漏了，是"一处只留一份表"：`_IMPLEMENTED_MODES`
    已经是那颗最准的表（它对 mode 的定义就是"实现过的模式"），第二份副本会漂。
    拒绝走的是 `SearchResponseModel(error=...)` 而不是异常 —— 而且 `duration_ms=0`
    就是"一次包都没发"的证人：真的搜过不可能零毫秒。
    """
    cases = [("mode", "blurry", search._IMPLEMENTED_MODES),
             ("freshness", "decade", ("day", "week", "month", "year"))]
    for arg, bad, values in cases:
        result = asyncio.run(search.web_search(None, "some query", **{arg: bad}))
        msg = result.error or ""
        assert bad in msg, f"{bad!r} 的拒本没提这个值：{msg}"
        for value in values:
            assert value in msg, f"{arg} 的拒本没列全，{value} 不在：{msg}"
        assert result.duration_ms == 0, f"{arg}={bad!r} 拒本之前已经跑过一次搜索"
        assert not result.results


# ─── 3 · 合法值的形状与"没设"的形状 ─────────────────────────────

@pytest.mark.parametrize("given,wanted", [
    ("HTML", "html"), ("  Markdown  ", "markdown"), ("BEST", "best"),
    ("Neural", "neural"), ("BM25", "bm25"),
])
def test_capitalization_and_space_are_noise_not_a_different_value(given, wanted):
    assert params._coerce_enum_arg(given, "x", ("html", "markdown", "best",
                                                "neural", "bm25")) == wanted


@pytest.mark.parametrize("given", [None, ""])
def test_absent_and_empty_both_mean_not_set_so_the_tools_own_default_applies(given):
    """空串读成"没设"，而不是读成一种格式。

    摘掉 enum 之后 `extraction_type=""` 第一次能走到内部：它不等于任何一颗字面量，
    于是会一路掉进 `else "markdown"`（`_extracted_type_for`）—— 那正是"传了但被静默
    改回默认"这一族的形状。这一条把它挡在边界，交给 `_forward` 的"缺席才用默认"。
    """
    assert params._coerce_enum_arg(given, "extraction_type",
                                  params._READ_EXTRACTION_TYPES) is None
    out = params._validate_tool_args("web_read", {"url": URL,
                                                  "extraction_type": given})
    assert out["extraction_type"] is None
    # `_forward` 丢 None，所以方法签名上那个非 None 默认重新生效（缓存 INSERT 的
    # extraction_type 列是 NOT NULL，None 传下去过不了）。
    assert "extraction_type" not in MasterFetchServer()._forward(
        "web_read", out, skip=("url",))


def test_a_non_string_enum_value_is_refused_as_a_type_error():
    with pytest.raises(ValueError, match="must be a string"):
        params._coerce_enum_arg(7, "focus_mode", params._FOCUS_MODES)


# ─── 4 · 一份表，不许漂 ─────────────────────────────────────────

def test_the_params_table_matches_the_python_annotations():
    """`params` 的集合 == 签名上的 `Literal`。

    这两处各说各话过一次就是整批的动机：Literal 只到客户端为止（`args.get()` 读参数），
    而 enum 摘掉之后 params 这份表是**唯一**在运行时被读的那份 —— 它与签名漂了，
    工具就会接受一颗自己处理不了的写法。
    """
    assert set(params._EXTRACTION_TYPES) == set(
        get_args(server_mod.ExtendedExtractionType))
    assert set(params._READ_EXTRACTION_TYPES) == set(
        get_args(server_mod.ReadExtractionType))
    assert set(params._FOCUS_ENGINES) == set(FOCUS_ENGINES)
    assert set(params._FORCE_FETCHER_TOOLS) == {"web_read", "web_crawl"}


def test_searchs_enums_are_not_copied_into_the_boundary_table():
    for spec in params._ENUM_ARGS.values():
        assert "mode" not in spec and "freshness" not in spec, (
            "第二份 mode/freshness 表：search.py 的 _IMPLEMENTED_MODES 才是那一份")


def test_every_enum_argument_the_wire_advertises_is_checked_somewhere():
    """公开面上的每一颗枚举参数，必须有一处运行时校验器读得到它 —— 摘 enum 的前提。

    遍历口径是 _TOOL_DEFS 的属性名，不是这张表的手抄名单：将来谁在 wire 上加一颗
    新的字符串参数并只写"合法值有哪几颗"，这条就红，逼他回答"谁在运行时守这句话"。
    """
    owned = set()
    for tool, spec in params._ENUM_ARGS.items():
        owned |= {(tool, arg) for arg in spec}
    owned |= {("web_read", "force_fetcher"), ("web_crawl", "force_fetcher")}
    owned |= {("web_read", "anti_detect"), ("web_act", "anti_detect")}
    owned |= {("web_search", "mode"), ("web_search", "freshness")}
    named = {"extraction_type", "focus_mode", "focus_engine", "force_fetcher",
             "anti_detect", "mode", "freshness"}
    for tool, spec in _defs().items():
        for arg in spec["inputSchema"]["properties"]:
            if arg in named:
                assert (tool, arg) in owned, f"{tool}.{arg} 在 wire 上，但没人校验"


# ─── 5 · 曾经的"不相等 ⇒ 静默换一条路" ──────────────────────────

def test_a_misspelled_focus_engine_is_refused_instead_of_becoming_bm25():
    """这条是整批最像"没事"的一处：拼错 focus_engine 从来不是错误，是一次降档。

    `normalize_engine` 把不可识别的值读成 ''（=BM25），当年是合理的，因为 enum 在
    schema 上挡着；摘掉之后它是唯一防线之外的静默。现在非法值在边界就拒，
    `normalize_engine` 的宽容只对内部调用方成立。
    """
    with pytest.raises(ValueError) as e:
        params._validate_tool_args("web_read", {"url": URL,
                                                "focus_engine": "nueral"})
    assert "bm25" in str(e.value) and "neural" in str(e.value)
    assert "nueral" in str(e.value)


def test_a_bad_readback_format_reaches_the_caller_as_the_usual_envelope():
    """web_read 的坏参数走信封，不走 Python 栈（与 urls / anti_detect 同一条口径）。

    这一条盯的是"错误本身长什么样"：同一个 typo 在 web_read 上必须是那个 200-with-
    error 的信封（宿主把它当普通一次失败读），不能是一句 traceback。
    """
    content, structured = asyncio.run(
        MasterFetchServer()._dispatch("web_read", {"url": URL,
                                                  "extraction_type": "markdown+"}))
    blob = str(content) + str(structured)
    assert "extraction_type" in blob, blob[:200]
    for value in params._READ_EXTRACTION_TYPES:
        assert value in blob, f"信封里没列出 {value}"
    assert "Traceback" not in blob and "MasterFetchServer.py" not in blob


def test_the_removed_enum_was_not_the_only_thing_between_a_caller_and_a_wrong_tier():
    """web_crawl 的 force_fetcher：分发层那份重复校验已经撤掉，边界这一份必须接得住。

    撤它不是嫌它多余，是因为它长在 `_dispatch` 的 try 之外：同一个 typo 在 web_read
    上拿到 invalid_request 信封，在 web_crawl 上拿到一句裸 ValueError —— 一处两个
    形状。现在两处同形。
    """
    with pytest.raises(ValueError, match="force_fetcher"):
        asyncio.run(MasterFetchServer()._dispatch(
            "web_crawl", {"url": URL, "force_fetcher": "hpt"}))


# ============================================================================
# 来源：test_focus.py
# ============================================================================
# Focus tests: BM25 content filtering.

class TestFocusContent:

    def test_relevant_blocks_kept(self):
        text = (
            "Python is a programming language.\n\n"
            "Java is also a programming language.\n\n"
            "Rust is a systems programming language with memory safety.\n\n"
            "JavaScript runs in the browser."
        )
        result = focus_content(text, "Python programming")
        assert "Python is a programming language" in result
        # The header should mention focus
        assert "Focus:" in result

    def test_empty_query_returns_original(self):
        text = "Some content.\n\nMore content."
        assert focus_content(text, "") == text

    def test_none_query_returns_original(self):
        text = "Some content.\n\nMore content."
        assert focus_content(text, None) == text

    def test_single_block_returns_original(self):
        text = "Just one block of text."
        assert focus_content(text, "anything") == text

    def test_empty_text_returns_original(self):
        assert focus_content("", "query") == ""

    def test_no_matching_terms_returns_fallback_top(self):
        text = "Block A about cooking.\n\nBlock B about gardening.\n\nBlock C about painting."
        result = focus_content(text, "quantum physics")
        # Fallback: should return top blocks (not empty)
        assert "Block" in result
        assert "Focus:" in result

    def test_heading_preceding_kept_block_preserved(self):
        text = (
            "# Section Header\n\n"
            "This block talks about Python.\n\n"
            "# Other Header\n\n"
            "This block talks about Java."
        )
        result = focus_content(text, "Python")
        assert "Section Header" in result  # heading preserved for context
        assert "Python" in result

    def test_order_preserved(self):
        text = (
            "First mention of Python.\n\n"
            "Something about Java.\n\n"
            "Second mention of Python.\n\n"
            "Something about Go."
        )
        result = focus_content(text, "Python")
        first_pos = result.find("First mention")
        second_pos = result.find("Second mention")
        assert first_pos < second_pos

    def test_focus_header_includes_query_and_block_count(self):
        text = "Apple pie recipe.\n\nBanana bread recipe.\n\nCherry tart recipe.\n\nDate cake recipe."
        result = focus_content(text, "apple")
        assert "Focus:" in result
        assert "'apple'" in result


class TestSplitBlocks:

    def test_splits_on_blank_lines(self):
        blocks = _split_blocks("A\n\nB\n\nC")
        assert len(blocks) == 3

    def test_consecutive_blank_lines_dont_create_empty_blocks(self):
        blocks = _split_blocks("A\n\n\n\nB")
        assert len(blocks) == 2

    def test_no_blank_lines_returns_one_block(self):
        blocks = _split_blocks("A\nB\nC")
        assert len(blocks) == 1

    def test_empty_text_returns_empty_list(self):
        assert _split_blocks("") == []


class TestIsHeading:

    def test_markdown_h1(self):
        assert _is_heading("# Title") is True

    def test_markdown_h2(self):
        assert _is_heading("## Subtitle") is True

    def test_not_heading(self):
        assert _is_heading("Regular paragraph") is False

    def test_blank_then_heading(self):
        assert _is_heading("\n\n# Heading") is True


class TestTokens:

    def test_lowercases(self):
        assert "hello" in _tokens("Hello World")

    def test_filters_single_chars(self):
        tokens = _tokens("a b cd ef")
        assert "cd" in tokens
        assert "ef" in tokens
        assert "a" not in tokens
        assert "b" not in tokens

    def test_extracts_alphanumeric(self):
        tokens = _tokens("python3 async2026")
        assert "python3" in tokens
        assert "async2026" in tokens

    def test_empty_text(self):
        assert _tokens("") == []


# ─── Heading-aware BM25 + table/code preservation ──

class TestHeadingAwareBM25:

    def test_blocks_under_matching_heading_boosted(self):
        text = """# Introduction

This is an intro paragraph with no query terms.

## Model Architecture

The hidden dimension is critical for performance.
Each layer processes the full representation.

## Training Details

We used Adam optimizer with learning rate scheduling."""

        result = focus_content(text, "model architecture dimension")
        assert "Model Architecture" in result

    def test_heading_without_query_terms_not_boosted(self):
        text = """## Training Details

We used Adam optimizer with cosine annealing.

## Model Architecture

The architecture uses 96 transformer layers."""

        result = focus_content(text, "training details optimizer")
        assert "Adam optimizer" in result

    def test_heading_level_hierarchy(self):
        text = """## Architecture

### Transformer Architecture

The attention mechanism is key.

### RNN Architecture

Recurrent connections process sequences."""

        result = focus_content(text, "architecture transformer attention")
        assert "Transformer Architecture" in result
        assert "attention mechanism" in result

    def test_table_preservation(self):
        text = """# Introduction

Some intro text here.

| Model | d_model | layers |
|---|---|---|
| GPT-3 | 12288 | 96 |

## Other Section

Unrelated content about training."""

        result = focus_content(text, "GPT-3 d_model 12288")
        assert "12288" in result
        assert "|" in result  # table format preserved


class TestTableCodeDetection:

    def test_is_table_markdown(self):
        assert _is_table("| A | B | C |\n|---|---|---|\n| 1 | 2 | 3 |") is True

    def test_is_table_not_prose(self):
        assert _is_table("This is a paragraph of text with no pipes.") is False

    def test_is_code_fenced(self):
        assert _is_code("```python\ndef foo():\n    pass\n```") is True

    def test_is_code_indented(self):
        assert _is_code("    def foo():\n        return 42\n    bar = foo()") is True

    def test_is_code_not_prose(self):
        assert _is_code("This is a normal paragraph.") is False


# ─── Anchoring the cut to the best block ───────────────────────────────
#
# A SENTENCE query used to keep most of the page. BM25+ keeps idf > 0 for every
# term, so each of a question's ubiquitous words adds a small positive score to
# nearly every block and the sum clears the absolute threshold. Measured on
# docs.python.org/3/library/asyncio-task.html (88 blocks): 'TaskGroup' kept 9%
# of blocks while "What is the difference between asyncio.gather and
# asyncio.TaskGroup?" kept 76% — i.e. the documented usage (focus='question')
# was the case that saved the least context. The cut is therefore anchored to
# the best CONTENT block's score.
#
# The corpus below has GRADED relevance (the four query terms appear in 4, 2
# and 1 of 20 blocks), which is what a real page looks like. Uniform filler
# cannot reproduce the defect at all: with df == n the idf collapses to ~0.5/n,
# so even the absolute cut drops it — measured while writing this test.


class TestFocusIsAnchoredToTheBestBlock:

    QUERY = "TaskGroup cancel timeout shield"

    @classmethod
    def _graded_page(cls) -> str:
        return "\n\n".join(
            ["TaskGroup cancel timeout shield."]      # all four terms
            + ["TaskGroup cancel."] * 3               # two
            + ["TaskGroup."] * 6                      # one
            + ["Unrelated paragraph about something else."] * 10)

    @staticmethod
    def _kept(result: str) -> int:
        """The count the agent itself reads in the Focus header."""
        return int(re.search(r"showing (\d+) of (\d+) blocks", result).group(1))

    def test_a_sentence_query_no_longer_keeps_half_the_page(self):
        page = self._graded_page()
        absolute_only = focus_content(page, self.QUERY, relative_threshold=0.0)
        anchored = focus_content(page, self.QUERY)
        assert self._kept(absolute_only) == 10, "锚定前的行为（复现缺陷形状）"
        assert self._kept(anchored) == 4
        assert self._kept(anchored) < self._kept(absolute_only)

    def test_the_best_block_always_survives(self):
        """The cut must never remove the block that IS the answer."""
        for relative in (0.0, 0.33, 0.9):
            out = focus_content(self._graded_page(), self.QUERY,
                                relative_threshold=relative)
            assert "timeout shield" in out
            assert self._kept(out) >= 1

    def test_a_keyword_query_keeps_everything_it_kept_before(self):
        """No regression on the case that already worked: with identical
        relevant blocks the best score IS the threshold-clearing score, so the
        relative cut cannot drop any of them."""
        page = "\n\n".join(["TaskGroup timeout."] * 3
                           + ["Unrelated paragraph about something else."] * 10)
        for relative in (0.0, 0.33, 0.9):
            out = focus_content(page, "timeout", relative_threshold=relative)
            assert self._kept(out) == 3
            assert out.count("TaskGroup timeout.") == 3

    def test_the_absolute_threshold_is_still_a_floor(self):
        """A page whose best block is itself weak keeps the permissive
        behaviour — the relative cut may not turn a weak match into an empty
        page."""
        page = self._graded_page()
        everything = focus_content(page, self.QUERY, threshold=0.0,
                                   relative_threshold=0.0)
        assert self._kept(everything) == 20
