"""test_context_4.py —— 由 7 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import inspect
import pytest
from dhole_mcp import search as search_mod
from dhole_mcp.server import MasterFetchServer
from dhole_mcp import wire
from dhole_mcp.context import _FOCUS, _FOCUS_MODE
from dhole_mcp.models import ResponseModel
from dhole_mcp.focus import snap_to_boundary
from dhole_mcp.quality import _summary_label
from dhole_mcp.server import MIN_CHUNK_CHARS, _apply_chunking
import asyncio
import json
from types import SimpleNamespace
from dhole_mcp.context import _arg_notes_scope
from dhole_mcp.quality import _agent_hints
import re
from dhole_mcp.context import nothing_went_out, tool_scope
from dhole_mcp.context import _ARG_NOTES, _CACHE_CTX, _SESSION_ID
from dhole_mcp.context import BACKSTOP_GRACE_S
from dhole_mcp.quality import _is_over_budget, _over_budget_result


# ============================================================================
# 来源：test_the_search_door_declares_itself_on_every_refusal.py
# ============================================================================
# 搜索门上的那半句「这一发什么也没发生」：每一条拒绝都要说，且只有一位作者（评审 R02-088）。

CLAUSE = "Nothing was searched - no engine was asked."

# 能走到的拒绝形状：直接调方法即可，全部在打引擎之前返回（最后一支用 spy 钉住这件事）。
REFUSALS = [
    pytest.param({"engines": ["nope"]}, id="陌生引擎名"),
    pytest.param({"location": "worldwide"}, id="不是 ISO 的 location"),
    pytest.param({"page": 99}, id="page 越界"),
    pytest.param({"after": "last tuesday"}, id="读不出来的日期"),
    pytest.param({"mode": "bogus"}, id="陌生的 mode"),
    pytest.param({"freshness": "hour"}, id="只收四颗的 freshness"),
    pytest.param({"fetch_min_consensus": 1}, id="共识地板 1（filters nothing）"),
    pytest.param({"mode": "find_similar"}, id="find_similar 不给 url"),
    pytest.param({"query": "x" * 3000}, id="超过 2048 字符的 query"),
]


def _call(kwargs: dict):
    import asyncio
    out = asyncio.run(MasterFetchServer().web_search(**({"query": "widgets"} | kwargs)))
    assert out.error, f"这一形状本该被拒，却回了没有 error 的包: {out}"
    assert out.results == []
    return out


def _way_out(kwargs: dict) -> str:
    return (_call(kwargs).next_action or "")


class TestEveryRefusalDeclares:
    """声明是拒绝信封的属性，不是某张表里的赠品。"""

    @pytest.mark.parametrize("kwargs", REFUSALS)
    def test_a_refused_search_says_nothing_was_asked(self, kwargs):
        way = _way_out(dict(kwargs))
        assert CLAUSE in way, way

    @pytest.mark.parametrize("kwargs", REFUSALS)
    def test_the_declaration_is_spoken_once(self, kwargs):
        """两遍等于两位作者：拼接处一旦重复，读的人要猜哪一遍是真的。"""
        way = _way_out(dict(kwargs))
        assert way.count(CLAUSE) == 1, way

    @pytest.mark.parametrize("kwargs", REFUSALS)
    def test_the_declaration_is_the_first_thing_said(self, kwargs):
        """顺序也是契约：调用方扫一眼行首就能判断这一发出没出去过。"""
        way = _way_out(dict(kwargs))
        assert way.startswith(CLAUSE), way

    def test_a_door_that_writes_its_own_way_out_still_declares(self):
        """这一支是 F77/F79 之后仍然成立的那条：写了 next_action 不等于替声明签了字。

        修之前红在两处（`r95_search.json` 的 S7 与 `r96_declaration.json` 的 D8）：
        `search.py` 的 find_similar 与 `server.py` 的 fetch_min_consensus 都自带出路，
        于是 `next_action or _param_repair_hint(...)` 走前一支，那半句整条消失。
        """
        for kwargs in ({"mode": "find_similar"}, {"fetch_min_consensus": 1}):
            way = _way_out(dict(kwargs))
            assert CLAUSE in way, way
            assert way.strip() != CLAUSE.strip(), f"只有声明、没有出路: {way}"

    def test_the_empty_query_stops_speaking_in_another_door_voice(self):
        """手搓信封那处（server.py:9855 空 query）从前说 "no request was made"。

        那句话在本门上不算假，但它是**第二位作者**：同一条门上「有没有发出去」只有一个说法。
        修之前红 —— D12 读到的就是旧句子。
        """
        way = _way_out({"query": ""})
        assert CLAUSE in way, way
        assert "no request was made" not in way.lower(), way

    @pytest.mark.parametrize("kwargs", REFUSALS + [pytest.param({"query": ""}, id="空 query")])
    def test_a_refusal_still_labels_the_usage_log_line(self, kwargs):
        """`_param_refusal` 的契约是三格齐全 —— 用量日志里失败那一行要有标签。

        这一支在修之前红在两颗手搓的信封上：超过 2048 字符那条 `summary` 是空串，
        空 query 那条走的是自己拼的 `SearchResponseModel`。
        """
        out = _call(dict(kwargs))
        assert (out.summary or "").startswith("invalid request"), out.summary


class TestOneAuthorPerSentence:
    """那半句在源码里只许有一处作者；修法句与声明的装配只许有一处。"""

    def test_the_clause_is_authored_exactly_once(self):
        """按函数的常量池数，不按文件文本数 —— 注释与文档里引用它不算写它。

        这一把的尺子量过两次搬家：R02-088 时作者从 `_param_repair_hint` 的兜底支搬进
        `_nothing_was_searched()`（那一版断言的是"search.py 里恰好一位"）；R02-089 又把
        取词上移到 `context.nothing_went_out()` —— 因为按门取词这件事跨模块，留在搜索模块里
        迟早再长出第二份。**改的是期待的位置，不是期待本身**：全链上持有这句字面量的函数
        仍然恰好一个，只是那位现在住在跨门的那一处。
        """
        from dhole_mcp import context, params, server

        holders = []
        for mod in (search_mod, context, params, server):
            for name, fn in inspect.getmembers(mod, inspect.isfunction):
                consts = [c for c in getattr(fn.__code__, "co_consts", ())
                          if isinstance(c, str) and c != fn.__doc__]
                if CLAUSE in consts:
                    holders.append(f"{mod.__name__}.{name}")
        assert len(holders) == 1, f"声明有 {len(holders)} 位作者: {holders}"
        assert holders[0].startswith("dhole_mcp.context"), holders

    @pytest.mark.parametrize("door,expected", [
        ("web_read", "Nothing was fetched."),
        ("web_crawl", "Nothing was fetched."),
        ("web_discover", "Nothing was fetched."),
        ("doc_parse", "Nothing was fetched."),
        ("feed_read", "Nothing was fetched."),
        ("web_request", "Nothing was sent."),
        ("web_act", "Nothing was run."),
        ("web_search", CLAUSE),
    ])
    def test_each_door_gets_its_own_word_from_one_table(self, door, expected):
        """门类是逐扇声明的，不是从某颗参数猜的（评审 R02-089）。

        旧判据问"这扇收不收 `cache_ttl`" —— 11 扇里只有 3 扇收，其余被默认支推到 `sent`，
        于是同一扇 `web_discover` 上并存三种说法（`r105_one_door_three_verbs.json`）。
        """
        from dhole_mcp.context import nothing_went_out
        from dhole_mcp.context import tool_scope
        with tool_scope(door):
            assert nothing_went_out() == expected, door

    def test_the_undeclared_doors_keep_the_old_sentence(self):
        """【原判据搬家 · R02-116 与 R02-115】这颗守卫等的那份"它们自己的证据"到了，两扇都到了。

        原句写的是"没量到受害者的门，不替它们发明新词"，按 §12 让 `sys_state`/`sys_guide`
        原样保留等自己的证据。证据后来各自量到：
          · `sys_state`（R02-116 · r189 六面）：同一扇门里 erasure 面说自己写的
            `Nothing was erased.`，参数面听默认支 —— 一句有作者、一句没有。登记的句子
            **逐字未变**，所以那颗前向断言至今仍是绿的（这条要信）。
          · `sys_guide`（R02-115 · r192 六面）：成功面与 unknown-topic 面都是散文，而陌生参数
            与错类型两面交 JSON 13 格抓取壳 —— 一扇两种语言。新词因此不是"替它发明"，
            是它自己那扇从没写过的申报位第一次有词。
        期待强度没降，对象换了：**没被认领的门名仍须落默认支**。这颗要是红了，说明有人把
        默认支掏空了 —— 那才是 F83 这把刀最初要防的方向。

        另记一句量具账（不记产品账）：这格红在**独占全量**而不是红在我修完刀跑的那一片邻座 ——
        我修前跑了含本文件的 11 个信封族文件，修后只跑了其中 8 个。判据面跑窄了一次，归本仓
        评审自己的操作层（§6.3 B 层）。
        """
        from dhole_mcp.context import _DOOR_KIND, nothing_went_out, tool_scope
        assert _DOOR_KIND.get("sys_state") == "state", _DOOR_KIND
        assert _DOOR_KIND.get("sys_guide") == "guide", _DOOR_KIND
        with tool_scope("no_such_door_yet"):
            assert nothing_went_out() == "Nothing was fetched or changed."

    def test_the_repair_hint_no_longer_smuggles_the_declaration(self):
        """装配点唯一：`_param_repair_hint` 只交修法句，那半句由 `_param_refusal` 接上。

        红在修之前 —— 旧版 `_param_repair_hint` 返回 `f"{CLAUSE} {hint}"`，声明与修法句在
        同一处焊死，所以「调用点没写 next_action」成了声明存在的**前提**。这一支钉的是形状，
        不是文本：把声明搬回 hint 里、让 `_param_refusal` 重复拼接，这里也会红。
        """
        hint = search_mod._param_repair_hint("Invalid engine: 'nope' (one of [...])")
        assert hint and not hint.startswith(CLAUSE), hint


class TestTheWayOutIsStillFree:
    """修句子的代价不许是「开始真的发包」：这些拒绝的全部价值在于一个引擎也没问。"""

    def test_declaring_costs_no_request(self, monkeypatch):
        called = []

        async def spy(*a, **k):
            called.append(a)
            return ([], [])

        monkeypatch.setattr(search_mod, "multi_search", spy)
        for kwargs in ({"mode": "find_similar"}, {"fetch_min_consensus": 1}, {"query": ""}):
            _call(kwargs)
        assert called == [], f"加了声明就顺手发了包: {called}"


class TestThisDoorDoesNotSpeakForOthers:
    """反向守卫：别把「按门取词」做成「所有门都说搜索这句话」。"""

    def test_a_read_door_refusal_does_not_claim_a_search(self):
        import asyncio

        out = asyncio.run(MasterFetchServer().web_read(url="not-a-url"))
        assert out.error, out
        assert CLAUSE not in (out.next_action or ""), out.next_action


# ============================================================================
# 来源：test_the_number_that_changes_with_the_focus_string_says_it_is_filtered.py
# ============================================================================
# 随 focus 词变的那个总数，必须自己说它是被过滤过的那一路；切口不许落在词中间。

COOKIE = "第{i}段讲 cookie 的存活时间，配置项叫 DHOLE_COOKIE_TTL_S，默认三千秒。"
OTHER = "第{i}段是别的话题：图表、排版、字体回退与一些无关的说明，用来把这一页填满一点。"


def _page(hits: int = 6, filler: int = 18) -> ResponseModel:
    paras = []
    for i in range(hits):
        paras.append(COOKIE.format(i=i))
        for k in range(filler // hits):
            paras.append(OTHER.format(i=f"{i}{k}"))
    return ResponseModel(url="https://e.test/p", status=200,
                         content=["\n\n".join(paras)], extracted_type="markdown")


def _chunked(result: ResponseModel, *, focus: str | None, max_chars: int = 4_000,
             offset: int = 0, focus_mode: str = "filter") -> ResponseModel:
    token = _FOCUS.set(focus)
    mode_token = _FOCUS_MODE.set(focus_mode)
    try:
        return _apply_chunking(result.model_copy(), max_chars=max_chars, offset=offset)
    finally:
        _FOCUS_MODE.reset(mode_token)
        _FOCUS.reset(token)


# ─── 1 · 会变的那个数要说它是哪一路 ──────────────────────────────

def test_a_focus_cut_page_reports_which_stream_it_counted():
    out = _chunked(_page(), focus="cookie 存活时间")
    assert out.pagination_stream == "focused", out.pagination_stream
    assert out.page_total_chars == len("\n\n".join(_page().content)), (
        "`page_total_chars` 应当是同一页**未过滤**的长度，不是另一个 guess")
    assert out.total_extracted_chars < out.page_total_chars, (
        "这一路没被切短，那「哪一路」就没内容可说")
    assert "focus-filtered" in out.pagination_basis, out.pagination_basis


def test_a_focus_header_is_not_counted_as_page_body():
    """重点：focus 头部是说明，不是页面正文。

    2026-10-28 实测 `/article`：`page_total_chars=387`，而 filter-best 的
    `total_extracted_chars=474` —— 头部（约 437 字符）被算进了“过滤后正文”，
    于是同一句里出现“474 of 387 chars this page holds”。小页面最容易露馅：
    头部长过正文时，过滤后必然“比整页还长”。这里断言 basis 里用于比较的
    正文数不超过整页数，且头部单独点名。
    """
    page = _page(hits=2, filler=0)
    out = _chunked(page, focus="cookie 存活时间", focus_mode="best")
    page_chars = len("\n\n".join(page.content))
    assert out.page_total_chars == page_chars
    assert out.total_extracted_chars > out.page_total_chars, (
        "夹具失灵：小页面的 focus 头部应当让 total_extracted_chars 超过整页，"
        "这正是旧句子的形状")
    m = re.search(r"focus-filtered body: ([\d,]+) of ([\d,]+) page chars",
                  out.pagination_basis or "")
    assert m, out.pagination_basis
    kept_body, compared_page = (int(x.replace(",", "")) for x in m.groups())
    assert compared_page == page_chars
    assert kept_body <= compared_page, out.pagination_basis
    assert "focus header is not page content" in out.pagination_basis


def test_two_different_focus_words_on_one_page_both_say_so():
    """六个值的来路：换 focus 词就是换一路。两发都自己认领，读的人不必猜页面变没变。"""
    a = _chunked(_page(), focus="cookie 存活时间")
    b = _chunked(_page(), focus="字体回退")
    assert a.total_extracted_chars != b.total_extracted_chars, (
        "夹具失灵：两个 focus 词应当切出两路不同的长度，这一条就没有对照")
    for one in (a, b):
        assert one.pagination_stream == "focused"
        assert one.page_total_chars == a.page_total_chars == b.page_total_chars, (
            "同一页的未过滤长度随 focus 词变了 —— 那才叫真的在骗人")


def test_an_unfocused_read_says_nothing_about_a_stream():
    """默认值就是"这一路是全文"，一句人人都知道的话不占字节。"""
    out = _chunked(_page(), focus=None)
    assert out.pagination_stream == "full", out.pagination_stream
    assert out.page_total_chars == 0 and out.pagination_basis == ""
    dumped = wire._wire_dump(out)
    for name in ("pagination_stream", "page_total_chars", "pagination_basis"):
        assert name not in dumped, f"{name} 上线了：干净响应在付费"


def test_the_no_more_content_branch_carries_the_basis_too():
    """`[No more content.]` 骗人的正是那一支：翻到"没有更多"时更得说清是哪一路。"""
    out = _chunked(_page(), focus="cookie 存活时间", max_chars=60, offset=10 ** 6)
    assert out.content == ["[No more content.]"], out.content
    assert out.pagination_stream == "focused", out.pagination_stream
    assert out.page_total_chars > out.total_extracted_chars


# ─── 2 · 切口落在界上，而字一个都不丢 ────────────────────────────

def test_a_window_ends_at_a_clause_not_mid_word():
    """裸切片实测剪断过中文正文的"值"字 —— 这一窗必须退回到最近的界。

    R01-024 之前这一条要先 `split("\\n\\n[Truncated")[0]` 把旁注剪掉才拿到正文；
    现在 `content[0]` 就是正文，剪的那一步没了，断言反而更硬。
    """
    out = _chunked(_page(hits=4, filler=40), focus=None, max_chars=100)
    body = out.content[0]
    assert body[-1] not in "，。；：！？" , f"界上不该把标点留在窗口尾：{body[-6:]!r}"
    assert out.next_offset == len(body), (
        "next_offset 与切回去的长度不一致 —— 那就是真丢了字")
    assert out.chars_snapped_back == 100 - len(body), (
        f"吸附了 {out.chars_snapped_back}，界在 {len(body)}：两个数说的是同一件事吗")


def test_the_snapped_characters_arrive_on_the_next_page():
    """吸附挪界不删字：这一窗 + 下一窗，接起来仍然是那一路的全文。"""
    page = _page(hits=4, filler=40)
    first = _chunked(page, focus=None, max_chars=100)
    kept = len(first.content[0])
    second = _chunked(page, focus=None, max_chars=100, offset=first.next_offset)
    whole = "\n\n".join(page.content)
    assert first.next_offset == kept
    assert whole[:kept] + whole[kept:] == whole
    assert second.content[0].startswith(whole[kept:kept + 20]), (
        "下一页不是从这一页停下的地方开始的 —— 那几个字确实丢了")


def test_the_last_window_never_snaps():
    """最后一片没有"下一页"可以去：吸附它就是把字扔掉。"""
    page = _page(hits=2, filler=4)
    whole = "\n\n".join(page.content)
    out = _chunked(page, focus=None, max_chars=max(1, len(whole) - MIN_CHUNK_CHARS + 1))
    assert out.is_truncated is False, out.content[0][-60:]
    assert out.chars_snapped_back == 0
    assert out.content[0] == whole, "最后一片该是剩下的全部，一个字都不该少"


def test_a_cut_that_already_lands_on_a_boundary_pays_no_note():
    head = "一句话说完了。" * 10
    assert snap_to_boundary(head) in (len(head), head.rfind("。"))
    assert snap_to_boundary("abc def") == 3, "词末是兜底，不是首选"


def test_no_boundary_at_all_returns_the_whole_window():
    """没有标点也没有空格时原样交回 —— "找不到界"不等于"可以剪半截"。"""
    run = "没有标点也没有空格的一长串中文正文用来测试兜底那一支" * 3
    assert snap_to_boundary(run) == len(run)


# ─── 3 · 一处作者：标签与分页读同一颗规则 ────────────────────────

def test_the_summary_label_and_the_page_share_one_rule():
    """同一串文本，标签剪在哪里与分页剪在哪里必须是同一颗函数说了算。"""
    msg = ("这份文档讲 cookie 的存活时间，配置项叫 DHOLE_COOKIE_TTL_S，"
           "默认值是三千秒，可以被会话级参数覆盖掉。后面还有很多字，用来把它顶过帽。")
    cap = 40
    label = _summary_label(msg, cap=cap)
    at = snap_to_boundary(msg[:cap])
    assert label == msg[:at].rstrip(), (label, msg[:at])
    assert not label.endswith(("，", "。", " ", "：")), label[-4:]


def test_a_chinese_label_no_longer_ends_on_a_colon_or_a_dangling_open_paren():
    """标签那一族的实测形状（"…Valid values:"、"…can compile (S"）现在也管中文标点。"""
    label = _summary_label("有效值包括：markdown、text、article、html 与 structured "
                           "五种，其中 html 跳过阅读器直接交回源码正文。", cap=20)
    assert not label.endswith("："), label
    assert "、" not in label[-1:], label


def test_the_new_fields_have_a_single_producer_in_chunking():
    """三颗字段只在 `_apply_chunking` 里被写：别处再算一遍就是第二个作者。"""
    import inspect
    from dhole_mcp import server
    src = inspect.getsource(server)
    assert src.count('"pagination_stream"') == 1, src.count('"pagination_stream"')
    assert src.count('"chars_snapped_back"') == 1


def test_a_wire_row_keeps_its_pagination_truth_when_it_is_not_the_default():
    """压实机制按"等于自己的默认值"删字段：'full' 被删、'focused' 必须活下来。"""
    out = _chunked(_page(), focus="cookie 存活时间")
    dumped = wire._wire_dump(out)
    assert dumped["pagination_stream"] == "focused"
    assert dumped["page_total_chars"] == out.page_total_chars
    assert dumped["pagination_basis"]


@pytest.mark.parametrize("focus,expected_stream",
                         [("cookie 存活时间", "focused"), (None, "full")])
def test_the_stream_field_survives_every_chunking_branch(focus, expected_stream):
    """两条出口（翻过头 / 正常一片）都得带着这一颗：model_copy 漏一处就静默消失过。"""
    page = _page()
    big = _chunked(page, focus=focus, max_chars=4_000)
    assert big.pagination_stream == expected_stream
    past = _chunked(page, focus=focus, max_chars=60, offset=10 ** 6)
    assert past.pagination_stream == ("focused" if focus else "full")


# ============================================================================
# 来源：test_a_degradation_that_only_lives_in_metadata_was_never_announced.py
# ============================================================================
# 一次降级只有活在 metadata 第 14 行，就等于没说过。

PUBLIC = "https://example.com/report"
SNAPSHOT = "https://web.archive.org/web/20230415120000/https://example.com/report"


def _archived(**updates) -> ResponseModel:
    """一封真的来自档案档的回执（字段形状照 `_fetch_from_archive` 那一步之后）。"""
    out = ResponseModel(status=200, content=["the report, as it read in 2023"] * 20,
                        url=PUBLIC, fetcher_used="http", source="archive.org",
                        archived_at="2023-04-15",
                        escalation_path="http→stealthy→archive.org",
                        total_extracted_chars=1_200, content_type="text/html")
    return out.model_copy(update=updates)


class TestTheArchiveAnswerSpeaksInTheConclusion:

    def test_the_one_line_an_agent_scans_names_the_copy_and_its_date(self):
        summary, _next, ok = _agent_hints(_archived())
        assert "archived copy" in summary, summary
        assert "2023-04-15" in summary, summary
        assert "http→stealthy→archive.org" in summary, summary
        assert ok is True, "档案档确实答出来了 —— 说它是降级不等于说它失败"

    def test_a_live_answer_says_nothing_about_archives(self):
        """反向：不降级的时候不许有那句 —— 否则那句话就不再是信号。"""
        summary, _next, _ok = _agent_hints(_archived(source="live", archived_at="",
                                                     escalation_path=""))
        assert "archived" not in summary, summary

    def test_an_undated_snapshot_does_not_become_an_empty_comma(self):
        """日期缺失要说出来。拼接不能造出 `archived copy, ` 那种**看起来说过**的半句。"""
        summary, _next, _ok = _agent_hints(_archived(archived_at=""))
        assert "archived copy, date unknown" in summary, summary

    def test_the_route_clause_only_appears_when_a_route_was_walked(self):
        summary, next_action, _ok = _agent_hints(_archived(escalation_path=""))
        assert "archived copy, 2023-04-15" in summary, summary
        assert "(via " not in summary, summary
        assert next_action is not None  # 只是把三样拼起来，没有顺手改别的字段


class _Wayback:
    """把 `_fetch_from_archive` 的两跳都接在本地：availability API + 快照那一跳。"""

    def __init__(self, monkeypatch, snapshot_response: ResponseModel):
        body = json.dumps({"archived_snapshots": {"closest": {
            "url": SNAPSHOT, "available": True, "timestamp": "20230415120000"}}})

        async def fake_http_get(url, timeout=10):
            return SimpleNamespace(status=200, body=body)

        async def fake_get(self, url, **kwargs):
            return snapshot_response.model_copy(update={"url": url})

        monkeypatch.setattr("dhole_mcp.server._fallback_http_get", fake_http_get)
        monkeypatch.setattr(MasterFetchServer, "get", fake_get)


def _snapshot(content=("body", "of the archived page"), **updates) -> ResponseModel:
    return ResponseModel(status=200, content=list(content), url=SNAPSHOT,
                         fetcher_used="http", content_type="text/html",
                         total_extracted_chars=sum(len(c) for c in content),
                         **updates)


def _take_down(monkeypatch, snapshot: ResponseModel):
    server = MasterFetchServer()
    _Wayback(monkeypatch, snapshot)
    with _arg_notes_scope() as notes:
        out = asyncio.run(server._fetch_from_archive(PUBLIC, "markdown", None, 0, 1000))
    return out, notes


class TestTheSnapshotProvenanceSurvivesTheUrlRewrite:

    def test_the_snapshot_url_is_kept_while_the_answer_is_named_by_the_origin(self,
                                                                             monkeypatch):
        out, _notes = _take_down(monkeypatch, _snapshot())
        assert out is not None
        assert out.url == PUBLIC, "调用方问的是原站，`url` 不能改成副本地址"
        assert out.metadata.get("snapshot_url") == SNAPSHOT, out.metadata
        assert out.source == "archive.org"
        assert out.archived_at == "2023-04-15", out.archived_at

    def test_a_canonical_pointing_at_the_origin_is_kept(self, monkeypatch):
        """不能过度开火：原站自己声明的 canonical 就是要引用的东西。"""
        snap = _snapshot(metadata={"canonical": "https://example.com/report/"})
        out, notes = _take_down(monkeypatch, snap)
        assert out.metadata.get("canonical") == "https://example.com/report/", out.metadata
        assert not any("canonical" in n for n in notes), notes

    def test_a_canonical_that_resolves_to_the_snapshot_host_is_dropped_and_named(
            self, monkeypatch):
        """相对 canonical 在副本主机上解析出来的那个 URL，没有任何人发布过它。"""
        bogus = "https://web.archive.org/report"
        snap = _snapshot(metadata={"canonical": bogus, "title": "The report"})
        out, notes = _take_down(monkeypatch, snap)
        assert "canonical" not in out.metadata, out.metadata
        assert out.metadata.get("title") == "The report", "只删那一颗，别把整张 metadata 扫掉"
        assert any(bogus in n and "snapshot host" in n for n in notes), notes


class TestTheSnapshotOwnFailureIsNamedNotErased:

    def test_a_hard_block_on_the_snapshot_does_not_disappear_with_the_wipe(self,
                                                                          monkeypatch):
        """快照答出来了但带着自己的失败：记下名字，判决位留空。

        摘掉修复（改回 `snap_resp.error = ""`）这一条就红 —— 那句话没有任何地方可以再
        被读到，而这正是 `snap_resp.error = ""` 那行注释（"clear any error from the HTTP
        fetch"）曾经做到过的事。
        """
        blocked = _snapshot(error="auth_required: the snapshot itself is behind a login")
        out, _notes = _take_down(monkeypatch, blocked)
        assert out is not None
        assert out.error == "", out.error
        assert out.metadata.get("snapshot_error") == (
            "auth_required: the snapshot itself is behind a login"), out.metadata

    def test_a_clean_snapshot_leaves_no_error_trace_behind(self, monkeypatch):
        """没有失败时不许凭空造一个 `snapshot_error` 键 —— 缺席就是没说。"""
        out, _notes = _take_down(monkeypatch, _snapshot())
        assert "snapshot_error" not in out.metadata, out.metadata

    def test_the_conclusion_still_carries_the_demotion_after_all_of_it(self, monkeypatch):
        """三件事叠在一起时，summary 那句话仍然在场（字段之间不许互相吞）。"""
        snap = _snapshot(metadata={"canonical": "https://web.archive.org/report"})
        out, _notes = _take_down(monkeypatch, snap)
        summary, _next, ok = _agent_hints(out)
        assert "archived copy, 2023-04-15" in summary, summary
        assert ok is True, (summary, out.error)


# ============================================================================
# 来源：test_a_pages_note_does_not_live_inside_the_pages_text.py
# ============================================================================
# R01-024（并入 R01-005）· 一页被切成窗时，那行旁注不许混在正文里交付。
# 重名消歧：COOKIE → COOKIE__a_pages_note_does_not_li_06a7、OTHER → OTHER__a_pages_note_does_not_li_06a7、_chunked → _chunked__a_pages_note_does_not_li_06a7、_page → _page__a_pages_note_does_not_li_06a7

COOKIE__a_pages_note_does_not_li_06a7 = "第{i}段讲 cookie 的存活时间，配置项叫 DHOLE_COOKIE_TTL_S，默认三千秒。"
OTHER__a_pages_note_does_not_li_06a7 = "第{i}段是别的话题：图表、排版、字体回退与一些无关的说明，用来把这一页填满一点。"


def _page__a_pages_note_does_not_li_06a7(hits: int = 6, filler: int = 40) -> ResponseModel:
    paras = []
    for i in range(hits):
        paras.append(COOKIE__a_pages_note_does_not_li_06a7.format(i=i))
        for k in range(filler // hits):
            paras.append(OTHER__a_pages_note_does_not_li_06a7.format(i=f"{i}{k}"))
    return ResponseModel(url="https://e.test/p", status=200,
                         content=["\n\n".join(paras)], extracted_type="markdown")


def _chunked__a_pages_note_does_not_li_06a7(result: ResponseModel, *, focus: str | None = None, max_chars: int = 4_000,
             offset: int = 0) -> ResponseModel:
    token = _FOCUS.set(focus)
    try:
        return _apply_chunking(result.model_copy(), max_chars=max_chars, offset=offset)
    finally:
        _FOCUS.reset(token)


# ─── 1 · 交付形状：正文块里只有正文 ───────────────────────────────

def test_the_window_delivers_only_the_pages_own_text():
    out = _chunked__a_pages_note_does_not_li_06a7(_page__a_pages_note_does_not_li_06a7(), max_chars=120)
    assert out.is_truncated, "这一发本该是中间的一窗"
    assert "[Truncated" not in out.content[0], out.content[0][-120:]
    assert not out.content[0].endswith(")"), out.content[0][-80:]


def test_the_note_names_every_figure_it_used_to_name_in_the_text():
    """搬走位置不许搬走信息：四颗数一句不少。"""
    out = _chunked__a_pages_note_does_not_li_06a7(_page__a_pages_note_does_not_li_06a7(), max_chars=120)
    note = out.truncation_note
    assert note, out
    body = out.content[0]
    assert f"showing {len(body):,}" in note, (note, len(body))
    assert f"{out.total_extracted_chars:,} extracted chars" in note, note
    assert f"Next offset: {out.next_offset}]" in note, note
    if out.chars_snapped_back:
        assert f"{out.chars_snapped_back:,} chars moved to the next window" in note, note


def test_the_two_coordinate_systems_are_one_number_now():
    """修前实测的形状：`showing 357` 与 `len(content[0])==531` 差一颗旁注的长。"""
    out = _chunked__a_pages_note_does_not_li_06a7(_page__a_pages_note_does_not_li_06a7(), max_chars=120)
    assert len(out.content[0]) == out.next_offset, (
        f"正文 {len(out.content[0])} 与 next_offset {out.next_offset} 不是同一个数 —— "
        "说明还有别的东西混在带内")


def test_a_page_that_arrived_whole_pays_no_note():
    out = _chunked__a_pages_note_does_not_li_06a7(_page__a_pages_note_does_not_li_06a7(hits=1, filler=1), max_chars=4_000)
    assert not out.is_truncated, out.is_truncated
    assert out.truncation_note == ""
    assert "truncation_note" not in wire._wire_dump(out), "默认值上线了（压实表里没这颗？）"


def test_the_last_window_opens_with_its_own_marker_not_a_suffix():
    """翻到底那一支的 `[No more content.]` 是整块正文的替身，不是挂在正文尾巴上的旁注：
    它不在本刀的范围里，也不许被这次搬家顺手抹掉。"""
    page = _page__a_pages_note_does_not_li_06a7(hits=2, filler=6)
    last = _chunked__a_pages_note_does_not_li_06a7(page, max_chars=60, offset=10 ** 6)
    assert last.content == ["[No more content.]"], last.content
    assert last.truncation_note == "", last.truncation_note
    assert not last.is_truncated


# ─── 2 · 边界与接续：搬走旁注之后仍然是逐字无缝 ────────────────────

def test_the_snapped_characters_still_arrive_on_the_next_page():
    """P05 实测的加分项（重叠=0）不许被这次搬家弄坏：正文块拼起来就是那一路的全文。"""
    page = _page__a_pages_note_does_not_li_06a7(hits=4, filler=40)
    whole = "\n\n".join(page.content)
    first = _chunked__a_pages_note_does_not_li_06a7(page, max_chars=100)
    second = _chunked__a_pages_note_does_not_li_06a7(page, max_chars=100, offset=first.next_offset)
    kept = first.next_offset
    assert whole[:kept] == first.content[0], "第一窗的正文不再是它声称的那一段"
    assert second.content[0].startswith(whole[kept:kept + 20]), (
        "下一页不是从这一页停下的地方开始的")
    assert first.chars_snapped_back == 0 or first.content[0] == whole[:kept]


def test_the_pointer_to_the_next_window_still_lives_where_an_agent_looks():
    """`next_action` 那句"offset=N 继续翻"是出路的本体，必须原样在。"""
    out = _chunked__a_pages_note_does_not_li_06a7(_page__a_pages_note_does_not_li_06a7(), max_chars=120)
    assert f"offset={out.next_offset}" in out.next_action, out.next_action
    assert out.next_offset and out.is_truncated


# ─── 3 · R01-005 那一半：这句话要说它数的是哪一路 ─────────────────

def test_a_focused_stream_says_which_coordinate_its_offsets_count_against():
    """带 focus 时 `total_extracted_chars` 是过滤之后那一路的长度 —— 旁注必须自己说。

    夹具要够长：`MIN_CHUNK_CHARS=500` 那一档以下 dhole 把剩下的并进这一窗（不发第二刀），
    于是根本没有"窗"可说。第一版拿 hits=6/filler=60 打，focus 后只剩 398 字，走的是
    "整页都给你"那一支 —— 断言的是不存在的形状，不是产品的错。
    """
    page = _page__a_pages_note_does_not_li_06a7(hits=20, filler=200)
    out = _chunked__a_pages_note_does_not_li_06a7(page, focus="cookie 存活时间", max_chars=120)
    assert out.is_truncated, (out.total_extracted_chars, out.truncation_note)
    assert out.pagination_stream == "focused", out.pagination_stream
    assert out.page_total_chars > out.total_extracted_chars, (
        out.page_total_chars, out.total_extracted_chars)
    note = out.truncation_note
    assert f"this page holds {out.page_total_chars:,} before focus" in note, note
    assert "these offsets count against the filtered text" in note, note


def test_an_unfocused_window_names_no_second_coordinate():
    """没动那一路就不许多说一句：默认那一路是全文，人人都知道的话不占字节。"""
    out = _chunked__a_pages_note_does_not_li_06a7(_page__a_pages_note_does_not_li_06a7(), max_chars=120)
    assert out.pagination_stream == "full", out.pagination_stream
    assert "before focus" not in out.truncation_note, out.truncation_note


# ─── 4 · 手册：这颗字段要住在 agent 会翻的地方 ────────────────────

def test_the_note_is_documented_where_an_agent_will_look():
    from dhole_mcp import guide

    corpus = "\n".join(p.read_text(encoding="utf-8") for p in guide._ROOT.rglob("*.md"))
    assert "truncation_note" in corpus, "字段上了 wire 而手册没有它：只读手册的 agent 不认识它"


def test_the_wire_carries_the_field_by_name():
    dumped = wire._wire_dump(_chunked__a_pages_note_does_not_li_06a7(_page__a_pages_note_does_not_li_06a7(), max_chars=120))
    assert "truncation_note" in dumped, sorted(dumped)


# ============================================================================
# 来源：test_the_store_door_says_it_stored_nothing.py
# ============================================================================
# 存储门自己说的话（评审 R02-102）：`cred_defaults` 的每一种拒绝都说同一句本门话，且说一遍。
# 重名消歧：CLAUSE → CLAUSE__the_store_door_says_it_s_132c、REFUSALS → REFUSALS__the_store_door_says_it_s_132c

CLAUSE__the_store_door_says_it_s_132c = "Nothing was stored or forgotten."
OLD_DEFAULT = "Nothing was fetched or changed."
RE_CLAUSE = re.compile(r"[Nn]othing was [a-zA-Z ,\-]+?\.")

# 五种门口拒绝：全部在写库之前返回，不碰磁盘、不碰网络。
REFUSALS__the_store_door_says_it_s_132c = [
    pytest.param({"set": {"bogus": 1}}, id="陌生键"),
    pytest.param({"set": {"proxy": "http://x"}, "unset": ["proxy"]}, id="同一颗既存又忘"),
    pytest.param({"set": {"auth": {"user": "u", "pass": "p"}}}, id="凭据没给 host"),
    pytest.param({"set": {"proxy": "http://x", "host": "api.example.com"}},
                 id="给了 host 却没给需要 host 的键"),
]


async def _face(args: dict) -> dict:
    """走**真握手**：陌生参数名那一颗是在 `call_tool` 里被 `ArgumentRefusal` 转成信封的，
    直接调 `_dispatch` 会拿到异常而不是回执 —— 而这一行要判的正是线上读到的那句话。
    """
    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport

    transport = InMemoryTransport(MasterFetchServer()._mcp_server())
    async with transport as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            res = await session.call_tool("cred_defaults", args)
    d = json.loads("".join(getattr(b, "text", "") for b in res.content))
    assert d.get("error"), f"这一形状本该被拒，却回了没有 error 的包: {d}"
    return d


class TestTheStoreDoorSpeaksOneSentence:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("args", REFUSALS__the_store_door_says_it_s_132c)
    async def test_a_refusal_from_the_store_door_is_named_as_input(self, args):
        d = await _face(args)
        assert d["error"].startswith("invalid_request:"), d["error"][:180]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("args", REFUSALS__the_store_door_says_it_s_132c)
    async def test_it_says_its_own_sentence_not_the_fetch_door_s(self, args):
        na = (await _face(args)).get("next_action") or ""
        assert na.startswith(CLAUSE__the_store_door_says_it_s_132c), na[:200]
        assert OLD_DEFAULT not in na, f"这一扇还在借用别的门的动词: {na[:200]}"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("args", REFUSALS__the_store_door_says_it_s_132c)
    async def test_the_sentence_is_spoken_once(self, args):
        """两遍等于两位作者：字面量搬进装配点后，旧那句若没删干净会拼成两句。"""
        na = (await _face(args)).get("next_action") or ""
        assert len(RE_CLAUSE.findall(na)) == 1, na[:220]

    @pytest.mark.asyncio
    async def test_the_unknown_argument_name_agrees_with_the_key_level_refusal(self):  # noqa: D
        """C6 那一格（陌生参数名，装配层拒）必须与门内那几格说同一句。

        修前它落 `context` 的表外默认支说 `fetched or changed`，而同一扇的门内五处说
        `stored or forgotten` —— 一扇两种申报就是 R02-102 的读数本身。
        """
        d = await _face({"delete": ["proxy"]})
        na = d.get("next_action") or ""
        assert na.startswith(CLAUSE__the_store_door_says_it_s_132c), na[:200]
        assert "fetched or changed" not in na, na[:200]


class TestTheWordsHaveOneAuthor:
    def test_the_door_is_declared_by_class_not_by_default(self):
        assert nothing_went_out("cred_defaults") == CLAUSE__the_store_door_says_it_s_132c

    def test_the_body_of_the_door_no_longer_writes_the_sentence(self):
        """这一扇的函数体里不许再出现门话字面量 —— 词只许向 `context` 那一位取。

        只判 `cred_defaults` 这一颗方法：仓库里其余按门已经写对的句子不在这把刀的半径里
        （F86 的同一取舍，写在那把尺的 docstring 里）。
        """
        src = inspect.getsource(MasterFetchServer.cred_defaults)
        hits = [ln.strip()[:90] for ln in src.splitlines()
                if re.search(r"Nothing was (stored|fetched|sent|run)", ln)
                and not ln.strip().startswith("#")]
        assert hits == [], f"这一扇里还有人在抄门话: {hits}"

    def test_the_undeclared_pair_keeps_the_old_sentence(self):
        """【原判据搬家 · F99】这颗守卫跟着证据走完了它该走的全程：表外那两扇如今都有主了。

        原句是"剩下两扇仍没有受害证据，不替它们发明新词"。F98 给 `sys_state` 收了证据
        （句子逐字未变，只是不再由默认支代答），F99 给 `sys_guide` 收了更硬的一面：它的
        陌生参数拒绝从前穿一副 13 格的抓取壳，而它连成功面都是散文（真机
        `r192_sys_guide_faces.json` 六面）—— 一扇两种语言，这就是"发明新词"所缺的那份证据。

        期待强度没有降，只是换了对象：**没被认领的门名仍须落默认支**。这条要是红了，就等于
        有人把默认支掏空了 —— 那正是 F83 那把刀最初要防的方向。
        """
        from dhole_mcp.context import _DOOR_KIND
        assert _DOOR_KIND.get("sys_state") == "state", _DOOR_KIND
        assert _DOOR_KIND.get("sys_guide") == "guide", _DOOR_KIND
        with tool_scope("no_such_door_yet"):
            assert nothing_went_out() == OLD_DEFAULT

    def test_cred_defaults_is_no_longer_in_the_undeclared_list(self):
        from dhole_mcp.context import _DOOR_KIND
        assert _DOOR_KIND.get("cred_defaults") == "store", _DOOR_KIND


async def _face_ok(args: dict) -> dict:
    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport

    transport = InMemoryTransport(MasterFetchServer()._mcp_server())
    async with transport as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            res = await session.call_tool("cred_defaults", args)
    return json.loads("".join(getattr(b, "text", "") for b in res.content))


class TestNothingElseMoved:
    """成功那一面一字不改：这一扇的 `message` 说的是库里现在有什么，不是申报句。"""

    @pytest.mark.asyncio
    async def test_an_empty_store_still_answers_in_its_own_words(self):
        d = await _face_ok({"show": True})
        assert not d.get("error"), d
        assert "Nothing is stored" in (d.get("message") or ""), d.get("message")
        assert d.get("error", "").startswith("invalid_request:") is False


# ============================================================================
# 来源：test_a_refused_session_id_leaves_nothing_for_the_next_call.py
# ============================================================================
# 被拒的 session_id 不许在原地留下任何一次调用的状态（2026-10-08 验收 · D-02 的回归锁）

URL = "https://jar.example.com/page"
BAD = "a session name with spaces"


class _Tiers:
    """HTTP 档的记录器：不发网络，只看那一发进来时上下文里有什么。

    `get` 必须是**未绑定**的函数存进类属性（第一个参数是 MasterFetchServer 自己）——
    批量那条路会按签名把 kwargs 往参数上绑，绑好的方法会把 url 吃掉。
    """

    def __init__(self) -> None:
        self.seen: list[tuple[str, str]] = []

    def install(self, monkeypatch) -> "_Tiers":
        recorder = self

        async def get(self, url, **kwargs):
            recorder.seen.append((url, _SESSION_ID.get()))
            return ResponseModel(url=url, status=200, content=["<p>page</p>"],
                                 content_type="text/html", fetcher_used="http",
                                 total_extracted_chars=4)

        monkeypatch.setattr(MasterFetchServer, "get", get)
        return recorder


@pytest.fixture()
def tiers(monkeypatch):
    # robots 那一档也会发包，本锁不测它。
    monkeypatch.setattr("dhole_mcp.robots.check_robots_allow",
                        lambda *a, **k: (True, ""), raising=False)
    return _Tiers().install(monkeypatch)


class TestTheRefusalWritesNothing:

    @pytest.mark.asyncio
    async def test_a_refused_session_id_leaves_the_jar_name_unset(self, tiers):
        before = (_SESSION_ID.get(), _CACHE_CTX.get(), _ARG_NOTES.get())
        r = await MasterFetchServer().web_read(url=URL, session_id=BAD, cache_ttl=0)
        assert r.status == 0 and "session_id" in r.error, (r.status, r.error)
        assert "Nothing was fetched" in r.next_action, r.next_action
        assert tiers.seen == [], "被拒的一次还是发了一发"
        after = (_SESSION_ID.get(), _CACHE_CTX.get(), _ARG_NOTES.get())
        assert after[0] == before[0] == "", after
        assert after[1] == before[1], "_CACHE_CTX 带着上一发的指纹进了下一次"
        assert after[2] == before[2], "_ARG_NOTES 还是上一发那只列表"

    @pytest.mark.asyncio
    async def test_the_next_call_in_the_same_context_sees_nothing_of_it(self, tiers):
        """残留只有在**下一次调用**上才算：那一发必须是干净的匿名一发。"""
        server = MasterFetchServer()
        await server.web_read(url=URL, session_id=BAD, cache_ttl=0)
        r2 = await server.web_read(url=URL, cache_ttl=0)
        assert r2.status == 200, r2.error
        assert not getattr(r2, "session_id", ""), r2.session_id
        assert tiers.seen[-1] == (URL, ""), f"上一发拒绝的名字还挂在上下文里: {tiers.seen}"

    @pytest.mark.asyncio
    async def test_a_batch_refuses_the_whole_call_not_one_row(self, tiers):
        """一颗坏名字不能让批量里的别行偷偷跑起来（同一个 id 覆盖整批）。

        两个位子上都要拦：分发层的白名单（`_ARG_NOTES` 之前、连一次 ContextVar 都不
        写），以及 `_with_smart_fetch_options` 那枚闸门。只留一处，另一处就会有人挪。
        """
        r = await MasterFetchServer()._dispatch(
            "web_read", {"urls": [URL, "https://jar.example.com/other"],
                         "session_id": BAD, "cache_ttl": 0})
        assert tiers.seen == [], tiers.seen
        payload = json.loads(r[0][0].text)
        rows = payload.get("results") or [payload]
        assert rows, payload
        assert all("session_id" in (row.get("error") or "") for row in rows), rows


class TestALegalNameStillReachesTheTier:
    """反向锁：闸门挪在前面不等于名字不再起作用。"""

    @pytest.mark.asyncio
    async def test_a_valid_name_is_visible_inside_the_fetch_and_reset_after(self, tiers):
        await MasterFetchServer().web_read(url=URL, session_id="alice-jar_1", cache_ttl=0)
        assert tiers.seen == [(URL, "alice-jar_1")], tiers.seen
        assert _SESSION_ID.get() == "", "写完没 reset：下一发会顶着别人的登录态跑"

    @pytest.mark.asyncio
    async def test_the_shape_that_is_allowed_unchanged_still_answers(self, tiers):
        """没给名字 = 匿名，一次都不许多余拒绝（这条门不能变成新的默认拦路）。"""
        r = await MasterFetchServer().web_read(url=URL, cache_ttl=0)
        assert r.status == 200, r.error
        assert tiers.seen == [(URL, "")], tiers.seen

    @pytest.mark.asyncio
    async def test_a_64_character_name_is_still_a_name(self, tiers):
        long = "a" * 64
        r = await MasterFetchServer().web_read(url=URL, session_id=long, cache_ttl=0)
        assert r.status == 200, r.error
        assert tiers.seen[-1] == (URL, long), tiers.seen


# ============================================================================
# 来源：test_a_budget_overrun_says_both_numbers.py
# ============================================================================
# R01-042 · 一条超时不许在同一封响应里有两个数，还不说它们的关系。
# 重名消歧：URL → URL__a_budget_overrun_says_bo_dc38

URL__a_budget_overrun_says_bo_dc38 = "https://example.com/big.pdf"
GRACE_MS = int(BACKSTOP_GRACE_S * 1000)


def _build(budget_ms: float, elapsed_ms: float, stage: str = "HTTP tier"):
    return _over_budget_result(URL__a_budget_overrun_says_bo_dc38, budget_ms, elapsed_ms, stage, "http")


def test_the_error_names_both_the_asked_budget_and_the_time_spent():
    r = _build(10000, 10876)
    assert "10000" in r.error and "10876" in r.error, r.error
    # 两个格子必须认同一个花掉的数（建议位里还有别的数 —— 上限、余量，
    # 所以这里查的是"这一发实际花了多久"那一个，不是最后一个数）
    assert "10876ms" in r.next_action, (r.error, r.next_action)


def test_the_machine_readable_prefix_survives_the_rewording():
    """`_is_over_budget` 认的是 "timeout: the " 这个前缀，每一档都靠它收尾 ——
    重写句子不许把这条机器可读的边抹掉。"""
    r = _build(10000, 10876)
    assert r.error.startswith("timeout: the "), r.error
    assert _is_over_budget(r)


def test_a_spill_inside_the_grace_is_called_the_grace():
    """10876 - 10000 = 876ms < 1500ms 的宽限：那是"让在跑的那一步收个尾"的量，
    不是第二个预算。这句话从前没人说，读者只能把两个数当成谁抄错了。"""
    r = _build(10000, 10876)
    low = r.error.lower()
    assert "grace" in low, r.error
    assert str(GRACE_MS) in r.error, r.error
    assert "past the budget" not in low, r.error


def test_an_overrun_past_the_grace_is_reported_as_an_overrun():
    """P10 那一发的形状：花了 68113ms，点名的预算是 10000ms。
    这时候响应必须把它**越过了预算与宽限**直说，并说出超了多少。"""
    r = _build(10000, 68113)
    assert "past the budget" in r.error.lower(), r.error
    assert str(68113 - 10000) in r.error, r.error
    assert _is_over_budget(r)


def test_the_overrun_is_not_blamed_on_the_asked_figure():
    """越限那一发里，"raise timeout" 不是出路：这次已经把 10000ms 花到 68113ms 了。
    建议位可以继续给通用杠杆，但那句必须带上"这一发已经超出自己点名的预算"这一事实。"""
    r = _build(10000, 68113)
    assert "68113" in r.next_action, r.next_action
    assert "past the budget" in r.next_action.lower() or "past the budget" in r.error.lower()


def test_a_call_that_stopped_inside_its_budget_says_so_plainly():
    r = _build(30000, 29500)
    assert "inside" in r.error.lower() or "within" in r.error.lower(), r.error
    assert "grace" not in r.error.lower(), r.error


def test_no_sentence_of_the_advice_starts_where_a_lower_case_word_begins():
    """本刀自己带出的错（R01-010 测量时读到，见 fix-R01-F42.md 的《复验》之后一段）。

    `relation` 那句是为 `error` 的括号写的 —— 分号之后小写开头是对的 —— 却被原样拼进
    `next_action` 的句首。实测那一封读起来是：
    `Budget exhausted after 32424ms at the fetch tiers. it ran 2424ms past the budget…`
    句号之后一个小写词，是给人看的句子里最贵的一种错：读者会以为上一句还没说完。
    """
    for budget, elapsed in ((10000, 12424), (10000, 10876), (30000, 29500)):
        r = _build(budget, elapsed)
        # 量字段边界，不全文分句：建议里有 "e.g." 那样的缩写，按 ". " 切出来的不是句子
        head_end = r.next_action.index(". ") + 2
        opener = r.next_action[head_end:head_end + 1]
        assert opener.isupper() or opener.isdigit(), (budget, elapsed, r.next_action)


# ─── 反向守卫：这一刀不许动已有的两条专项说明 ─────────────────────────────

def test_a_document_download_keeps_its_own_diagnosis():
    """专项判决（"这是下载，raise 也没用"）本来就走 `diagnosis`，不许被通用句盖掉。"""
    r = _over_budget_result(URL__a_budget_overrun_says_bo_dc38, 10000, 10876, "HTTP tier", "http",
                            diagnosis="the file is 4.1MB; a download gets ONE attempt")
    assert "a download gets ONE attempt" in r.next_action, r.next_action
    assert "10876" in r.next_action, r.next_action


def test_the_terminal_marker_and_the_empty_body_are_unchanged():
    r = _build(10000, 10876)
    assert r.status == 0 and r.content == []
    assert "No content was extracted." in r.error, r.error
