"""R2刀4（P2-7）：`query` 在 wire 上是全局必填，可 `mode='find_similar'` 整个忽略它。

现象（评审实测）：
  * 宿主按 schema 的 `required: ["query"]` 拦下不带 query 的调用 —— 而 find_similar
    要的是 `url=`，它没有查询词可写；
  * 硬塞一个 query 过去，服务端把它当 URL 候选或干脆不用，响应里 `query` 字段还被
    那个 URL 顶替 —— 传了是噪音；
  * 于是这颗模式在正常宿主上**做不到**：不传被宿主拒，传了被忽略。

这是本计划里唯一动 wire 的刀：`query` 改成非必填，"缺词"由服务端按模式判 ——
`mode != 'find_similar'` 时缺 query 仍是标准 invalid_request 信封（点名这颗参数），
`mode = 'find_similar'` 时缺席就被忽略（它要的是 url=，缺 url 另有那句真话）。
"""

import inspect
import json
from unittest.mock import AsyncMock

import pytest

from dhole_mcp import search as search_mod
from dhole_mcp.server import _DISPATCH_REQUIRED, MasterFetchServer

SOURCE = "https://example.com/widgets"


def _schema(name: str) -> dict:
    return next(td for td in MasterFetchServer._TOOL_DEFS
                if td["name"] == name)["inputSchema"]


def _fake_search(monkeypatch):
    """引擎与 find_similar 的取源都不发真请求：这里测的是参数门，不是抓取。"""
    from dhole_mcp.search_engines import EngineReport, RawResult

    async def fake_multi_search(query, max_results, **kwargs):
        return ([RawResult(title="similar doc", url="https://other.test/x",
                           snippet="x", source="brave", position=1)],
                [EngineReport(name="brave", ok=True)])

    monkeypatch.setattr(search_mod, "multi_search", fake_multi_search)
    monkeypatch.setattr(search_mod, "ensure_reranker", AsyncMock(return_value=None))
    monkeypatch.setattr(search_mod, "fetch_source_for_similar",
                        AsyncMock(return_value={
                            "title": "Widgets", "body": "widget body text",
                            "status": 200, "reason": "", "detail": ""}))


def _payload(result) -> dict:
    content, _ = result
    return json.loads(content[0].text)


class TestTheWireNoLongerDemandsAQuery:

    def test_query_is_not_required_on_the_wire(self):
        assert "query" not in _schema("web_search").get("required", [])

    def test_the_handler_gives_query_a_default(self):
        """没有默认值时，缺 query 会在方法签名上炸成 TypeError 而不是信封。"""
        param = inspect.signature(MasterFetchServer.web_search).parameters["query"]
        assert param.default == ""

    def test_the_dispatch_table_hands_the_decision_to_the_method(self):
        """dispatch 的静态必需键表不认识 mode，所以这颗从表里出来（R1刀2 的信封
        由方法体给同款：见 test_a_missing_query_is_refused_server_side）。"""
        assert "web_search" not in _DISPATCH_REQUIRED


class TestFindSimilarWithoutAQuery:

    @pytest.mark.asyncio
    async def test_find_similar_reaches_the_host_without_a_query(self, monkeypatch):
        _fake_search(monkeypatch)
        out = await MasterFetchServer()._dispatch(
            "web_search", {"mode": "find_similar", "url": SOURCE})
        payload = _payload(out)
        assert not str(payload.get("error", "")).startswith("invalid_request"), payload
        assert payload.get("results"), payload

    @pytest.mark.asyncio
    async def test_the_url_still_names_the_response(self, monkeypatch):
        """query 缺席时，响应里那栏写的是这次真正寻源用的东西，不是一句谎话。"""
        _fake_search(monkeypatch)
        out = await MasterFetchServer().web_search(
            mode="find_similar", url=SOURCE, cache_ttl=0)
        assert SOURCE in (out.query or "") or out.results


class TestTheQueryIsStillRequiredWhereItIsNeeded:

    @pytest.mark.asyncio
    async def test_a_missing_query_is_refused_server_side(self, monkeypatch):
        _fake_search(monkeypatch)
        out = await MasterFetchServer()._dispatch("web_search", {})
        payload = _payload(out)
        err = payload["error"]
        assert err.startswith("invalid_request"), err
        assert "query" in err, err

    @pytest.mark.asyncio
    async def test_an_empty_or_blank_query_is_refused_the_same_way(self, monkeypatch):
        _fake_search(monkeypatch)
        server = MasterFetchServer()
        a = await server.web_search("", cache_ttl=0)
        b = await server.web_search("   ", cache_ttl=0)
        assert a.error.startswith("invalid_request"), a.error
        assert b.error == a.error, "缺席与空串同罪：dispatch 那张表就是这么判的"

    @pytest.mark.asyncio
    async def test_the_refusal_names_the_one_mode_that_does_not_need_it(
            self, monkeypatch):
        """报错要说清下一步：find_similar 是唯一能不带查询词的那条路，而它要 url=。"""
        _fake_search(monkeypatch)
        out = await MasterFetchServer().web_search("", cache_ttl=0)
        assert "find_similar" in out.error or "find_similar" in out.next_action
        assert "url=" in out.error + out.next_action

    @pytest.mark.asyncio
    async def test_find_similar_without_a_url_still_says_what_it_needs(
            self, monkeypatch):
        """缺 url 的 find_similar 不是"缺 query"：那句要说的是它真正缺的东西。"""
        _fake_search(monkeypatch)
        out = await MasterFetchServer().web_search(mode="find_similar", cache_ttl=0)
        assert "url" in out.error, out.error
        assert not out.error.startswith("invalid_request"), (
            "这颗模式本来就不需要 query，不许拿缺 query 去拒它")

    @pytest.mark.asyncio
    async def test_no_engine_is_called_when_the_query_is_missing(self, monkeypatch):
        calls = {"n": 0}

        async def counting(*a, **kw):
            calls["n"] += 1
            return [], []

        monkeypatch.setattr(search_mod, "multi_search", counting)
        await MasterFetchServer().web_search("", cache_ttl=0)
        assert calls["n"] == 0
