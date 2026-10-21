"""test_browser_5.py —— 由 3 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import inspect
import time
import pytest
from dhole_mcp.browser import BrowserSession
from dhole_mcp.server import PIN_NOTES
import json
import re
import socket
import sys
import threading
from pathlib import Path
from dhole_mcp import identity
from dhole_mcp.server import MasterFetchServer
import asyncio
from dhole_mcp.browser import PIN_TTL_S
from dhole_mcp.server import _SessionEntry
from dhole_mcp.wire import _wire_dump


# ============================================================================
# 来源：test_a_pinned_page_is_not_the_page_an_action_chain_named.py
# ============================================================================
# R01-027 · 驻留页不是点名那一页时，动作不许打在那一页上。

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


# ============================================================================
# 来源：test_a_chain_answers_the_challenge_of_the_page_it_is_standing_on.py
# ============================================================================
# 一条链要能答它站着的那一页 —— 而跨调用重试答的是上一枚挑战（真浏览器、真靶站）。

EVALS = Path(__file__).resolve().parent.parent / "evals"
if str(EVALS) not in sys.path:
    sys.path.insert(0, str(EVALS))


_REAL_GETADDRINFO = socket.getaddrinfo


def _browser_or_skip():
    from dhole_mcp.browser import check_browser_available
    if not check_browser_available():
        pytest.skip("no browser deps for the live tier")
    return True


@pytest.fixture
def target_site(monkeypatch):
    import fixture_site as fs

    monkeypatch.setattr(socket, "getaddrinfo", _REAL_GETADDRINFO)
    srv = fs.make_server("127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}", srv
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def private_door_open():
    """回环靶站要过 SSRF 门。

    不需要"用完关回去"：conftest 的 `_no_real_home_state_writes` 把 `identity.db` 重定向
    进每条用例自己的 tmp_path，这一格开的门活不出这条用例。
    """
    identity.set_value("allow_private", True)
    yield


def _rows(result) -> list[dict]:
    return (result.metadata or {}).get("actions") or []


@pytest.mark.live
@pytest.mark.asyncio
async def test_one_chain_reads_the_challenge_and_answers_that_same_page(target_site,
                                                                        private_door_open):
    """读到的那枚就是填进去的那枚 —— 站点那侧自己承认答对了。"""
    import fixture_site as fs
    _browser_or_skip()
    base, _ = target_site
    fs.CHALLENGE_LOG.unlink(missing_ok=True)

    result = await MasterFetchServer().web_act(
        url=f"{base}/challenge/text",
        actions=[{"read": {"selector": "#challenge", "as": "code"}},
                 {"fill": {"selector": "#answer", "text": "{=code}"}},
                 {"submit": {"selector": "#challenge-btn", "confirm": True}}],
        timeout=90000)

    rows = _rows(result)
    assert [r.get("ok") for r in rows] == [True, True, True], rows
    read_row, fill_row, submit_row = rows
    assert read_row["read_as"] == "code"
    assert re.fullmatch(r"[A-HJ-NP-Z2-9]{5}", read_row["read"]), read_row
    assert submit_row.get("submitted") is True, submit_row

    body = "".join(result.content or [])
    assert "抄写正确" in body, (result.summary, body[:300])

    # 独立旁证：站点自己判的卷，不是 dhole 说自己填对了。
    last = fs.CHALLENGE_LOG.read_text(encoding="utf-8").strip().splitlines()[-1]
    record = json.loads(last)
    assert record["ok"] is True, record
    assert record["answer"] == read_row["read"], (record, read_row)


@pytest.mark.live
@pytest.mark.asyncio
async def test_a_second_call_sees_a_different_challenge(target_site, private_door_open):
    """那句 "read-and-retry rounds" 的反证：两次导航不是同一枚挑战。

    断言的是 token 变了（答案由 token 派生），不是"答案大概不会撞上"。
    """
    _browser_or_skip()
    base, _ = target_site
    seen = []
    for _ in range(2):
        result = await MasterFetchServer().web_act(
            url=f"{base}/challenge/text",
            actions=[{"read": {"selector": "#token", "attribute": "value", "as": "t"}},
                     {"read": {"selector": "#challenge", "as": "code"}}],
            timeout=90000)
        rows = _rows(result)
        assert [r.get("ok") for r in rows] == [True, True], rows
        seen.append((rows[0]["read"], rows[1]["read"]))
    assert seen[0][0] != seen[1][0], f"两次导航拿到同一枚 token: {seen}"
    assert seen[0][1] != seen[1][1], f"两次导航的答案相同，这一格就没在量重试: {seen}"


# ============================================================================
# 来源：test_a_expired_pin_answers_the_zero_on_the_wire.py
# ============================================================================
# R02-073 · 「窗口过了」那一格在线上交不出它承诺的那个 0。
# 重名消歧：PINNED → PINNED__a_expired_pin_answers_th_dcb5、_Page → _Page__a_expired_pin_answers_th_dcb5

PINNED__a_expired_pin_answers_th_dcb5 = "http://127.0.0.1:8399/account"


class _Page__a_expired_pin_answers_th_dcb5:
    def __init__(self, url: str):
        self.url = url
        self.closed = False

    def is_closed(self) -> bool:
        return self.closed

    def remove_listener(self, *a):
        pass

    async def close(self):
        self.closed = True


def _slot(url: str = PINNED__a_expired_pin_answers_th_dcb5, *, age_s: float = 0.0) -> BrowserSession:
    s = BrowserSession()
    s._is_alive = True
    s._pin = {"page": _Page__a_expired_pin_answers_th_dcb5(url), "stamp": time.monotonic() - age_s,
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
    assert row.get("pinned_url") == PINNED__a_expired_pin_answers_th_dcb5, row
    assert "pinned_left_s" in row, f"手册承诺的那个 0 没上线：{row}"
    assert row["pinned_left_s"] == 0, row


def test_a_live_pin_answers_a_positive_countdown():
    """正控制：这颗数本来就该上线，别让「修形状」修成一直缺席。"""
    row = _wire_row(_slot(age_s=10), "live")
    assert row.get("pinned_url") == PINNED__a_expired_pin_answers_th_dcb5, row
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
    assert shapes["expired"] == json.dumps({"pinned_left_s": 0, "pinned_url": PINNED__a_expired_pin_answers_th_dcb5},
                                           sort_keys=True), shapes


def test_the_countdown_is_not_a_second_timer():
    """判决仍然只有一个作者：普查那颗数与 `_pin_take` 那道门同生同灭。"""
    for age, want_refused in ((PIN_TTL_S - 2, False), (PIN_TTL_S + 2, True)):
        session = _slot(age_s=age)
        row = _wire_row(session, f"age{int(age)}")
        left = row.get("pinned_left_s")
        _, why = asyncio.run(session._pin_take(PINNED__a_expired_pin_answers_th_dcb5))
        assert (why == "ttl") is want_refused, (age, left, why)
        if want_refused:
            assert left == 0, (age, left, "门已经拒了，普查还在说还剩几秒")
        else:
            assert left > 0, (age, left, "门还放行，普查却报不出剩余")
