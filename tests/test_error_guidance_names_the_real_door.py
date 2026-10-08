"""next_action 要说真话：错误指引家族的三处（评审R1刀3）。

三类同病的实测（2026-10-07 评审）：
  1. tavily 缺 key：error 正确说 ``DHOLE_TAVILY_API_KEY`` 未设，next_action
     却说「Rephrase (more specific…)」——换说法永远修不了一把不存在的钥匙；
  2. ``web_request`` 的校验错误引用 **web_read** 的样板（"web_read takes an
     absolute http(s) URL…"）——共享校验路上的工具名没跟着换；
  3. 云元数据端点（169.254.169.254，任何拼写永不放行）：next_action 说
     「Correct the argument and call again」——改参数永远不可能成功，这是
     设计边界，不是可修的参数。

附带一条假话同刀修：``_rank`` 把「空结果」读成「重排器缺席」——rerank()
对空输入一律答 None（没有东西可排），与重排器在不在无关；在重排器**已经
暖着**的进程里，一条空结果的错误响应会带着「the reranker has not run in
this process yet」的假话出去。
"""

from __future__ import annotations

import pytest

from dhole_mcp.search_metasearch import MetaSearchException
from dhole_mcp.server import MasterFetchServer


def _stub_search(monkeypatch, exc: Exception):
    """让 web_search 走真管道，只有引擎层抛指定的错（与既有回归测试同一手法）。"""
    from dhole_mcp import search as S

    async def fake_multi_search(query, max_results, **kwargs):
        raise exc

    async def fake_get_cached(*a, **k):
        return None

    async def fake_set_cached(*a, **k):
        return None

    async def fake_ensure_reranker(*a, **k):
        return None

    monkeypatch.setattr(S, "multi_search", fake_multi_search)
    monkeypatch.setattr(S, "get_cached", fake_get_cached)
    monkeypatch.setattr(S, "set_cached", fake_set_cached)
    monkeypatch.setattr(S, "ensure_reranker", fake_ensure_reranker)
    return S


class TestAMissingKeyIsNotARephraseProblem:

    @pytest.mark.asyncio
    async def test_the_env_var_is_named_and_rephrasing_is_off_the_table(
            self, monkeypatch):
        # 文案逐字来自 search_metasearch 的单 keyed 引擎抛错路径。
        S = _stub_search(monkeypatch, MetaSearchException(
            "Engine 'tavily' requires DHOLE_TAVILY_API_KEY, which is not set."))
        out = await S.web_search(None, "test query", 6)
        assert "DHOLE_TAVILY_API_KEY" in (out.next_action or "")
        assert "Rephrase" not in (out.next_action or ""), \
            "换说法修不了一把不存在的钥匙"
        assert "DHOLE_TAVILY_API_KEY" in (out.error or "")

    @pytest.mark.asyncio
    async def test_the_json_engine_missing_key_spelling_is_covered_too(
            self, monkeypatch):
        # search_json 缺 key 的另一份拼写（无 "Engine '…'" 前缀）同样命中。
        from dhole_mcp.search import _search_next_action
        row = _search_next_action(
            [], [], "tavily requires DHOLE_TAVILY_API_KEY, which is not set",
            ["tavily"])
        assert "DHOLE_TAVILY_API_KEY" in row
        assert "Rephrase" not in row


class TestTheRerankerClaimIsFactual:

    def test_nothing_to_rerank_makes_no_absence_claim(self, monkeypatch):
        """空结果 ≠ 重排器缺席：重排器暖着的时候尤其不许说反话。"""
        from dhole_mcp import search as S

        class _Warm:
            def score(self, q, docs):
                return [0.5] * len(docs)

        monkeypatch.setattr("dhole_mcp.reranker.get_reranker", lambda: _Warm())
        monkeypatch.setattr(S, "unavailable_reason", lambda: "")
        ranked, scores, mode, note = S._rank("q", [], "neural")
        assert ranked == [] and mode in ("neural", "merge")
        assert "has not run" not in note, "重排器在进程里明明跑过/暖着"
        assert "NOT active" not in note

    def test_a_real_absence_with_candidates_still_explains_itself(self, monkeypatch):
        """有东西可排、重排器真的不在 → 缺席说明照旧（零回归）。"""
        from dhole_mcp import search as S
        from dhole_mcp.search_engines import RawResult

        monkeypatch.setattr("dhole_mcp.reranker.get_reranker", lambda: None)
        monkeypatch.setattr(S, "unavailable_reason", lambda: "")
        rows = [RawResult(title="t", url="https://e.example/", snippet="s",
                          source="bing", position=1, consensus=1,
                          sources=("bing",))]
        ranked, scores, mode, note = S._rank("q", rows, "neural")
        assert mode == "merge" and ranked
        assert "neural rerank unavailable" in note


class TestTheTemplateNamesTheCaller:

    @pytest.mark.asyncio
    async def test_web_request_refusals_say_web_request(self):
        out = await MasterFetchServer().web_request(url="", method="POST")
        assert "url is required" in (out.error or "")
        assert "web_request takes an absolute http(s) URL" in (out.next_action or "")
        assert "web_read takes" not in (out.next_action or "")

    @pytest.mark.asyncio
    async def test_web_act_refusals_say_web_act(self):
        out = await MasterFetchServer().web_act(url="", actions=[{"wait": 1}])
        assert "url is required" in (out.error or "")
        assert "web_act takes an absolute http(s) URL" in (out.next_action or "")
        assert "web_read takes" not in (out.next_action or "")

    @pytest.mark.asyncio
    async def test_web_read_keeps_its_own_name(self):
        """样板点名调用方这条锁的判据是**用默认模板的那类拒绝**。

        2026-10-08 刀3 之后，内网地址那一类不再走默认模板（它有自己那句出路），所以
        这颗改用一颗真的没写对的 URL 当载体 —— 契约不变：共享校验路不许把
        web_request 的调用说成 web_read。
        """
        out = await MasterFetchServer().web_read(url="not-a-url-at-all")
        assert "web_read takes an absolute http(s) URL" in (out.next_action or "")
        assert "web_request takes" not in (out.next_action or "")

    @pytest.mark.asyncio
    async def test_a_private_address_no_longer_borrows_the_url_template(self):
        """刀3 的另一半：地址被拒时，那句 URL 拼法教程本身就是错的话。"""
        out = await MasterFetchServer().web_read(url="http://10.0.0.5/")
        assert "internal/private IP" in (out.error or "")
        assert "takes an absolute http(s) URL" not in (out.next_action or "")
        assert "allow_private" in (out.next_action or ""), "这一类有出口，要指出来"


class TestAPermanentBoundaryDoesNotOfferAnArgumentFix:

    @pytest.mark.asyncio
    async def test_cloud_metadata_refusal(self):
        out = await MasterFetchServer().web_read(
            url="http://169.254.169.254/latest/meta-data/")
        assert "cloud metadata endpoint" in (out.error or "")
        assert "No argument fixes this" in (out.next_action or "")
        assert "Correct the argument" not in (out.next_action or "")

    @pytest.mark.asyncio
    async def test_the_same_boundary_on_the_web_request_probe_path(self):
        # HEAD 不过写门，URL 校验的拒绝原文可达；工具名已无关紧要——
        # 边界句替换的是"怎么改参数"那句话。
        out = await MasterFetchServer().web_request(
            url="http://169.254.169.254/", method="HEAD")
        assert "cloud metadata endpoint" in (out.error or "")
        assert "No argument fixes this" in (out.next_action or "")

    @pytest.mark.asyncio
    async def test_dns_rebinding_service_refusal(self):
        out = await MasterFetchServer().web_read(
            url="http://127.0.0.1.nip.io/")
        assert "DNS rebinding service" in (out.error or "")
        assert "No argument fixes this" in (out.next_action or "")
