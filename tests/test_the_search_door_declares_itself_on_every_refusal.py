# -*- coding: utf-8 -*-
"""搜索门上的那半句「这一发什么也没发生」：每一条拒绝都要说，且只有一位作者（评审 R02-088）。

量到的形状（真机 `review/.runtime/p1/r96_declaration.json`，HEAD cede0af · 12 发请求前拒绝）：
同一扇 `web_search` 上六种结果 —— 三句 `Nothing was searched - no engine was asked.`、
两句**根本没有那半句**（S7/D8：自己写了出路的那两颗站点）、一句 `no request was made`
（D12：手搓的信封）。另两句是别的门上的动词（`fetched or changed` / `sent`），站点为全部门
共用，另开一行。

来路只有一条：`_param_refusal` 写的是 `next_action or _param_repair_hint(msg)`，而声明句住在
`_param_repair_hint` 的返回值里。修法句可以没有，声明句不可以没有 —— 所以修法句的作者在这里
被顺手当成了声明的作者。下面每一支都钉这个形状。
"""
import inspect

import pytest

from dhole_mcp import search as search_mod
from dhole_mcp.server import MasterFetchServer

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
