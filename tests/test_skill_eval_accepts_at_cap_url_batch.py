"""尺子只认 nested 一条路 —— 三档在 tag-pages 上退回 urls= 批量全红，是尺子与语料打架。

症状（2026-10-06 F-A-J2，第三轮实测 evals.json runs 字段）：tag-pages 三档全
红且形状一致 —— 30 个标签恰好贴着 nested_max=30 的上限，三档都退回 urls=
批量；而 urls= 是 tools.md 明教的合法路径。断言只认 nested 一条路，把语料教
的等价走法判成错。同轮：few_calls 是 5 格里 3 格的唯一红项（先探针一发再扇
出），README:483 已判探针是合理保守行为 —— 扣分项该降级为记录项。

判决（三步全在尺子/语料，不碰 wire）：① 评分器新增 any_of 组合断言（分支内
全要，分支间任一），tag-pages 的 used_the_fan_out 接受「nested 三件套」或
「贴上限的 urls= 批量」两条路（新增 arg op len_ge 量批量大小）；② few_calls
三条全部 record_only —— 行照印、不进格子的分母；③ 新增 no_identical_retry
断言类型给 diagnosis 分支格用（同参连发两次 = 重试同 call）。真实重跑（tag-
pages ≥2 档转绿、两新格 C−B ≥ +2）需要真站点真 agent，留给停机点；本测试只
验收尺子本体。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_SPEC = Path(__file__).resolve().parents[1] / "evals" / "skill_eval_score.py"
_spec = importlib.util.spec_from_file_location("skill_eval_score", _SPEC)
score = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(score)


def _call(args: dict, tool: str = "web_read") -> dict:
    return {"tool": tool, "args": args}


def _url_batch(n: int) -> list[str]:
    return [f"https://quotes.toscrape.com/tag/{i}/" for i in range(n)]


def test_the_ruler_accepts_an_at_cap_urls_batch_as_the_fan_out():
    """等价形状：30 个标签贴着上限走 urls= 批量，与 nested 扇出同判 ok。"""
    nested_branch = [
        {"type": "arg", "path": "nested_selector", "op": "nonempty"},
        {"type": "arg", "path": "nested_max", "op": ">=", "value": 12},
        {"type": "arg", "path": "nested_fields", "op": "contains", "value": "array"},
    ]
    urls_branch = [{"type": "arg", "path": "urls", "op": "len_ge", "value": 12}]
    any_of = {"type": "any_of", "branches": [nested_branch, urls_branch]}

    nested_calls = [_call({"url": "https://quotes.toscrape.com/",
                           "nested_selector": "a.tag", "nested_max": 30,
                           "nested_fields": [{"field": "正文", "selector": ".quote span.text", "type": "array"}]})]
    ok, why = score.check(any_of, nested_calls, "")
    assert ok, f"nested 路不再 ok: {why}"

    batch_calls = [_call({"urls": _url_batch(30)})]
    ok, why = score.check(any_of, batch_calls, "")
    assert ok, f"贴上限的 urls= 批量仍被拒: {why}"

    neither = [_call({"url": "https://quotes.toscrape.com/"})]
    ok, why = score.check(any_of, neither, "")
    assert not ok, "两条路都没走也算对，等价形状就成了放行"


def test_record_only_assertions_are_printed_but_not_counted():
    """few_calls 降级为记录项：行照印，不进格子的分母。"""
    case = {
        "id": "tag-pages",
        "assertions": [
            {"name": "used_the_fan_out", "type": "any_of", "branches": [
                [{"type": "arg", "path": "nested_selector", "op": "nonempty"}],
                [{"type": "arg", "path": "urls", "op": "len_ge", "value": 12}],
            ]},
            {"name": "few_calls", "type": "tool_count", "tool": "web_read",
             "op": "<=", "value": 3, "record_only": True},
        ],
    }
    calls = [_call({"url": "https://quotes.toscrape.com/"}),
             _call({"urls": _url_batch(30)})]
    row_pass, n, rows = score.score_case(case, calls, "")
    assert (row_pass, n) == (1, 1), "record_only 断言不该进分母"
    probe_first = [_call({"url": "https://quotes.toscrape.com/"})] * 4
    row_pass, n, rows = score.score_case(case, probe_first, "")
    assert row_pass == 0 and n == 1, "扇出没走时计入项仍要红"
    note = [r for r in rows if r[0] == "few_calls"]
    assert note and note[0][1] is False and note[0][3], (
        "四连发的 few_calls 照印为红记录，但不决定格子红绿")


def test_no_identical_retry_flags_same_args_sent_twice():
    a = {"type": "no_identical_retry", "tool": "web_read"}
    ok, _ = score.check(a, [_call({"url": "https://x/", "focus": "q"}),
                            _call({"url": "https://x/", "focus": "q"})], "")
    assert not ok, "同参连发两次没有红"
    ok, _ = score.check(a, [_call({"url": "https://x/"}),
                            _call({"url": "https://x/", "anti_detect": "stealthy"})], "")
    assert ok, "换了杠杆的行动被误判成重试"


def test_the_shipped_ruler_parses_and_uses_the_new_shapes():
    """随仓库走的 evals.json 必须是尺子此刻认得的形状。"""
    spec = json.loads((Path(__file__).resolve().parents[1]
                       / "src" / "dhole_mcp" / "skills" / "dhole-web"
                       / "evals" / "evals.json").read_text(encoding="utf-8"))
    cases = {c["id"]: c for c in spec["evals"]}
    tag = cases["tag-pages"]
    fan_out = next(a for a in tag["assertions"] if a["name"] == "used_the_fan_out")
    assert fan_out["type"] == "any_of" and len(fan_out["branches"]) >= 2
    record_only = [a for c in spec["evals"] for a in c["assertions"]
                   if a.get("record_only")]
    assert {a["name"] for a in record_only} == {"few_calls"}, (
        "降级成记录项的应该恰好是三条 few_calls")
    assert "mojibake-pivot" in cases and "diagnosis-branch" in cases, (
        "只 references 能答的两格还没进尺子")
