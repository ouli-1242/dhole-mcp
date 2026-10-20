# -*- coding: utf-8 -*-
"""R02-073 · 「窗口过了」那一格在线上交不出它承诺的那个 0。

现场（不是编的，是 R02-F59 的 P5 第一趟量到的，`review/round-02/sessions/P02-R5-F59-p5.jsonl`
第 `census-after-ttl` 那一发）：睡过 `PIN_TTL_S` 之后普查那一行是

    {"session_id": "r02-f59-pin", "browser_open": true, ...,
     "pinned_url": "http://127.0.0.1:8399/hoverdrag"}

—— 地址在，`pinned_left_s` **缺席**。而 `models.py` 的字段描述与 `tools.md` 手册那一行都写着
「a `pinned_url` with `pinned_left_s: 0` = the window passed」。读者被告知会看到一个 0，
线上永远不给这个 0。

根因不在 `pin_census()`（它算的是对的），在压实层的那条通则：
`wire._wire_omittable` 把「等于自己默认值的字段」省略掉，而 `pinned_left_s` 的默认值是 `0`
⇒ 状态②（过期、那一页还挂在槽里等下一发来收）与状态①（什么都没钉）在**这颗数**上被压成同一个
形状。`wire.py` 文件头自己写的政策是「verdict 不是 default —— 每一个作为判决而不是默认值的
False 都要留在网上」（`content_ok` 就是这一族），本条是同一族里漏掉的一颗：**0 带地址是判决，
0 不带地址才是默认**。

修法不许是「把整颗字段从省略表里摘出来」—— 那会给每一行（绝大多数什么都没钉着）加两格样板，
正是 R02-F59 立沉默锁要避免的东西。要改的是这颗数的**默认**：没钉着时交 `None`（缺席说的就是
「这一行没有驻留的页」），钉着时交那一个数，**包括 0** —— 0 != None，于是判决上得了线，
而沉默还是沉默。

反面锁（不许顺手多修）：
  * 不许为此新增第二只钟、不许读 `PIN_TTL_S` 的第二个抄本；
  * 不许把状态①与②抹成一种（那是要修的病，不是要省的字数）；
  * 普查依旧只读：不许因为交得出 0 就顺手收页。
"""

from __future__ import annotations

import asyncio
import json
import time

from dhole_mcp.browser import PIN_TTL_S, BrowserSession
from dhole_mcp.server import MasterFetchServer, _SessionEntry
from dhole_mcp.wire import _wire_dump

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


def _slot(url: str = PINNED, *, age_s: float = 0.0) -> BrowserSession:
    s = BrowserSession()
    s._is_alive = True
    s._pin = {"page": _Page(url), "stamp": time.monotonic() - age_s,
              "response": None, "listener": None, "route": None}
    return s


def _wire_row(session: BrowserSession, sid: str) -> dict:
    """真走一遍装配与压实：现场那一发读到的就是这条路交出的东西。"""
    server = MasterFetchServer()
    server._sessions[sid] = _SessionEntry(session=session, session_type="stealthy")
    rows = asyncio.run(server._session_rows())
    return next(r for r in (_wire_dump(x) for x in rows) if r["session_id"] == sid)


def test_the_expired_pin_answers_the_zero_the_handbook_promises():
    """状态②：窗口过了、页还挂在槽里 —— 线上必须同时看得到地址和那颗 0。"""
    row = _wire_row(_slot(age_s=PIN_TTL_S + 5), "expired")
    assert row.get("pinned_url") == PINNED, row
    assert "pinned_left_s" in row, f"手册承诺的那个 0 没上线：{row}"
    assert row["pinned_left_s"] == 0, row


def test_a_live_pin_answers_a_positive_countdown():
    """正控制：这颗数本来就该上线，别让「修形状」修成一直缺席。"""
    row = _wire_row(_slot(age_s=10), "live")
    assert row.get("pinned_url") == PINNED, row
    assert 0 < row["pinned_left_s"] <= PIN_TTL_S, row


def test_a_session_with_nothing_pinned_stays_silent_on_both_fields():
    """状态①：什么都没钉 —— 两格都不许出现（不许为修②给每一行加样板）。"""
    row = _wire_row(BrowserSession(), "idle")
    assert "pinned_url" not in row, row
    assert "pinned_left_s" not in row, row


def test_the_three_states_are_three_different_wire_shapes():
    """①缺席 ②地址+0 ③地址+n —— 两种状态不许压成同一种形状。

    这一格是本条的判决本身：现场那一发读到的就是「②与①在这一颗数上不可分辨」。
    """
    by = {
        tag: _wire_row(session, tag)
        for tag, session in (("idle", BrowserSession()),
                             ("expired", _slot(age_s=PIN_TTL_S + 5)),
                             ("live", _slot(age_s=10)))
    }
    shapes = {tag: json.dumps({k: v for k, v in row.items()
                               if k.startswith("pinned_")}, sort_keys=True)
              for tag, row in by.items()}
    assert len(set(shapes.values())) == 3, shapes
    assert shapes["idle"] == "{}", shapes
    assert shapes["expired"] == json.dumps({"pinned_left_s": 0, "pinned_url": PINNED},
                                           sort_keys=True), shapes


def test_the_countdown_is_not_a_second_timer():
    """判决仍然只有一个作者：普查那颗数与 `_pin_take` 那道门同生同灭。"""
    for age, want_refused in ((PIN_TTL_S - 2, False), (PIN_TTL_S + 2, True)):
        session = _slot(age_s=age)
        row = _wire_row(session, f"age{int(age)}")
        left = row.get("pinned_left_s")
        _, why = asyncio.run(session._pin_take(PINNED))
        assert (why == "ttl") is want_refused, (age, left, why)
        if want_refused:
            assert left == 0, (age, left, "门已经拒了，普查还在说还剩几秒")
        else:
            assert left > 0, (age, left, "门还放行，普查却报不出剩余")
