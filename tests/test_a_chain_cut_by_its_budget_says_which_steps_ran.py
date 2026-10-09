"""预算掐断一条动作链时，回执要说清哪几步已经真的落在页面上了（D-35b）。

症状（实测 `.scratch/liveverify/d35_silence.py` 的 `round_budget_cut`：本机靶站、真 stdio
客户端、链 = `fill` → `wait 30000` → `click`，默认 25 秒预算，全程没有 elicitation 参与）：

    sec=26.5  error="timeout: the 25000ms call budget ran out during the actions
    (stealthy) tier. No content was extracted."
    summary="network error · 0B markdown"
    rows=[{"action":"fill 'input[name=note]'","ok":true}]
    next_action="… Raise timeout …, pass force_fetcher='http' …, or switch source"

三句都站不住：那一步 `fill` **确实执行了**（行上写着 ok:true，字段里的文本还在页面上），
而"network error" 把人送去查代理、"switch source" 把人从一张已经填好的表单上拽走 ——
D-07 早就为同一种事定过调（"排在 submit 前面的动作已经真实落在页面上"），这一支只是
那条链被时钟掐断时没人说这句话。

两处改，各自都有反面守卫：
  · `quality._agent_hints` 的 status==0 阶梯上多认一次"预算用完"（作者仍是
    `quality._is_over_budget` 那一颗，不重新解析散文）：标签是 "out of time"。
  · `quality._report_action_outcomes` 在"这是一次预算掐断"且**有步骤跑成了**的时候，
    把跑成的那几步报进 summary，并在原有建议后面接一句：重跑整条链会把它重放一遍。
"""

from __future__ import annotations

from dhole_mcp.models import ResponseModel
from dhole_mcp.quality import (_agent_hints, _is_over_budget, _over_budget_result,
                               _report_action_outcomes)


def _cut(stage: str = "actions (stealthy) tier") -> ResponseModel:
    return _over_budget_result("http://127.0.0.1:5331/form", 25000, 26518,
                               stage, "stealthy")


def _page_action(*outcomes):
    return type("PA", (), {"outcomes": list(outcomes)})()


class TestTheBudgetCutIsNotANetworkFailure:

    def test_the_summary_names_the_clock_not_the_network(self):
        summary, _next, ok = _agent_hints(_cut())
        assert summary.startswith("out of time · 0B markdown"), summary
        assert "network error" not in summary, "预算用完不是网络故障，没有代理可查"
        assert ok is False

    def test_a_real_network_failure_still_says_so(self):
        """反面守卫：新认的那一支不许把整个 status==0 阶梯吞掉。"""
        r = ResponseModel(url="http://127.0.0.1:1/x", status=0, content=[],
                          error="network_error: connection refused")
        summary, _next, _ok = _agent_hints(r)
        assert "network error" in summary, summary


class TestTheCutChainSaysWhichStepsRan:

    def test_the_steps_already_on_the_page_are_named(self):
        r = _cut()
        _report_action_outcomes(r, _page_action(
            {"action": "fill 'input[name=note]'", "ok": True},
            {"action": "wait 30000", "ok": False, "error": "cancelled"}))
        assert "1 of 2 actions already ran" in r.summary, r.summary
        assert "fill 'input[name=note]'" in r.summary, r.summary

    def test_the_advice_says_a_replay_is_not_free(self):
        r = _cut()
        _report_action_outcomes(r, _page_action(
            {"action": "fill 'input[name=note]'", "ok": True}))
        assert "ALREADY on the page" in r.next_action, r.next_action
        assert "replays" in r.next_action, r.next_action
        # 原来那句"raise timeout"仍然在 —— 这一刀是补话，不是换建议
        assert "Raise timeout" in r.next_action, r.next_action

    def test_a_summary_that_was_empty_does_not_gain_a_separator(self):
        r = _cut()
        r.summary = ""
        _report_action_outcomes(r, _page_action(
            {"action": "click 'a.next'", "ok": True}))
        assert not r.summary.startswith(" · "), r.summary
        assert "None" not in r.summary, r.summary

    def test_nothing_is_claimed_when_no_step_ran(self):
        """第一条动作就没跑完 —— 不能顺手宣布"有步骤已经落在页面上"。"""
        r = _cut()
        _report_action_outcomes(r, _page_action(
            {"action": "fill 'input[name=note]'", "ok": False, "error": "no such element"}))
        assert "already ran" not in r.summary, r.summary


class TestAFinishedChainIsNotDescribedAsCut:
    """反面守卫：那句话只属于被时钟掐断的那一次。"""

    def test_a_normal_answer_keeps_its_own_receipt(self):
        r = ResponseModel(url="http://127.0.0.1:5331/form", status=200,
                          content=["ok"], extracted_type="markdown")
        r.summary = "200 OK · 2B markdown · stealthy"
        _report_action_outcomes(r, _page_action(
            {"action": "fill 'input[name=note]'", "ok": True},
            {"action": "click 'button[name=send]'", "ok": True, "submitted": True}))
        assert "already ran" not in r.summary, r.summary
        assert "ALREADY on the page" not in (r.next_action or ""), r.next_action

    def test_the_cut_predicate_is_the_one_author(self):
        """判"是不是预算掐断"只有 `_is_over_budget` 一颗，这里不许另起一套字符串匹配。"""
        assert _is_over_budget(_cut()) is True
        assert _is_over_budget(ResponseModel(
            url="http://x", status=0, content=[],
            error="network_error: timeout of 30s exceeded")) is False
