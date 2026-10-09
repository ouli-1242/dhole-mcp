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

同一条理由管着**逐条刮发布日期再过滤**：那也是个"没做"，而响应不许把它说成"这家不能
按日期过滤"。收不到日期过滤的引擎分两种责任方（`search_engines._DATE_PARAM_UNWIRED`）：
一种确实没有可登记的日期参数，话只说"线路里没发"；另一种的参数是 dhole 自己的注释点名
存在、却没接线的（baidu 的 gpc/stf），那就写成我们的缺口。替第三方断言能力，和替它
掩盖我们的缺口，是同一句谎的两种拼法。

`exact` 曾经是一颗硬编的字面量（freshness 分支）或只看"发出去的那一档等于最窄覆盖档"
（after 分支）。两种都在同一件事上说谎：默认池里 baidu/so360/sogou/yandex **收了同样
的查询、一个字都没按日期过滤**，而那颗字段自述（`SearchResponseModel.date_filter`）写的
恰恰是「a mixed round is NOT a date-limited result set」。现在两条分支用同一个定义，
且 `note` 非空 ⟺ `exact` 为 false —— 见 `TestTheWindowTranslation` 的不变量那条。
"""

from __future__ import annotations

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

    def test_the_default_pool_split_is_what_the_report_measured(self):
        filtered, unfiltered = date_support(POOL)
        assert filtered == ["bing", "bing_global", "brave", "duckduckgo"]
        assert unfiltered == ["baidu", "so360", "sogou", "yandex"]

    def test_an_unknown_engine_counts_as_unfiltered(self):
        assert date_support(["newengine"]) == ([], ["newengine"])


class TestTheWindowTranslation:

    def test_a_three_day_window_gets_the_week_preset(self):
        applied, info, why = _date_window(days_ago(3), None, None, POOL)
        assert why == ""
        assert (applied, info["sent"]) == ("week", "freshness=week")
        assert info["engines_filtered"] == ["bing", "bing_global", "brave",
                                            "duckduckgo"]
        assert "baidu" in info["engines_unfiltered"]

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
        assert "baidu" in info["engines_unfiltered"]
        assert info["exact"] is False, (
            "有引擎根本没收到日期过滤却仍然应答了，这一轮就不是 exact")
        assert "no date filter" in info["note"]
        assert "baidu" in info["note"]

    def test_a_pool_that_can_all_be_filtered_is_exact(self):
        """正向半边：exact 不许被改成永远 False —— 那只是把谎换成了另一个方向。

        只留一个能过滤的引擎：没有 unfiltered，窗口就是结果集真正满足的窗口。
        """
        _applied, info, _why = _date_window(None, None, "week", ["bing"])
        assert info["engines_unfiltered"] == []
        assert info["exact"] is True, "全能过滤还报 not exact，等于把告诫变成噪声"
        assert info["note"] == ""

    def test_a_pool_of_only_date_blind_engines_names_them(self):
        """全都收不到过滤：告诫要点名，而且**两种责任方要分开说**（S8-full）。

        sogou 是"线路里没有这一说"，baidu 是"dhole 自己没接线它的 gpc/stf"。把后者也
        写成前者的措辞，就是替一个第三方断言它做不到的事，而 dhole 的代码注释里明明
        写着那个参数存在 —— 声明与线路必须一致。
        """
        _applied, info, _why = _date_window(None, None, "month", ["baidu", "sogou"])
        assert info["engines_filtered"] == []
        assert info["exact"] is False
        assert "dhole sent sogou no date filter" in info["note"], info["note"]
        assert "dhole has not wired a date filter for baidu (gpc/stf)" in info["note"]
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

    def test_before_comes_back_as_a_reason_not_a_silent_no_op(self):
        applied, info, why = _date_window(None, days_ago(30), None, POOL)
        assert (applied, info) == (None, {})
        assert "before is not supported" in why
        assert "feed_read(since=" in why

    def test_the_alternative_is_named(self):
        _a, _i, why = _date_window(None, "2026-01-01", None, POOL)
        assert "after=" in why

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
        # before 要说清是被拒而不是被忽略，并给出替代路径。
        assert "date_filter" in props["after"]["description"], "after= 的窗口语义没上线"
        assert "REFUSED" in props["before"]["description"], "before= 被拒的说明没上线"
        assert "feed_read" in props["before"]["description"], "没给出带日期条目的替代路径"

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
        assert "baidu" in hint, f"没点名的告诫等于没说: {hint!r}"

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
            return "一个源页的标题", "源页正文，够长的一段文字用来派生查询词。" * 8

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
