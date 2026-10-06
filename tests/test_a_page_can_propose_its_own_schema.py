"""动态 schema：`schema="auto"` 既抽取结构，又**提议**一份可复用的选择器 schema。

两条缺口，两个方向：

1. `structured.extract_structured` 的 docstring 一直承诺 `schema="auto"` 触发自动
   抽取，但 `_normalize_schema` 先把字符串丢给 `json.loads("auto")` —— 裸串在解析
   那一步就抛 `not valid JSON`，只有 `{"type": "auto"}` 能过。文档承诺的写法被代码
   拒绝，且这条路径此前**零测试覆盖**（探针实测：`'auto' -> RAISES`）。
2. auto 模式此前只回表格 / 列表 / JSON-LD，不告诉调用方"这份结构能用什么选择器
   抓"。于是同一站点的后续页只能靠人再猜一遍选择器。现在它从页面的重复结构里
   推一份 `_schema`（容器 + 字段选择器），抄回去即可复用。

这些都是确定性启发式，不猜语义：找重复最多的 `标签.class` 组当容器，取其第一个
实例里带文本的后代当字段。所以这里的断言钉的是"形状正确、可复用、不越界"，不是
"提得漂亮"。
"""

import json
from unittest.mock import AsyncMock

import pytest

from dhole_mcp.server import MasterFetchServer, ResponseModel, _normalize_schema
from dhole_mcp.structured import extract_structured, _propose_schema, _parse_html

# 四个同构的容器：propose 应当认出 li.book 是容器，title/price/link 是字段。
LISTING = """<html><body>
<ul class="book-list">
  <li class="book"><h3 class="title">Alpha</h3><span class="price">$10</span><a class="link" href="/a">buy</a></li>
  <li class="book"><h3 class="title">Beta</h3><span class="price">$20</span><a class="link" href="/b">buy</a></li>
  <li class="book"><h3 class="title">Gamma</h3><span class="price">$30</span><a class="link" href="/c">buy</a></li>
  <li class="book"><h3 class="title">Delta</h3><span class="price">$40</span><a class="link" href="/d">buy</a></li>
</ul></body></html>"""

# 一篇文章：没有重复容器，propose 应当什么都提不出来。
ARTICLE = """<html><body><article>
<h1 class="headline">A single story</h1>
<p class="lede">One paragraph, no repetition anywhere on this page.</p>
</article></body></html>"""


class TestTheAutoStringIsAccepted:
    """回归：`schema="auto"` 此前被 JSON 解析拒绝，文档承诺与实际行为不符。"""

    def test_the_bare_auto_string_normalizes(self):
        assert _normalize_schema("auto") == {"type": "auto"}

    def test_case_and_surrounding_space_do_not_matter(self):
        assert _normalize_schema("AUTO") == {"type": "auto"}
        assert _normalize_schema("  auto  ") == {"type": "auto"}

    def test_the_json_spellings_still_work(self):
        assert _normalize_schema('{"type": "auto"}') == {"type": "auto"}
        assert _normalize_schema({"mode": "auto"}) == {"mode": "auto"}

    def test_a_non_auto_string_is_still_a_json_error(self):
        """只有 `auto` 是特例；别的裸串仍然报错，不静默吞掉。"""
        with pytest.raises(ValueError, match="not valid JSON"):
            _normalize_schema("book")


class TestTheProposedSchema:
    """`_propose_schema` 的纯函数契约。"""

    def test_it_finds_the_repeating_container_and_its_fields(self):
        prop = _propose_schema(_parse_html(LISTING))
        assert prop["type"] == "object"
        (container, spec), = prop["properties"].items()
        assert container == "book"
        assert spec["selector"] == "li.book"
        assert spec["type"] == "array"
        assert set(spec["properties"]) == {"title", "price", "link"}

    def test_the_proposal_is_itself_a_valid_schema_shape(self):
        """提议必须能当 schema 用：容器在 properties 里，顶层没有 selector。

        顶层放 selector 会被 `_normalize_schema` 具名拒绝（批次12），所以提议若把
        容器提到顶层就是自相矛盾 —— 这里钉住它不会。
        """
        prop = _propose_schema(_parse_html(LISTING))
        assert "selector" not in prop
        assert prop["type"] != "array"
        # 直接喂回 normalize：应当原样通过，而不是抛 "container" / "nothing to extract"。
        assert _normalize_schema(prop) is prop

    def test_a_page_with_no_repetition_proposes_nothing(self):
        assert _propose_schema(_parse_html(ARTICLE)) == {}

    def test_two_repeats_are_below_the_floor(self):
        """`_MIN_GROUP = 3`：两页里各一次的重合不算结构。"""
        html = """<html><body>
        <div class="card">One</div><div class="card">Two</div>
        </body></html>"""
        assert _propose_schema(_parse_html(html)) == {}

    def test_classless_generic_descendants_are_not_fields(self):
        """无 class 的 div/span/p 当字段名没有意义，不当字段。"""
        html = """<html><body>
        <li class="row">A</li><li class="row">B</li><li class="row">C</li>
        </body></html>"""
        prop = _propose_schema(_parse_html(html))
        assert prop == {}   # 容器里除了裸文本没有具名后代 → 提不出字段

    def test_the_field_count_is_capped(self):
        cells = "".join(f'<span class="f{i}">v{i}</span>' for i in range(20))
        html = "<html><body>" + "".join(
            f'<div class="item">{cells}</div>' for _ in range(4)) + "</body></html>"
        prop = _propose_schema(_parse_html(html))
        (spec,) = prop["properties"].values()
        assert len(spec["properties"]) == 8   # _MAX_PROPOSED_FIELDS


class TestTheProposalRoundTrips:
    """提议的价值在于**复用**：抄回去要真能抽出记录，且保留分组。"""

    def test_the_proposed_schema_extracts_the_records(self):
        prop = _propose_schema(_parse_html(LISTING))
        out = extract_structured(LISTING, prop)
        records = out["book"]
        assert [r["title"] for r in records] == ["Alpha", "Beta", "Gamma", "Delta"]
        assert records[0]["price"] == "$10"
        assert records[3]["link"] == "buy"

    def test_grouping_is_kept_not_flattened(self):
        """每个容器的字段跟着自己的记录走，不是全页并成一条。"""
        prop = _propose_schema(_parse_html(LISTING))
        out = extract_structured(LISTING, prop)
        assert len(out["book"]) == 4
        assert {r["title"] for r in out["book"]} == {"Alpha", "Beta", "Gamma", "Delta"}


class TestAutoModeCarriesTheProposal:
    """`extract_structured(html, {"type": "auto"})` 的输出里带 `_schema`。"""

    def test_auto_includes_the_proposed_schema(self):
        out = extract_structured(LISTING, {"type": "auto"})
        assert out["_mode"] == "auto"
        assert "_schema" in out
        assert "book" in out["_schema"]["properties"]

    def test_auto_accepts_the_bare_string_too(self):
        """`_normalize_schema` 把 "auto" 归一成 {"type":"auto"}，两者等价。"""
        out = extract_structured(LISTING, _normalize_schema("auto"))
        assert "_schema" in out

    def test_a_page_without_structure_has_no_schema_key(self):
        out = extract_structured(ARTICLE, {"type": "auto"})
        assert "_schema" not in out


def _html_result() -> ResponseModel:
    return ResponseModel(
        status=200, content=[LISTING], url="https://example.com",
        fetcher_used="http", content_type="text/html",
        extracted_type="html",
    )


class TestTheServerPath:
    """端到端：`web_read(schema="auto")` 返回 structured，且带提议。"""

    def _server(self):
        server = MasterFetchServer()
        server._auto_escalate = AsyncMock(return_value=_html_result())
        return server

    @pytest.mark.asyncio
    async def test_auto_string_drives_a_structured_read(self):
        server = self._server()
        out = await server.web_read("https://example.com",
                                    schema="auto", cache_ttl=0)
        assert out.extracted_type == "structured"
        payload = json.loads(out.content[0])
        assert payload["_mode"] == "auto"
        assert "book" in payload["_schema"]["properties"]

    @pytest.mark.asyncio
    async def test_auto_does_not_count_as_a_failed_extraction(self):
        """auto 命中结构时必须 content_ok，不能被当成 schema_no_match。"""
        server = self._server()
        out = await server.web_read("https://example.com",
                                    schema="auto", cache_ttl=0)
        assert not out.error


class TestTheHandbookStillDocumentsIt:
    """发现链守卫：wire 不写 `auto`（连接期不为此加字），只逐字指向手册那一节。

    能力要可达，链条就得完整：`_TOOL_DEFS` 里 `schema` 的描述写着
    `sys_guide(topic='behavior', section='Structured extraction')`，所以那一节
    必须真的讲了 `auto` 与 `_schema` —— 否则重写手册的人会无声地把这颗能力弄丢，
    而 wire 上的指针仍在，指向一段不再提它的文字。这正是"搬家不是丢"那条守卫的
    同一类关切，只是这里搬的方向相反：wire 从来没有过它，它一开始就在手册里。
    """

    def _volume(self, name: str) -> str:
        from dhole_mcp import guide
        return (guide._ROOT / "references" / name).read_text(encoding="utf-8")

    def test_the_behavior_volume_documents_auto(self):
        text = self._volume("tools.md")
        assert 'schema="auto"' in text
        assert "_schema" in text
        assert "propose" in text.lower() or "proposes" in text.lower()

    def test_the_wire_points_at_the_section_that_documents_it(self):
        """指针与落点必须对得上：wire 说去哪一节，那一节就得有。"""
        from dhole_mcp.server import MasterFetchServer
        defs = {d["name"]: d for d in MasterFetchServer._TOOL_DEFS}
        schema_desc = defs["web_read"]["inputSchema"]["properties"]["schema"]["description"]
        assert "section='Structured extraction'" in schema_desc
        assert "Structured extraction (schema)" in self._volume("tools.md")

    def test_the_call_volume_has_a_copy_ready_auto_example(self):
        assert 'schema": "auto"' in self._volume("examples.md")

