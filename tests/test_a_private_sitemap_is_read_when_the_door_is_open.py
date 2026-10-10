"""私网站点的 sitemap：放行要放行到底，报告要说得出**真话**。

2026-10-09 独立审计 D2（真机复现）抓到的是一条**同时**踩了两条纪律的缺陷：

1. **门不一致。** `cred_defaults` 存了 `allow_private` 之后，web_read / web_act /
   web_request / feed_read 四扇门都放行回环，`web_crawl` 的**种子**也放行了
   （那是一次已修的回归，守卫见 test_crawl_honors_stored_allow_private.py）——
   但作用域只包住种子那一行就退出。sitemap 的发现（`sitemap.py` 里逐条
   `validate_url`）、robots.txt 的复校验（`robots.py` `_fetch_rules`）都发生在
   那之后，于是同一个 `allow_private` 在**读**的门上是开的、在**爬**的门上是死的。
   实测形状：一个 robots.txt 明写 `Sitemap:`、`/sitemap.xml` 也确实返回 200 合法
   XML 的本机 dev 站，`web_discover(sitemap=true)` 报「没找到」。

2. **报告失实。** 抓不到 sitemap 时那句总结逐字写着「robots.txt had no Sitemap
   directive and /sitemap.xml returned nothing」。在上面那个站点上**两半都是假的**：
   robots.txt 确有指令，而 `/sitemap.xml` **从未被请求**。读者照着去查 robots.txt
   会发现自己没写错，然后失去对整条回执的信任 —— 这是本仓库反复点名要杜绝的形状。

用例分两组。**真 socket 组**测门：放行时 sitemap 必须真被读到；没放行时种子自己
就该被拒（那是**正确**行为，判据是它拒得说得清，而不是含混的「没找到」）。
**离线组**测话：用假 `http_get` 把「指令指向私网」这个形状喂进去 —— 真 socket 组
到不了那个分支（私网种子先被拒），而这句话正是 D2 的失实之处，必须单独钉住。
"""

from __future__ import annotations

import http.server
import socket
import threading

import pytest

from dhole_mcp import identity
from dhole_mcp import sitemap as sm
from dhole_mcp.server import MasterFetchServer

#: 真解析器必须在**导入时**抓住。conftest 的 `_offline_dns`（autouse）把
#: `socket.getaddrinfo` 钉成公网地址，好让套件不依赖本机 hosts；如果在夹具里写
#: `socket.getaddrinfo`，拿到的是**已经被钉住的那个**，于是 127.0.0.1 被判成
#: 93.184.216.34 —— 守卫放行，用例测的东西整个消失了（实测踩过）。
_REAL_GETADDRINFO = socket.getaddrinfo

SITEMAP_XML = (
    b"<?xml version='1.0' encoding='UTF-8'?>"
    b"<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>"
    b"<url><loc>__BASE__/a</loc></url>"
    b"<url><loc>__BASE__/b</loc></url>"
    b"<url><loc>__BASE__/c</loc></url>"
    b"</urlset>"
)

#: robots.txt 声明了 Sitemap，且 /sitemap.xml 是**合法**的 —— 站点这边完全没问题。
#: 唯一的问题在 dhole 那扇门上。
ROBOTS_WITH_SITEMAP = b"User-agent: *\nDisallow: /private\nSitemap: __BASE__/sitemap.xml\n"
#: 对照：robots.txt 真的没有 Sitemap 指令，且**连惯例路径也没有** sitemap。
ROBOTS_WITHOUT_SITEMAP = b"User-agent: *\nDisallow: /private\n"


def _make_site(robots_body: bytes, *, serve_conventional_sitemap: bool = True):
    """一个只会答 robots.txt / sitemap.xml / 一个普通页的最小站。"""

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):  # 静音
            pass

        def do_GET(self):
            base = f"http://127.0.0.1:{self.server.server_address[1]}"
            path = self.path.split("?", 1)[0]
            if path == "/robots.txt":
                body, ctype = robots_body.replace(b"__BASE__", base.encode()), \
                    "text/plain; charset=utf-8"
            elif path in ("/sitemap.xml", "/sitemap_index.xml"):
                if not serve_conventional_sitemap:
                    self.send_response(404)
                    self.end_headers()
                    return
                body, ctype = SITEMAP_XML.replace(b"__BASE__", base.encode()), "application/xml"
            elif path in ("/a", "/b", "/c", "/"):
                body, ctype = b"<html><body><h1>page</h1></body></html>", "text/html; charset=utf-8"
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def _serve(robots_body: bytes, *, serve_conventional_sitemap: bool = True):
    srv = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0),
        _make_site(robots_body, serve_conventional_sitemap=serve_conventional_sitemap))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


@pytest.fixture
def private_site_with_sitemap(monkeypatch):
    # 靶站是真 socket，守卫要看见真地址；测试进程自己的 DNS 替身会把它变成假 IP。
    monkeypatch.setattr(socket, "getaddrinfo", _REAL_GETADDRINFO)
    srv = _serve(ROBOTS_WITH_SITEMAP)
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


# ───────────────────────── 真 socket 组：门 ─────────────────────────

def _assert_the_door_starts_closed():
    """这颗用例的门必须是它自己开的，不是上一个用例留下的。

    这一组曾被记为"全量套件里绿、单独跑红"。今天量不到那个现场（文件单独跑绿、这两颗单独挑
    出来跑也绿，退回 D1 那一刀之前同样绿），但它防的形状是真的：一条没有前置检查的门守卫，
    可以因为别人留着的态而绿着测不到东西。证人：在下面的断言之前先
    `identity.set_value("allow_private", True)`，这条断言立刻红。

    这里只看见得上的那一条缝：identity 的连接是模块级单例，conftest 每颗用例换一份 tmp_path
    的 identity.db **并且** close() 断掉旧句柄（不 close 就等于什么都没隔离，旧句柄还指着
    上一颗用例那份文件）。门本身（`_ALLOW_PRIVATE` 那颗 ContextVar）由 conftest 的
    `_private_door_does_not_leak_across_tests` 逐颗复位，不在这里重复一道。
    """
    assert not identity.get("allow_private", ""), identity.all_rows()


@pytest.mark.asyncio
async def test_the_door_is_open_on_the_sitemap_too(private_site_with_sitemap):
    """存了 allow_private，sitemap 就必须**真被读到** —— 这是 D2 的正面判据。"""
    _assert_the_door_starts_closed()
    identity.set_value("allow_private", True)
    result = await MasterFetchServer().web_discover(
        private_site_with_sitemap, sitemap=True)
    assert result.sitemap_used is True, (
        "种子放行了、sitemap 却在守卫那里死掉 —— 同一扇门两个答案：\n"
        f"summary={result.summary!r}\nerror={result.error!r}")
    assert not result.error, result.error
    assert result.pages_discovered == 3, [p.url for p in result.pages]


@pytest.mark.asyncio
async def test_without_the_door_the_refusal_names_the_address(private_site_with_sitemap):
    """没放行时种子自己就该被拒 —— 而那句拒绝必须点得出**地址**与**出路**。

    这里判的不是"失败了"，是"失败说得清"：D2 的失实之处在于报告断言了没发生过的
    探测；种子的拒绝本来是说得清的（点名私网段 + 给出 allow_private 那条路），
    这条守卫把它钉住，防止"修失实摘要"时把这条正确的拒绝一起改坏。
    """
    _assert_the_door_starts_closed()
    result = await MasterFetchServer().web_discover(
        private_site_with_sitemap, sitemap=True)
    assert result.sitemap_used is False
    assert "private IP" in (result.error or ""), result.error
    assert "allow_private" in (result.next_action or ""), result.next_action
    # 一句都不许提"Sitemap 指令"：这条路上根本没走到读 robots.txt 的那一步。
    assert "Sitemap directive" not in (result.summary or ""), result.summary


@pytest.mark.asyncio
async def test_the_old_sentence_survives_where_it_is_true(monkeypatch):
    """robots.txt 真的没有指令、惯例路径也 404 时，那句老文案必须原样还在。

    修一句假话不等于把那句话删掉：它在**为真**的场合仍是最准的一句，而把它一并
    删掉，就等于用另一种含混替换了原来的自信 —— 两个方向都是"诊断答错"。
    """
    monkeypatch.setattr(socket, "getaddrinfo", _REAL_GETADDRINFO)
    srv = _serve(ROBOTS_WITHOUT_SITEMAP, serve_conventional_sitemap=False)
    try:
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        _assert_the_door_starts_closed()
        identity.set_value("allow_private", True)   # 让种子过门，走到 sitemap 那一段
        result = await MasterFetchServer().web_discover(base, sitemap=True)
        assert result.sitemap_used is False
        assert "had no Sitemap directive" in (result.summary or ""), result.summary
    finally:
        srv.shutdown()
        srv.server_close()


# ───────────────────────── 离线组：话 ─────────────────────────

def _discover_with_robots(robots_text: bytes):
    """假 transport：robots.txt 由调用方给，其余一律 404。"""
    def http_get(url: str):
        if url.endswith("/robots.txt"):
            return (200, robots_text)
        return None
    return sm.discover_sitemap("https://site.test/", http_get=http_get, max_urls=100)


def test_a_guarded_sitemap_directive_is_recorded_not_silently_dropped():
    """被守卫拦下的 Sitemap 指令必须**记账**：过去它是一句 `continue`。

    「index 里有 30 个子 sitemap、其中 12 个指向私网」与「index 只有 18 个」在
    报告里长得一模一样 —— 地图短了一截，没有任何一处能解释为什么。
    """
    res = _discover_with_robots(
        b"User-agent: *\nSitemap: http://169.254.169.254/sitemap.xml\n"
        b"Sitemap: https://site.test/ok.xml\n")
    assert res.robots_had_sitemap is True, "robots.txt 里确有 Sitemap 指令"
    assert res.guard_refused == ["http://169.254.169.254/sitemap.xml"], res.guard_refused
    # 公网那条仍然要留在候选里 —— 记账不是把整条指令一起废掉。
    assert "https://site.test/ok.xml" in res.sitemaps_used or res.via == "robots"


def test_a_guarded_directive_makes_the_summary_name_the_guard():
    """有指令、但指令指向私网 → 总结必须说「守卫拒了它」，不能说「没有指令」。"""
    from dhole_mcp.crawl import _no_sitemap_next_action, _no_sitemap_summary

    diag = {"robots_checked": True, "robots_had_sitemap": True,
            "guard_refused": ["http://169.254.169.254/sitemap.xml"], "via": "conventional"}
    s = _no_sitemap_summary("http://127.0.0.1:8399/", diag)
    assert "had no Sitemap directive" not in s, s
    assert "guard" in s and "169.254.169.254" in s, s
    # 出路指向那扇真门，不是「换个站」——那不是站的问题。
    assert "allow_private" in _no_sitemap_next_action(diag)


def test_a_directive_that_read_nothing_is_not_called_missing():
    """有指令、没被守卫拦、却什么都没读出来 → 也不能说「没有指令」。"""
    from dhole_mcp.crawl import _no_sitemap_summary

    diag = {"robots_checked": True, "robots_had_sitemap": True,
            "guard_refused": [], "via": "robots"}
    s = _no_sitemap_summary("https://site.test/", diag)
    assert "had no Sitemap directive" not in s, s
    assert "declares a Sitemap" in s, s


def test_the_summary_stays_hedged_when_robots_could_not_be_read():
    """robots.txt 根本没读到 → 不许断言它有没有指令。"""
    from dhole_mcp.crawl import _no_sitemap_summary

    diag = {"robots_checked": False, "robots_had_sitemap": False,
            "guard_refused": [], "via": "conventional"}
    s = _no_sitemap_summary("https://site.test/", diag)
    assert "had no Sitemap directive" not in s, s
    assert "could not be read" in s, s
