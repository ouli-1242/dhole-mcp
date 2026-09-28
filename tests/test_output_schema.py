"""DHOLE_OUTPUT_SCHEMA 的契约测试：说出口的话必须成立。

规范把 outputSchema 与 structuredContent 绑成同一个决定：声明了 schema，
返回的 structuredContent **MUST** 符合它，客户端 **SHOULD** 校验，并且可以把
不符合当成错误。所以这里测的从来不是"schema 长得对不对"，而是
**"我们真实的响应是否落在自己承诺的范围内"** —— 一份会说谎的契约比没有契约
更糟：没有契约时客户端只会把响应当数据分析，有契约而不符合时它有权报错。

被测的形状特意覆盖了那些容易漏的角落：错误响应、304（按设计没有正文）、
robots 拒绝（一个请求都没发）、批量模式（另一种顶层形状）、以及地图模式
（行被模型自己的序列化器裁到只剩 URL）。
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import typing
from pathlib import Path

import jsonschema
import pytest

import dhole_mcp.server as server_mod
from dhole_mcp.server import (
    BulkResponseModel,
    MasterFetchServer,
    ResponseModel,
    _WIRE_OMIT,
    _enabled_output_schema,
    _output_schema_for,
    _wire_core_schema,
    _wire_json,
)

REPO = Path(__file__).resolve().parents[1]

DECLARED = ("smart_fetch", "parse", "smart_crawl", "smart_search",
            "feed_fetch", "cache_clear")


def _check(tool: str, payload) -> None:
    """Validate one real payload against the schema that tool advertises."""
    schema = _output_schema_for(tool)
    assert schema is not None, f"{tool} 没有声明 schema"
    jsonschema.validate(instance=payload, schema=schema)


def _wire(obj):
    return _wire_json(obj)[1]


class TestTheWireModelAcceptsIt:
    """每个被 advertise 的形状必须过 SDK 自己的 per-version Tool 模型。

    这是 16.0 审计里那个开关打不开 tools/list 的根因守卫：MCP 在 2025-11-25
    及以前的线协议上把 outputSchema 钉成 ``type:"object"`` 的具名模型，一个
    顶层 anyOf 会让 SDK 判定整个 ListToolsResult 非法，runner 随即抛
    -32603 —— 客户端看到的是 0 个工具，比不声明 schema 糟得多。而
    ``Tool(**td)`` 在运行时那个宽 ``dict[str, Any]`` 注解下是过的，所以光读
    ``_enabled_tool_defs()`` 的 dict、或者光用 jsonschema 校验形状，都测不到
    这条线：只有服务端真正走一遍的那份 wire 模型才测得到。
    """

    @staticmethod
    def _tool_models():
        """``{protocol_version: Tool}`` — 与 server runner 校验时用的同一批模型。"""
        from mcp_types.methods import SERVER_RESULTS
        out = {}
        for method, version in SERVER_RESULTS:
            if method != "tools/list":
                continue
            result_model = SERVER_RESULTS[(method, version)]
            out[version] = typing.get_args(result_model.model_fields["tools"].annotation)[0]
        return out

    def test_the_sieve_is_reachable(self):
        """SDK 换了内部布局、这个守卫静默不覆盖任何版本时，必须先在这里红。"""
        assert self._tool_models(), "拿不到 per-version Tool 模型，本文件其余断言形同虚设"

    def test_every_advertised_shape_survives_every_protocol_version(self,
                                                                    monkeypatch):
        monkeypatch.setattr(server_mod, "_OUTPUT_SCHEMA", True)
        models = self._tool_models()
        assert len(models) >= 4, f"只发现 {len(models)} 个版本，覆盖不足"
        for version, Tool in sorted(models.items()):
            for td in MasterFetchServer._enabled_tool_defs():
                try:
                    Tool.model_validate(td, by_name=False)
                except Exception as e:  # noqa: BLE001 - 报错要带版本与工具名
                    pytest.fail(
                        f"tools/list 在 {version} 上会被 SDK 判为非法结果，"
                        f"客户端将看到 0 个工具（{td['name']}）：{e}")

    def test_the_union_is_not_sieved_away(self, monkeypatch):
        """smart_fetch 的两种顶层形状在每个版本上都还在 schema 里说得出。

        加上顶层 ``type`` 只是让线协议收下表；如果某个版本把不认识的
        ``anyOf`` 键丢掉，契约就退化成"一个 object"，批量那一半没人知道。
        """
        monkeypatch.setattr(server_mod, "_OUTPUT_SCHEMA", True)
        for version, Tool in sorted(self._tool_models().items()):
            td = [t for t in MasterFetchServer._enabled_tool_defs()
                  if t["name"] == "smart_fetch"][0]
            dumped = Tool.model_validate(td, by_name=False).model_dump(
                by_alias=True, exclude_none=True, mode="json")
            branches = (dumped["outputSchema"] or {}).get("anyOf")
            assert isinstance(branches, list) and len(branches) == 2, \
                f"{version} 把 smart_fetch 的 union 丢了：{dumped.get('outputSchema')}"

    def test_every_declared_root_is_an_object(self):
        """outputSchema 的根必须是 object（≤2025-11-25 线协议的硬要求）。"""
        for tool in DECLARED:
            schema = _output_schema_for(tool)
            assert schema["type"] == "object", f"{tool} 的根不是 object"


class TestTheContractHoldsForRealResponses:
    def test_a_normal_parse(self):
        _content, payload = asyncio.run(MasterFetchServer()._dispatch(
            "parse", {"file_path": "tests/gbk_sample.csv"}))
        _check("parse", payload)

    def test_a_404_with_no_body(self):
        r = ResponseModel(status=404, content=[], url="https://x.test/gone",
                          error="http_error_404", content_ok=False,
                          summary="404 ERR", next_action="check the URL")
        _check("parse", _wire(r))

    def test_a_304_that_carries_no_body_by_design(self):
        # 最容易被 schema 误伤的形状：状态 304、content 空、却 content_ok=true。
        r = ResponseModel(status=304, content=[], url="https://x.test/p",
                          content_ok=True, not_modified=True,
                          cache_validators={"etag": '"v7"'}, summary="304 OK",
                          next_action="unchanged")
        _check("parse", _wire(r))

    def test_a_robots_refusal_that_made_no_request(self):
        r = ResponseModel(status=0, content=[], url="https://x.test/deny",
                          error="robots_disallowed: ...", content_ok=False,
                          fetcher_used="none", summary="no request made")
        _check("parse", _wire(r))

    def test_an_invalid_request_envelope(self):
        _content, payload = asyncio.run(MasterFetchServer()._dispatch(
            "smart_fetch", {"url": "https://x.test", "max_content_chars": "abc"}))
        _check("smart_fetch", payload)

    def test_the_bulk_shape_is_the_other_half_of_the_union(self):
        b = BulkResponseModel(results=[ResponseModel(
            status=200, content=["x"], url="https://x.test/1", content_ok=True,
            summary="ok", error="", next_action="")], total=1, successful=1)
        _check("smart_fetch", _wire(b))

    def test_a_single_and_a_bulk_payload_are_not_both_accepted_by_one_branch(self):
        """union 必须真的二分：每一支收一种顶层形状，否则声明等于没声明。

        ``anyOf`` 本身可以松到两支都收同一个 payload —— 那时契约只是在冒充
        "两种形状"，而客户端从 required 里读到的是随机一半字段。
        """
        row = ResponseModel(status=200, content=["x"], url="https://x.test/1",
                            content_ok=True, summary="ok", error="",
                            next_action="")
        single = _wire(row)
        bulk = _wire(BulkResponseModel(results=[row], total=1, successful=1))
        verdicts = []
        for branch in _output_schema_for("smart_fetch")["anyOf"]:
            check = jsonschema.Draft202012Validator(branch).is_valid
            verdicts.append((check(single), check(bulk)))
        assert sorted(verdicts) == [(False, True), (True, False)], (
            f"两个分支没有各收一种形状：{verdicts}")

    def test_a_crawl_map_whose_rows_are_pruned_to_the_url(self):
        from dhole_mcp.crawl import CrawlPage, CrawlResponseModel
        rows = [CrawlPage(url=f"https://d.test/{i}", status=200,
                          page_type="discover_only", content_ok=True,
                          summary="mapped") for i in range(3)]
        c = CrawlResponseModel(start_url="https://d.test", pages=rows,
                              discover_only=True, pages_discovered=3)
        payload = _wire(c)
        assert all("page_type" not in row for row in payload["pages"]), \
            "地图模式该上收的列没收干净，required=[url] 的前提就不成立"
        assert all(row["url"] for row in payload["pages"])
        _check("smart_crawl", payload)

    def test_a_search_round(self):
        from dhole_mcp.search import SearchResponseModel, SearchResult
        s = SearchResponseModel(query="q", total_results=1, results=[
            SearchResult(title="T", url="https://x.test/1", relevance_score=0.0)])
        _check("smart_search", _wire(s))

    def test_feed_rows_projected_as_dicts(self):
        from dhole_mcp.feed import FeedItem, FeedResult
        row = FeedResult(source_url="https://x.test/f", source_title="T",
                         items=[FeedItem(url="https://x.test/1", title="a")]
                         ).model_dump()
        payload = _wire_json({"feeds": [row]})[1]
        _check("feed_fetch", payload)

    def test_the_full_wire_shape_validates_too(self, monkeypatch):
        """逃生阀开着（字段恒定）也必须符合：required 是下界，不是全集。"""
        monkeypatch.setattr(server_mod, "_WIRE_FULL", True)
        _content, payload = asyncio.run(MasterFetchServer()._dispatch(
            "parse", {"file_path": "tests/gbk_sample.csv"}))
        assert set(payload) == set(ResponseModel.model_fields)
        _check("parse", payload)

    def test_the_contract_actually_bites(self):
        """防的是"松到什么都通过"：少了必备字段必须报错。

        上面那些 validate 只证明我们不违反；不证明校验会拦住违反。删掉一个
        恒定字段（status）必须让 jsonschema 拒收，否则整份 schema 是空承诺，
        而这套测试也一起失去意义。
        """
        ok = _wire(ResponseModel(status=200, content=["x"], url="https://x.test",
                                 content_ok=True, summary="ok", error="",
                                 next_action=""))
        _check("parse", ok)
        broken = dict(ok)
        del broken["status"]
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance=broken, schema=_output_schema_for("parse"))
        # 类型也要拦得住：content 是字符串数组，不是裸字符串
        wrong = dict(ok, content="x")
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance=wrong, schema=_output_schema_for("parse"))


class TestTheSchemaIsDerivedNotRelisted:
    def test_required_is_what_the_compaction_never_removes(self):
        """required 由 _WIRE_OMIT 反推，不是另写一份清单。

        两处各写一遍就是这次审计在其他地方清掉的那种漂移；这条把两者钉死，
        唯一允许的例外是模型自带序列化器还会继续裁行的情况（地图模式）。
        """
        models = {"parse": ResponseModel, "smart_crawl": None,
                  "smart_search": None, "cache_clear": None}
        from dhole_mcp.crawl import CrawlResponseModel
        from dhole_mcp.feed import FeedResult
        from dhole_mcp.search import SearchResponseModel
        pairs = [(ResponseModel, _output_schema_for("parse")),
                 (CrawlResponseModel, _output_schema_for("smart_crawl")),
                 (SearchResponseModel, _output_schema_for("smart_search")),
                 (FeedResult, _output_schema_for("feed_fetch")
                  ["properties"]["feeds"]["items"]),
                 (server_mod.CacheInfoModel, _output_schema_for("cache_clear"))]
        for model, schema in pairs:
            expect = sorted(set(model.model_fields)
                            - _WIRE_OMIT.get(model.__name__, frozenset()))
            if model.__name__ in server_mod._WIRE_GUARANTEED:
                assert schema["required"] == list(
                    server_mod._WIRE_GUARANTEED[model.__name__]), model.__name__
                assert set(schema["required"]) <= set(expect)
            else:
                assert schema["required"] == expect, model.__name__
            assert set(schema["properties"]) == set(expect), model.__name__
        assert models  # 表名保留，读的人知道覆盖了哪些模型

    def test_no_property_is_declared_with_a_type_we_guessed(self):
        """每个声明出来的属性都要能在模型上找到同名字段。"""
        schema = _output_schema_for("smart_search")
        from dhole_mcp.search import SearchResponseModel
        assert set(schema["properties"]) <= set(SearchResponseModel.model_fields)


class TestTheAdvertiseBoundary:
    def test_default_wire_declares_nothing(self):
        """默认关：这轮瘦身量出来的连接期数字仍然作数。"""
        assert server_mod._OUTPUT_SCHEMA is False
        for td in MasterFetchServer._enabled_tool_defs():
            assert "outputSchema" not in td, td["name"]

    def test_enabling_it_adds_it_to_the_wire_for_declared_tools_only(self,
                                                                     monkeypatch):
        monkeypatch.setattr(server_mod, "_OUTPUT_SCHEMA", True)
        defs = {td["name"]: td for td in MasterFetchServer._enabled_tool_defs()}
        for name in DECLARED:
            assert "outputSchema" in defs[name], name
        for name in ("screenshot", "resolve_url"):
            assert "outputSchema" not in defs[name], \
                f"{name} 没有可承诺的形状，声明了就是空承诺"
        # 而类的字面量没被改坏：测量与测试读的是同一份基线
        for td in MasterFetchServer._TOOL_DEFS:
            assert "outputSchema" not in td

    def test_the_second_channel_is_forced_on_with_it(self):
        """声明 schema 却不发 structuredContent 是自相矛盾的配置。"""
        code = ("import dhole_mcp.server as s;"
                "print(s._OUTPUT_SCHEMA, s._STRUCTURED_CONTENT)")
        env = {**os.environ, "DHOLE_OUTPUT_SCHEMA": "1",
               "PYTHONPATH": str(REPO / "src")}
        out = subprocess.run([sys.executable, "-c", code], env=env,
                             capture_output=True, text=True)
        assert out.returncode == 0, out.stderr[-400:]
        assert out.stdout.strip() == "True True", \
            "DHOLE_OUTPUT_SCHEMA 必须隐含打开结构化通道"

    def test_the_helper_reports_one_size_for_the_whole_set(self):
        total, per = _enabled_output_schema()
        assert total == sum(per.values()) and per
        assert total < 4500, f"整套核心契约 {total} 字符，超出可接受范围"

    def test_the_connect_cost_with_it_on_is_the_number_readme_quotes(self,
                                                                     monkeypatch):
        """开关的代价必须能在线上复现，否则 README 的「实测」是空话。

        16.0 审计时这一条测不到：开关一开整张 tools/list 就被 SDK 判为非法
        结果，README 引的那个开销数字从来没真的上线过。这里对照真正上线的那份
        载荷：同一批 def，逐个加上 outputSchema 前后的字符差。helper 报的是
        ``{"outputSchema": …}`` 整段，装进数组时外层那对花括号换成一个逗号，
        所以每个声明的工具差 1 字符。
        """
        monkeypatch.setattr(server_mod, "_OUTPUT_SCHEMA", True)

        def compact(tools):
            return len(json.dumps({"tools": tools}, separators=(",", ":")))

        defs = MasterFetchServer._enabled_tool_defs()
        off = [{k: v for k, v in td.items() if k != "outputSchema"} for td in defs]
        declared = sum(1 for td in defs if "outputSchema" in td)
        delta = compact(defs) - compact(off)
        total = _enabled_output_schema()[0]
        assert declared == len(DECLARED), f"声明了 {declared} 个，README 说的是 {len(DECLARED)}"
        assert delta == total - declared, (
            f"线上字符差 {delta}，helper 报 {total}：README 引的必须是其中之一，"
            "否则这个数字不可复现")


class TestTheContractCannotRotSilently:
    def test_adding_a_model_field_cannot_make_the_schema_lie(self):
        """新增字段若不进 omit 表，就会自动进 required —— 于是这里会红。

        这是有意的摩擦：那种字段在线上是否恒定存在，必须先有人判断过一次，
        而不是让声明的契约悄悄比实际行为更强（或更弱）。
        """
        from dhole_mcp.crawl import CrawlResponseModel
        schema = _wire_core_schema(CrawlResponseModel)
        for name, spec in CrawlResponseModel.model_fields.items():
            if name in _WIRE_OMIT["CrawlResponseModel"]:
                assert name not in schema["properties"], name
            else:
                assert name in schema["required"], \
                    f"{name} 恒定上线却没进 required：契约少说了一个字段"
