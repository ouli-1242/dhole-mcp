# -*- coding: utf-8 -*-
"""一条链要能答它站着的那一页 —— 而跨调用重试答的是上一枚挑战（真浏览器、真靶站）。

`live`（真 Chromium + 回环靶站），默认跑不到：`-m live` 才选上。

两件事分别钉住这一刀的两侧：

**A3-b 的正面**：靶站 `/challenge/text` 把答案写成页面的文本（`<p id=challenge>`）。
`read → fill {=code} → submit` 一条链跑完，站点那侧的 `challenge.log` 记下 `ok=true`。
从前这格只能"这次调用读、下次调用填"，而下次调用是**另一次导航**。

**A3-a 的反面**：同一个 URL 连读两次，两次的 token 不同 —— 于是"最多 3 轮读—填—重试"
这句话在实现上不可能成立：第 2 轮填的是第 1 轮那一枚的答案。这不是运气差，是结构。
`evals/captcha_baseline.py` 的基线本来就建立在"每次 GET 出新 token"之上（README 记着），
是常驻层的那句话没跟上基线。

靶站起在**真 socket** 上并把 DNS 换回真解：SSRF 守卫要看真地址，而测试进程自己的
`getaddrinfo` 替身会把 127.0.0.1 变成一个假 IP（`test_a_private_sitemap_is_read_when_the_door_is_open.py`
立的先例，这里照抄）。
"""

from __future__ import annotations

import json
import re
import socket
import sys
import threading
from pathlib import Path

import pytest

EVALS = Path(__file__).resolve().parent.parent / "evals"
if str(EVALS) not in sys.path:
    sys.path.insert(0, str(EVALS))

from dhole_mcp import identity  # noqa: E402
from dhole_mcp.server import MasterFetchServer  # noqa: E402

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
