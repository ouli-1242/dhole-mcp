# -*- coding: utf-8 -*-
"""被预算掐断的那一步必须留下自己的一行 —— 不然"跑到第几步"这件事根本没处查。

症状（15 场景真人体测，场景 10，实录见 `review/`）：靶站 `/captcha` 上取一枚元素截图，
`{"screenshot": {"selector": "#captcha"}}`，`timeout=60000`。61,529ms 之后交回的是：

    error    = "timeout: the 60000ms call budget ran out during the actions (stealthy) tier.
                No content was extracted."
    content  = []
    metadata = {只有 title/lang/robots —— 没有 actions}

一行回执行都没有。`tests/test_a_chain_cut_by_its_budget_says_which_steps_ran.py`（D-35b）
修的是**行已经存在却没被说出口**的那一半；这一刀修的是另一半：**行根本没生成**。
链里那一步是被时钟掐死的，抛的是 `CancelledError`，而 `asyncio.CancelledError` 自 3.8 起
从 `BaseException` 来、不再是 `Exception` 的子类 —— 所以循环末尾那条
`except Exception` 接不到它，异常直接穿过循环，`outcomes` 停在掐断之前的样子。

后果不是"少了条日志"，是**两种完全不同的处境在字节上长得一模一样**：
8 步的链跑到第 7 步被掐（前 6 步真的落在页面上了），与一步没走（页面还是干净的），
交回的都是一句 "No content was extracted" + 零行回执。而这两种的处理恰好相反 ——
前者该从第 7 步续上，后者该整条重来；读的人唯一的依据被吞掉了，于是只能重跑整条链，
代价又是一整轮浏览器时间（实测那一格烧掉 61.5 秒后，人还在等第一次结果）。

判据的形状：

1. **被掐的那一步留下自己的行**，写明它没跑完、可能只做到一半，并写清是第几步 / 共几步。
2. **掐完照原样交回**：这一刀不改任何截止时间，也不接住取消 —— 外面那道 backstop 仍然
   是它来建"预算用完"的那个响应，否则等于把取消吞成一个假成功。
3. **已经跑完的步骤不能因为后面那步断了就被抹掉**：`elif failed:` 那句
   "these interactions never happened, so the content is the page BEFORE them" 从前在
   至少有一步 ok 的时候也会说出口（实测：`fill '#qty' ok:true` 与一次失败的 `read` 同链），
   于是要么信一句假话、要么重跑整条链再填一遍那个框 —— 后者比不说更贵。
4. **"断在第几步"要上到 summary**：那一行是细节，`metadata.actions` 逐行读才看得见；
   一个人扫一眼 summary 就该知道要不要重跑。
5. **完整的链不许被说成断过**（反面守卫）。
"""

from __future__ import annotations

import asyncio

import pytest

from dhole_mcp.actions import build_page_action
from dhole_mcp.models import ResponseModel
from dhole_mcp.quality import _over_budget_result, _report_action_outcomes

# ─── 一页：解析到第二个选择器时，执行中的链被掐死 ───────────────────


class _Ok:
    """一步能成的动作只需要这些器官（`read` 走 inner_text）。"""

    @property
    def first(self):
        return self

    async def inner_text(self, timeout=None):
        return "正文"


class _Page:
    """`locator()` 是 `_target_locator` 踏进页面后的第一个调用，所以取消从这里抛
    最省事 —— 不必猜 click 内部还调了哪几颗。"""

    def locator(self, sel):
        if sel == "#boom":
            # 真机上这一步不是"失败"，是被取消：await 正在等的时候任务被掐。
            raise asyncio.CancelledError()
        return _Ok()


def _run(chain, page):
    """跑到被掐为止，把 CancelledError 接回来交给调用方断言。"""
    pa = build_page_action(chain)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(pa(page))
    return pa


def _cut(stage="actions (stealthy) tier"):
    return _over_budget_result("http://127.0.0.1:8399/captcha", 60000, 61529,
                               stage, "")


# ─── 1 · 被掐的那一步留下自己的行 ──────────────────────────────────


def test_a_step_killed_by_the_budget_leaves_its_own_row():
    pa = _run([{"read": "#p"}, {"click": "#boom"}], _Page())
    rows = pa.outcomes
    assert len(rows) == 2, "第一步成了就该有一行，第二步被掐也该有一行"
    assert rows[0]["ok"] is True
    assert rows[1]["ok"] is False
    assert rows[1]["cut_by_budget"] is True


def test_the_cut_row_says_which_step_of_how_many():
    pa = _run([{"read": "#p"}, {"click": "#boom"}], _Page())
    row = pa.outcomes[1]
    assert row["step"] == "2/2", row
    assert "click '#boom'" in row["action"]


def test_the_cut_row_says_the_maybe_done_half_rather_than_a_clean_failure():
    """一步被掐 ≠ 一步没跑：它的效果可能已经落了一半在页面上，这必须说出口。"""
    pa = _run([{"click": "#boom"}], _Page())
    assert "never finished" in pa.outcomes[0]["error"]
    assert "half-done" in pa.outcomes[0]["error"]


def test_the_chain_still_reports_how_many_steps_it_was_asked_to_walk():
    pa = _run([{"read": "#p"}, {"click": "#boom"}], _Page())
    assert pa.total_steps == 2
    assert len(pa.outcomes) == 2, "被掐时只走到第 2 步，行数不该假装是 3"


# ─── 2 · 取消照原样交回，不接成假成功 ──────────────────────────────


def test_cancellation_is_handed_back_not_swallowed():
    """这一刀只加一行，不改截止时间：外面那道 backstop 才是建超时响应的人。"""
    pa = build_page_action([{"click": "#boom"}])
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(pa(_Page()))


def test_a_step_that_fails_normally_still_takes_the_old_branch():
    """反面守卫：普通失败（元素找不到）不许被写成"被预算掐了"。"""
    class _NoSuchPage:
        def locator(self, sel):
            raise RuntimeError("waiting for locator")

    pa = build_page_action([{"click": "#absent"}])
    asyncio.run(pa(_NoSuchPage()))
    row = pa.outcomes[0]
    assert row["ok"] is False and "cut_by_budget" not in row


# ─── 3 · 断在第几步要上到 summary ─────────────────────────────────


def test_the_summary_says_where_the_chain_stopped():
    r = _cut()
    r.summary = "out of time · 0B markdown"
    pa = _run([{"read": "#p"}, {"click": "#boom"}], _Page())
    _report_action_outcomes(r, pa)
    assert "chain cut by the budget at step 2/2" in r.summary, r.summary
    assert "1 step(s) landed before it" in r.summary, r.summary


def test_a_single_step_chain_cut_reports_zero_landed_not_a_claim_of_progress():
    """体测里那一格就是这条：唯一一步被掐，0 步落地。"""
    r = _cut()
    r.summary = "out of time · 0B markdown"
    pa = _run([{"click": "#boom"}], _Page())
    _report_action_outcomes(r, pa)
    assert "at step 1/1" in r.summary, r.summary
    assert "0 step(s) landed" in r.summary, r.summary
    assert "already ran" not in r.summary, "没有一步跑成，不许说跑成了"


def test_the_cut_receipt_reaches_the_response_metadata():
    """回执存在不等于看得见：这一行得真的进 metadata.actions。"""
    r = _cut()
    pa = _run([{"read": "#p"}, {"click": "#boom"}], _Page())
    _report_action_outcomes(r, pa)
    rows = r.metadata["actions"]
    assert rows[1]["cut_by_budget"] is True and rows[1]["step"] == "2/2"


# ─── 4 · 已经落地的步骤不许被一句模板话抹掉 ────────────────────────


def test_a_partial_chain_is_not_described_as_never_having_happened():
    """`fill` 成了、`read` 失败 —— 那句 "these interactions never happened" 是假的。"""
    r = ResponseModel(url="http://127.0.0.1:8399/checkout", status=200, content=["x"])
    r.summary = "200 OK"
    r.next_action = ""  # 只有没人给过建议时，那一句才会被写出口
    pa = type("PA", (), {"outcomes": [
        {"action": "fill '#qty'", "ok": True},
        {"action": "read '#qty'", "ok": False, "error": "read found no text"}],
        "total_steps": 2})()
    _report_action_outcomes(r, pa)
    # 要禁的是那句**整条链**的 blanket 话（"these interactions never happened"）；
    # "失败的那一步没发生过"这一句是真话，也是 `test_extended_report_regressions.py`
    # 从另一边钉着的 —— 两个方向共用一句判据时，needle 必须精确到那一支说的是哪些步骤。
    assert "these interactions never happened" not in r.next_action, r.next_action
    assert "the failed one never happened" in r.next_action, r.next_action
    assert "DID take effect" in r.next_action
    assert "fill '#qty'" in r.next_action


def test_a_chain_where_nothing_ran_keeps_the_clean_slate_sentence():
    """反面守卫：一步都没落地的时候，那句"页面还是它们之前的样子"是真话。"""
    r = ResponseModel(url="http://127.0.0.1:8399/checkout", status=200, content=["x"])
    r.summary = "200 OK"
    r.next_action = ""
    pa = type("PA", (), {"outcomes": [
        {"action": "click '#nope'", "ok": False, "error": "no element"}],
        "total_steps": 1})()
    _report_action_outcomes(r, pa)
    assert "never happened" in r.next_action, r.next_action


def test_a_finished_chain_is_never_described_as_cut():
    r = ResponseModel(url="http://127.0.0.1:8399/checkout", status=200, content=["x"])
    r.summary = "200 OK · markdown"
    pa = type("PA", (), {"outcomes": [{"action": "wait 500", "ok": True}],
                         "total_steps": 1})()
    _report_action_outcomes(r, pa)
    assert "chain cut" not in r.summary, r.summary


# ─── 5 · 新键得写在人查字段的地方 ─────────────────────────────────


def test_the_cut_keys_are_documented_where_a_field_is_looked_up():
    from dhole_mcp import guide
    fields = (guide._ROOT / "references" / "fields.md").read_text(encoding="utf-8")
    for needle in ("cut_by_budget", "step"):
        assert needle in fields, needle
