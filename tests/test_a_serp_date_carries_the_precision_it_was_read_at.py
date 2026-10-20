# -*- coding: utf-8 -*-
"""SERP 上那枚日期章要带着它的精度一起走 —— 否则 "3 hours ago" 会被存成一句精确的谎。

为什么需要（实测，不是假想）：`tests/engine_fixtures/` 那份取证（`.scratch/date_yield_probe.py`，
2026-10-10）量到日期**真的在版面上**：sogou 8 条容器里 **8 条**带日期（`span.cite-date`），
baidu 5 / 6 条容器里各 2 条（`div.cos-row`、`div.cosc-card-footer`），so360 7 条里 1 条，
bing_global 10 条里 1 条 —— 而那一条写的是 **"… ago"**（`span.news_dt`，而且已经在摘要里）。
今天这些字**一个都没被取过**：`TextResult` 只有 title/href/body。

反过来说明精度为什么不能省：把 "2 days ago" 记成 `2026-10-08`，读者读到的是一个看起来
到日的日期，而它的真值取决于"这一轮是什么时候跑的"。4.6 的 `before=` 要逐行拒的就是这种
行 —— 拒得对的前提是精度跟着值一起出口，而不是一句整体拒绝。
"""

from __future__ import annotations

from datetime import date

from dhole_mcp.envelope import parse_serp_date

NOW = date(2026, 10, 10)


# ─── 相对写法：永远不许冒充日精度 ────────────────────────────────

def test_an_hours_ago_stamp_is_relative_not_a_day():
    d, precision, matched = parse_serp_date("3 hours ago", now=NOW)
    assert d == NOW
    assert precision == "relative"
    assert matched == "3 hours ago"


def test_a_chinese_relative_stamp_resolves_to_the_same_precision():
    d, precision, matched = parse_serp_date("5天前", now=NOW)
    assert d == date(2026, 10, 5)
    assert precision == "relative"
    assert "5天前" in matched


def test_the_chinese_forms_without_a_number_still_say_relative():
    assert parse_serp_date("昨天", now=NOW)[:2] == (date(2026, 10, 9), "relative")
    assert parse_serp_date("前天", now=NOW)[:2] == (date(2026, 10, 8), "relative")
    assert parse_serp_date("刚刚", now=NOW)[:2] == (NOW, "relative")


def test_weeks_and_months_and_years_are_relative_with_their_own_offsets():
    assert parse_serp_date("2 weeks ago", now=NOW)[0] == date(2026, 9, 26)
    assert parse_serp_date("1 month ago", now=NOW)[0] == date(2026, 9, 10)
    assert parse_serp_date("1 year ago", now=NOW)[0] == date(2025, 10, 10)
    assert all(parse_serp_date(s, now=NOW)[1] == "relative"
               for s in ("2 weeks ago", "1 month ago", "1 year ago"))


def test_relative_arithmetic_does_not_read_the_wall_clock():
    """`now` 可注入是这条守卫存在的前提：读今天的日期会让这一族测试在跨月的凌晨自己变红。"""
    a = parse_serp_date("10 days ago", now=date(2026, 3, 5))
    b = parse_serp_date("10 days ago", now=date(2026, 12, 31))
    assert a[0] == date(2026, 2, 23) and b[0] == date(2026, 12, 21)
    assert a[1] == b[1] == "relative"


# ─── 绝对写法：精度到日，并把原文带出来 ──────────────────────────

def test_the_iso_stamp_keeps_day_precision_and_quotes_where_it_came_from():
    d, precision, matched = parse_serp_date("2026-10-01", now=NOW)
    assert (d, precision) == (date(2026, 10, 1), "day")
    assert matched == "2026-10-01"


def test_the_chinese_absolute_stamp_is_a_day_not_a_month():
    """`2026年10月1日` 不许被"只到月"那条规则截走 —— 那是把一个已知的事实改写成猜测。"""
    assert parse_serp_date("2026年10月1日", now=NOW)[:2] == (date(2026, 10, 1), "day")
    assert parse_serp_date("2026-10-01 12:00:00", now=NOW)[1] == "day"


def test_the_english_absolute_stamps_are_days():
    for text, want in (("March 14, 2026", date(2026, 3, 14)),
                       ("14 March 2026", date(2026, 3, 14)),
                       ("Mar 14, 2026", date(2026, 3, 14))):
        d, precision, _m = parse_serp_date(text, now=NOW)
        assert (d, precision) == (want, "day"), text


# ─── 只到月与只到年：补出来的那位是下界，不是断言 ────────────────

def test_a_month_only_stamp_is_a_month_with_the_day_at_its_floor():
    for text in ("2026-03", "2026年3月", "2026.3"):
        d, precision, _m = parse_serp_date(text, now=NOW)
        assert (d, precision) == (date(2026, 3, 1), "month"), text


def test_a_month_named_in_words_is_a_month():
    assert parse_serp_date("March 2026", now=NOW)[:2] == (date(2026, 3, 1), "month")
    assert parse_serp_date("Oct 2026", now=NOW)[:2] == (date(2026, 10, 1), "month")


def test_a_bare_year_is_a_date_only_when_the_text_is_nothing_but_the_year():
    """整串就是一个年份才认 —— "See 1984 by Orwell" 不是一条发布日期。"""
    d, precision, _m = parse_serp_date("2026", now=NOW)
    assert (d, precision) == (date(2026, 1, 1), "year")
    assert parse_serp_date("See 1984 by Orwell", now=NOW)[0] is None
    assert parse_serp_date("£45.17 in stock, updated 2019 edition", now=NOW)[0] is None


def test_a_future_year_is_not_a_publish_date():
    assert parse_serp_date("2031", now=NOW)[0] is None


# ─── 没有日期时不发明一个 ────────────────────────────────────────

def test_text_without_a_date_returns_no_date_no_precision_and_no_quote():
    for text in ("", "   ", "In stock", "£45.17", "首页 关于 登录"):
        d, precision, matched = parse_serp_date(text, now=NOW)
        assert (d, precision, matched) == (None, "", ""), text


def test_the_precisions_are_the_four_declared_ones():
    """手册与 `before=` 的逐行判决读的就是这四个取值；第五种取值是契约外的东西。"""
    seen = {parse_serp_date(t, now=NOW)[1] for t in (
        "2026-10-01", "3 hours ago", "2026年3月", "2026", "no date here")}
    assert seen == {"day", "relative", "month", "year", ""}
