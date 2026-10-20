# -*- coding: utf-8 -*-
"""R02-029 · 驻留的时间账要能在**事前**问到，不是等它过期后从 summary 里得知。

现场（P02-07 邬桐，`sessions/P02-07-s6a-sessions.json` / `P02-07-s7b-sessions-after-idle.json`
/ `P02-07-s7-after-idle.json`）：她的动作是「把窗口开着，二十分钟之后回来再点一次」，
回来之前先 `sys_state(show=['sessions'])` 查一眼还在不在 —— 普查每一行只有
`session_id / browser_open / browser_type / browser_created` 四颗，**没有驻留这一格**。
她只能盲打一发，然后在**那一发的 summary 里**读到页面已过期。

读码定到的根因（不是「没存」）：
  * 那一页**是存着的**：`browser.py:2331` 把槽存成
    `{"page", "route", "listener", "response", "stamp"}`，`stamp = time.monotonic()`；
  * 过期判据也**是有的**：`_pin_take` 里 `time.monotonic() - stamp > PIN_TTL_S`（`PIN_TTL_S = 300`）；
  * 缺的是普查**没问它**：`server._session_rows` 只读 `alive / session_type / created_at`，
    一句没提 `self._pin`。

所以这一刀不加第三条判据，只把已有的那一条**换个朝向交出来**：`BrowserSession.pin_census()`
用 `PIN_TTL_S` 与同一个 `stamp` 做减法，给 `pinned_url` + `pinned_left_s`（倒计时，不是
时刻）。倒计时这个形状有现成的同族先例 —— 同一张表里的 `expires_in_s` 就是这么写的：
「expires in 21h」是能行动的，一个 `1790566780.9` 不是；而且 `stamp` 是单调钟，
它压根不是一个能对外报的时刻。

反面锁（不许顺手多修的东西）：没驻留时这两个数都在省略表里（与 `browser_open=false`
同一族的沉默）；`pin_census` 不许关闭那一页、不许摘那一页、不许改动 `_pin_take` 的判据。
"""

from __future__ import annotations

import asyncio
import time

import pytest

from dhole_mcp.browser import PIN_TTL_S, BrowserSession
from dhole_mcp.server import MasterFetchServer, _SessionEntry
from dhole_mcp.wire import _WIRE_OMIT, _wire_dump

PINNED = "http://127.0.0.1:8399/account"


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


def _pinned(url: str = PINNED, *, age_s: float = 0.0) -> BrowserSession:
    s = BrowserSession()
    s._is_alive = True  # 手上真有一页的会话是活着的（普查只在活着的那一侧问驻留）
    s._pin = {"page": _Page(url), "stamp": time.monotonic() - age_s,
              "response": None, "listener": None, "route": None}
    return s


# ─── 1 · 事前问得到的那两格 ─────────────────────────────────────────────────

def test_a_session_without_a_pin_says_nothing_is_pinned():
    got = BrowserSession().pin_census()
    # 没钉着那一支交 None 而不是 0：带地址的 0 是判决，压实层要把它留在网上
    # （R02-073 改的就是「用 0 表示没钉着」这个期望本身，见
    # `tests/test_a_expired_pin_answers_the_zero_on_the_wire.py`）。
    assert got == {"pinned_url": "", "pinned_left_s": None}, got


def test_a_fresh_pin_names_the_page_and_counts_down_from_the_same_ttl():
    got = _pinned().pin_census()
    assert got["pinned_url"] == PINNED, got
    assert 0 < got["pinned_left_s"] <= PIN_TTL_S, got


def test_the_countdown_is_the_ttl_constant_minus_the_age_of_the_slot():
    """不是「另一个 300」：句里那个数必须与 `_pin_take` 用的是同一个作者。"""
    for age in (120, 240, PIN_TTL_S - 1):
        got = _pinned(age_s=age).pin_census()
        assert abs(got["pinned_left_s"] - (PIN_TTL_S - age)) <= 1, (age, got)


def test_a_pin_whose_window_has_passed_still_names_its_page():
    """0 秒 + 有地址 = 「这一页还在槽里，只是过期了，下一发看它时才放掉」。

    这与「什么都没钉」是两件不同的事，说成一件就是把一种状态抹成另一种。
    """
    got = _pinned(age_s=PIN_TTL_S + 5).pin_census()
    assert got["pinned_left_s"] == 0, got
    assert got["pinned_url"] == PINNED, got


def test_a_pinned_page_that_someone_else_closed_is_not_reported_as_still_there():
    s = _pinned()
    s._pin["page"].closed = True
    assert s.pin_census() == {"pinned_url": "", "pinned_left_s": None}, s.pin_census()


def test_reading_the_census_does_not_touch_the_pin():
    """普查是只读的：不许为了「还剩几秒」把那一步的复用性一起付掉。"""
    s = _pinned()
    s.pin_census()
    assert s._pin is not None, "问一眼就把驻留页摘了/关了"
    assert s._pin["page"].closed is False


# ─── 2 · 与那道门同一个作者（两侧对得上）────────────────────────────────────

@pytest.mark.asyncio
async def test_the_census_counts_down_to_exactly_where_the_gate_refuses():
    for age, want_left_positive, want_why in (
            (PIN_TTL_S - 2, True, ""),
            (PIN_TTL_S + 2, False, "ttl")):
        s = _pinned(age_s=age)
        left = s.pin_census()["pinned_left_s"]
        assert (left > 0) is want_left_positive, (age, left)
        slot, why = await s._pin_take(PINNED)
        assert why == want_why, (age, why)
        assert (slot is not None) is want_left_positive, (age, slot)


# ─── 3 · 普查真的把这两格交出来（现场那发问的就是这个）──────────────────────

def _rows(server: MasterFetchServer) -> list:
    return asyncio.run(server._session_rows())


def test_the_census_row_carries_the_pin_that_the_call_left_behind():
    server = MasterFetchServer()
    server._sessions["shop"] = _SessionEntry(session=_pinned(),
                                             session_type="stealthy")
    row = [r for r in _rows(server) if r.session_id == "shop"][0]
    assert row.pinned_url == PINNED, row
    assert 0 < row.pinned_left_s <= PIN_TTL_S, row


def test_a_jar_only_session_is_not_claimed_to_hold_a_page():
    """她的普查里就有这样一行（只登录过、没开浏览器）—— 不许读成「有一页钉着，0 秒」。"""
    from dhole_mcp import sessions as jar

    jar.store("onlyjar", PINNED, {"sid": "v"})
    try:
        server = MasterFetchServer()
        row = [r for r in _rows(server) if r.session_id == "onlyjar"]
        assert row, "名册里找不到这一行"
        assert row[0].browser_open is False
        assert row[0].pinned_url == "" and row[0].pinned_left_s is None, row[0]
    finally:
        jar.clear("onlyjar")


# ─── 4 · 沉默与线的形状 ─────────────────────────────────────────────────────

def test_both_pin_fields_ride_off_the_wire_when_nothing_is_pinned():
    """没驻留时不许在每一行加两格样板；正控制：真驻留着时必须上得了线。"""
    assert {"pinned_url", "pinned_left_s"} <= _WIRE_OMIT["SessionCensusRow"]
    server = MasterFetchServer()
    server._sessions["idle"] = _SessionEntry(session=BrowserSession(),
                                             session_type="stealthy")
    server._sessions["live"] = _SessionEntry(session=_pinned(),
                                             session_type="stealthy")
    by = {r["session_id"]: r for r in (_wire_dump(x) for x in _rows(server))}
    assert "pinned_url" not in by["idle"], by["idle"]
    assert by["live"]["pinned_url"] == PINNED, by["live"]
    assert by["live"]["pinned_left_s"] > 0, by["live"]


# ─── 5 · 问得到，也要查得着（手册层：她那发是先读手册再决定要不要查）────────

def test_the_tools_handbook_says_the_pin_is_askable_before_the_fact():
    from pathlib import Path

    text = (Path(__file__).resolve().parent.parent / "src" / "dhole_mcp" / "skills"
            / "dhole-web" / "references" / "tools.md").read_text(encoding="utf-8")
    line = [entry for entry in text.splitlines()
            if entry.startswith("- No arguments = **the census**")]
    assert len(line) == 1, "普查那一行丢了或被拆成两行"
    said = line[0]
    assert "pinned_url" in said and "pinned_left_s" in said, said
    # 三种状态各有说法，不许两种抹成一种
    assert "nothing pinned" in said and "window passed" in said, said
    # 判据是同一个作者，手册不许把它写成第二只钟
    assert "same gate the next pinned call applies" in said, said

