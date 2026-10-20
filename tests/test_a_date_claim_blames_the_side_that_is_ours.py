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
    """收不到日期过滤分两种责任方，措辞必须跟着分。

    期4.4 把这张表的例子搬走了：`baidu` 的 `gpc=stf=` 现在差分得出（
    `build_payload(timelimit='w')` 比 `None` 那发多出那颗键），于是它从"我们没接线"
    搬进"已接线"，`_DATE_PARAM_UNWIRED` 空了。这一族因此分成两条钉：
      · **今天的真话** —— 未登记的 sogou/so360/yandex 只归线路事实，而 baidu 不再
        出现在任何一句"没收到过滤"里（替一家已接线的引擎背缺口，和替它编能力一样错）。
      · **机制本身** —— 那张表一旦再住进一家，措辞必须换成"我们的缺口"。用一次性
        登记来钉，而不是让这条断言随空表一起变成 vacuous。
    """

    def test_an_unregistered_engine_is_called_a_wire_fact_not_a_limit(self):
        note = _note(["sogou"])
        assert "dhole sent sogou no date filter" in note, note
        for word in ("cannot", "incapable", "does not support"):
            assert word not in note, note

    def test_the_wired_engine_is_no_longer_blamed_as_unwired(self):
        """旧断言：`_note(["baidu"])` 里写着 "dhole has not wired ... for baidu (gpc/stf)"。

        移动的那一格是实测的（见 `TestTheUnwiredTableEarnsItsName.
        test_the_table_is_empty_because_the_one_name_in_it_was_wired`），所以这里改钉
        新真话：baidu 现在站在 filtered 那一侧，且那句归我们的缺口不再上线。
        """
        _applied, info, _why = _date_window(None, None, "month", ["baidu"])
        assert info["engines_filtered"] == ["baidu"], info
        assert info["engines_unfiltered"] == [], info
        assert info["exact"] is True, info
        assert info["note"] == "", info
        assert "has not wired" not in _note(["baidu", "sogou"]), _note(["baidu", "sogou"])

    def test_the_rest_are_reported_as_wire_facts_not_engine_limits(self):
        note = _note(["so360", "sogou", "yandex"])
        assert "dhole sent so360, sogou, yandex no date filter" in note, note
        for word in ("cannot", "incapable", "does not support"):
            assert word not in note, note

    def test_a_mixed_pool_names_the_blind_ones_and_not_the_wired_one(self):
        """默认池同时有已接线与未登记两种：点名只点后者，前者不许出现在缺口句里。"""
        note = _note(POOL)
        assert "dhole sent" in note, note
        for blind in ("so360", "sogou", "yandex"):
            assert blind in note, note
        assert "baidu" not in note, f"已接线的一家又被写进缺口句: {note}"

    def test_the_unwired_wording_survives_if_the_table_gains_a_name(self, monkeypatch):
        """机制半边：表里一旦有名字，那句话必须换成"我们的缺口"，且带上参数名。"""
        from dhole_mcp import search_engines

        monkeypatch.setitem(search_engines._DATE_PARAM_UNWIRED, "sogou", "timeScope")
        note = _note(["sogou"])
        assert "dhole has not wired a date filter for sogou (timeScope)" in note, note
        assert "dhole sent sogou" not in note, note

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
        """如果哪天有人接了线，这颗就必须从表里删掉 —— 否则响应会反过来冤枉 dhole。

        这条正是期4.4 把自己执行掉的那条：表里当时住着 baidu，而它的
        `build_payload(timelimit='w')` 现在多出 `gpc=stf=<起>,<止>|stf_type=2`（差分
        当场可见），于是这一条把它判出了表。今天表是空的，所以这一族断言换成正反两面：
          · 空表是真的（`test_the_table_is_empty_because_its_one_name_was_wired`）
          · 机制还在（下面这条 monkeypatch 出一家"点名未接线"的，差分必须真把它判成
            未接线 —— 否则这张表就成了一颗只会被读、不会被验的字符串）
        """
        from dhole_mcp import search_metasearch as ms

        for name, param in sorted(_DATE_PARAM_UNWIRED.items()):
            eng = ms._TEXT_ENGINES[name]()
            base = eng.build_payload("q", "us-en", "moderate", None, 1)
            week = eng.build_payload("q", "us-en", "moderate", "w", 1)
            diff = {k for k, v in week.items() if base.get(k) != v}
            assert not diff, (f"{name} 现在真的发出日期过滤了（多出 {diff}）—— "
                              f"它已不再属于未接线名单，删掉这一项并把 {param} 那行注释改掉")

    def test_the_table_is_empty_because_its_one_name_was_wired(self):
        """今天的账：`_DATE_PARAM_UNWIRED` 没有住户，而 baidu 站在已接线那一侧。

        两边都要钉 —— 只钉"表空"会让任何人删掉一行都算通过；这一条同时要求 baidu 的
        payload 真的差分得出那颗键，所以它空得有理。
        """
        from dhole_mcp import search_metasearch as ms

        assert _DATE_PARAM_UNWIRED == {}, _DATE_PARAM_UNWIRED
        assert "baidu" in _DATE_AWARE_ENGINES
        eng = ms._TEXT_ENGINES["baidu"]()
        base = eng.build_payload("q", "us-en", "moderate", None, 1)
        week = eng.build_payload("q", "us-en", "moderate", "w", 1)
        assert "gpc" in week and "gpc" not in base, (base, week)
        # 它自己那行注释必须不再声明"未实现" —— 表与注释是一对，改一处就要改两处
        # （这正是这一族缺陷的原始形状：注释说有参数、线路一个字节都没发）。
        import inspect

        src = inspect.getsource(type(eng).build_payload)
        assert "未实现" not in src, src[:200]

    def test_the_split_partitions_the_blind_list(self):
        _aware, blind = split_date_blindness([n for n in POOL if n in _DATE_PARAM_UNWIRED]
                                             + ["sogou"])
        assert _aware == [] and blind == ["sogou"], (_aware, blind)


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
    """`before=` 那句仍只许说 dhole 发得出什么 —— 判决改成逐行之后这一条不变。

    旧断言钉的是拒绝句里的 "dhole has no window that bounds results to
    older-than-a-date"。期4.6 把整颗参数的拒绝换成了逐行判决（动它的那个实测：
    sogou/baidu/bing 的结果容器开始带日期，"行上没有日期"这句变成了假话），而这句
    话本身**仍然是**这一轮的真话 —— 引擎那一侧确实一个字节都没收到，所以它继续留在
    `date_filter.note` 里，只是后面接上了"那 dhole 做了什么"。
    """

    def test_the_per_row_sentence_names_dholes_window_not_the_pools_ability(self):
        _a, info, _why = _date_window(None, "2026-01-01", None, POOL)
        note = info["note"]
        assert "dhole has no way to send" in note, note
        assert "no engine in the pool can" not in note, note
        assert "cannot be date-filtered" not in note, note
        assert "feed_read(since=" in note, note


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
    """`_date_window` 建对了不算，消费者那一路（`fetch_hint`）也得把话说到眼前。

    期4.4 之后池子里的"两种责任方"只剩一种（baidu 接线了），所以这一条改钉今天的
    三件事：已接线的 baidu 站在 `engines_filtered` 里（不再被写进任何缺口句）、
    未登记的 so360 被点名为线路事实、而那一句谎（"cannot be date-filtered"）仍不许
    出现在调用方第一眼读的字段上。
    """
    from dhole_mcp import search
    from dhole_mcp.search_engines import EngineReport, RawResult

    ranked = [RawResult(title="固态电池 量产", url="https://a.test/1",
                        snippet="固态电池 量产 时间", source="bing", position=1)]
    reports = [EngineReport(name=n, ok=True, status="ok")
               for n in ("bing", "baidu", "so360")]

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
                                  engines=["bing", "baidu", "so360"],
                                  freshness="month",
                                  cache_ttl=0, mode="auto")
    hint = out.fetch_hint or ""
    assert out.date_filter["exact"] is False, out.date_filter
    assert "baidu" in out.date_filter["engines_filtered"], out.date_filter
    assert "baidu" not in out.date_filter["engines_unfiltered"], out.date_filter
    assert "dhole sent so360 no date filter" in hint, hint
    assert "has not wired" not in hint, hint
    assert "cannot be date-filtered" not in hint, hint
