"""对话框要么被作答、要么被如实记账，不许再静默。

现场（2026-10-24，可提升清单 1）：Playwright 对没人监听的对话框默认自动
dismiss——安全侧正确，但它**静默**：agent 点了"删除"，页面弹 confirm，没人答，
点按落空，回执里一个字都没有。动作链从今挂一本账：

  * 链首装监听器，链上弹的每一个对话框都从这里走：有人先行武装
    {"dialog": "accept"|"dismiss"} 就照武装的答（prompt 的 prompt_text 一并
    交出去），没人武装就照旧 dismiss——**并记下来**；
  * 链尾多一行 "dialogs"：弹没弹、弹的什么、谁答的、武装了没用上的，一眼可查。

缺省行为不回退：不带 dialog 动作的链与从前逐字同形（自动 dismiss 照旧发生，
只是从"没人知道"变成"账本一行"）。
"""

from __future__ import annotations

import asyncio

import pytest

from dhole_mcp.actions import _validate_actions, build_page_action


class _FakeDialog:
    def __init__(self, type_: str, message: str):
        self.type = type_
        self.message = message
        self.accepted: list = []
        self.dismissed = 0

    async def accept(self, prompt_text=None):
        self.accepted.append(prompt_text)

    async def dismiss(self):
        self.dismissed += 1


class _FakePage:
    def __init__(self, dialogs=()):
        self._dialog_handler = None
        self._pending = list(dialogs)
        self._listeners: list = []
        self.clicks = 0

    def locator(self, sel):
        return self

    @property
    def first(self):
        return self

    async def click(self, **kw):
        self.clicks += 1
        # 点击是弹窗的扳机：预置的对话框在点击后交给监听器
        while self._pending:
            dlg = self._pending.pop(0)
            if self._dialog_handler:
                asyncio.get_running_loop().create_task(
                    self._dialog_handler(dlg))
                # 给处理器一个排队的回合
                await asyncio.sleep(0)

    async def evaluate(self, js, arg=None):
        return "btn"

    def on(self, event, handler):
        if event == "dialog":
            self._dialog_handler = handler
        self._listeners.append((event, handler))

    def remove_listener(self, event, handler):
        self._listeners = [(e, h) for e, h in self._listeners
                           if (e, h) != (event, handler)]
        if (event, handler) not in self._listeners and event == "dialog" \
                and self._dialog_handler is handler:
            pass  # 假页不做真移除；断言靠 _listeners 内容


def _dialogs_row(outcomes):
    rows = [o for o in outcomes if o.get("action") == "dialogs"]
    return rows[-1] if rows else None


# ── 1 · 校验 ────────────────────────────────────────────────────────────

def test_dialog_takes_only_accept_or_dismiss():
    with pytest.raises(ValueError, match="accept"):
        _validate_actions([{"dialog": "maybe"}])


def test_dialog_prompt_text_is_capped():
    with pytest.raises(ValueError, match="prompt_text"):
        _validate_actions([{"dialog": {"answer": "accept",
                                          "prompt_text": "x" * 201}}])


def test_dialog_normalizes_the_dict_shape():
    out = _validate_actions([{"dialog": {"answer": "accept",
                                            "prompt_text": "...answer with..."}}])
    assert out[0]["dialog"] == {"answer": "accept", "prompt_text": "...answer with..."}


# ── 2 · 武装作答 ────────────────────────────────────────────────────────

def test_an_armed_accept_answers_the_confirm():
    dlg = _FakeDialog("confirm", "delete this item?")
    page = _FakePage(dialogs=[dlg])
    pa = build_page_action([{"dialog": "accept"}, {"click": "#delbtn"}])
    asyncio.run(pa(page))
    assert dlg.accepted == [None] and dlg.dismissed == 0
    seen = _dialogs_row(pa.outcomes)["seen"]
    assert seen[0]["type"] == "confirm"
    assert seen[0]["answered"] == "accepted (armed)"


def test_an_armed_prompt_hands_over_its_text():
    dlg = _FakeDialog("prompt", "your name?")
    page = _FakePage(dialogs=[dlg])
    pa = build_page_action([{"dialog": {"answer": "accept",
                                        "prompt_text": "huan"}},
                            {"click": "#namebtn"}])
    asyncio.run(pa(page))
    assert dlg.accepted == ["huan"]


def test_an_armed_dismiss_declines_it():
    dlg = _FakeDialog("confirm", "really?")
    page = _FakePage(dialogs=[dlg])
    pa = build_page_action([{"dialog": "dismiss"}, {"click": "#delbtn"}])
    asyncio.run(pa(page))
    assert dlg.dismissed == 1 and dlg.accepted == []
    row = _dialogs_row(pa.outcomes)["seen"][0]
    assert row["answered"] == "dismissed (armed)"


# ── 3 · 没人武装：照旧 dismiss，但要有账 ────────────────────────────────

def test_an_unarmed_dialog_is_dismissed_and_recorded():
    dlg = _FakeDialog("confirm", "delete this item?")
    page = _FakePage(dialogs=[dlg])
    pa = build_page_action([{"click": "#delbtn"}])
    asyncio.run(pa(page))
    assert dlg.dismissed == 1 and dlg.accepted == []
    seen = _dialogs_row(pa.outcomes)["seen"][0]
    assert seen["answered"] == "auto-dismissed"
    assert "dialog" in seen["note"]


def test_an_unused_arm_is_reported_not_lost():
    page = _FakePage()          # 没有预置对话框
    pa = build_page_action([{"dialog": "accept"}, {"click": "#delbtn"}])
    asyncio.run(pa(page))
    row = _dialogs_row(pa.outcomes)
    assert row["seen"][-1]["armed_unused"] == 1


def test_a_chain_without_dialogs_grows_no_extra_row():
    page = _FakePage()
    pa = build_page_action([{"click": "#plain"}])
    asyncio.run(pa(page))
    assert _dialogs_row(pa.outcomes) is None
    assert len(pa.outcomes) == 1


# ── 4 · 监听器有借有还 ──────────────────────────────────────────────────

def test_the_listener_is_removed_when_the_chain_ends():
    page = _FakePage()
    pa = build_page_action([{"click": "#plain"}])
    asyncio.run(pa(page))
    assert page._dialog_handler is None or \
        ("dialog", page._dialog_handler) not in page._listeners


def test_two_dialogs_two_answers_in_order():
    d1 = _FakeDialog("confirm", "first?")
    d2 = _FakeDialog("confirm", "second?")
    page = _FakePage(dialogs=[d1, d2])
    pa = build_page_action([{"dialog": "accept"}, {"dialog": "dismiss"},
                            {"click": "#delbtn"}, {"click": "#delbtn"}])
    asyncio.run(pa(page))
    assert d1.accepted == [None] and d2.dismissed == 1
    seen = _dialogs_row(pa.outcomes)["seen"]
    assert [s["answered"] for s in seen[:2]] == ["accepted (armed)", "dismissed (armed)"]
