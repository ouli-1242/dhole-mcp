# -*- coding: utf-8 -*-
"""R01-027 · 驻留页不是点名那一页时，动作不许打在那一页上。

现场（P06 何俊，`sessions/P06-s7-pin-set.json` / `P06-s8-pin-reuse.json`，对照
`P06-s5c-hover-html.json`）：S3 把 `/account` 钉在 session `shop` 上；随后

    web_act(session_id="shop", pin=true, url="http://127.0.0.1:8399/hoverdrag",
            actions=[{"hover": {"index": 0}}, {"wait": 600}], extraction_type="html")

回的是 177 字符的**账户页** HTML（`<title>账户</title>` + 退出登录），不是 /hoverdrag 的
1909 字符。同站（host 相同）在 `_pin_take` 的四条闸门里是**允许复用**的，而 `index` 是在
用户点名的那一页上数出来的元素序号 —— 在账户页上 `hover index 0` 就是「退出登录」那颗按钮。
回执确实说了「跑在你上次钉住的那页」，但没说那是**哪一页**，也没说**点名的地址没有被采用**。

这一刀拆成两件事，各按其危险度：

* **动作**（会改世界的那一半）：目标不一致就不复用 —— 这一发导航到点名的那一页去。
  驻留本身不被拆：那一页仍留在槽里，下一发 pin 照常站在它上面（H8 买到的 3.9s→0.4s
  不许因此变成永远重新导航）。
* **只读**（snapshot / html）：站在驻留页上是 pin 的语义，保留；但句子要说清是哪一页
  （见 server 侧 `PIN_NOTES`）。
"""

from __future__ import annotations

import inspect
import time

import pytest

from dhole_mcp.browser import BrowserSession
from dhole_mcp.server import PIN_NOTES

PINNED = "http://127.0.0.1:8399/account"
NAMED = "http://127.0.0.1:8399/hoverdrag"


class _Page:
    def __init__(self, url: str):
        self.url = url
        self.closed = False

    def is_closed(self) -> bool:
        return self.closed

    def remove_listener(self, *a):
        pass

    async def close(self):
        self.closed = True


def _session(page: _Page) -> BrowserSession:
    s = BrowserSession()
    s._pin = {"page": page, "stamp": time.monotonic(), "response": None,
              "listener": None, "route": None, "url": page.url}
    return s


# ─── 1 · 判定本身 ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_action_chain_does_not_take_a_pinned_page_that_is_a_different_page():
    page = _Page(PINNED)
    s = _session(page)
    slot, why = await s._pin_take(NAMED, for_actions=True)
    assert slot is None and why == "target", (slot, why)
    assert page.closed is False, "点名页与驻留页不同时，驻留页被关掉了：pin 被这一刀拆了"


@pytest.mark.asyncio
async def test_the_pin_survives_that_refusal_for_the_next_call():
    """拒绝这一发的复用，不等于没收这一份驻留 —— 下一发点回 /account 还得站在它上面。"""
    page = _Page(PINNED)
    s = _session(page)
    await s._pin_take(NAMED, for_actions=True)
    slot, why = await s._pin_take(PINNED, for_actions=True)
    assert slot is not None and why == "", (slot, why)
    assert slot["page"] is page


@pytest.mark.asyncio
async def test_a_read_still_stands_on_the_pinned_page():
    """只读复用是 pin 的语义本身（H8），一个字的默认行为都不许动。"""
    page = _Page(PINNED)
    s = _session(page)
    slot, why = await s._pin_take(NAMED, for_actions=False)
    assert slot is not None and why == "", (slot, why)


@pytest.mark.asyncio
async def test_an_action_chain_still_reuses_when_it_named_that_very_page():
    page = _Page(PINNED)
    s = _session(page)
    slot, why = await s._pin_take(PINNED, for_actions=True)
    assert slot is not None and why == ""


@pytest.mark.asyncio
async def test_a_different_host_still_closes_the_pin_as_it_always_did():
    """换站那条闸门是本刀之前就有的行为，不许被新分支顺手动掉。"""
    page = _Page(PINNED)
    s = _session(page)
    slot, why = await s._pin_take("http://elsewhere.test/x", for_actions=True)
    assert (slot, why) == (None, "host")
    assert page.closed is True


@pytest.mark.parametrize("a,b,expected", [
    ("http://h/a", "http://h/a", True),
    ("http://h/a/", "http://h/a", True),
    ("http://h/a#top", "http://h/a", True),
    ("http://h/a?x=1", "http://h/a?x=1", True),
    ("http://h/a", "http://h/b", False),
    ("http://h/a?x=1", "http://h/a?x=2", False),
    ("http://h/deep/page", "http://h/deep", False),
    ("", "http://h/a", False),
    ("not a url", "http://h/a", False),
])
def test_the_target_comparison_is_a_document_comparison_not_a_string(a, b, expected):
    """同一个文档 = 同 host + 同 path（尾斜杠不算两个页）+ 同 query；fragment 不算换页。
    判定写成函数是为了可测，而它只回答「是不是同一张纸」，不回答别的。"""
    assert BrowserSession._same_document(a, b) is expected


def test_fetch_asks_that_question_with_the_chain_it_is_about_to_run():
    """接线不许靠记忆：`fetch` 必须把「这一发带不带动作」告诉 `_pin_take`。"""
    src = inspect.getsource(BrowserSession.fetch)
    assert "for_actions=" in src, "复用闸门没有收到这一发的动作信息"


def test_the_note_says_which_page_was_left_behind_and_that_the_pin_still_holds():
    text = PIN_NOTES["target"]
    assert "not" in text.lower()
    assert "pin" in text.lower() and "stays" in text.lower(), text
    assert "{pinned}" in text and "{named}" in text, (
        "句子必须能把两页各是谁说出口，否则用户还是不知道动作打在哪一页")
