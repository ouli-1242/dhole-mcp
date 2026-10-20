# -*- coding: utf-8 -*-
"""R02-004 · 零结果那条「死路」必须给出一条按得下去的路，而且名字要来自这一发自己。

题面（P02-01 庄砚·S4，赶稿的人）：`next_action` 把「换词修不好」说到位了，却没说「那做什么」。
先按 F33/F39 那一课复验题面：画像那一发的现场（引擎都答了、6 行被词面闸门丢光）在 R02-046 的
CJK 词面腿落地之后**对那句原查询已经不再复发**（今天交回 6 行，`review/.runtime/p4/f40_scene.txt`）；
但形状本身还在 —— 构造一发（生僻词 + 池子半残）就复现，而**今天的话术里确实有个空洞**：

    Check the connection, or retry with engines=[...] to ask a different index.

`engines=[...]` 是让调用方**自己填空**，而同一发的 `engine_detail` 里已经写着 bing 与 yandex
`verdict=healthy`（量具 `review/.runtime/p4/f40_measure.py`：三发零结果形状里「点名了能按的名字」
= 0 发，「只指环境变量/CLI」= 2 发；`search.py` 里 `engine_state` 与 `sys_state` 各出现 0 次）。
数据就在同一份回执里，出路却不肯把它读出来 —— 这才是这条账今天的真实形状。

另一条出路「检查网络」在这一侧也真有一把可叫的自检（`sys_state(show=["engine_state"])`：
每家的 verdict + 剩余冷却秒数 + 上一轮从解析出的节点里读出几行），只是没有任何一句搜索回执指向它。

这把刀只立一条判据：**出路要么点名这一发答过的引擎，要么在没人答的时候指向那把自检**。
不写成保证（上一轮答过不等于这一轮答得上），也不编名字（没有 healthy 就不许给名字）。
"""

from __future__ import annotations

import inspect
import re

from dhole_mcp import search
from dhole_mcp.search import (_engine_detail_rows, _search_next_action,
                              _way_out_for_a_dead_end)
from dhole_mcp.search_engines import EngineReport


def _detail(*pairs) -> list[dict]:
    return [{"name": n, "verdict": v} for n, v in pairs]


HEALTHY_TWO = _detail(("bing", "healthy"), ("yandex", "healthy"),
                      ("brave", "blocked"), ("so360", "timeout"))


def test_the_way_out_names_the_engines_that_answered_this_round():
    line = _way_out_for_a_dead_end(HEALTHY_TWO)
    assert 'engines=["bing", "yandex"]' in line, line
    assert "engines=[...]" not in line, "空格不许留在有名字可给的那一发里"


def test_a_single_answering_engine_is_named_the_same_way():
    line = _way_out_for_a_dead_end(_detail(("sogou", "healthy"), ("brave", "blocked")))
    assert 'engines=["sogou"]' in line, line
    assert "engines=[...]" not in line


def test_the_name_list_comes_from_this_rounds_own_verdicts_not_a_registry():
    """只有 verdict=healthy 才算数：答了但空、没插探针、被撤下的都不许当名字端出去。"""
    line = _way_out_for_a_dead_end(_detail(
        ("bing", "healthy"), ("sogou", "upstream_empty"), ("brave", "not_instrumented"),
        ("duckduckgo", "preempted"), ("yandex", "stale"), ("mojeek", "parser_drift")))
    assert 'engines=["bing"]' in line, line
    for name in ("sogou", "brave", "duckduckgo", "yandex", "mojeek"):
        assert name not in line.split("engines=")[1], (name, line)


def test_nothing_answered_healthy_points_at_the_self_check_that_exists():
    line = _way_out_for_a_dead_end(_detail(("brave", "blocked"), ("baidu", "timeout")))
    assert 'sys_state(show=["engine_state"])' in line, line
    assert "engines=" not in line, "没人答的时候不许编一个名字"


def test_an_empty_table_also_points_at_the_self_check():
    """一家都没被问到（detail 为空）也不许留下空白：那把自检仍然答得出谁在被拦。"""
    line = _way_out_for_a_dead_end([])
    assert 'sys_state(show=["engine_state"])' in line, line
    assert "engines=" not in line


def test_the_sentence_is_not_written_as_a_promise():
    line = _way_out_for_a_dead_end(HEALTHY_TWO)
    assert "not a promise" in line, line


def test_the_discard_branch_carries_the_named_way_out():
    """题面那一发（引擎都答了、全被词面闸门丢光）：出路里要有名字，不要再是空参数。"""
    na = _search_next_action([], ["brave", "so360"],
                            "Engines delivered 6 results, but none came back - dhole dropped "
                            "them rather than return noise: 6 matched no query term.",
                            engines_used=["bing", "yandex"],
                            engine_detail=HEALTHY_TWO)
    assert 'engines=["bing", "yandex"]' in na, na
    assert "engines=[...]" not in na, na
    # 解释仍在（这一刀换掉的是空洞，不是那句诊断）
    assert "rephrasing cannot fix it" in na, na


def test_the_blocked_branch_still_says_the_seconds_and_adds_the_self_check():
    na = _search_next_action([], ["brave"], "No results (engines rate-limited).",
                             engines_used=[],
                             engine_detail=_detail(("brave", "blocked"),
                                                   ("so360", "timeout")))
    assert 'sys_state(show=["engine_state"])' in na, na
    assert "cooling" in na or "Retry" in na, na


def test_a_round_that_returned_rows_never_sells_a_way_out():
    """有行的时候这不是死路，那句出路一个字都不该出现（沉默锁）。"""
    from dhole_mcp.search import SearchResult
    rows = [SearchResult(title="年度报告", url="https://a.example/x", snippet="一段正文",
                         source="bing", position=1, relevance_score=0.9,
                         fetch_relevance="high")]
    na = _search_next_action(rows, [], "", engines_used=["bing"],
                             engine_detail=_detail(("bing", "healthy")))
    assert "sys_state" not in na, na
    assert "engines=[" not in na, na


def test_error_and_next_action_carry_the_same_way_out_word_for_word():
    """两处读作一条指示，所以不许一处给名字、另一处留给空参数。"""
    line = _way_out_for_a_dead_end(HEALTHY_TWO)
    err = ("Engines delivered 6 results, but none came back - dhole dropped them rather "
           "than return noise: 6 matched no query term. " + line)
    na = _search_next_action([], ["brave"], "…matched no query term…",
                             engines_used=["bing", "yandex"], engine_detail=HEALTHY_TWO)
    assert line in na, na
    assert "engines=[...]" not in err and "engines=[...]" not in na


def test_both_reply_authors_call_the_one_author_and_nobody_re_writes_it():
    src = inspect.getsource(search)
    # 定义 1 + `_search_next_action` 的五条出路里 4 条 + `web_search` 侧 2 条
    # （`_way_out` 那份共用底稿挂在四张 error 脸上，以及词面丢光那一支）
    assert src.count("_way_out_for_a_dead_end(") == 7, src.count("_way_out_for_a_dead_end(")
    flat = re.sub(r"\s+", "", inspect.getsource(search.web_search))
    assert flat.count("+_way_out)") == 4, "error 那四张脸必须都走同一句出路"
    for fn in (search._search_next_action, search.web_search):
        body = inspect.getsource(fn)
        assert "retry with engines=[...]" not in body, (
            "空参数还留在交回执的那一处 = 同一条死路有第二个作者")
    assert inspect.getsource(search._search_next_action).count(
        "_way_out_for_a_dead_end(") == 4


def test_the_detail_rows_are_built_by_the_engine_that_mints_them():
    """名字表用的就是上线那一份 `engine_detail` 的作者，不在别处再分类一次。"""
    reports = [EngineReport(name="bing", ok=True, status="ok", yield_verdict="healthy"),
               EngineReport(name="brave", blocked=True, status="blocked",
                            yield_verdict="blocked")]
    names = lambda line: line.split("engines=")[1].split(" -")[0]  # noqa: E731
    assert names(_way_out_for_a_dead_end(_engine_detail_rows(reports))) == \
        names(_way_out_for_a_dead_end(_detail(("bing", "healthy"))))


def test_the_manual_says_which_key_the_way_out_presses():
    from dhole_mcp import guide
    corpus = "\n".join(p.read_text(encoding="utf-8") for p in guide._ROOT.rglob("*.md"))
    assert re.search(r"(?<![\w.])engine_state(?![\w])", corpus), (
        "手册没写这条出路指的是哪一把自检")
    assert "answered this round" in corpus, (
        "手册没写这条出路点的是**本轮答过的**引擎，读者会以为是个静态名单")
