"""`evaluate` 能跑任意页面 JS，所以它必须过一次明面的确认（矩阵的「跑任意 JS」格）。

设计取舍：JS 能读 `document.cookie` 和登录态，回执会把读到的值带进 transcript。
因此它不是普通动作——没有 `confirm: true`（也没有 out-of-band 的人工放行）时，
表达式**一个字都不执行**，回执落一行 `pending_gate: evaluate`，next_action 告诉
调用方去问用户、然后带嵌套的 confirm 重跑。

结果只收 JSON 可序列化的值；非 JSON 值交 repr 并标 `result_repr`，超帽交前缀并标
`result_truncated`，全量字数在 `result_chars_total`。每次 evaluate（放行或暂停）
都进 `DHOLE_USAGE_LOG` 的 `browser_evaluate` 通道。
"""

from __future__ import annotations

import asyncio
import json

import pytest

from dhole_mcp import actions
from dhole_mcp.actions import _validate_actions, build_page_action
from dhole_mcp.models import ResponseModel
from dhole_mcp.quality import _report_action_outcomes


class _Page:
    def __init__(self, value="ok"):
        self.value = value
        self.calls: list[str] = []
        self._listeners: list = []

    def on(self, event, handler):
        self._listeners.append((event, handler))

    def remove_listener(self, event, handler):
        self._listeners = [x for x in self._listeners if x != (event, handler)]

    async def evaluate(self, script, arg=None):
        self.calls.append(script)
        return self.value


def _run(chain, monkeypatch, *, human=None, value="ok"):
    async def ask(*a, **k):
        return human

    monkeypatch.setattr(actions, "out_of_band_confirm", ask)
    page = _Page(value=value)
    pa = build_page_action(chain)
    asyncio.run(pa(page))
    return page, pa


# ── 1 · 校验 ──────────────────────────────────────────────────────────

def test_a_bare_expression_is_the_shorthand_and_starts_unconfirmed():
    assert _validate_actions([{"evaluate": "document.title"}]) == [
        {"evaluate": {"expr": "document.title", "confirm": False}}]


def test_the_dict_shape_carries_confirm():
    assert _validate_actions([{"evaluate": {"expr": "1+1", "confirm": True}}]) == [
        {"evaluate": {"expr": "1+1", "confirm": True}}]


@pytest.mark.parametrize("bad", [
    {"evaluate": ""},
    {"evaluate": "   "},
    {"evaluate": 3},
    {"evaluate": {"confirm": True}},
    {"evaluate": {"expr": "1+1", "confirm": "yes"}},
    {"evaluate": {"expr": "1+1", "script": "x"}},
    {"evaluate": {"expr": "x" * (actions.MAX_EVALUATE_EXPR_CHARS + 1)}},
])
def test_a_shape_the_validator_cannot_run_is_refused_up_front(bad):
    with pytest.raises(ValueError):
        _validate_actions([bad])


# ── 2 · 确认闸：没点头就一个字都不跑 ───────────────────────────────────

def test_without_confirm_the_expression_never_runs(monkeypatch):
    page, pa = _run([{"evaluate": {"expr": "document.cookie"}}], monkeypatch)
    row = pa.outcomes[0]
    assert page.calls == [], "没有确认却执行了 JS"
    assert row["pending_approval"] is True and row["pending_gate"] == "evaluate", row
    assert "cookies" in row["error"], row["error"]
    assert "confirm: true" in row["error"], row["error"]


def test_a_declined_ask_is_final_even_with_confirm(monkeypatch):
    page, pa = _run([{"evaluate": {"expr": "1+1", "confirm": True}}],
                    monkeypatch, human=False)
    row = pa.outcomes[0]
    assert page.calls == [], "人说不，JS 仍然跑了"
    assert row["pending_approval"] is True and row["pending_gate"] == "evaluate", row
    assert "declined" in row["error"], row["error"]


def test_a_confirmed_expression_runs_and_its_value_is_on_the_row(monkeypatch):
    page, pa = _run([{"evaluate": {"expr": "1+1", "confirm": True}}],
                    monkeypatch, value=2)
    assert page.calls == ["1+1"]
    row = pa.outcomes[0]
    assert row["ok"] is True and row["evaluated"] is True, row
    assert row["result"] == 2, row


def test_an_out_of_band_yes_runs_it_without_the_parameter(monkeypatch):
    page, pa = _run([{"evaluate": {"expr": "document.title"}}],
                    monkeypatch, human=True, value="Hi")
    assert page.calls == ["document.title"]
    assert pa.outcomes[0]["ok"] is True, pa.outcomes


def test_the_human_is_shown_the_expression_being_approved(monkeypatch):
    """弹窗必须带着"要跑的那行代码"——只看一句"批准运行 JS"不构成知情。

    超帽表达式交前 300 字符并说出全量长度：同意的人要知道自己没看到全部。
    """
    seen: list[str] = []

    async def ask(prompt, *a, **k):
        seen.append(prompt)
        return True

    monkeypatch.setattr(actions, "out_of_band_confirm", ask)
    long_expr = "document.cookie /*" + "x" * 400 + "*/"
    pa = build_page_action([{"evaluate": {"expr": long_expr, "confirm": True}}])
    asyncio.run(pa(_Page(value="ok")))
    assert seen, "确认闸没有问人"
    assert long_expr[:300] in seen[0], seen[0]
    assert str(len(long_expr)) in seen[0], seen[0]
    assert "cookies" in seen[0], seen[0]


# ── 3 · 结果的账 ─────────────────────────────────────────────────────

def test_a_value_past_the_cap_says_how_much_was_cut(monkeypatch):
    monkeypatch.setattr(actions, "MAX_EVALUATE_CHARS", 10)
    _, pa = _run([{"evaluate": {"expr": "'x'.repeat(100)", "confirm": True}}],
                 monkeypatch, value="x" * 100)
    row = pa.outcomes[0]
    assert row["result_truncated"] is True, row
    assert row["result_chars_total"] == len(json.dumps("x" * 100)), row
    assert row["result"] == json.dumps("x" * 100)[:10], row


def test_a_non_json_value_rides_as_repr_and_says_so(monkeypatch):
    marker = object()
    _, pa = _run([{"evaluate": {"expr": "someFunction", "confirm": True}}],
                 monkeypatch, value=marker)
    row = pa.outcomes[0]
    assert row["result_repr"] is True, row
    assert row["result"] == repr(marker), row


# ── 4 · 回应面：暂停的 evaluate 不许被写成 submit 的暂停 ───────────────

def test_a_pending_evaluate_gets_its_own_next_action(monkeypatch):
    _, pa = _run([{"evaluate": {"expr": "document.cookie"}}], monkeypatch)
    result = ResponseModel(url="https://e.test/", status=200,
                           content=["body"], summary="200 OK")
    _report_action_outcomes(result, pa)
    assert "evaluate" in result.next_action, result.next_action
    assert "cookies" in result.next_action, result.next_action
    assert '{"evaluate": {"expr": "...", "confirm": true}}' in result.next_action, (
        result.next_action)
    assert "submit action is PAUSED" not in result.next_action, result.next_action


def test_every_evaluate_lands_in_the_audit_log(monkeypatch, tmp_path):
    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("DHOLE_USAGE_LOG", str(log))
    _, pa = _run([{"evaluate": {"expr": "1+1", "confirm": True}}],
                 monkeypatch, value=2)
    result = ResponseModel(url="https://e.test/", status=200, content=["body"])
    _report_action_outcomes(result, pa)
    events = [json.loads(line) for line in
              log.read_text(encoding="utf-8").splitlines()]
    evaluated = [e for e in events if e.get("event") == "evaluate"]
    assert evaluated, events
    assert evaluated[0]["channel"] == "browser_evaluate"
    assert evaluated[0]["pending"] is False


def test_an_approved_expression_that_throws_is_audited_too(monkeypatch, tmp_path):
    """审计记的是"谁放行了一次页面 JS"，不是"它恰好成功了"。

    抛异常的表达式已经跑过页面代码（副作用与读取都可能发生），所以它同样要留痕。
    """
    log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("DHOLE_USAGE_LOG", str(log))

    class _Boom(_Page):
        async def evaluate(self, script, arg=None):
            self.calls.append(script)
            raise RuntimeError("page-side boom")

    async def ask(*a, **k):
        return None

    monkeypatch.setattr(actions, "out_of_band_confirm", ask)
    pa = build_page_action([{"evaluate": {"expr": "boom()", "confirm": True}}])
    asyncio.run(pa(_Boom()))
    row = pa.outcomes[0]
    assert row["ok"] is False and "boom" in row["error"], row

    result = ResponseModel(url="https://e.test/", status=200, content=["body"])
    _report_action_outcomes(result, pa)
    events = [json.loads(line) for line in
              log.read_text(encoding="utf-8").splitlines()]
    evaluated = [e for e in events if e.get("event") == "evaluate"]
    assert evaluated, events
    assert evaluated[0]["pending"] is False, evaluated[0]
