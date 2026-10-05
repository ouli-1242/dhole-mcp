"""批次 6c：枚举参数的合法值由 **dhole** 报，不由宿主的 JSON-Schema 门报。

2026-10-05 真实使用实测里，`web_search(freshness="decade")` 被宿主挡在门外，退回
原话是 "must be equal to one of the allowed values" —— 一句不列合法值的拒绝。而
dhole 自己那条 `_validate_freshness` 早就写得更好：`Invalid freshness: 'decade'
(day|week|month|year)`。线格式挂着 enum，就等于让那句更有用的错误永无机会运行
（房内主张见 `params.py`：「签名上的 `Literal` 只是文档，dispatcher 用 `args.get()`
读参数」；退役别名 'dynamic' 的点名是同一主张的既有先例）。

判据（摘除的顺序就是它的顺序 —— 校验器必须先于 enum 存在，否则非法值第一次落到
按字面量写的内部代码，而每一条那样的代码都是"不相等 ⇒ 换一条路"）：

1. wire 上一处 `enum` 数组都不剩（递归扫全量，含 anyOf/properties 嵌套）。
2. 每一颗枚举参数传错，dhole 自己报，且**列出全部合法值**：extraction_type
   （web_read / web_act / web_request）、focus_mode、focus_engine（web_read /
   web_crawl）、force_fetcher（web_read / web_crawl）、anti_detect、mode、freshness。
3. 大小写与空白是噪声不是另一个值；缺席、`None`、`""` 一律读成"没设"，工具自己的
   默认生效 —— 而不是把 `None` 传到 `extraction_type` 那种"默认不是 None"的签名上
   （`_forward` 的 docstring 记着那次缓存 INSERT NOT NULL 事故）。
4. 值集只有一份真相：`params` 的表 == `server` 的 `Literal` == `focus.ENGINES`；
   mode/freshness 的所有者是 `search.py`，`params` 里**不许**再抄一遍。
5. 那条真正的事故轴：非法值不许再走到"不相等 ⇒ 静默换一条路"。
   `focus_engine="nueral"` 过去被 `normalize_engine` 读成 ''（=BM25）且无人上报；
   `mode="blurry"` 过去按 merge 排序并把 `rerank_mode=merge` 报回来，像那是要求的一样。
"""

from __future__ import annotations

import asyncio
import json
from typing import get_args

import pytest

from dhole_mcp import params, search
from dhole_mcp import server as server_mod
from dhole_mcp.focus import ENGINES as FOCUS_ENGINES
from dhole_mcp.server import MasterFetchServer

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
