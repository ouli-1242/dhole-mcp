# -*- coding: utf-8 -*-
"""R01-042 · 一条超时不许在同一封响应里有两个数，还不说它们的关系。

现场（P10 何广 H2/S9，`sessions/P10-s9-h7-timeout.json`）：
`web_read(timeout=10000)` 打一份大 PDF，实际跑了 **68113ms**（harness wall 69s），而
同一封响应里 `error` 写 "the **10000ms** call budget ran out"、`next_action` 写
"Budget exhausted after **68113ms**" —— 两个数都在，关系一个都没说。

修前在受控靶站上量过（`review/.runtime/p4/slow_budget_probe.py` + `spy_r01_042.py`）：

```
/slow.pdf  @timeout=10000 → duration_ms 12047 / wall 12804   （预算守得住，溢出 ~2s）
/slow.html @timeout=30000 → duration_ms 30026 / wall 30031   （阶梯也没超）
原样那一发今天重放        → duration_ms 10876 / wall 11647   （68s 不可复现，见 fix 文档）
```

所以本刀**不假装修好了 68 倍**：那条现场今天量不到（站点当天的速度决定它），台账里
按未复现如实记。可核对、可复现的是这一件：花掉的时间与点名的预算之间那笔差，
响应自己必须说清楚 —— 包括超出宽限（`BACKSTOP_GRACE_S`）的那一种，因为那正是
"MCP 客户端 30s 超时会被这一发拖死"的形状，而从前它只说"10000ms 的预算用完了"。
"""

from __future__ import annotations

from dhole_mcp.context import BACKSTOP_GRACE_S
from dhole_mcp.quality import _is_over_budget, _over_budget_result

URL = "https://example.com/big.pdf"
GRACE_MS = int(BACKSTOP_GRACE_S * 1000)


def _build(budget_ms: float, elapsed_ms: float, stage: str = "HTTP tier"):
    return _over_budget_result(URL, budget_ms, elapsed_ms, stage, "http")


def test_the_error_names_both_the_asked_budget_and_the_time_spent():
    r = _build(10000, 10876)
    assert "10000" in r.error and "10876" in r.error, r.error
    # 两个格子必须认同一个花掉的数（建议位里还有别的数 —— 上限、余量，
    # 所以这里查的是"这一发实际花了多久"那一个，不是最后一个数）
    assert "10876ms" in r.next_action, (r.error, r.next_action)


def test_the_machine_readable_prefix_survives_the_rewording():
    """`_is_over_budget` 认的是 "timeout: the " 这个前缀，每一档都靠它收尾 ——
    重写句子不许把这条机器可读的边抹掉。"""
    r = _build(10000, 10876)
    assert r.error.startswith("timeout: the "), r.error
    assert _is_over_budget(r)


def test_a_spill_inside_the_grace_is_called_the_grace():
    """10876 - 10000 = 876ms < 1500ms 的宽限：那是"让在跑的那一步收个尾"的量，
    不是第二个预算。这句话从前没人说，读者只能把两个数当成谁抄错了。"""
    r = _build(10000, 10876)
    low = r.error.lower()
    assert "grace" in low, r.error
    assert str(GRACE_MS) in r.error, r.error
    assert "past the budget" not in low, r.error


def test_an_overrun_past_the_grace_is_reported_as_an_overrun():
    """P10 那一发的形状：花了 68113ms，点名的预算是 10000ms。
    这时候响应必须把它**越过了预算与宽限**直说，并说出超了多少。"""
    r = _build(10000, 68113)
    assert "past the budget" in r.error.lower(), r.error
    assert str(68113 - 10000) in r.error, r.error
    assert _is_over_budget(r)


def test_the_overrun_is_not_blamed_on_the_asked_figure():
    """越限那一发里，"raise timeout" 不是出路：这次已经把 10000ms 花到 68113ms 了。
    建议位可以继续给通用杠杆，但那句必须带上"这一发已经超出自己点名的预算"这一事实。"""
    r = _build(10000, 68113)
    assert "68113" in r.next_action, r.next_action
    assert "past the budget" in r.next_action.lower() or "past the budget" in r.error.lower()


def test_a_call_that_stopped_inside_its_budget_says_so_plainly():
    r = _build(30000, 29500)
    assert "inside" in r.error.lower() or "within" in r.error.lower(), r.error
    assert "grace" not in r.error.lower(), r.error


def test_no_sentence_of_the_advice_starts_where_a_lower_case_word_begins():
    """本刀自己带出的错（R01-010 测量时读到，见 fix-R01-F42.md 的《复验》之后一段）。

    `relation` 那句是为 `error` 的括号写的 —— 分号之后小写开头是对的 —— 却被原样拼进
    `next_action` 的句首。实测那一封读起来是：
    `Budget exhausted after 32424ms at the fetch tiers. it ran 2424ms past the budget…`
    句号之后一个小写词，是给人看的句子里最贵的一种错：读者会以为上一句还没说完。
    """
    for budget, elapsed in ((10000, 12424), (10000, 10876), (30000, 29500)):
        r = _build(budget, elapsed)
        # 量字段边界，不全文分句：建议里有 "e.g." 那样的缩写，按 ". " 切出来的不是句子
        head_end = r.next_action.index(". ") + 2
        opener = r.next_action[head_end:head_end + 1]
        assert opener.isupper() or opener.isdigit(), (budget, elapsed, r.next_action)


# ─── 反向守卫：这一刀不许动已有的两条专项说明 ─────────────────────────────

def test_a_document_download_keeps_its_own_diagnosis():
    """专项判决（"这是下载，raise 也没用"）本来就走 `diagnosis`，不许被通用句盖掉。"""
    r = _over_budget_result(URL, 10000, 10876, "HTTP tier", "http",
                            diagnosis="the file is 4.1MB; a download gets ONE attempt")
    assert "a download gets ONE attempt" in r.next_action, r.next_action
    assert "10876" in r.next_action, r.next_action


def test_the_terminal_marker_and_the_empty_body_are_unchanged():
    r = _build(10000, 10876)
    assert r.status == 0 and r.content == []
    assert "No content was extracted." in r.error, r.error
