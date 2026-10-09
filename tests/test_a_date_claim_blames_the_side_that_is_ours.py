""""这家不能按日期过滤" 是一个关于第三方索引的说法，而 dhole 只知道自己发出去什么（S8-full）。

`date_filter.note` 此前把收不到日期过滤的引擎一律写成 "cannot be date-filtered at all"。
对 so360 / sogou / yandex 这句勉强是同一件事（线路里确实没有这一说），对 **baidu** 却是
dhole 自己的代码在反对它 —— 吞掉 `timelimit` 那一行的注释原文写着
"时间过滤（gpc/stf）未实现"：参数**存在**，没接线的是我们。把我们的没做说成它的不能，
是本批母题（静默降级）换了个方向：这一次被掩盖的不是结果，是责任。

同族还有三处：
  · `SearchResponseModel.date_filter` 的自述抄了一份四家名单（池子随 `engines=` 变，
    抄来的名单只会腐烂）
  · `after=` 超过最粗一档那一支说 "no engine answers beyond a year" —— 事实是 dhole
    发得出最粗的一档是 year，而发更宽会把调用方要的页面筛掉
  · `before=` 的拒绝句说 "no engine in the pool can bound results to older-than-a-date"

手册的 Dates 段也养着同一份手抄名单。这一刀的折中是：**名单可以留在手册里，但必须钉在
表上** —— 那一段只许出现"已接线"或"已声明未接线"的名字，两个集合都要齐。少一个名字是
手册漏了一家，多一个名字是有人在替引擎编能力。
"""

from __future__ import annotations

import pytest

from dhole_mcp import guide
from dhole_mcp.search import SearchResponseModel, _date_window, _DATE_PARAM_UNWIRED
from dhole_mcp.search_engines import (
    DEFAULT_ENGINES, _DATE_AWARE_ENGINES, split_date_blindness,
)

POOL = list(DEFAULT_ENGINES)


def _note(engines, **kw) -> str:
    _applied, info, _why = _date_window(kw.get("after"), kw.get("before"),
                                        kw.get("freshness", "week"), engines)
    return info.get("note", "")


class TestWhoseGapItIs:
    """收不到日期过滤分两种责任方，措辞必须跟着分。"""

    def test_an_engine_with_no_wired_parameter_is_called_ours(self):
        note = _note(["baidu"])
        assert "dhole has not wired a date filter for baidu (gpc/stf)" in note, note
        assert "cannot" not in note, note

    def test_the_rest_are_reported_as_wire_facts_not_engine_limits(self):
        note = _note(["so360", "sogou", "yandex"])
        assert "dhole sent so360, sogou, yandex no date filter" in note, note
        for word in ("cannot", "incapable", "does not support"):
            assert word not in note, note

    def test_a_mixed_pool_keeps_the_two_clauses_apart(self):
        """默认池同时有这两种：一句归线路、一句归我们，不许糊成同一句能力判断。"""
        note = _note(POOL)
        assert "dhole sent" in note and "dhole has not wired" in note, note
        assert "baidu (gpc/stf)" in note, note

    def test_no_note_anywhere_still_claims_an_engine_cannot_filter(self):
        """全池枚举：这句谎一次都不许出现，无论池子怎么拼。"""
        pools = [POOL, ["bing"], ["baidu", "yandex"], ["sogou"], ["so360", "bing"], []]
        for engines in pools:
            for args in ({"freshness": "month"}, {"after": "2026-01-01"}):
                note = _note(engines, **args)
                assert "cannot be date-filtered" not in note, (engines, args, note)


class TestTheUnwiredTableEarnsItsName:
    """`_DATE_PARAM_UNWIRED` 不许变成第二份会腐烂的表：每一颗都必须当场可验。"""

    def test_unwired_names_are_not_claimed_as_aware(self):
        assert not set(_DATE_PARAM_UNWIRED) & _DATE_AWARE_ENGINES, \
            "同一家的两种说法同时上线，等于没有说法"

    def test_every_unwired_name_is_a_real_engine(self):
        from dhole_mcp import search_metasearch as ms

        registry = set(ms._TEXT_ENGINES) | set(ms.KEYED_ENGINES)
        assert set(_DATE_PARAM_UNWIRED) <= registry, \
            f"表里点名了不存在的引擎: {set(_DATE_PARAM_UNWIRED) - registry}"

    def test_the_gap_is_real_today_the_payload_still_drops_it(self):
        """如果哪天有人接了线，这颗就必须从表里删掉 —— 否则响应会反过来冤枉 dhole。"""
        from dhole_mcp import search_metasearch as ms

        for name, param in sorted(_DATE_PARAM_UNWIRED.items()):
            eng = ms._TEXT_ENGINES[name]()
            base = eng.build_payload("q", "us-en", "moderate", None, 1)
            week = eng.build_payload("q", "us-en", "moderate", "w", 1)
            diff = {k for k, v in week.items() if base.get(k) != v}
            assert not diff, (f"{name} 现在真的发出日期过滤了（多出 {diff}）—— "
                              f"它已不再属于未接线名单，删掉这一项并把 {param} 那行注释改掉")

    def test_the_split_partitions_the_blind_list(self):
        _aware, blind = split_date_blindness([n for n in POOL if n in _DATE_PARAM_UNWIRED]
                                             + ["sogou"])
        assert _aware == ["baidu"] and blind == ["sogou"], (_aware, blind)


class TestTheFieldDescriptionCarriesNoHandCopiedPool:
    """自述里抄一家名单 = 第二次上线那份表，而池子会随 `engines=` 变。"""

    def test_the_date_filter_description_names_no_engine(self):
        from dhole_mcp import search_metasearch as ms

        text = SearchResponseModel.model_fields["date_filter"].description or ""
        for name in sorted(set(ms._TEXT_ENGINES) | set(ms.KEYED_ENGINES) | _DATE_AWARE_ENGINES):
            assert name not in text, f"字段自述又把 {name} 抄了一遍: {text[:120]}"


class TestTheCoarsestWindowIsOurs:

    def test_an_ancient_after_says_what_dhole_can_send(self):
        """旧句是 "no engine answers beyond a year"（替引擎断言），新句只说我们发得出什么。"""
        _a, info, _w = _date_window("2020-01-01", None, None, POOL)
        assert info["exact"] is False
        assert "the coarsest window dhole can send is 'year'" in info["note"], info["note"]
        assert "no engine answers" not in info["note"], info["note"]
        assert info["sent"].startswith("nothing"), info["sent"]


class TestTheBeforeRefusalClaimsOnlyOurWire:

    def test_the_refusal_names_dholes_window_not_the_pools_ability(self):
        _a, _i, why = _date_window(None, "2026-01-01", None, POOL)
        assert "dhole has no window that bounds results to older-than-a-date" in why, why
        assert "no engine in the pool can" not in why, why
        assert "feed_read(since=" in why, why


class TestTheManualsDateParagraphIsPinnedToTheTable:
    """手册那段名单与代码那张表互为上界：少一家是漏，多一家是替引擎编能力。"""

    @staticmethod
    def _date_paragraph() -> str:
        text = (guide._ROOT / "references" / "tools.md").read_text(encoding="utf-8")
        start = text.index("**Dates**")
        rest = text[start + len("**Dates**"):]
        end = rest.find("\n\n**")
        return rest if end < 0 else rest[:end]

    def _named(self, paragraph: str) -> set[str]:
        from dhole_mcp import search_metasearch as ms

        all_names = set(ms._TEXT_ENGINES) | set(ms.KEYED_ENGINES)
        return {n for n in all_names if f"`{n}`" in paragraph}

    def test_the_paragraph_names_nothing_outside_the_two_tables(self):
        named = self._named(self._date_paragraph())
        allowed = _DATE_AWARE_ENGINES | set(_DATE_PARAM_UNWIRED)
        assert named <= allowed, (
            f"手册把 {named - allowed} 写进了日期过滤那一段 —— 它既不在已接线的表里，"
            f"也没有被声明为未接线，这是替一家引擎编能力")

    def test_every_aware_engine_is_still_listed(self):
        named = self._named(self._date_paragraph())
        assert _DATE_AWARE_ENGINES <= named, f"手册漏了: {_DATE_AWARE_ENGINES - named}"

    def test_the_declared_gap_is_written_down_where_readers_decide(self):
        """baidu 这一颗的用处全在读者会不会把它读成"百度的结果不新" —— 那句话必须在
        他被这一句劝走的地方等着他。"""
        paragraph = self._date_paragraph()
        for name, param in sorted(_DATE_PARAM_UNWIRED.items()):
            assert f"`{name}`" in paragraph, name
            assert param.split("/")[0] in paragraph, (name, param)
        assert "do not conclude" in paragraph.lower(), paragraph


@pytest.mark.asyncio
async def test_the_split_reaches_the_caller_on_a_real_round(monkeypatch):
    """`_date_window` 建对了不算，消费者那一路（`fetch_hint`）也得把两句话说齐。"""
    from dhole_mcp import search
    from dhole_mcp.search_engines import EngineReport, RawResult

    ranked = [RawResult(title="固态电池 量产", url="https://a.test/1",
                        snippet="固态电池 量产 时间", source="bing", position=1)]
    reports = [EngineReport(name="bing", ok=True, status="ok"),
               EngineReport(name="baidu", ok=True, status="empty")]

    async def fake_multi(query_, max_results, **kwargs):
        return list(ranked), list(reports)

    async def _none():
        return None

    async def no_get(*a, **kwargs):
        return None

    async def no_set(*a, **kwargs):
        return None

    monkeypatch.setattr(search, "multi_search", fake_multi)
    monkeypatch.setattr(search, "ensure_reranker", _none)
    monkeypatch.setattr(search, "get_cached", no_get)
    monkeypatch.setattr(search, "set_cached", no_set)
    out = await search.web_search(None, "固态电池 量产", 5,
                                  engines=["bing", "baidu"], freshness="month",
                                  cache_ttl=0, mode="auto")
    hint = out.fetch_hint or ""
    assert out.date_filter["exact"] is False, out.date_filter
    assert "dhole has not wired a date filter for baidu (gpc/stf)" in hint, hint
    assert "cannot be date-filtered" not in hint, hint
