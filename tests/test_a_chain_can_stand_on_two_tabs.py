"""一条链可以站在两枚页签上。

现场（2026-10-24，可提升清单 1d）：动作链从头到尾只有一页——「A 页表单填一半、
切 B 页查资料、回来提交」这类真人的多页流程做不了。对标 Playwright MCP 的
browser_tabs 补上，形状按 dhole 的合同收：

  * 页签是**动作**（`{"tab": "new"|"close"|"list"|N|{"open": url}}`），不是参数
    ——链内切页之后，后面的每一步都站在新活动页上（`page` 重绑），读回/截图
    跟着走；浏览器层从 `pin_state["active_page"]` 接手，落账不张冠李戴。
  * 最后一枚拒关——链总要有一页来交答案；close 之后活动页回 0 号位。
  * attach 模式拒开页签——那是用户自己的浏览器、用户自己的标签页。
  * open 的落地按入口站判：拐进内网/私有地址的页签就地关闭、按名拒绝。
  * 跨调用持久走 pin：钉住的调用把页签留进会话登记表，下一次带 pin 的调用
    `{"tab": N}` 直达（本文件只钉动作层；登记表的生死归 browser 层的收尾合同）。
"""

from __future__ import annotations

import asyncio

import pytest

from dhole_mcp.actions import _validate_actions, action_label, build_page_action


class _FakeContext:
    def __init__(self, page_factory):
        self._page_factory = page_factory
        self.opened = 0

    async def new_page(self):
        self.opened += 1
        return self._page_factory(f"tab://{self.opened}")


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
    def __init__(self, url: str = "https://fixture.test/"):
        self.url = url
        self.context = None          # 由装配处接上
        self.goto_calls: list = []
        self.closed = False
        self.dialog_handlers: list = []
        self.filled: list = []
        self.nths: list = []

    def set_default_navigation_timeout(self, ms):
        pass

    def set_default_timeout(self, ms):
        pass

    async def goto(self, url, **kw):
        self.goto_calls.append(url)
        self.url = url

    async def close(self):
        self.closed = True

    def is_closed(self):
        return self.closed

    def on(self, event, handler):
        if event == "dialog":
            self.dialog_handlers.append(handler)

    def remove_listener(self, event, handler):
        if handler in self.dialog_handlers:
            self.dialog_handlers.remove(handler)

    def locator(self, sel):
        return self

    @property
    def first(self):
        return self

    def nth(self, i):
        self.nths.append(i)
        return self

    async def fill(self, text, **kw):
        self.filled.append(text)

    async def wait_for_timeout(self, ms):
        await asyncio.sleep(ms / 1000)

    async def evaluate(self, js, arg=None):
        return "field"


def _rig():
    """主页面 + 会开新页的 context；返回 (page, made_pages)。"""
    main = _FakePage("https://fixture.test/form")
    made: list = []

    def factory(url):
        p = _FakePage(url)
        made.append(p)
        return p

    main.context = _FakeContext(factory)
    return main, made


# ── 1 · 校验 ────────────────────────────────────────────────────────────

def test_tab_shapes_validate():
    out = _validate_actions([{"tab": "new"}, {"tab": "close"}, {"tab": "list"},
                             {"tab": 2}, {"tab": {"open": "https://x.test/"}}])
    assert [next(iter(a)) for a in out] == ["tab"] * 5
    assert out[3]["tab"] == 2
    assert out[4]["tab"] == {"open": "https://x.test/"}


@pytest.mark.parametrize("bad", [True, "maybe", 99, {"open": "ftp://x"},
                                 {"open": "https://x/", "extra": 1}, {}, 1.5])
def test_bad_tab_shapes_are_refused(bad):
    with pytest.raises(ValueError):
        _validate_actions([{"tab": bad}])


def test_tab_label_names_the_verb():
    assert action_label({"tab": "new"}) == "tab new"
    assert action_label({"tab": 3}) == "tab switch to 3"
    assert "tab open" in action_label({"tab": {"open": "https://x.test/"}})


# ── 2 · 执行：开、切、关、列 ────────────────────────────────────────────

def test_new_tab_opens_and_following_steps_run_on_it():
    main, made = _rig()
    pa = build_page_action([{"tab": "new"}, {"fill": {"selector": "#q",
                                                     "text": "hello"}}])
    asyncio.run(pa(main))
    assert len(made) == 1
    assert made[0].filled == ["hello"]
    assert main.filled == []
    assert pa.pin_state["active_page"] is made[0]
    # 全表如实交出（含 0 号位）：浏览器层自己排除活动页
    assert pa.pin_state["tab_pages"] == [main, made[0]]


def test_open_carries_the_url_and_reports_its_index():
    main, made = _rig()
    pa = build_page_action([{"tab": {"open": "https://fixture.test/other"}},
                            {"read": {"selector": "#x"}}])
    asyncio.run(pa(main))
    assert made[0].goto_calls == ["https://fixture.test/other"]
    row = pa.outcomes[0]
    assert row["tab_opened"] == 1 and row["url"] == "https://fixture.test/other"


def test_switch_by_index_moves_back_and_forth():
    main, made = _rig()
    pa = build_page_action([{"tab": "new"}, {"tab": 0},
                            {"fill": {"selector": "#q", "text": "back"}}])
    asyncio.run(pa(main))
    assert main.filled == ["back"] and made[0].filled == []
    assert pa.pin_state["active_page"] is main


def test_switch_beyond_inventory_fails_naming_it():
    main, made = _rig()
    pa = build_page_action([{"tab": 5}])
    asyncio.run(pa(main))
    row = pa.outcomes[0]
    assert row["ok"] is False and "does not exist" in row["error"]


def test_closing_the_last_tab_is_refused():
    main, _ = _rig()
    pa = build_page_action([{"tab": "close"}])
    asyncio.run(pa(main))
    row = pa.outcomes[0]
    assert row["ok"] is False and "only tab" in row["error"]
    assert main.closed is False


def test_close_drops_the_active_tab_and_lands_on_zero():
    main, made = _rig()
    pa = build_page_action([{"tab": "new"}, {"tab": "close"}])
    asyncio.run(pa(main))
    assert made[0].closed is True
    row = pa.outcomes[1]
    assert row["tab_closed"] == 1 and row["tab_active_now"] == 0
    assert pa.pin_state["active_page"] is main


def test_list_returns_the_inventory():
    main, made = _rig()
    pa = build_page_action([{"tab": {"open": "https://fixture.test/b"}},
                            {"tab": "list"}])
    asyncio.run(pa(main))
    row = pa.outcomes[1]
    assert row["tabs"] == [{"index": 0, "url": "https://fixture.test/form"},
                           {"index": 1, "url": "https://fixture.test/b"}]


# ── 3 · 边界：attach、内网、开页失败 ────────────────────────────────────

def test_attach_sessions_refuse_tab_actions():
    main, made = _rig()
    pa = build_page_action([{"tab": "new"}])
    pa.pin_state["attach"] = True
    asyncio.run(pa(main))
    assert pa.outcomes[0]["ok"] is False
    assert "own browser" in pa.outcomes[0]["error"]
    assert made == []


def test_an_open_that_lands_inside_is_closed_and_refused(monkeypatch):
    main, made = _rig()
    monkeypatch.setattr("dhole_mcp.browser._landed_url_refused",
                        lambda landed, entry: True)
    pa = build_page_action([{"tab": {"open": "https://fixture.test/x"}}])
    pa.pin_state["entry_url"] = "https://fixture.test/form"
    asyncio.run(pa(main))
    row = pa.outcomes[0]
    assert row["ok"] is False and "internal/private" in row["error"]
    assert made and made[0].closed is True          # 页签就地关闭
    assert pa.pin_state["tab_pages"] == [main]      # 拒绝的页签不在表里


def test_a_failed_open_closes_its_tab_and_says_why():
    main, made = _rig()

    def factory(url):
        p = _FakePage(url)
        async def boom(url_, **kw):
            raise RuntimeError("net::ERR_NAME_NOT_RESOLVED at " + url_)
        p.goto = boom
        made.append(p)
        return p

    main.context = _FakeContext(factory)
    pa = build_page_action([{"tab": {"open": "https://nope.test/"}}])
    asyncio.run(pa(main))
    row = pa.outcomes[0]
    assert row["ok"] is False and "tab open failed" in row["error"]
    assert made[0].closed is True
    assert pa.pin_state["tab_pages"] == [main]      # 开失败的页签不在表里


# ── 4 · 对话框账本跟着活动页走 ──────────────────────────────────────────

def test_the_dialog_ledger_follows_the_new_tab():
    """断言时机在链**内**：page_action 的 finally 会把监听器摘干净（有借有还），
    所以事后看 handler 是空的——账本是否跟页，要由"链上新页弹的对话框进了账"
    来证明。"""
    main, made = _rig()

    def factory(url):
        p = _FakePage(url)
        # 新页一被创建就弹一个 confirm（模拟切过去之后页面弹窗）
        orig_on = p.on

        def on(event, handler):
            orig_on(event, handler)
            if event == "dialog" and p.dialog_handlers:
                loop = asyncio.get_event_loop()
                dlg = _FakeDialog("confirm", "delete?")
                loop.create_task(p.dialog_handlers[-1](dlg))

        p.on = on
        made.append(p)
        return p

    main.context = _FakeContext(factory)
    pa = build_page_action([{"tab": "new"}, {"wait": 50}])
    asyncio.run(pa(main))
    dlg_rows = [o for o in pa.outcomes if o.get("action") == "dialogs"]
    assert dlg_rows and dlg_rows[-1]["seen"][0]["message"] == "delete?", pa.outcomes


def test_tab_pages_survive_into_pin_state_at_chain_end():
    main, made = _rig()
    pa = build_page_action([{"tab": "new"}, {"tab": "new"}])
    asyncio.run(pa(main))
    assert pa.pin_state["tab_pages"] == [main] + made
