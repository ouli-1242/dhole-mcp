"""靶站当爬虫的种子：存量 allow_private 必须在爬的门上同样成立。

2026-10-05 全真模拟（review/realrun）抓到：`cred_defaults` 存了 `allow_private`
之后，web_read / web_act / web_request / feed_read 四扇门都放行回环，唯独
web_crawl / web_discover 仍拒 —— `crawl.py` 的种子 `validate_url` 在任何作用域
之外裸跑（feed_read 当年同类问题修的时候，这颗漏了）。每页的真实抓取走 web_read
（它的装饰器自己推存量放行），所以只有第一颗种子撞墙：本机 dev 站是最常见的爬取
对象，这条不一致恰好挡在最常用的那一步。

两用例都走真 socket（`live_site`，靶站 0 号端口真起）：
  - 没存量放行 → 种子被拒，错误点名私网，一页不发；
  - 存了放行   → 爬真跑起来，页面进来。
"""

from __future__ import annotations

import socket
import sys
import threading
from pathlib import Path

import pytest

EVALS = Path(__file__).resolve().parent.parent / "evals"
if str(EVALS) not in sys.path:
    sys.path.insert(0, str(EVALS))

import fixture_site as fs  # noqa: E402

from dhole_mcp import identity  # noqa: E402
from dhole_mcp.server import MasterFetchServer  # noqa: E402

_REAL_GETADDRINFO = socket.getaddrinfo


@pytest.fixture
def live_site(monkeypatch, tmp_path):
    monkeypatch.setattr(socket, "getaddrinfo", _REAL_GETADDRINFO)
    monkeypatch.setattr(fs, "RUNTIME", tmp_path)
    monkeypatch.setattr(fs, "REQUESTS", tmp_path / "requests.log")
    srv = fs.make_server("127.0.0.1", 0)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.asyncio
async def test_without_the_stored_allowance_the_seed_is_refused(live_site):
    result = await MasterFetchServer().web_crawl(
        f"{live_site}/collections?items=3", max_pages=2, max_depth=1)
    assert "private IP" in (result.error or ""), result.error
    assert result.pages == []


@pytest.mark.asyncio
async def test_stored_allow_private_opens_the_crawl_door(live_site):
    identity.set_value("allow_private", True)
    result = await MasterFetchServer().web_crawl(
        f"{live_site}/collections?items=3", max_pages=2, max_depth=1)
    assert not result.error, result.error
    assert result.pages, "放行后应真爬到页面"
    assert any("/work/" in p.url for p in result.pages), \
        [p.url for p in result.pages]
