# -*- coding: utf-8 -*-
"""驻留的那一页答的就是它上一发显示的那道题（真浏览器 + 真靶站，`-m live` 才选上）。

体验报告 S10 的那一句是"我明明看清了它说我错！"，而它不是运气差：靶站 `/captcha` 每次
`GET` 发一枚**新 token**（`fixture_site.new_captcha_token`），答案由那枚 token 确定性
派生（`captcha_text`），`/captcha/verify` 只认 POST 里带回来的那一枚 token。`web_act`
默认每次调用都 `new_page()` + `goto()`，所以"上一发看清了图、这一发填进去"在结构上
必然答的是上一道题 —— 跨调用重试永远 403。

`pin=true` 修的是这个结构，这一格量的就是修好以后站点自己判的卷：

* **正面**：第一次调用读 token 并驻留；第二次同 session、同 URL 再读 —— token 逐字没变
  （没重新导航的证据），填的是第一次那枚的派生答案，`CAPTCHA_LOG` 最后一行 `ok: true`，
  交回来的页面写着"验证码正确"。
* **反面**：同一条路把 `pin` 去掉，第二次读到的就是另一枚 token，第一次那个答案递过去
  必被拒（403）。`test_a_chain_answers_the_challenge_of_the_page_it_is_standing_on.py::
  test_a_second_call_sees_a_different_challenge` 钉住了"两次导航不是同一枚挑战"，本文件
  把它的后果补完 —— 不只是不同，而且是**答错**。
* **驻留页上的审批话要说实话**：`{"submit": ...}` 被审批闸拦下时那句"把同一串动作重跑
  一遍"在驻留页上是半句假话（前面的 fill 已经跑过了），所以它必须点名驻留。

答案由 token 派生而不是 OCR：这一格量的是**结构**（同一页、同一枚 token），不是模型能
不能认出扭曲的字 —— 后者是 `evals/captcha_baseline.py` 的事。
"""

from __future__ import annotations

import json
import socket
import sys
import threading
from pathlib import Path

import pytest

EVALS = Path(__file__).resolve().parent.parent / "evals"
if str(EVALS) not in sys.path:
    sys.path.insert(0, str(EVALS))

from dhole_mcp import identity  # noqa: E402
from dhole_mcp.server import PIN_NOTES, MasterFetchServer  # noqa: E402

_REAL_GETADDRINFO = socket.getaddrinfo
_READ_TOKEN = [{"read": {"selector": "input[name=\"token\"]",
                         "attribute": "value", "as": "token"}}]


def _browser_or_skip():
    from dhole_mcp.browser import check_browser_available
    if not check_browser_available():
        pytest.skip("no browser deps for the live tier")
    return True


@pytest.fixture
def target_site(monkeypatch):
    """真 socket + 真 DNS：SSRF 守卫要看真地址，替身会把 127.0.0.1 变成假 IP。"""
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
    """回环靶站要过 SSRF 门；conftest 把 identity.db 重定向进每条用例自己的 tmp_path。"""
    identity.set_value("allow_private", True)
    yield


def _rows(result) -> list[dict]:
    return (result.metadata or {}).get("actions") or []


@pytest.fixture
def one_server():
    """两次调用必须落在**同一个** server 实例上：会话表是实例状态（`self._sessions`），
    各造一个 `MasterFetchServer()` 的话第二次拿到的是另一台浏览器 —— 驻留自然不成立，
    而那是一句关于测试装置的话，不是一句关于 dhole 的话。"""
    return MasterFetchServer()


def _read_token(result) -> str:
    rows = _rows(result)
    assert rows and rows[0].get("ok") is True, rows
    return rows[0]["read"]


def _last_captcha_record() -> dict:
    import fixture_site as fs

    lines = fs.CAPTCHA_LOG.read_text(encoding="utf-8").strip().splitlines()
    assert lines, "站点那侧一条尝试都没记下"
    return json.loads(lines[-1])


@pytest.mark.live
@pytest.mark.asyncio
async def test_the_pinned_page_answers_the_challenge_it_showed(target_site,
                                                              private_door_open,
                                                              one_server):
    """第二次调用不重新导航，所以它答的是第一次那一道 —— 站点自己判的卷。"""
    import fixture_site as fs
    _browser_or_skip()
    base, _ = target_site
    fs.CAPTCHA_LOG.unlink(missing_ok=True)

    first = await one_server.web_act(
        url=f"{base}/captcha", session_id="pin-answer", pin=True,
        actions=_READ_TOKEN, timeout=90000)
    try:
        token = _read_token(first)
        assert PIN_NOTES["pinned"] in (first.summary or ""), first.summary

        second = await one_server.web_act(
            url=f"{base}/captcha", session_id="pin-answer", pin=True,
            actions=_READ_TOKEN
            + [{"fill": {"selector": "#answer", "text": fs.captcha_text(token)}},
               {"submit": {"selector": "#captcha-btn", "confirm": True}}],
            timeout=90000)
        rows = _rows(second)
        assert PIN_NOTES["reused"] in (second.summary or ""), second.summary
        # 同一枚 token = 这一页没有重新导航（token 是页面里的那一格，不是 dhole 记的）。
        assert rows[0]["read"] == token, (rows[0], token)
        assert rows[-1].get("submitted") is True, rows[-1]
        assert "验证码正确" in "".join(second.content or []), second.summary

        record = _last_captcha_record()
        assert record["ok"] is True, record
        assert record["token"] == token, record
        assert record["answer"] == fs.captcha_text(token), record
    finally:
        await one_server._close_one_session("pin-answer")


@pytest.mark.live
@pytest.mark.asyncio
async def test_the_same_two_calls_without_pin_answer_the_wrong_question(
        target_site, private_door_open, one_server):
    """反面对照：不驻留时第二次是另一道题，第一次的答案递过去就是 403。

    这一条不是"再证一遍旧结论"：它把"两次导航 token 不同"的后果说完 —— 后果是被拒，
    而拒的是**上一次的**那道题。
    """
    import fixture_site as fs
    _browser_or_skip()
    base, _ = target_site
    fs.CAPTCHA_LOG.unlink(missing_ok=True)

    first = await one_server.web_act(
        url=f"{base}/captcha", session_id="pin-absent",
        actions=_READ_TOKEN, timeout=90000)
    try:
        token = _read_token(first)

        second = await one_server.web_act(
            url=f"{base}/captcha", session_id="pin-absent",
            actions=_READ_TOKEN
            + [{"fill": {"selector": "#answer", "text": fs.captcha_text(token)}},
               {"submit": {"selector": "#captcha-btn", "confirm": True}}],
            timeout=90000)
        rows = _rows(second)
        assert rows[0]["read"] != token, "不驻留也拿到了同一枚 token —— 靶站改了形状"
        assert "验证码错误" in "".join(second.content or []), second.summary
        assert _last_captcha_record()["ok"] is False, _last_captcha_record()
        for summary in (first.summary or ""), (second.summary or ""):
            assert "pinned" not in summary, summary
    finally:
        await one_server._close_one_session("pin-absent")


@pytest.mark.live
@pytest.mark.asyncio
async def test_a_blocked_submit_on_a_pinned_page_says_the_page_is_pinned(
        target_site, private_door_open, one_server):
    """驻留页上"把同一串动作重跑一遍"是半句假话，所以那句话必须点名驻留。"""
    import fixture_site as fs
    _browser_or_skip()
    base, _ = target_site
    fs.CAPTCHA_LOG.unlink(missing_ok=True)

    result = await one_server.web_act(
        url=f"{base}/captcha", session_id="pin-gate", pin=True,
        actions=[{"fill": {"selector": "#answer", "text": "ABCDE"}},
                 {"submit": {"selector": "#captcha-btn"}}],
        timeout=90000)
    try:
        gated = [r for r in _rows(result) if r.get("pending_approval")]
        assert gated, _rows(result)
        text = gated[-1]["error"]
        assert "THIS PAGE IS PINNED" in text, text
        assert "re-run just this submit step" in text, text
        assert fs.CAPTCHA_LOG.exists() is False, "被拦下的那一发不该把表单送出去"
    finally:
        await one_server._close_one_session("pin-gate")
