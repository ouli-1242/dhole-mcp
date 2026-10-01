"""M 轴（M-1/M-2/M-3）：神经 focus、答案块定位、意图命中提示。

设计红线（计划第六章）：
* flag（DHOLE_NEURAL_FOCUS）**默认关**——focus 是所有抓取路径的公共节点，
  每页 × 每 block 的 CPU 推理延迟不能默认发生；
* 模型缺失在工具入口**显式报错**，不静默下载（~280MB 的惊吓），也不静默降回
  BM25（flag 开着的语义就是"我要神经分"）；BM25 回退只属于"打分器中途失败"；
* reranker 只做文本相关性打分——这里所有判定都是纯文本 + 假打分器，不引入
  任何模型依赖（$0 无密钥边界）。
"""

from __future__ import annotations

import pytest

from dhole_mcp import server as server_mod
from dhole_mcp.focus import (
    _NEURAL_MAX_CHARS,
    _neural_block_scores,
    _split_for_encoder,
    focus_content,
)
from dhole_mcp.server import (
    MasterFetchServer,
    ResponseModel,
    _apply_chunking,
    _focus_intent_hint,
)


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
        monkeypatch.setenv("DHOLE_NEURAL_FOCUS", "1")
        import dhole_mcp.reranker as rr

        async def no_model(*a, **kw):
            return None
        monkeypatch.setattr(rr, "get_reranker", lambda: None)
        monkeypatch.setattr(rr, "ensure_reranker", no_model)

        async def no_fetch(*a, **kw):
            raise AssertionError("model missing must stop the call before a fetch")
        monkeypatch.setattr(MasterFetchServer, "_force_fetch", no_fetch)
        monkeypatch.setattr(MasterFetchServer, "_auto_escalate", no_fetch)

        out = await MasterFetchServer().smart_fetch(
            url="https://example.com/", focus="topic")
        assert "neural_focus_model_missing" in out.error
        assert out.status == 0

    @pytest.mark.asyncio
    async def test_smart_fetch_without_focus_needs_no_model(self, monkeypatch):
        monkeypatch.setenv("DHOLE_NEURAL_FOCUS", "1")
        import dhole_mcp.reranker as rr

        async def no_model(*a, **kw):
            raise AssertionError("ensure_reranker ran without a focus query")
        monkeypatch.setattr(rr, "get_reranker", lambda: None)
        monkeypatch.setattr(rr, "ensure_reranker", no_model)

        result = ResponseModel(url="https://example.com/", status=200,
                               content=["body"], fetcher_used="http")
        monkeypatch.setattr(MasterFetchServer, "_auto_escalate", no_fetch_ok(result))
        out = await MasterFetchServer().smart_fetch(url="https://example.com/")
        assert out.content == ["body"]

    @pytest.mark.asyncio
    async def test_flag_off_never_touches_the_reranker(self, monkeypatch):
        monkeypatch.delenv("DHOLE_NEURAL_FOCUS", raising=False)
        import dhole_mcp.reranker as rr

        def boom():
            raise AssertionError("flag off but the reranker was consulted")
        monkeypatch.setattr(rr, "get_reranker", boom)

        result = ResponseModel(url="https://example.com/", status=200,
                               content=["body"], fetcher_used="http")
        monkeypatch.setattr(MasterFetchServer, "_auto_escalate", no_fetch_ok(result))
        out = await MasterFetchServer().smart_fetch(url="https://example.com/",
                                                    focus="topic")
        assert out.content == ["body"]  # 原样通过；stub 不经 _apply_chunking

    @pytest.mark.asyncio
    async def test_smart_crawl_refuses_without_the_model(self, monkeypatch):
        monkeypatch.setenv("DHOLE_NEURAL_FOCUS", "1")
        import dhole_mcp.reranker as rr

        async def no_model(*a, **kw):
            return None
        monkeypatch.setattr(rr, "get_reranker", lambda: None)
        monkeypatch.setattr(rr, "ensure_reranker", no_model)

        async def no_crawl(*a, **kw):
            raise AssertionError("the crawl ran despite a missing model")
        monkeypatch.setattr(server_mod, "_neural_focus_enabled", lambda: True)
        out = await MasterFetchServer().smart_crawl(
            "https://example.com/", focus="topic", max_pages=1)
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
        """_apply_chunking 全链路：flag 开 + 假打分器全低分 → 提示出现在响应里。"""
        monkeypatch.setenv("DHOLE_NEURAL_FOCUS", "1")
        import dhole_mcp.reranker as rr
        monkeypatch.setattr(rr, "get_reranker", lambda: FakeReranker(zero=False))

        multi_block = "\n\n".join([
            "# Intro", "Setup instructions for the tutorial.",
            "## Details", "More unrelated prose about installation.",
        ])
        result = ResponseModel(url="https://example.com/", status=200,
                               content=[multi_block], fetcher_used="http")
        server_mod._FOCUS.set("how to fly to the moon cheaply")
        try:
            out = _apply_chunking(result)
        finally:
            server_mod._FOCUS.set(None)
        assert "focus match is weak" in out.next_action


# ─── M-2：爬取页面过滤 ──────────────────────────────────────────────────────


class TestCrawlFilter:

    def test_relevant_page_is_kept(self, monkeypatch):
        monkeypatch.setenv("DHOLE_NEURAL_FOCUS", "1")
        import dhole_mcp.reranker as rr
        monkeypatch.setattr(rr, "get_reranker", lambda: FakeReranker())
        from dhole_mcp.crawl import _page_relevant
        # 未命中 table 的文档得 0.001 -> 跑题带 -> 丢
        assert _page_relevant("cooking recipes for pancakes", "asyncio") is False

    def test_off_topic_page_is_dropped(self, monkeypatch):
        monkeypatch.setenv("DHOLE_NEURAL_FOCUS", "1")
        import dhole_mcp.reranker as rr
        monkeypatch.setattr(rr, "get_reranker",
                            lambda: FakeReranker(table={"asyncio": 0.97}))
        from dhole_mcp.crawl import _page_relevant
        assert _page_relevant("asyncio event loop tutorial", "asyncio") is True

    def test_no_reranker_fails_open(self, monkeypatch):
        monkeypatch.setenv("DHOLE_NEURAL_FOCUS", "1")
        import dhole_mcp.reranker as rr
        monkeypatch.setattr(rr, "get_reranker", lambda: None)
        from dhole_mcp.crawl import _page_relevant
        assert _page_relevant("anything", "goal") is None

    def test_flag_off_skips_the_filter(self, monkeypatch):
        monkeypatch.delenv("DHOLE_NEURAL_FOCUS", raising=False)
        import dhole_mcp.reranker as rr

        def boom():
            raise AssertionError("flag off but the filter consulted the model")
        monkeypatch.setattr(rr, "get_reranker", boom)
        from dhole_mcp.crawl import _page_relevant
        assert _page_relevant("anything", "goal") is None


# ─── W15：按次选打分器（flag = 许可，focus_engine = 意愿）────────────────────


def _never_consulted():
    def boom(*a, **kw):
        raise AssertionError("this path must not consult the reranker at all")
    return boom


async def _never_ensured(*a, **kw):
    raise AssertionError("this path must not load (or download) the reranker")


class TestThePerCallScorerChoice:
    """M-1 的开关语义从"全局能力"变成"许可 + 这一次要不要"。

    钉住的三条：不传参数时行为逐字节不变；``bm25`` 在 flag 开着时也不用模型
    （否则"这次想省钱"只能靠关全局能力）；``neural`` 在没有许可时是**拒绝**而不
    是降级（一次静默降级会把"神经/BM25 的差别"变成不可解释的玄学）。
    """

    @pytest.mark.parametrize("engine,flag,expected", [
        ("", False, False), ("", True, True),
        ("bm25", False, False), ("bm25", True, False),
        ("neural", False, False), ("neural", True, True),
    ])
    def test_effective_choice_is_willingness_and_permission(self, engine, flag, expected):
        from dhole_mcp import focus as _f
        token = _f.FOCUS_ENGINE.set(engine)
        try:
            assert _f.neural_for_call(flag) is expected
        finally:
            _f.FOCUS_ENGINE.reset(token)

    def test_willingness_without_permission_still_reads_as_requested(self):
        """"要神经"必须能被闸看见——否则拒绝分支永远不触发，意愿参数成了装饰。"""
        from dhole_mcp import focus as _f
        token = _f.FOCUS_ENGINE.set("neural")
        try:
            assert _f.engine_wants_neural(False) is True
            assert _f.neural_for_call(False) is False
        finally:
            _f.FOCUS_ENGINE.reset(token)

    @pytest.mark.asyncio
    async def test_bm25_is_honored_even_with_the_flag_on(self, monkeypatch):
        monkeypatch.setenv("DHOLE_NEURAL_FOCUS", "1")
        import dhole_mcp.reranker as rr
        monkeypatch.setattr(rr, "get_reranker", _never_consulted())
        monkeypatch.setattr(rr, "ensure_reranker", _never_ensured)

        result = ResponseModel(url="https://example.com/", status=200,
                               content=["body"], fetcher_used="http")
        monkeypatch.setattr(MasterFetchServer, "_auto_escalate", no_fetch_ok(result))
        out = await MasterFetchServer().smart_fetch(url="https://example.com/",
                                                    focus="topic",
                                                    focus_engine="bm25")
        assert out.content == ["body"]

    @pytest.mark.asyncio
    async def test_neural_without_permission_is_refused_not_downgraded(self, monkeypatch):
        monkeypatch.delenv("DHOLE_NEURAL_FOCUS", raising=False)

        async def no_fetch(*a, **kw):
            raise AssertionError("a refused call must not reach the network")
        monkeypatch.setattr(MasterFetchServer, "_auto_escalate", no_fetch)

        out = await MasterFetchServer().smart_fetch(url="https://example.com/",
                                                    focus="topic",
                                                    focus_engine="neural")
        # 拒绝走的是所有参数级拒绝的同一条通道（"invalid_request: …" 前缀），
        # 而不是一个只有这个开关会产生的新形状。
        assert "focus_engine_not_permitted" in out.error
        assert out.status == 0, "不是伪装成成功的 BM25 结果"
        assert "DHOLE_NEURAL_FOCUS" in out.error
        assert "dhole init --neural" in out.next_action

    @pytest.mark.asyncio
    async def test_the_old_model_gate_is_not_loosened(self, monkeypatch):
        """许可 + 意愿都在场，模型缺失仍是 neural_focus_model_missing。"""
        monkeypatch.setenv("DHOLE_NEURAL_FOCUS", "1")
        import dhole_mcp.reranker as rr

        async def no_model(*a, **kw):
            return None
        monkeypatch.setattr(rr, "get_reranker", lambda: None)
        monkeypatch.setattr(rr, "ensure_reranker", no_model)

        async def no_fetch(*a, **kw):
            raise AssertionError("missing model must stop the call before a fetch")
        monkeypatch.setattr(MasterFetchServer, "_auto_escalate", no_fetch)

        out = await MasterFetchServer().smart_fetch(url="https://example.com/",
                                                    focus="topic",
                                                    focus_engine="neural")
        assert "neural_focus_model_missing" in out.error

    def test_the_chunker_obeys_the_per_call_choice(self, monkeypatch):
        """意愿要活到打分那一刻，不只是活到闸里——两条路的表头就是证据。"""
        monkeypatch.setenv("DHOLE_NEURAL_FOCUS", "1")
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
        assert "neural relevance" in run("")  # 不传 = 跟随 flag（W15 之前的一切）


class TestTheCrawlFollowsTheSameRule:
    """爬取比抓取更需要这句实话：12.5s/页 × max_pages。"""

    @pytest.mark.asyncio
    async def test_crawl_refuses_neural_without_permission(self, monkeypatch):
        monkeypatch.delenv("DHOLE_NEURAL_FOCUS", raising=False)

        # 拒绝必须发生在爬取本体之前，所以本体被换成一个记过的 stub：真爬取不仅
        # 慢，还会把这个单元测试变成对 example.com 的一次真实拜访。
        ran: list[bool] = []

        async def fake_crawl(*a, **kw):
            ran.append(True)
            from dhole_mcp.crawl import CrawlResponseModel as _CRM
            return _CRM(start_url="https://example.com/", pages=[])
        monkeypatch.setattr("dhole_mcp.crawl.smart_crawl", fake_crawl)

        out = await MasterFetchServer().smart_crawl("https://example.com/",
                                                    focus="topic",
                                                    focus_engine="neural")
        assert "focus_engine_not_permitted" in out.error
        assert out.pages == []
        assert not ran, "the crawl body ran despite the refusal"
        assert "dhole init --neural" in out.next_action

    @pytest.mark.asyncio
    async def test_crawl_with_bm25_needs_no_model(self, monkeypatch):
        """flag 开着的老行为是"入口必查模型"；focus_engine='bm25' 把它绕开了。"""
        monkeypatch.setenv("DHOLE_NEURAL_FOCUS", "1")
        import dhole_mcp.reranker as rr
        monkeypatch.setattr(rr, "get_reranker", _never_consulted())
        monkeypatch.setattr(rr, "ensure_reranker", _never_ensured)

        from dhole_mcp.crawl import CrawlResponseModel as _CRM

        async def fake_crawl(*a, **kw):
            return _CRM(start_url="https://example.com/", pages=[])
        monkeypatch.setattr("dhole_mcp.crawl.smart_crawl", fake_crawl)

        out = await MasterFetchServer().smart_crawl("https://example.com/",
                                                   focus="topic",
                                                   focus_engine="bm25")
        assert not out.error, "走到了爬取本体（stub 无页），闸没有拦住"

    def test_crawl_reads_the_same_per_call_var(self, monkeypatch):
        from dhole_mcp import crawl as _crawl
        monkeypatch.setenv("DHOLE_NEURAL_FOCUS", "1")
        token = server_mod._FOCUS_ENGINE.set("bm25")
        try:
            assert _crawl._neural_for_this_call() is False
        finally:
            server_mod._FOCUS_ENGINE.reset(token)
        token2 = server_mod._FOCUS_ENGINE.set("")
        try:
            assert _crawl._neural_for_this_call() is True
        finally:
            server_mod._FOCUS_ENGINE.reset(token2)
