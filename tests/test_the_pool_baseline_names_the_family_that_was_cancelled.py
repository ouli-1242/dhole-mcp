"""池子基线要把"被取消的那一家"点出来，而不是把它算成一家没结果的引擎。

这条判据是 `review/2026-10-10-用户体验模拟报告.md` 里一整轮体测的来源：`consensus_basis`
报 `1 of 6 (degraded_pool)`，读的人分不清另外五家是**拒答**、**被取消**、还是**被地域挡**
—— 而三种原因的修法互相排斥（拒答要换档、被取消要抬那 2.0 秒的软截止、地域要换 URL 或代理）。
`engine_preempted` 这个字段早就在响应上了（体验修复刀2-S7 把它从 degraded 的分母里扣出去
过），可它只进 `next_action` 的一句话，不进任何一张能比较的表。

`evals/pool_baseline.py` 就是那张表。它是运行期量具（要联网），所以**本文件离线**：量的
是它的两个纯函数怎么归桶、怎么说话。联网那一步的产物是 JSON，不在 CI 里复现。

四件事：

1. 被取消的一家落在 `cancelled` 桶，不许混进 `empty`（答了而没结果）或 `blocked`（答不了）
   —— 这是那三种修法能被分开的前提。
2. 一句话里同时给出桶与 `engine_health()` 的 verdict，读的人不必再去开第二个工具。
3. 计数是**累加**的：一家在第 1 轮答了、第 2 轮被取消，两笔都在；后写覆盖前写会让"这轮
   到底谁没说话"随轮次顺序变。
4. 题集是 `evals/queries.jsonl` 那份 72 题活集，与**产出**排名基线语料的 `capture_serp.py`
   读同一颗路径。两条基线量同一批问题，否则一次池子变化可以被读成一次排名变化。
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "evals"))

import pool_baseline as PB  # noqa: E402


def _row(**updates) -> dict:
    row = {"id": "q001", "ms": 900.0, "results": 10, "basis": "degraded_pool",
           "used": ["bing", "brave"], "blocked": ["mojeek"],
           "empty": [], "preempted": ["yahoo_jp"], "error": ""}
    row.update(updates)
    return row


# ─── 1 · 四桶各归各的 ────────────────────────────────────────────

def test_a_cancelled_family_is_named_as_cancelled_not_as_a_missing_result():
    tally = PB.family_tally([_row()])
    assert tally["yahoo_jp"]["cancelled"] == 1, tally
    assert not tally["yahoo_jp"]["empty"] and not tally["yahoo_jp"]["blocked"], tally
    assert tally["mojeek"]["blocked"] == 1, tally
    assert tally["bing"]["answered"] == 1, tally


def test_the_three_ways_to_be_absent_stay_three_sentences():
    """一家"这轮没带回结果"有三种成因，而修法是互斥的 —— 话术也不许并成一句。"""
    health = {"yahoo_jp": {"verdict": "not_asked"}, "mojeek": {"verdict": "blocked"},
              "startpage": {"verdict": "upstream_empty"}}
    cancelled = PB.why_that_engine("yahoo_jp", Counter({"cancelled": 2}), health)
    blocked = PB.why_that_engine("mojeek", Counter({"blocked": 2}), health)
    empty = PB.why_that_engine("startpage", Counter({"empty": 2}), health)
    assert "被取消" in cancelled and "抬软截止" in cancelled, cancelled
    assert "答不了" in blocked, blocked
    assert "没可用结果" in empty, empty
    assert len({cancelled, blocked, empty}) == 3, (cancelled, blocked, empty)


def test_the_verdict_rides_the_same_line_as_the_bucket():
    """桶是这一轮的观测，verdict 是池子记住的判定 —— 两句要在同一行才读得出矛盾。"""
    line = PB.why_that_engine("mojeek", Counter({"blocked": 1}),
                              {"mojeek": {"verdict": "parser_drift"}})
    assert "blocked" in line and "parser_drift" in line, line
    # 矛盾是信息：这一轮它"被拒"，而池子说它其实解析不出东西。


# ─── 2 · 计数累加，不后写覆盖 ────────────────────────────────────

def test_a_family_that_answered_once_and_was_cancelled_once_keeps_both():
    tally = PB.family_tally([_row(used=["yahoo_jp"], preempted=[]),
                             _row(used=["bing"], preempted=["yahoo_jp"])])
    assert tally["yahoo_jp"]["answered"] == 1, tally
    assert tally["yahoo_jp"]["cancelled"] == 1, tally
    assert sum(tally["yahoo_jp"].values()) == 2, tally


def test_an_engine_seen_in_neither_bucket_is_not_invented():
    """`engine_health()` 里有它（上一轮记的），这一轮响应四桶都没有 —— 不猜它做了什么。"""
    tally = PB.family_tally([_row()])
    assert "startpage" not in tally, tally
    line = PB.why_that_engine("startpage", Counter(), {"startpage": {"verdict": "stale"}})
    assert "not_seen" in line and "stale" in line, line


# ─── 3 · 与集 A 同一份题集 ───────────────────────────────────────

def test_the_queries_come_from_the_same_file_the_serp_capture_freezes():
    """两条基线量的是同一批问题：池子基线与**产出**排名基线语料的那一步读同一颗路径。

    这里说的"同源"不是"两个脚本读同一个文件" —— `ranking_baseline.py` 读的是
    `evals/corpus/serp/**/qrels.jsonl`，那份语料由 `capture_serp.py` 按 `queries.jsonl`
    抽家族冻结而成。链上唯一的题集入口就是这颗常量，所以守住它：池子基线若改读一份
    "长得像的"新题集，一次池子变化就能被读成一次排名变化。
    """
    rows = PB.load_queries(4)
    assert len(rows) == 4, rows
    assert len({r["id"] for r in rows}) == 4, "题号重复：两轮量的不是同一批问题"
    assert all(r.get("text") for r in rows), rows
    import capture_serp
    assert PB.load_queries(4) == rows, "题集读法不确定：同一份文件两次读出两批题"
    assert capture_serp.QUERIES.is_file(), (
        "题集换了地方，这条守卫要跟着换 —— 换了而没人知道，两条基线就此分家")
    assert str(capture_serp.QUERIES).endswith("queries.jsonl"), capture_serp.QUERIES
    # 集 A 的题号来自这份题集：语料目录里存在过的 qid 必须是这份文件里的 qid
    known = {json.loads(line)["id"]
             for line in capture_serp.QUERIES.read_text(encoding="utf-8").splitlines()
             if line.strip()}
    if capture_serp.CORPUS.is_dir():
        frozen = {p.name.split("__")[0] for p in capture_serp.CORPUS.glob("*.html")}
        assert frozen <= known, sorted(frozen - known)[:5]
