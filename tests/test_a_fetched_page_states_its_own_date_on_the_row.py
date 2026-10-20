# -*- coding: utf-8 -*-
"""抓回来的那一页自己写了日期，这一行就要把源站的说法与引擎的说法并排放着。

期4 前四刀让 `published` 成为引擎替一行说的话，而 `date_coverage.rows_verified_by_fetch`
当时是一颗**永远报 0** 的取值 —— 字段与账本的键都写好了，没有一条路把它写成非零。
那一族的教训在 `quality._substance_verdict` 里记着：一个声明了却在线上取不到的取值
比少一个取值更糟，读手册的人会以为判过了。这一刀把它变成真话。

判据的形状：

1. **说法要分家，不许合并。** `published` 是引擎版面说"这行是这天的"；
   `published_verified` 是**源站自己**在那一页上写的发表日期。两者不同时两个都留在行上
   —— 核验从不覆盖引擎的说法，覆盖就是把一次分歧抹平成一句谎。
2. **只用页面自述的那一颗。** `creation_date` / `modified_time` 都不叫"发表"：协作站建页
   日期与"这页什么时候写的"是两件事（`envelope.compute_freshness` 为同一个理由把 wiki 的
   age 报成 null），"最后改过"更不是。
3. **空串合并两种"没核验到"**（那一行没被取回 / 那页没写发表日期），两者都**不是**"这页
   没有日期"，也不许被 `before=` 拿去丢行 —— 页面上的一行字不能证明一件事不存在。
4. **账本要跟着重算。** `date_coverage` 在建响应模型时算过一次，而"有几行的源站报了日期"
   只有 server 那一侧的扇出跑完才存在（扇出才有 `web_read`）。这一格由
   `serp_dates.recount_verified` 在抓完之后落账，判决本身仍只有一位作者。
5. **什么都没核验到时不凭空写账**：一份为此而生、却全是零的账本等于宣布"判过了"。
"""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock

import pytest

from dhole_mcp import search as search_mod
from dhole_mcp import serp_dates, wire
from dhole_mcp.search import SearchResult, recount_row_dates
from dhole_mcp.search_engines import EngineReport, RawResult
from dhole_mcp.server import MasterFetchServer, ResponseModel

# ─── 1 · 只读页面自述的那一颗 ────────────────────────────────────

def test_the_pages_own_published_time_is_read_with_the_same_floor_as_published():
    assert serp_dates.page_stated_date(
        {"published_time": "2024-11-22T08:00:00+08:00"}) == "2024-11-22"
    assert serp_dates.page_stated_date({"published_time": "2024-11-22"}) == "2024-11-22"
    # 页面只写到月：值落 1 号。那是**帽**不是声明，而两颗日期字段共用同一个约定 ——
    # 各写各的精度口径，读者就得先猜它们是不是同一把尺。
    assert serp_dates.page_stated_date({"published_time": "2024-11"}) == "2024-11-01"


def test_the_two_date_fields_state_one_floor_convention():
    """两颗字段的说明必须说同一套精度口径（这一条钉的是文案，不是算术）。"""
    for name in ("published", "published_verified"):
        desc = SearchResult.model_fields[name].description.lower()
        assert "floor" in desc and "1st" in desc, f"{name} 的说明没写帽这一约定"


def test_a_page_that_states_nothing_verifies_nothing():
    for meta in ({}, {"published_time": ""}, {"published_time": "not a date"},
                 {"published_time": 1732233600}, None):
        assert serp_dates.page_stated_date(meta) == "", meta


def test_the_creation_date_and_the_modified_time_are_not_offered_as_publish_dates():
    """一张 wiki 的建页日期、一篇改过无数次的 `modified_time`，都不回答"这页什么时候
    发表"。把它们当核验，就是把"页面上有个日期"说成"源站声明了发表日"。"""
    assert serp_dates.page_stated_date({"creation_date": "2019-03-01"}) == ""
    assert serp_dates.page_stated_date({"modified_time": "2026-01-05"}) == ""


# ─── 2 · 账本跟着重算，而判决仍只有一位作者 ───────────────────────

def _rows(n: int = 3) -> list[SearchResult]:
    return [SearchResult(title=f"t{i}", url=f"https://a{i}.test/x") for i in range(n)]


def test_an_empty_ledger_gains_the_whole_account_when_a_source_date_appears():
    rows = _rows()
    rows[0].published = "2024-11-22"
    rows[0].published_verified = "2024-11-22"
    led = recount_row_dates(rows, {})
    assert led["rows_total"] == 3
    assert led["rows_with_date"] == 1 and led["rows_no_date"] == 2
    assert led["rows_verified_by_fetch"] == 1


def test_the_before_partition_stands_and_only_the_verified_column_moves():
    """问过 `before=` 的那一轮：丢了几行是**已经发生过的判决**，重算不许改历史。"""
    cutoff = date(2024, 1, 1)
    rows = _rows()
    rows[0].published, rows[0].published_precision = "2023-05-05", "day"
    rows[1].published, rows[1].published_precision = "2024-06-06", "day"
    rows[2].published_precision = ""
    _kept, ledger = serp_dates.partition_for_before(
        search_mod._date_rows_dates(rows), cutoff)
    assert ledger["rows_dropped_by_before"] == 1, ledger
    assert ledger["rows_kept"] == 2, ledger

    rows[2].published_verified = "2024-06-06"
    after = recount_row_dates(rows, ledger)
    assert after["rows_verified_by_fetch"] == 1
    for key in ("rows_dropped_by_before", "rows_kept", "rows_total", "rows_no_date"):
        assert after[key] == ledger[key], f"重算动了历史那一格: {key}"


def test_a_verified_date_is_never_an_input_to_the_before_verdict():
    """核验是"这一行的说法有了出处"，不是"这一行更该被留下"。丢行只读 `published`
    那一族（4.6 的判据），所以给一行加上源站日期不许改变它的去向：一条证明晚于截止的
    行照旧被丢，哪怕它的源站自己报了日期。"""
    cutoff = date(2024, 1, 1)
    plain = SearchResult(title="t", url="https://a.test/x", published="2025-05-05",
                         published_precision="day")
    verified = SearchResult(title="t", url="https://a.test/x", published="2025-05-05",
                            published_precision="day", published_verified="2025-05-05")
    assert serp_dates.before_verdict(search_mod._row_date(plain), cutoff) == "drop"
    assert serp_dates.before_verdict(search_mod._row_date(verified), cutoff) == "drop"


# ─── 3 · 线上那一条路：真的走完 server.web_search 的扇出 ──────────

def _fake_search(dates: list[str], engines=("brave", "bing", "baidu", "so360")):
    async def fake_multi_search(query, max_results, **kwargs):
        rows = [RawResult(title=f"固态电池 量产 {i}",
                          url=f"https://a{i}.example.test/doc",
                          snippet="固态电池 量产 公告", source="bing", position=i + 1,
                          consensus=2, published=dates[i],
                          published_precision="day" if dates[i] else "")
                for i in range(len(dates))]
        return rows, [EngineReport(name=e, ok=True) for e in engines]
    return fake_multi_search


def _round(monkeypatch, metadata: dict, dates: list[str] | None = None):
    monkeypatch.setattr(search_mod, "multi_search", _fake_search(dates or ["2024-11-20"]))
    monkeypatch.setattr(search_mod, "ensure_reranker", AsyncMock(return_value=None))
    server = MasterFetchServer()
    server.web_read = AsyncMock(return_value=ResponseModel(
        url="u", status=200, content=["固态电池量产公告的正文一页。"], content_ok=True,
        metadata=metadata))
    return server


@pytest.mark.asyncio
async def test_a_fetched_page_writes_its_own_date_onto_its_row(monkeypatch):
    out = await _round(monkeypatch, {"published_time": "2024-11-22T08:00:00Z"}
                       ).web_search("固态电池 量产", fetch_content=True, cache_ttl=0)

    assert out.results[0].published_verified == "2024-11-22"
    assert out.date_coverage["rows_verified_by_fetch"] == 1
    assert out.date_coverage["rows_total"] == 1


@pytest.mark.asyncio
async def test_verification_never_overwrites_what_the_engine_said(monkeypatch):
    """两说不同时，行上必须两个都在 —— 把源站的日期写进 `published` 就是替引擎改口。"""
    out = await _round(monkeypatch, {"published_time": "2024-11-22"}
                       ).web_search("固态电池 量产", fetch_content=True, cache_ttl=0)

    assert out.results[0].published == "2024-11-20"
    assert out.results[0].published_verified == "2024-11-22"


@pytest.mark.asyncio
async def test_a_fetched_page_without_a_stated_date_verifies_nothing(monkeypatch):
    out = await _round(monkeypatch, {}).web_search("固态电池 量产",
                                                   fetch_content=True, cache_ttl=0)
    assert out.results[0].published_verified == ""
    assert out.date_coverage["rows_verified_by_fetch"] == 0, "账本不跟着动就是假账"


@pytest.mark.asyncio
async def test_a_round_that_fetched_nothing_writes_no_date_ledger(monkeypatch):
    """形状 5：`fetch_content` 没开时，那一格维持它本来的样子（这里没有别的日期可说，
    所以账本缺席）—— 凭空写一份全零的账就是替读者宣布"判过了"。"""
    monkeypatch.setattr(search_mod, "multi_search", _fake_search([""]))
    monkeypatch.setattr(search_mod, "ensure_reranker", AsyncMock(return_value=None))
    out = await MasterFetchServer().web_search("固态电池 量产", cache_ttl=0)

    assert out.results[0].published_verified == ""
    assert out.date_coverage == {}
    assert "date_coverage" not in wire._wire_dump(out)
    assert "published_verified" not in wire._wire_dump(out.results[0]), "干净行要零字节"
