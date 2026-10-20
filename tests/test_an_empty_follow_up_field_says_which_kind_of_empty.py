"""空的 `related_queries` 必须说得出是哪一种空。

台账 R02-068。量出来的分布（本轮 16 发已入账真 SERP，`review/.runtime/p4/f52_cause.py`）：
**切不出词的 2 发**、**切得出词但没有跨源重复的 7 发**、以及 R02-F52 修完之后新增的第三种
**候选都被形状门挡掉的**。三种在交付面上都长成一个 `[]`，字段说明只有一句
「Empty if none derived」—— 而三种空对调用方是三种出路：第一种换什么问法都没用，
第二种是诚实的证据不足，第三种说明这一轮的行里翻来覆去就是你自己那几个词。

所以判据分三格各自的句子，加两把锁：最常见的「没有重复」继续沉默（给每一发都加一句
客套，会把真正需要出路的那两格淹掉），有交回时一个字都不许多说。
"""
import re

from dhole_mcp.search import (SearchResult, _follow_up_cause, _related_queries,
                              compute_fetch_hint)


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
import pytest  # noqa: E402

from dhole_mcp import search  # noqa: E402
from dhole_mcp.search import RawResult  # noqa: E402
from dhole_mcp.search_engines import EngineReport  # noqa: E402


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
