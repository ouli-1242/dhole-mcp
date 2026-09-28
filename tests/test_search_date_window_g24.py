"""G24 回归：搜索的日期窗口要说实话。

实测（离线枚举每个引擎真正发出去的 payload）：默认池里 **没有任何引擎接受绝对日期
区间**，能过滤的那几个也只有 day/week/month/year 四档。所以 `after=<某日>` 只能被
放大到「能覆盖它的最窄一档」，而放大这件事必须写在响应里 —— 否则调用方以为自己拿到
的是「9 月 25 日之后」，实际是「最近一周（比你的窗口窄）」或「最近一年（比你的宽）」。

`before` 直接拒绝而不是忽略：预设只能限定「不早于」，而搜索结果里根本没有发布日期
字段（SearchResult 一个日期字段都没有），dhole 既问不出来也验不了。一个什么都不做
的参数正是这份审计报告在找的那类东西。

`total_estimate`（报告的另一半）**没有实现**：六个引擎的解析器都不读「共约 N 条结果」
那一行，要拿到它得给每个引擎加一段 HTML 正则 —— 那正是 G15/G16/G17 那类「字段要么
真要么别给」的坑。这里记下不做的理由，而不是造一个假数字。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest

from dhole_mcp.search import _date_window, _parse_date_arg
from dhole_mcp.search_engines import DEFAULT_ENGINES, _DATE_AWARE_ENGINES, date_support

POOL = list(DEFAULT_ENGINES)


def days_ago(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime("%Y-%m-%d")


class TestTheTableIsTheEngines:
    """日期能力表必须等于引擎实际发出去的东西，否则报告会替引擎撒谎。"""

    def test_text_engines_match_the_table(self):
        from dhole_mcp import search_metasearch as ms

        for name, cls in sorted(ms._TEXT_ENGINES.items()):
            eng = cls()
            try:
                base = eng.build_payload("q", "us-en", "moderate", None, 1)
                week = eng.build_payload("q", "us-en", "moderate", "w", 1)
            except NotImplementedError:
                continue
            diff = {k: v for k, v in week.items() if base.get(k) != v
                    and k not in {"searchid"}}  # yandex rotates an id per call
            assert bool(diff) == (name in _DATE_AWARE_ENGINES), (name, diff)

    def test_keyed_engines_match_the_table(self):
        from dhole_mcp import search_metasearch as ms

        for name, cls in sorted(ms.KEYED_ENGINES.items()):
            _url, _h, plain = cls().build_request("q", 5, None)
            _url, _h, week = cls().build_request("q", 5, "week")
            diff = {k: v for k, v in week.items() if plain.get(k) != v}
            assert bool(diff) == (name in _DATE_AWARE_ENGINES), (name, diff)

    def test_the_default_pool_split_is_what_the_report_measured(self):
        filtered, unfiltered = date_support(POOL)
        assert filtered == ["bing", "bing_global", "brave"]
        assert unfiltered == ["baidu", "sogou", "yandex"]

    def test_an_unknown_engine_counts_as_unfiltered(self):
        assert date_support(["newengine"]) == ([], ["newengine"])


class TestTheWindowTranslation:

    def test_a_three_day_window_gets_the_week_preset(self):
        applied, info, why = _date_window(days_ago(3), None, None, POOL)
        assert why == ""
        assert (applied, info["sent"], info["exact"]) == ("week", "freshness=week", True)
        assert info["engines_filtered"] == ["bing", "bing_global", "brave"]
        assert "baidu" in info["engines_unfiltered"]

    # Widths are date-only strings, so each means "from midnight UTC that day":
    # the real age is a little more than the round number, which is exactly why the
    # preset is the one that COVERS the window rather than the one closest to it.
    @pytest.mark.parametrize("days,preset", [(0, "day"), (2, "week"),
                                             (9, "month"), (40, "year")])
    def test_each_width_gets_the_narrowest_covering_preset(self, days, preset):
        _applied, info, _why = _date_window(days_ago(days), None, None, POOL)
        assert info["sent"] == f"freshness={preset}"

    def test_older_than_any_preset_filters_nothing_and_says_so(self):
        applied, info, why = _date_window(days_ago(800), None, None, POOL)
        assert why == "" and applied is None
        assert info["exact"] is False
        assert info["engines_filtered"] == []
        assert sorted(info["engines_unfiltered"]) == sorted(POOL)
        assert "none of the results are date-filtered" in info["note"]

    def test_a_tighter_freshness_from_the_caller_wins(self):
        """调用方自己点了 day，就不能因为他的 after 算出来是 week 而放宽。"""
        applied, info, _why = _date_window(days_ago(3), None, "day", POOL)
        assert applied == "day"
        assert info["sent"] == "freshness=day"
        assert info["exact"] is False

    def test_a_broader_freshness_is_narrowed_to_the_window(self):
        applied, _info, _why = _date_window(days_ago(2), None, "year", POOL)
        assert applied == "week"

    def test_freshness_alone_is_reported_too(self):
        """老参数同样值得说明：它也只覆盖得住池子里的一半引擎。"""
        _applied, info, _why = _date_window(None, None, "week", POOL)
        assert info["requested"] == "freshness=week" and info["exact"] is True
        assert "baidu" in info["engines_unfiltered"]

    def test_no_date_asked_no_report(self):
        _applied, info, _why = _date_window(None, None, None, POOL)
        assert info == {}

    @pytest.mark.parametrize("given", ["2026-09-20", "2026-09-20T08:00:00Z",
                                      "Sat, 20 Sep 2026 08:00:00 +0000"])
    def test_accepted_date_formats(self, given):
        assert _parse_date_arg(given, "after").tzinfo is not None

    @pytest.mark.parametrize("bad", ["last tuesday", "", 12345, None])
    def test_unreadable_dates_raise(self, bad):
        from dhole_mcp.security import SecurityError
        with pytest.raises(SecurityError):
            _parse_date_arg(bad, "after")


class TestBeforeIsRefusedNotIgnored:

    def test_before_comes_back_as_a_reason_not_a_silent_no_op(self):
        applied, info, why = _date_window(None, days_ago(30), None, POOL)
        assert (applied, info) == (None, {})
        assert "before is not supported" in why
        assert "feed_fetch(since=" in why

    def test_the_alternative_is_named(self):
        _a, _i, why = _date_window(None, "2026-01-01", None, POOL)
        assert "after=" in why

    def test_an_unreadable_before_is_still_a_date_error(self):
        from dhole_mcp.security import SecurityError
        with pytest.raises(SecurityError):
            _date_window(None, "sometime", None, POOL)


class TestTheWireText:

    def test_search_options_advertise_the_window(self):
        from dhole_mcp.server import MasterFetchServer
        tools = {t["name"]: t for t in MasterFetchServer._TOOL_DEFS}
        opts = tools["smart_search"]["inputSchema"]["properties"]["options"]["description"]
        # 袋子现在是「一行一键」的写法（"after: ..."），旧的 "after (" 是同段列举
        # 时代的形状。判据是键有没有被 advertised，不是它后面跟的是括号还是冒号。
        assert re.search(r"\bafter\s*[(:]", opts), "after= 的窗口语义没上线"
        assert re.search(r"\bbefore\s*[(:]", opts), "before= 被拒的说明没上线"
        assert "date_filter" in opts, "没告诉调用方去哪读实际发出去的日期窗口"

    def test_the_method_accepts_both(self):
        import inspect
        from dhole_mcp.server import MasterFetchServer
        params = inspect.signature(MasterFetchServer.smart_search).parameters
        assert {"after", "before"} <= set(params)
