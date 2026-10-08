"""Dhole 审查修复回归测试。

覆盖：
- P0: feed_read 不再崩溃（structured_content 合法 dict）+ URL 校验
- P1: 重定向链逐跳 SSRF 校验（fetcher 层）
- P1: max_redirects 生效
- P2: 域名 DNS 解析内网复查（17.0 起无条件开启，DHOLE_SSRF_DNS_RECHECK 已注销）
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dhole_mcp.fetcher import HTTPSession
from dhole_mcp.security import SecurityError, validate_url
from dhole_mcp.server import MasterFetchServer


# ---------------------------------------------------------------------------
# P0: feed_read 崩溃修复
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_feed_fetch_returns_dict_structured(mock_logger=None):
    """feed_read 分支应返回 dict structured_content（list 会 -32603）。"""

    srv = MasterFetchServer(cache_ttl=0)
    args = {"urls": ["https://example.com/feed"], "max_items": 3}

    # 真实的 FeedResult，不是一份手抄的替身：这一条测的就是"投影与模型一致"，
    # 而替身会把模型的新字段挡在测试之外（替身少写一格，投影就少读一格，两边一起
    # 过期 —— 与本文件注释里 `note` 溜过去的正是同一种漏法）。
    from dhole_mcp.feed import FeedResult

    _FakeResp = lambda: FeedResult(source_url="https://example.com/feed",  # noqa: E731
                                   source_title="t")

    with patch("dhole_mcp.feed.fetch_feeds", new=AsyncMock(return_value=[_FakeResp()])):
        with patch("socket.getaddrinfo",
                   return_value=[(2, 1, 6, "", ("93.184.216.34", 0))]):
            result = await srv.feed_read(urls=args["urls"], max_items=3)

    assert isinstance(result, list)
    assert isinstance(result[0], dict)
    assert "source_url" in result[0]
    assert "items" in result[0]
    # G19: the projection has to carry the discovery hop, or a caller that passed
    # a site page sees a feed URL it never named and no explanation why.
    assert "discovered_from" in result[0]
    # G23, and the durable form of it: this tool returns a hand-built dict, so any
    # model field not projected here does not exist for the caller. Comparing the
    # projection against FeedResult's own fields makes that invisible: a field
    # added to the model fails here until it is projected (the hand-written list
    # of names is what let `note` slip through).
    from dhole_mcp.feed import FeedResult
    assert set(result[0]) == set(FeedResult.model_fields), (
        "feed_read 的投影与 FeedResult 的字段已经不一致："
        f"少 {set(FeedResult.model_fields) - set(result[0])}，"
        f"多 {set(result[0]) - set(FeedResult.model_fields)}")


@pytest.mark.asyncio
async def test_feed_fetch_rejects_internal_url():
    """feed_read 传内网 URL 应报错（SSRF 防护）。"""
    srv = MasterFetchServer(cache_ttl=0)
    with pytest.raises(ValueError, match="校验失败"):
        await srv.feed_read(urls=["http://127.0.0.1:8080/x"], max_items=3)


# ---------------------------------------------------------------------------
# P1: 重定向逐跳 SSRF 校验
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redirect_to_internal_rejected():
    """公网 URL 重定向到内网应被 SecurityError 拒绝。"""
    session = HTTPSession(stealthy_headers=False, retries=0)
    session._client = MagicMock()

    def fake_get(method, url, headers=None, timeout=None, follow_redirects=False, **kw):
        resp = MagicMock()
        resp.status_code = 302
        resp.headers = {"location": "http://127.0.0.1:8080/internal.png"}
        resp.content = b""
        resp.reason = "Found"
        resp.cookies = []
        resp.url = url
        return resp

    session._client.request = MagicMock(side_effect=fake_get)
    with pytest.raises(SecurityError, match="internal/private IP"):
        await session.get("https://example.com/x.png", follow_redirects=True)


@pytest.mark.asyncio
async def test_max_redirects_bounded():
    """重定向次数受 max_redirects 限制（不会无限跟随）。"""
    session = HTTPSession(stealthy_headers=False, retries=0)
    session._client = MagicMock()
    calls = {"n": 0}

    def fake_get(method, url, headers=None, timeout=None, follow_redirects=False, **kw):
        calls["n"] += 1
        resp = MagicMock()
        resp.status_code = 302
        resp.headers = {"location": f"http://127.0.0.1:1/hop{calls['n']}"}
        resp.content = b""
        resp.reason = "Found"
        resp.cookies = []
        resp.url = url
        return resp

    session._client.request = MagicMock(side_effect=fake_get)
    # allow_internal=True 让重定向目标通过校验；max_redirects=2 应限制为
    # 初始 + 2 跳 = 3 次请求，第 4 次不再跟随（返回 302 而非继续）
    r = await session.get("http://127.0.0.1:9/x", follow_redirects=True,
                          max_redirects=2, allow_internal=True)
    assert calls["n"] == 3, f"max_redirects=2 应产生 3 次请求，实际 {calls['n']}"
    assert r.status == 302, "超过 max_redirects 后应返回 3xx 而非继续跟随"


# ---------------------------------------------------------------------------
# P2: DNS 复查（默认开启）
# ---------------------------------------------------------------------------


def test_dns_recheck_rejects_internal_resolution(monkeypatch):
    """域名解析到内网应拒绝（默认开启，不需要设任何环境变量）。"""
    monkeypatch.delenv("DHOLE_SSRF_DNS_RECHECK", raising=False)
    sp = __import__("dhole_mcp.security", fromlist=["_tunnel_exempt_engaged"])
    assert sp._tunnel_exempt_engaged() is False
    with patch("socket.getaddrinfo",
               return_value=[(2, 1, 6, "", ("192.168.1.10", 0))]):
        with pytest.raises(SecurityError, match="resolves to internal"):
            validate_url("https://mycorp.internal/x")


def test_dns_recheck_on_by_default(monkeypatch):
    """默认开启（fail-closed）：agent 抓来的 URL 不受信任，内网解析是主路径。"""
    monkeypatch.delenv("DHOLE_SSRF_DNS_RECHECK", raising=False)
    sp = __import__("dhole_mcp.security", fromlist=["_tunnel_exempt_engaged"])
    assert sp._tunnel_exempt_engaged() is False


def test_retired_dns_recheck_knob_moves_nothing(monkeypatch):
    """17.0：``DHOLE_SSRF_DNS_RECHECK`` 已注销 —— 设成什么都不改变判定。

    当年这个旋钮能让一个解析到内网的主机名放行，等于把一道 SSRF 守卫交给一个
    环境变量；现在唯一的放宽路径是「探测到 fake-ip」时的自动判定，且只放宽 DNS
    复检这一层（见 test_fake_ip_auto.py）。整段都待在假 getaddrinfo 里跑：判定
    既然只来自探测，测试就必须把探测的答案钉死，否则红的会是本机网络。
    """
    sp = __import__("dhole_mcp.security", fromlist=["_tunnel_exempt_engaged"])
    with patch("socket.getaddrinfo",
               return_value=[(2, 1, 6, "", ("192.168.1.10", 0))]):
        sp.reset_fake_ip_probe()
        sp._DNS_CHECK_CACHE.clear()
        for value in ("0", "false", "off", "no"):
            monkeypatch.setenv("DHOLE_SSRF_DNS_RECHECK", value)
            assert sp._tunnel_exempt_engaged() is False, value
        with pytest.raises(SecurityError, match="resolves to internal"):
            validate_url("https://mycorp.internal/x")
    sp.reset_fake_ip_probe()


def test_dns_recheck_accepts_public_resolution():
    """解析到公网 IP 应放行。"""
    with patch("socket.getaddrinfo",
               return_value=[(2, 1, 6, "", ("93.184.216.34", 0))]):
        assert validate_url("https://example.com/x") == "https://example.com/x"


def test_dns_recheck_honors_hosts_file_pin(monkeypatch):
    """hosts 文件里显式钉住的域名放行：那是本机用户的故意决定（阻断/mirror/
    分流），攻击者改不了 hosts 文件，所以不该被当成攻击者的 DNS 答案。

    这台机器就把 github.com / huggingface.co 钉到 127.0.0.1。
    """
    sp = __import__("dhole_mcp.security", fromlist=["_hosts_file_pin", "_resolves_to_internal"])
    monkeypatch.setattr(sp, "_hosts_file_pin", lambda host: "127.0.0.1")
    sp._DNS_CHECK_CACHE.clear()
    with patch("socket.getaddrinfo",
               return_value=[(2, 1, 6, "", ("127.0.0.1", 0))]):
        assert sp._resolves_to_internal("pinned.example") is None
        assert validate_url("https://pinned.example/x") == "https://pinned.example/x"


def test_hosts_file_parser_reads_real_file(tmp_path, monkeypatch):
    """解析器本身：注释/多主机名/大小写/尾点。"""
    hosts = tmp_path / "hosts"
    hosts.write_text(
        "# comment line\n"
        "127.0.0.1   pinned.example  SECOND.example.\n"
        "0.0.0.0 ad.example # trailing comment\n"
        "malformed-line\n",
        encoding="utf-8",
    )
    sp = __import__("dhole_mcp.security", fromlist=["_hosts_file_pin"])
    monkeypatch.setattr(sp, "_hosts_file_path", lambda: str(hosts))
    monkeypatch.setattr(sp, "_hosts_cache", None)
    assert sp._hosts_file_pin("pinned.example") == "127.0.0.1"
    assert sp._hosts_file_pin("second.example") == "127.0.0.1"
    assert sp._hosts_file_pin("AD.EXAMPLE") == "0.0.0.0"
    assert sp._hosts_file_pin("not-pinned.example") is None

# ---------------------------------------------------------------------------
# P1: sitemap SSRF（深挖发现）
# ---------------------------------------------------------------------------


def test_robots_sitemap_internal_rejected(monkeypatch):
    """robots.txt 声明的内网 Sitemap 地址应被过滤。"""
    from dhole_mcp.sitemap import _robots_sitemaps

    robots_body = (
        b"User-agent: *\n"
        b"Sitemap: http://169.254.169.254/latest/meta-data/\n"
        b"Sitemap: https://example.com/real-sitemap.xml\n"
    )

    def fake_get(url):
        return (200, robots_body)

    result, checked = _robots_sitemaps("https://example.com/", fake_get)
    assert checked is True
    assert result == ["https://example.com/real-sitemap.xml"], \
        f"内网 sitemap 应被过滤，实际: {result}"


def test_robots_sitemap_accepts_public(monkeypatch):
    """公网 Sitemap 地址应保留。"""
    from dhole_mcp.sitemap import _robots_sitemaps

    robots_body = b"User-agent: *\nSitemap: https://example.com/sitemap.xml\n"

    def fake_get(url):
        return (200, robots_body)

    result, checked = _robots_sitemaps("https://example.com/", fake_get)
    assert result == ["https://example.com/sitemap.xml"]
