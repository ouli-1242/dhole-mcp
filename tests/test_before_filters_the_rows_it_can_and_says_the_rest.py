# -*- coding: utf-8 -*-
"""`before=` 从此按行判决，而判决的边界由**精度**守着：能判的才判，判不了的留下并说出来。

前值（实测，2026-10-10）：`before=<date>` 是**整颗参数被拒**的，理由写在 `search.py:817`
与它的参数描述里 —— "search results carry no publish date"。那句话当时是真话，因为
`TextResult` 只有 title/href/body。4.1-4.3 之后重量一遍（冻存的各家 SERP 字节走**真
parser + 真盖章那一步**，落在行上的 `published` 才算数）：sogou 8 行里 **4** 行、baidu
两个版面 5 行里 **2** / 6 行里 **2**、bing 一个版面 4 行里 **4**、另一个版面 4 行里
**1**、bing_global 10 行里 **1** 且那一行是相对（"1 day ago"），而 so360 与 yandex
各 2 / 4 行**一行都没有**。版面那一层还有更多日期文本（`.scratch/date_yield_probe.py`
量的是各家容器里的日期形状：sogou 8 个容器 8 个有），差别在抽取器的日期选择器还没覆盖
到那些位置 —— 这一族因此是"部分行有值"而不是全有或全无，`date_coverage` 说的正是这个
稀疏程度。

所以"整颗拒"变成了两件事：能对哪几行就 filter 哪几行，剩下的**大声说出来**。丢一行是不可
逆的动作 —— 读者不会在别处遇见那一行 —— 于是只有精度内不留歧义时才允许丢：`2026-03`
早于 `2026-10-01` 是事实，`2026-10` 与 `2026-10-01` 谁先谁后取决于被抹掉的那一位，那种
行不许丢，也不许假装它比过了。

**方向（这一条搬过一次，搬的理由在这里）**：第一版把丢掉的那一侧写成了"比 cutoff 更早
的行"，而 `before=` 是一轮的**上界** —— 它与 `after=` 成对（那颗是下界），也正是所有
搜索 API 里 `before:` 的意思：要"发表早于这天"的行了。写反的那一版丢掉的恰好是调用方
要的那批，而回包字面上仍然像"过滤过了"：`rows_dropped_by_before` 有数、`summary` 重复了
那个数、`exact:false` 也写着 —— 一个看起来执行了的反向判决，比一次诚实的拒绝更难被发现。
今天钉的是：留下早于 cutoff 的，丢掉证明晚于它的，两边都证明不了的留下并计入
`unverifiable`。同一颗 `after=` 的文案（"For 'newer than' use after=<date>"）与旧的拒辞
（"presets only bound 'not older than'"）都早就写着这个方向；错的是谓词，不是说法。
"""

from __future__ import annotations

from datetime import date

from dhole_mcp.serp_dates import (DROP, KEEP, UNVERIFIABLE, RowDate, before_verdict,
                                  coverage, describe_ledger, partition_for_before,
                                  read_row)

CUTOFF = date(2026, 10, 1)
NOW = date(2026, 10, 10)


def _day(text: str) -> RowDate:
    return RowDate(value=date.fromisoformat(text), precision="day", basis=text)


# ─── 1 · 能判才判 ────────────────────────────────────────────────

def test_the_only_row_dropped_is_the_one_provably_newer_than_the_cutoff():
    assert before_verdict(_day("2026-09-30"), CUTOFF) == KEEP
    assert before_verdict(_day("2026-10-02"), CUTOFF) == DROP
    # `before` 是严格早于：落在截止那一天的行不在窗口内，它被丢而不是被留。
    assert before_verdict(_day("2026-10-01"), CUTOFF) == DROP


def test_a_month_wholly_before_the_cutoff_is_kept_and_a_month_wholly_after_it_drops():
    older = RowDate(value=date(2026, 3, 1), precision="month", basis="2026-03")
    later = RowDate(value=date(2026, 12, 1), precision="month", basis="2026-12")
    # 十一月的哪一天被抹掉了，而截止落在月内 —— 两边都证明不了，留下并说出来。
    spanning = RowDate(value=date(2026, 10, 1), precision="month", basis="2026-10")
    assert before_verdict(older, CUTOFF) == KEEP
    assert before_verdict(later, CUTOFF) == DROP
    assert before_verdict(spanning, date(2026, 10, 15)) == UNVERIFIABLE
    # 截止正好落在月初时不再"模糊"：十月没有任何一天早于 10-01，这一行是**证明**晚于。
    assert before_verdict(spanning, CUTOFF) == DROP


def test_a_year_wholly_before_the_cutoff_is_kept_and_a_year_after_it_drops():
    assert before_verdict(RowDate(value=date(2020, 1, 1), precision="year",
                                  basis="2020"), CUTOFF) == KEEP
    assert before_verdict(RowDate(value=date(2027, 1, 1), precision="year",
                                  basis="2027"), CUTOFF) == DROP
    assert before_verdict(RowDate(value=date(2026, 1, 1), precision="year",
                                  basis="2026"), CUTOFF) == UNVERIFIABLE


def test_a_relative_row_is_never_dropped_and_never_counted_as_compared():
    row = read_row("5天前", now=NOW)          # 2026-10-05，比 cutoff 晚
    assert (row.precision, row.value) == ("relative", date(2026, 10, 5))
    assert before_verdict(row, CUTOFF) == UNVERIFIABLE


def test_a_row_without_a_date_is_kept_and_says_so():
    assert before_verdict(RowDate(), CUTOFF) == KEEP
    assert read_row("In stock, £45.17") == RowDate()


# ─── 2 · 丢了多少要当场结清 ──────────────────────────────────────

def _mixed() -> list[RowDate]:
    return [_day("2020-08-31"), _day("2026-11-02"),
            RowDate(value=date(2026, 10, 1), precision="month", basis="2026-10"),
            read_row("3 hours ago", now=NOW),
            RowDate(),
            RowDate(value=date(2024, 5, 1), precision="day", basis="2024-05-01",
                    verified_by_fetch=True)]


def test_the_ledger_adds_up_and_names_every_class_of_row():
    # 这一族要同时摆出五种行，所以截止取在十月**之中**（10-15）：那个月的行落在窗口两侧，
    # 只能算"判不了"；取在月初时它变成"证明晚于"，那一格就再没有被验到过。
    kept, ledger = partition_for_before(_mixed(), date(2026, 10, 15))
    assert ledger["rows_total"] == 6
    assert ledger["rows_with_date"] == 5
    assert ledger["rows_no_date"] == 1
    assert ledger["rows_dropped_by_before"] == 1          # 只有那一条 day 精度且更晚
    assert ledger["rows_relative_precision"] == 1
    assert ledger["rows_unverifiable_by_before"] == 2     # month 同月 + relative
    assert ledger["rows_kept"] == len(kept) == 5
    assert ledger["rows_total"] - ledger["rows_dropped_by_before"] == ledger["rows_kept"]
    assert ledger["rows_verified_by_fetch"] == 1
    # 留下的那一侧必须是"早于截止"的那批：这一轮里被保留的两条 day 行都在这条线之前。
    assert [r.value for r in kept if r.precision == "day"] == [date(2020, 8, 31),
                                                               date(2024, 5, 1)]


def test_the_saying_names_the_dropped_count_before_anything_else():
    _k, ledger = partition_for_before(_mixed(), date(2026, 10, 15))
    line = describe_ledger(ledger, "2026-10-15")
    assert line.split(";")[0].endswith("5 carry a publish date")
    assert "1 dropped as newer than before=2026-10-15" in line
    assert "2 kept because their date is too coarse or relative" in line
    assert "1 kept because they carry no date at all" in line


def test_a_round_that_was_not_filtered_reports_no_drops_and_no_verdicts():
    """`coverage()` 是"没要 before 的那一轮"：那一轮里不许出现"被日期判过"的计数。"""
    led = coverage(_mixed())
    assert led["rows_dropped_by_before"] == 0
    assert led["rows_unverifiable_by_before"] == 0
    assert led["rows_relative_precision"] == 1
    assert led["rows_with_date"] == 5


# ─── 3 · 一行日期的三颗字段：值 + 精度 + 出处，无日期时全空 ──────

def test_the_three_fields_travel_together_or_not_at_all():
    assert read_row("5天前", now=NOW).as_fields() == {
        "published": "2026-10-05", "published_precision": "relative",
        "published_basis": "5天前"}
    assert RowDate().as_fields() == {"published": "", "published_precision": "",
                                     "published_basis": ""}


def test_the_row_reader_is_the_same_author_as_the_string_parser():
    """这里不许出现第二套日期解析 —— 行级读数与信封上的读数不能对同一枚章说两种话。"""
    from dhole_mcp.envelope import parse_serp_date
    text = "2024-11-22"
    assert read_row(text) == RowDate(*parse_serp_date(text, now=NOW)[:3])
