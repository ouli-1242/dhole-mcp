"""test_search_4.py —— 由 3 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pytest
from dhole_mcp.search import _date_window, _parse_date_arg
from dhole_mcp.search_engines import DEFAULT_ENGINES, _DATE_AWARE_ENGINES, date_support
import ast
import inspect
import socket
import threading
from pathlib import Path
import dhole_mcp.search as search_mod
from dhole_mcp import identity, security
from dhole_mcp.server import MasterFetchServer
from dhole_mcp import guide
from dhole_mcp.search import SearchResponseModel, _DATE_PARAM_UNWIRED
from dhole_mcp.search_engines import split_date_blindness


# ============================================================================
# 来源：test_search_date_window_g24.py
# ============================================================================
# G24 回归：搜索的日期窗口要说实话。

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
            # 'w' 而不是 'week'：生产路径上传的是 `multi_search` 归一化之后的短名
            # （`_FRESHNESS_TO_TIMELIMIT`）。此前这里用长名调用，于是"表说有日期能力、
            # 实际一个字节都没发"这一族 bug 在测试里看不见 —— 测试与产品共享同一个
            # 错误假设，两边一起绿。
            _url, _h, plain = cls().build_request("q", 5, None)
            _url, _h, week = cls().build_request("q", 5, "w")
            diff = {k: v for k, v in week.items() if plain.get(k) != v}
            assert bool(diff) == (name in _DATE_AWARE_ENGINES), (name, diff)

    def test_keyed_engines_send_the_date_value_their_api_understands(self):
        """`bool(diff)` 只问"有没有多个键"，不问"值对不对"。

        两家 keyed 后端的日期参数各有各的词表（tavily: day/week/month/year；
        bocha: oneDay/oneWeek/...），把内部短名 `w` 原样发出去同样"多了个键"，
        但上游照样当无效忽略 —— 而报告已经把这家算进 engines_filtered 了。
        """
        from dhole_mcp import search_metasearch as ms

        tables = {
            "tavily": ("time_range", {"d": "day", "w": "week",
                                      "m": "month", "y": "year"}),
            "bocha": ("freshness", {"d": "oneDay", "w": "oneWeek",
                                    "m": "oneMonth", "y": "oneYear"}),
        }
        for name, (param, table) in sorted(tables.items()):
            build = ms.KEYED_ENGINES[name]().build_request
            _url, _h, plain = build("q", 5, None)
            assert param not in plain, (name, "没传日期时不该带这个参数")
            for short, wanted in table.items():
                _url, _h, payload = build("q", 5, short)
                assert payload.get(param) == wanted, (name, short, payload)

    def test_baidu_sends_an_absolute_stf_range_that_widens_with_the_preset(self):
        """`bool(diff)` 说得出"多了一颗键"，说不出那颗键发的是什么。

        `gpc=stf=<起>,<止>|stf_type=2` 是绝对时间戳区间，而 dhole 只有 day/week/month/
        year 四档 —— 所以判据是"区间随档位变宽，且止点始终是此刻"：这一家收到的是
        一个真窗口，而不是一个被上游当无效忽略的短名。
        **这一条钉的是 dhole 发出去什么**，不是百度按它筛了什么（离线核不了后者）；
        结果集能不能按日期说话仍由 `date_filter` + `date_coverage` 说。
        """
        import re
        import time

        from dhole_mcp import search_metasearch as ms

        build = ms._TEXT_ENGINES["baidu"]().build_payload
        assert "gpc" not in build("q", "us-en", "moderate", None, 1)
        widths = {}
        for short in ("d", "w", "m", "y"):
            gpc = build("q", "us-en", "moderate", short, 1)["gpc"]
            m = re.fullmatch(r"stf=(\d+),(\d+)\|stf_type=2", gpc)
            assert m, (short, gpc)
            start, end = int(m.group(1)), int(m.group(2))
            assert abs(end - int(time.time())) <= 5, (short, gpc)
            widths[short] = end - start
        assert widths["d"] < widths["w"] < widths["m"] < widths["y"], widths

    def test_the_default_pool_split_is_what_the_report_measured(self):
        # 期4.4 移动的那一格（实测方式=这张表自己的判据）：`baidu` 的
        # `build_payload(timelimit='w')` 现在比 `None` 多出 `gpc=stf=<起>,<止>`，
        # 差分当场可验，所以它从"收不到过滤"搬进"收得到"。剩下三家仍是线路事实。
        filtered, unfiltered = date_support(POOL)
        assert filtered == ["baidu", "bing", "bing_global", "brave", "duckduckgo"]
        assert unfiltered == ["marginalia", "so360", "sogou", "yahoo", "yandex"]

    def test_an_unknown_engine_counts_as_unfiltered(self):
        assert date_support(["newengine"]) == ([], ["newengine"])


class TestTheWindowTranslation:

    def test_a_three_day_window_gets_the_week_preset(self):
        applied, info, why = _date_window(days_ago(3), None, None, POOL)
        assert why == ""
        assert (applied, info["sent"]) == ("week", "freshness=week")
        # 期4.4 移动的那一格：baidu 现在收了 `gpc=stf=`（差分当场可验），所以它从下面
        # 那份名单搬到上面那份；名单本身仍是实测的，不是手抄的。
        assert info["engines_filtered"] == ["baidu", "bing", "bing_global",
                                            "brave", "duckduckgo"]
        assert "so360" in info["engines_unfiltered"]

    def test_a_window_the_wire_sent_widely_is_never_called_exact(self):
        """反向半边：`after=` 算出最窄覆盖档 ≠ 结果集被过滤过。

        这一条曾经是 `exact is True`。`exact` 当时只看"发出去的那一档是不是
        cover"，而同一份 info 下面三行就列着 baidu/so360/sogou/yandex **收了同样的
        查询却一个字都没过滤**。默认池上四个引擎里有四个是瞎的，却恒报 exact ——
        而 date_filter 那颗字段的自述写着 "a mixed round is NOT a date-limited
        result set"，字段与它的自述相反。
        """
        _applied, info, _why = _date_window(days_ago(3), None, None, POOL)
        assert info["exact"] is False, "池子一半不接受日期条件，回包就不是按日期筛过的"
        assert "so360" in info["note"] and "yandex" in info["note"]

    def test_a_window_sent_to_a_pool_that_all_filters_is_exact(self):
        """正向半边：把池子换成只有一家能过滤的，exact 才该翻上来。"""
        _applied, info, _why = _date_window(days_ago(3), None, None, ["bing"])
        assert info["engines_unfiltered"] == []
        assert info["exact"] is True
        assert info["note"] == ""

    # Widths are date-only strings, so each means "from midnight UTC that day":
    # the real age is a little more than the round number, which is exactly why the
    # preset is the one that COVERS the window rather than the one closest to it.
    @pytest.mark.parametrize("days,preset", [(0, "day"), (2, "week"),
                                             (9, "month"), (40, "year")])
    def test_each_width_gets_the_narrowest_covering_preset(self, days, preset):
        _applied, info, _why = _date_window(days_ago(days), None, None, POOL)
        assert info["sent"] == f"freshness={preset}"

    @pytest.mark.parametrize("args", [
        (None, None, "week", POOL),          # freshness + 池里有瞎的
        (None, None, "month", []),           # 空池
        (days_ago(3), None, None, POOL),     # after 放宽到 cover
        (days_ago(3), None, "day", POOL),    # 调用方自己的更紧预设
        (days_ago(800), None, None, POOL),   # 超过任何一档
    ])
    def test_the_two_halves_never_contradict(self, args):
        """不变量：`note` 非空 ⟺ `exact` 为 false。

        单独钉一次每条分支都太容易漏掉新加的分支（这个函数有四个建 info 的地方）。
        一条谎配一句空话（exact false 却没人告诉我为啥）和一句告诫配一个绿灯
        （exact true 却在解释问题）都在这条闸下变红。
        """
        _applied, info, _why = _date_window(*args)
        assert bool(info["note"]) is (not info["exact"]), info

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
        """老参数同样值得说明：它也只覆盖得住池子里的一半引擎。

        这条测试曾经把自己的前提和断言写反：docstring 说"只覆盖得住一半引擎"，
        下一行却断言 `exact is True`。`exact` 当时是硬编的字面量，所以它绿的不是
        行为，是那句谎。现在它说的是同一件事的两半。
        """
        _applied, info, _why = _date_window(None, None, "week", POOL)
        assert info["requested"] == "freshness=week"
        # 期4.4：baidu 从这一份名单里出去了（它的 `gpc=stf=` 差分可见），剩下三家仍是
        # "线路里没有这一说"。这一条的前提也跟着改口：池子仍有一半引擎收不到过滤。
        assert "baidu" not in info["engines_unfiltered"]
        assert "so360" in info["engines_unfiltered"]
        assert info["exact"] is False, (
            "有引擎根本没收到日期过滤却仍然应答了，这一轮就不是 exact")
        assert "no date filter" in info["note"]
        assert "so360" in info["note"]

    def test_a_pool_that_can_all_be_filtered_is_exact(self):
        """正向半边：exact 不许被改成永远 False —— 那只是把谎换成了另一个方向。

        只留一个能过滤的引擎：没有 unfiltered，窗口就是结果集真正满足的窗口。
        """
        _applied, info, _why = _date_window(None, None, "week", ["bing"])
        assert info["engines_unfiltered"] == []
        assert info["exact"] is True, "全能过滤还报 not exact，等于把告诫变成噪声"
        assert info["note"] == ""

    def test_a_pool_of_only_date_blind_engines_names_them(self):
        """全都收不到过滤：告诫要点名，而且只说线路这一侧的事实（S8-full）。

        期4.4 之前这一格住着两家：sogou 是"线路里没有这一说"，baidu 是"dhole 自己点名
        了 gpc/stf 却没接线"。后者现在接线了（判据 = `gpc` 出现在
        `build_payload(timelimit='w')` 而 `None` 那发没有），把它的名字留在"未接线"
        那一格就会反过来冤枉 dhole —— 那张表自己的测试钉的正是这条。
        """
        _applied, info, _why = _date_window(
            None, None, "month", ["so360", "sogou", "yandex"])
        assert info["engines_filtered"] == []
        assert info["exact"] is False
        assert "dhole sent so360, sogou, yandex no date filter" in info["note"], info["note"]
        assert "baidu" not in info["note"], info["note"]
        assert "cannot be date-filtered" not in info["note"], \
            "这句把我们的没做说成它的不能"

    def test_an_empty_pool_still_says_why_it_is_not_exact(self):
        """空池：既没有 filtered 也没有 unfiltered，note 不许拼出一个空主语。

        这条存在的理由是它守着 `note` 的另一个分支 —— 没有它，那个分支就是
        永远到不了的死路（本批刚在 `_cdp_revoke_hosts` 那儿认过同样的账）。
        """
        _applied, info, _why = _date_window(None, None, "month", [])
        assert info["exact"] is False
        assert info["note"].strip()
        assert "  " not in info["note"], f"空主语拼进了句子: {info['note']!r}"
        assert "no engine in this pool" in info["note"]

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
    """`before=` 的旧契约（整颗参数被拒）与被量到的新事实。

    前值（实测 2026-10-10）：拒绝句写着 "search results carry no publish date"。那句话
    当时是真话，而期4.2 把它作废了 —— 冻结夹具上 sogou 8 个结果容器读出 4 个到日的
    日期、baidu 两套版面各 2/5 与 3/6、bing 系 10 个里 1 个。行**开始**带日期了，
    所以整颗参数的拒绝从"诚实"变成了"把一个能做的判决藏起来"。

    这一类现在钉的是**换掉之后的那半句真话**，而原来那三条一句没松：
      · 不许静默（旧：拒绝要给出理由；新：账要在 `date_coverage` 上，丢了几行要说）
      · 替代路径仍要点名（`feed_read(since=)` 仍然在句子里）
      · 读不懂的 `before=` 仍然当场抛，不降级成"没问"
    """

    def test_before_comes_back_as_a_reason_not_a_silent_no_op(self):
        applied, info, why = _date_window(None, days_ago(30), None, POOL)
        assert (applied, why) == (None, "")
        # 旧的 `assert (applied, info) == (None, {})` 钉的是"这颗参数什么也没建"；
        # 现在它建的是这一轮的判决记录，而那颗布尔必须仍是 False —— 一行都没被送进
        # 引擎，`exact` 翻上来就是替引擎发了一句它没发过的话。
        assert info["exact"] is False, info
        assert info["before"]["cutoff"] == days_ago(30), info
        assert "before is not supported" not in why
        assert "feed_read(since=" in info["note"], info["note"]

    def test_the_alternative_is_named(self):
        _a, info, _why = _date_window(None, "2026-01-01", None, POOL)
        assert "after=" in self._after_plus_before("2026-01-01"), \
            "两颗一起问时 after 那一半的账不能被吃掉"
        assert info["requested"].endswith("before=2026-01-01"), info["requested"]

    def _after_plus_before(self, before):
        """`after=` + `before=` 一起问：窗口那一半照旧，逐行那一半并进来。"""
        _a, info, _w = _date_window(days_ago(3), before, None, POOL)
        assert info["sent"] == "freshness=week", info
        assert info["before"]["cutoff"] == before, info
        return info["note"]

    def test_the_per_row_verdict_is_described_where_it_can_be_acted_on(self):
        """丢掉一行就得说：`before` 那一格必须讲清"谁被丢了、谁留下了、为什么留下"。"""
        _a, info, _w = _date_window(None, "2026-01-01", None, POOL)
        applied = info["before"]["applied"]
        assert "row by row" in applied, applied
        assert "rows_dropped_by_before" in applied, applied
        assert info["before"]["sent_to_engines"].startswith("nothing"), \
            "把逐行判决说成引擎过滤，是替八家引擎编一句它们没说过的话"

    def test_an_unreadable_before_is_still_a_date_error(self):
        from dhole_mcp.security import SecurityError
        with pytest.raises(SecurityError):
            _date_window(None, "sometime", None, POOL)


class TestTheWireText:

    def test_the_wire_advertises_the_window(self):
        from dhole_mcp.server import MasterFetchServer
        tools = {t["name"]: t for t in MasterFetchServer._TOOL_DEFS}
        props = tools["web_search"]["inputSchema"]["properties"]
        # 判据是「键有没有被 advertised」：after 要说清窗口被放宽并回读 date_filter，
        # before 要说清它是**逐行**做的（不是整颗被拒、也不是被忽略），并给出替代路径。
        assert "date_filter" in props["after"]["description"], "after= 的窗口语义没上线"
        before_desc = props["before"]["description"]
        # 旧句写着 "REFUSED, not ignored: ... results carry no publish date"。那半句
        # 在期4.2 之后是假话（sogou/baidu/bing 的容器实测带着日期），所以这里改钉
        # 现在真的那一套：逐行 + 丢掉要有计数 + 判不了的留下。
        assert "per row" in before_desc, before_desc
        assert "date_coverage" in before_desc, "丢了几行没上常驻层: " + before_desc
        assert "publish date" not in before_desc, f"还在说行上没有日期: {before_desc}"
        # 方向必须住在参数上：这是调用方决定传不传 `before=` 之前就要读到的那一句话，
        # 搬到响应侧就晚了（按需付付不了"要不要花这一发"这个决定）。
        assert "Upper bound" in before_desc and "newer" in before_desc, before_desc
        # 旧句里那颗 "feed_read(since=)" 是**搬家**不是删掉：拒辞时代它指"想要带日期的条目
        # 去哪儿"，而今天 `before=` 自己就执行判决，出路换成同一轮的那句 note —— 它既指
        # 反向的 `after=`，也指 `feed_read(since=)`。常驻层 159 字买不下两份指针，而响应侧
        # 那句只在真问过日期的那一发才送到，位置更便宜也更准。下面这条钉搬到的那处没空。
        assert "feed_read" not in before_desc, before_desc
        _applied, _info, _rej = _date_window(None, "2026-01-01", None, POOL)
        note = _info["note"]
        assert "feed_read(since=)" in note, f"出路没写在眼前那句里: {note}"
        assert "after=<date>" in note, f"反向的门没点名: {note}"
        # 常驻面是锁死的（32,828，余量 0）：这句改一个字都要当场量。
        assert len(before_desc) <= 152, len(before_desc)

    def test_the_method_accepts_both(self):
        import inspect
        from dhole_mcp.server import MasterFetchServer
        params = inspect.signature(MasterFetchServer.web_search).parameters
        assert {"after", "before"} <= set(params)


class TestTheNoteReachesTheAnswer:
    """`exact` 翻成 False 只是修了一半：告诫必须真的走到调用方眼前。

    `_date_window` 算出来的 `note` 由 `web_search` 读进 `fetch_hint`。所以「字段修对
    了、消费者还在按旧契约读」这条路必须单独钉，否则 `_date_note` 永远为空字符串，
    而上面每一条 `_date_window` 的断言照样全绿。摘掉修复逐条演示过，每条都真的红：

    - freshness 分支改回硬编 `exact = True` → 本类第一条 + `_date_window` 那三条
    - after 分支改回 `exact = applied == cover` → 本类第二条 + 不变量那条 args2/3
    - 任一分支的 `note` 改成恒空 → 不变量那条按分支红（`exact` false 却没人说为什么）
    - find_similar 分支不接 `_date_note` → 本类第三条（只有它红，正说明另两条钉的是
      别的路）

    入口是 `search.web_search(server, query, ...)`（`search()` 这个名字不存在，它挂
    在 `MasterFetchServer` 上）。`server` 只往 `multi_search` 里转发，而那颗已经被
    换成假的，所以一个 `object()` 就够 —— 用真 `MasterFetchServer` 会让这条测试变成
    另一件事的测试。

    一处**没被钉住**的改动，写明免得后来人以为它被钉住了：消费者那句 `if date_filter
    and not date_filter.get("exact", ...)` 的默认值也从 `True` 翻成了 `False`。翻它
    是 fail-closed —— 缺键该读成"别信这个日期窗口"，而不是"信"。但它今天**演示不出
    变红**：`_date_window` 建 `info` 的四条路每一条都显式写了 `exact`，所以默认值那
    条分支在生产里到不了。下面 `test_no_window_report_can_omit_the_exact_key` 钉的是
    这件事的前提（每条建 info 的路都显式带 `exact`），默认值本身留作无测的保险。
    """

    SERVER = object()

    def _run(self, monkeypatch, engines, **date_args):
        import asyncio

        from dhole_mcp import search
        from dhole_mcp.search_engines import EngineReport, RawResult

        async def fake_multi(query, max_results, **kwargs):
            return [RawResult(title="一个两年前的稿子",
                              url="https://libattery.ofweek.com/2025-02/x.html",
                              snippet="snippet", source=engines[0],
                              position=1)], [
                EngineReport(name=n, ok=True) for n in engines]

        async def fake_ensure_reranker():
            return None

        async def fake_get_cached(query, cache_type, css_selector, **kwargs):
            return None

        async def fake_set_cached(query, cache_type, content, status,
                                  css_selector, ttl, **kwargs):
            return None

        monkeypatch.setattr(search, "multi_search", fake_multi)
        monkeypatch.setattr(search, "ensure_reranker", fake_ensure_reranker)
        monkeypatch.setattr(search, "get_cached", fake_get_cached)
        monkeypatch.setattr(search, "set_cached", fake_set_cached)
        monkeypatch.setattr(
            search, "_rank",
            lambda q, ranked, mode, **kw: (ranked, [1.0] * len(ranked),
                                           "merge", ""))
        # 默认走 freshness=：这个类钉的是"告诫有没有走到调用方眼前"，两颗参数
        # 都从同一句 `date_filter.get("exact", False)` 过。
        kwargs = {"freshness": "month"}
        kwargs.update(date_args)
        return asyncio.run(search.web_search(self.SERVER, "固态电池 量产", 5,
                                             engines=engines, **kwargs))

    def test_a_date_blind_pool_puts_the_caveat_in_fetch_hint(self, monkeypatch):
        out = self._run(monkeypatch, ["yandex"])
        assert out.date_filter and out.date_filter["exact"] is False
        hint = out.fetch_hint or ""
        assert "no date filter" in hint, (
            f"exact 报 False 而告诫没上线，调用方仍会按字面信 freshness: {hint!r}")
        assert "yandex" in hint

    def test_an_after_window_reaches_the_hint_by_the_same_door(self, monkeypatch):
        """`after=` 走的是另一条建 info 的分支，消费者那一侧只有一条 `if`。

        钉这条的理由和上面那条一样：分支改了字段、hint 没跟上，字段就白修。
        默认池上 after 现在恒为 not-exact，所以这条同时也是"字段与自述不再相反"
        在真回包上的证据。
        """
        out = self._run_after(monkeypatch, POOL, days_ago(3))
        assert out.date_filter["exact"] is False
        hint = out.fetch_hint or ""
        assert "covers your window rather than matching it" in hint
        # 期4.4 之后要点名的是仍收不到过滤的那几家（baidu 已经在线路上了，再让它
        # 出现在这句里就是替一家已接线的引擎背一个它没有的缺口）。
        assert "so360" in hint, f"没点名的告诫等于没说: {hint!r}"

    def _run_after(self, monkeypatch, engines, after):
        out = self._run(monkeypatch, engines, after=after, freshness=None)
        assert out.date_filter["requested"] == f"after={after}", out.date_filter
        return out

    def test_a_fully_filterable_pool_is_not_spammed_with_a_caveat(
            self, monkeypatch):
        """反向半边：能过滤的池不许也吐一段告诫 —— 那会把信号磨成噪声。"""
        out = self._run(monkeypatch, ["bing"])
        assert out.date_filter["exact"] is True
        assert "no date filter" not in (out.fetch_hint or "")

    def test_the_similar_page_path_hears_it_too(self, monkeypatch):
        """find_similar 是第二条建 `fetch_hint` 的路，它曾经不接 `_date_note`。

        这条路把 `freshness=` 原样转发给引擎、也照样建 `date_filter`，而 hint 从
        `compute_fetch_hint` 直接起句 —— 同一个字段在 auto 上表态、在 find_similar
        上沉默。这类"两条路共用一份数据、只有一条读了它"正是上面那两条测试钉不到的。
        """
        from dhole_mcp import search

        async def fake_source(url, timeout=6):
            return {"title": "一个源页的标题",
                    "body": "源页正文，够长的一段文字用来派生查询词。" * 8,
                    "status": 200, "reason": "", "detail": ""}

        monkeypatch.setattr(search, "fetch_source_for_similar", fake_source)
        out = self._run(monkeypatch, ["yandex"],
                        mode="find_similar", url="https://example.com/src")
        hint = out.fetch_hint or ""
        # 先进了对的那条路，再谈它有没有表态：`rerank_mode` 在这里帮不上忙，
        # `_rank` 被换成了桩，它回什么 mode 就是什么。
        assert "find_similar to" in hint, f"根本没走到 find_similar 分支: {hint!r}"
        assert out.date_filter["exact"] is False, out.date_filter
        assert "no date filter" in hint, (
            f"date_filter 报了 not-exact 而这条路的 hint 没接上: {hint!r}")

    def test_no_window_report_can_omit_the_exact_key(self):
        """消费者那句默认值到不了，前提必须是每一条建 info 的路都写了这个键。

        这条不是装饰：它守的就是"缺键 fail-closed"这件事还成立的那半边 —— 哪天新增
        一条建 `date_filter` 的分支忘了带 `exact`，生产者侧的闸先红，而不是等到那份
        报告在调用方读成"按日期筛过"。`before=` 的分支返回空 dict，所以不进这个集合。
        """
        branches = [(None, None, "week", POOL), (None, None, "month", ["bing"]),
                    (None, None, "month", ["baidu"]), (None, None, "month", []),
                    (days_ago(3), None, None, POOL), (days_ago(3), None, "day", POOL),
                    (days_ago(800), None, None, POOL)]
        for args in branches:
            _applied, info, _why = _date_window(*args)
            assert info, args
            assert isinstance(info.get("exact"), bool), (args, info)


# ============================================================================
# 来源：test_a_stored_door_opens_on_every_door.py
# ============================================================================
# 存量 `allow_private` 是一把门，不是每扇门各自的一件私事（评审 R02-141 · F122）。

EVALS = Path(__file__).resolve().parent.parent / "evals"
SRC = Path(__file__).resolve().parent.parent / "src" / "dhole_mcp"

PRIVATE_TOKENS = ("private IP", "internal/private", "internal service",
                  "127.0.0.1 in 127.0.0.0/8")

_REAL_GETADDRINFO = socket.getaddrinfo


@pytest.fixture
def live_site(monkeypatch, tmp_path):
    """真起一台本机靶站（0 号端口），每条用例换一个端口 = 缓存键也换一个。"""
    import sys

    monkeypatch.setattr(socket, "getaddrinfo", _REAL_GETADDRINFO)
    sys.path.insert(0, str(EVALS))
    import fixture_site as fs

    monkeypatch.setattr(fs, "RUNTIME", tmp_path)
    monkeypatch.setattr(fs, "REQUESTS", tmp_path / "requests.log")
    srv = fs.make_server("127.0.0.1", 0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def no_engine_traffic(monkeypatch):
    """搜索扇不许因为这把刀就开始真的问引擎：任何一次 `multi_search` 都是失败。"""
    asked = []

    async def _spy(*a, **k):
        asked.append(a)
        return [], []

    monkeypatch.setattr(search_mod, "multi_search", _spy)
    return asked


def _say(out) -> str:
    """把这一发说过的话收成一段文本 —— 各扇的信封形状不同，判据只问说了什么。"""
    if isinstance(out, (list, tuple)):
        parts = []
        for item in out:
            if isinstance(item, dict):
                parts.extend(str(v) for v in item.values() if isinstance(v, str))
            else:
                parts.append(str(item))
        return " | ".join(parts)
    if isinstance(out, dict):
        return " | ".join(str(v) for v in out.values() if isinstance(v, str))
    return " | ".join(str(getattr(out, k, "") or "") for k in
                      ("error", "summary", "next_action", "message"))


def _face(name: str, base: str):
    """Drive one door at one loopback target and say whatever came back."""
    srv = MasterFetchServer()
    calls = {
        "web_read": lambda: srv.web_read(url=f"{base}/about"),
        "web_crawl": lambda: srv.web_crawl(url=f"{base}/collections?items=3",
                                           max_pages=2, max_depth=1),
        "web_discover": lambda: srv.web_discover(url=base),
        "feed_read": lambda: srv.feed_read(urls=[f"{base}/feed.xml"]),
        "web_search": lambda: srv.web_search(query="widgets", mode="find_similar",
                                             url=f"{base}/captcha"),
    }
    try:
        import asyncio

        return _say(asyncio.run(calls[name]()))
    except Exception as exc:  # a face that raises still owes the same sentence
        return f"{type(exc).__name__}: {exc}"


FACES = pytest.mark.parametrize("face", sorted(
    ("web_read", "web_crawl", "web_discover", "feed_read", "web_search")))


class TestOneJudgeOnEveryFace:
    """同一颗存量，在五扇上必须是同一个裁判。"""

    @FACES
    def test_without_the_stored_door_every_face_refuses_loopback(self, face, live_site):
        said = _face(face, live_site)
        assert any(t in said for t in PRIVATE_TOKENS), \
            f"{face} 在没有存量的时候竟然不拒回环：这一支是空的判据 {said!r}"

    @FACES
    def test_the_stored_door_opens_on_every_face(self, face, live_site,
                                                 no_engine_traffic):
        identity.set_value("allow_private", True)
        said = _face(face, live_site)
        assert not any(t in said for t in PRIVATE_TOKENS), \
            f"存了 allow_private 之后 {face} 仍按私网拒绝: {said!r}"
        assert no_engine_traffic == [], \
            f"{face} 为了放开回环开始问引擎了: {no_engine_traffic}"


class TestTheFaceThatNeverHadTheDoor:
    """搜索扇那条腿要走到它自己的那一面，不是含糊地"没拒"。"""

    def test_the_source_leg_reaches_the_page_it_came_to_read(self, live_site,
                                                             no_engine_traffic):
        identity.set_value("allow_private", True)
        said = _face("web_search", live_site)
        # 靶站 /captcha 那一面的自有判词（F121 立的那批 token）—— 到了这里才算真进门
        assert "bot_wall_detected" in said, said
        assert no_engine_traffic == []

    def test_the_same_url_without_the_door_stops_at_the_address(self, live_site,
                                                                no_engine_traffic):
        said = _face("web_search", live_site)
        assert "127.0.0.1 in 127.0.0.0/8" in said, said
        assert "bot_wall_detected" not in said


class TestTheCarveOutsStayShut:
    """收敛的是作用域，不是闸门的宽度（§12：安全闸不许因修而松）。"""

    @pytest.mark.parametrize("metadata", [
        "169.254.169.254", "100.100.100.200", "metadata.google.internal",
    ])
    def test_a_stored_door_naming_metadata_still_refuses_it(self, metadata):
        identity.set_value("allow_private", [metadata])
        with pytest.raises(security.SecurityError) as exc:
            security.validate_url(f"http://{metadata}/latest/meta-data/")
        assert "metadata" in str(exc.value).lower(), exc.value

    def test_the_loopback_branch_does_not_widen_to_the_whole_lan(self):
        identity.set_value("allow_private", True)
        with pytest.raises(security.SecurityError):
            security.validate_url("http://192.168.1.1/admin")

    def test_a_named_host_is_still_matched_literally(self):
        """点名 `127.0.0.1` 不等于放行八进制写法：那条路本来就是给绕过准备的。"""
        identity.set_value("allow_private", ["127.0.0.1"])
        hosts, loopback = security.private_hosts_allowed()
        assert "127.0.0.1" in hosts and loopback is False
        assert security._private_host_allowed("0177.0.0.1") is False

    def test_no_stored_value_leaves_the_default_of_reject_everything(self):
        assert security.private_hosts_allowed() == (frozenset(), False)
        with pytest.raises(security.SecurityError):
            security.validate_url("http://127.0.0.1/")


class TestOneReaderOfTheStoredValue:
    """存量值只许有一位读者：守卫。谁再抄一次 push，这一支就红。"""

    def test_the_guard_reads_the_store_with_no_scope_pushed(self):
        """没有门推作用域时，`private_hosts_allowed()` 自己就把存量并进来。"""
        identity.set_value("allow_private", ["my-box.local"])
        hosts, _ = security.private_hosts_allowed()
        assert "my-box.local" in hosts, hosts

    def test_env_and_store_and_a_scope_all_merge_into_one_answer(self):
        identity.set_value("allow_private", ["from-the-store.local"])
        with security.allow_private_scope(["from-the-scope.local"]):
            hosts, _ = security.private_hosts_allowed()
        assert {"from-the-store.local", "from-the-scope.local"} <= hosts, hosts

    def test_the_store_is_not_read_into_a_scope_anywhere_in_src(self):
        """`set_allow_private` / `allow_private_scope` 在 src 里只许住在定义它的那一页。

        判据按 AST 的调用名数，不按文本 —— 注释与文档里提它不算用它。修之前这一支红在
        四处（crawl.py 一处、server.py 三处）：那四处是同一件事的四个名字，第五扇
        （`web_search`）就是它们中间漏掉的那一个。
        """
        pushers = []
        for path in sorted(SRC.glob("*.py")):
            if path.name == "security.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id in ("set_allow_private",
                                             "allow_private_scope")):
                    pushers.append(f"{path.name}:{node.lineno}")
        assert pushers == [], f"作用域又被抄了一次: {pushers}"

    def test_the_store_has_exactly_one_reader_of_allow_private(self):
        """读存量这件事在 src 里只许出现在守卫那一步。"""
        readers = []
        for path in sorted(SRC.glob("*.py")):
            src = path.read_text(encoding="utf-8")
            for needle in ('get("allow_private")', "get('allow_private')"):
                if needle in src:
                    readers.append(path.name)
                    break
        assert readers == ["security.py"], readers


class TestTheStoredValueThatDoesNothingIsStillSaid:
    """删掉门侧那一次读，不许把「你存的这颗没用」那句话一起删掉。"""

    def test_a_value_that_parses_to_nothing_opens_nothing(self, live_site):
        """`[]` 并进门里也仍然是一个都不放 —— 合并不是"存了什么都算开门"。"""
        identity.set_value("allow_private", [])
        hosts, loopback = security.private_hosts_allowed()
        assert hosts == frozenset() and loopback is False
        assert any(t in _face("web_read", live_site) for t in PRIVATE_TOKENS)

    def test_the_note_is_still_said_when_the_call_gets_through(self, live_site):
        """存量值成不成立与这一次发不发得出去是两句话，前者不许被后者吃掉。

        这一发走的是作用域那层机制开的门（存量 `[]` 什么也没开），所以正文取回来了，
        而「你存的那颗没用」仍然要在 summary 里说 —— 这一支钉的是"门的读者换了人，
        报账的那一句还活着"。
        """
        import asyncio

        identity.set_value("allow_private", [])
        with security.allow_private_scope(True):
            out = asyncio.run(MasterFetchServer().web_read(url=f"{live_site}/about"))
        assert not out.error, out.error
        said = _say(out)
        assert "does nothing" in said, said
        assert "true (loopback only)" in said, said


class TestTheMechanismItselfIsStillUsable:
    """`allow_private_scope` 没有被这次收敛废掉：它仍是就地开一层的机制。"""

    def test_a_scope_without_any_stored_value_still_opens_its_host(self):
        with pytest.raises(security.SecurityError):
            security.validate_url("http://10.0.0.5/api")
        with security.allow_private_scope(["10.0.0.5"]):
            assert security.validate_url("http://10.0.0.5/api")
        with pytest.raises(security.SecurityError):
            security.validate_url("http://10.0.0.5/api")


class TestTheLawIsStatedWhereTheDoorSpeaks:
    """手册与出路句子不许再把它说成"抓取那几扇才有的一颗参数"。"""

    def test_no_manual_page_calls_it_a_fetch_only_option(self):
        skills = (SRC / "skills" / "dhole-web" / "references")
        offenders = []
        for path in sorted(skills.glob("*.md")):
            text = path.read_text(encoding="utf-8").lower()
            if "allow_private" in text and any(
                    phrase in text for phrase in
                    ("only web_read", "only for web_read", "fetch tools only")):
                offenders.append(path.name)
        assert offenders == [], offenders

    def test_the_guard_side_helper_is_the_one_that_documents_this(self):
        doc = inspect.getdoc(security.private_hosts_allowed) or ""
        assert doc, "守卫这一步的合并必须有自己的一句话"
        assert "存量" in doc or "cred_defaults" in doc, doc


# ============================================================================
# 来源：test_a_date_claim_blames_the_side_that_is_ours.py
# ============================================================================
# "这家不能按日期过滤" 是一个关于第三方索引的说法，而 dhole 只知道自己发出去什么（S8-full）。
# 重名消歧：POOL → POOL__a_date_claim_blames_the__3949

POOL__a_date_claim_blames_the__3949 = list(DEFAULT_ENGINES)


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
        note = _note(POOL__a_date_claim_blames_the__3949)
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
        pools = [POOL__a_date_claim_blames_the__3949, ["bing"], ["baidu", "yandex"], ["sogou"], ["so360", "bing"], []]
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
        _aware, blind = split_date_blindness([n for n in POOL__a_date_claim_blames_the__3949 if n in _DATE_PARAM_UNWIRED]
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
        _a, info, _w = _date_window("2020-01-01", None, None, POOL__a_date_claim_blames_the__3949)
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
        _a, info, _why = _date_window(None, "2026-01-01", None, POOL__a_date_claim_blames_the__3949)
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
