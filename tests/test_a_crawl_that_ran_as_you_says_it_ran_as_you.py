"""D1：带着你的凭据爬回来的二十页，得说那是你登录后的样子，而不是公开的样子。

计划里这一行原本写的是另一件事："一次调用参数里的 `allow_private` 参与进了持久全局，
为进程里后续所有无关调用撑开了 SSRF 栅栏"。**这条被实测推翻**，两处：

一、调用面上没有 `allow_private` 这颗参数了（17.0 §3-17 把它搬进 cred_defaults），
    所以没有任何"一次调用的参数"能写进持久状态；
二、每次调用重推的那一份是 ContextVar 而不是进程变量：装饰器的 tokens 表在 `finally`
    里逐颗 reset。实测：进用例之前 `security.private_hosts_allowed()` 是
    `(frozenset(), False)`；一次放行过私网的调用跑完，把存储清掉，同一条回环 URL
    立刻又被拒。所以"漏到下一次调用"今天不成立 —— 本文件仍把它钉成守卫，因为那正是
    下一次有人改成进程写入时会撞上的那一位。

计划要的"identity_applied 区分来自你的参数还是来自存储"也早就在了：`_identity_for`
只把**存储供的**键报名，调用方手传的 `cdp_url=` / `anti_detect=` 不给存储记功。

量出来的真缺陷是三处，同一个罪（静默）：

① `web_crawl` 的每一页都带着 `cred_defaults` 存的那台 host 的 auth 出去（实测线上
   确有 `Authorization`），而**信封里一个字都没有**：行是裁剪过的页报告，逐页那句
   `identity_applied` 死在内部。同一个模型上已有先例 —— `ssrf_recheck` 存在的理由
   就是"每页收据到不了调用方"。
② `feed_read` 一条凭据都不发（实测那一发没有 `Authorization`），而手册当时写着
   "同一条规则适用于每个工具"。这一刀改的是文档与回执的说法，不是把凭据流向一条新
   通道 —— 那要一起决定 cookie 罐、按凭据分键的缓存与逐跳剥离，不在这一刀里。
③ `sitemap=true` 那张地图与它前面的 robots.txt 也是零凭据出去的，于是"这站没有
   sitemap"或"sitemap 是空的"可能只是"这站的 sitemap 要 token"。

①那格是**逐页收据并上来的**，不是从存储现算的：一张连不上手的页、一趟只走 sitemap 的
地图，都没把凭据发出去过，说它发了就是新的谎（下面第三条用例守的就是这个过度声称）。
那条用例本来不存在 —— 它是先按"地图模式不许报名"写错了才发现的：链接走的地图**确实**
取了种子那一页，所以它报名是对的。写清楚，免得下一个人在这里再猜一遍。
"""
from __future__ import annotations

import http.server
import socket
import threading

import asyncio
import pytest

from dhole_mcp import identity
from dhole_mcp.server import MasterFetchServer

PAGE = (b"<html><head><title>T</title></head><body><h1>Title</h1><p>"
        + b"Real body text so the extractor has something to chew on. " * 6
        + b'</p><a href="/second">next</a></body></html>')
EMPTY_URLSET = (b'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/'
                b'schemas/sitemap/0.9"></urlset>')

# 真 socket：`_offline_dns`（autouse）把 getaddrinfo 钉成公网地址，回环靶站要在
# **导入时**抓住真解析器（先例见 W1 与 W3 的 echo 文件）。
_REAL_GETADDRINFO = socket.getaddrinfo

TOKEN = "sekret-" + "not-a-real-secret"


class _Recorder:
    """站点自己记账：每一发的路径 + 有没有带着 Authorization 出来。

    `sitemap` 决定这张站在地图上扮演哪一种站点：`None` 是"根本没有地图"（404），
    `EMPTY_URLSET` 是"地图读通了而里面一个 URL 都没有"（要 token 的站常回一页 HTML，
    parse 出来正好是空）。两句"没有地图"的话都得说破凭据那一层，所以两条都要有用例。
    """

    def __init__(self, sitemap_body: bytes | None = None):
        self.received: list[tuple[str, str | None]] = []
        self.sitemap_body = sitemap_body

    def handler(self):
        rec = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _answer(self):
                path = self.path.split("?")[0]
                rec.received.append((path, self.headers.get("Authorization")))
                if path.startswith("/sitemap") and rec.sitemap_body is not None:
                    body, ctype, code = rec.sitemap_body, "application/xml", 200
                elif path.startswith("/sitemap"):
                    body, ctype, code = b"nothing here", "text/plain", 404
                elif path == "/robots.txt":
                    body, ctype, code = b"User-agent: *\nDisallow:\n", "text/plain", 200
                else:
                    body, ctype, code = PAGE, "text/html", 200
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = _answer
        return Handler

    def authed(self) -> int:
        return sum(1 for _, a in self.received if a)

    def paths(self) -> list[str]:
        return [p for p, _ in self.received]


@pytest.fixture
def site(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _REAL_GETADDRINFO)
    rec = _Recorder()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), rec.handler())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    # 只走存下来的那一份放行（ContextVar 直推会漏出用例，W1 记过一次）。
    identity.set_value("allow_private", True)
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}", rec
    finally:
        srv.shutdown()
        srv.server_close()


def _dump(tool, args):
    out = asyncio.run(MasterFetchServer()._dispatch(tool, args))
    return out[1] if isinstance(out, tuple) and len(out) == 2 else {}


def _store_token(host: str = "127.0.0.1") -> None:
    identity.set_value("auth", {"type": "bearer", "token": TOKEN}, host)


# ─── 1 · 爬到的每一页带着凭据，信封要说 ──────────────────────────────────────

def test_a_crawl_that_sent_your_token_says_it_sent_it(site):
    base, rec = site
    _store_token()
    payload = _dump("web_crawl", {"url": f"{base}/", "max_pages": 2, "deadline_ms": 20000})
    assert rec.authed() >= 1, f"这一趟从未带凭据出门，receipt 也无从可说: {rec.paths()}"
    applied = payload.get("identity_applied", "")
    assert "auth" in applied, (
        f"每一页都带着存储的 token 取回来，信封却一个字不提: {applied!r}")
    assert TOKEN not in str(payload), "身份回执只许报名，不许报名以外的东西"


def test_a_crawl_with_nothing_stored_names_no_credential(site):
    """反向：没存凭据的时候不许出现凭据那两个字（无端提身份是新造的噪声）。

    `allow_private` 在这里是会出现的，而且**应该**出现 —— 那张门确实是存储供的，这一发
    确实靠它才读得到回环。这一格要说的是"这次用了存储里的哪几颗"，不是"这次像不像匿名"。
    """
    base, rec = site
    payload = _dump("web_crawl", {"url": f"{base}/", "max_pages": 1})
    applied = payload.get("identity_applied", "")
    assert rec.authed() == 0
    for name in ("auth", "cookies"):
        assert name not in applied, (name, applied)


def test_the_crawl_names_what_was_sent_not_what_is_stored(site):
    """守的是过度声称那一条：存储里**别家**的凭据不许算进这一趟。

    实现如果改成从存储现算（`in_force_rows` 那一类），这条会红：它报的是"这台机器
    知道什么"，而这一格要说的是"这次发了什么"。
    """
    base, rec = site
    _store_token("api.example.com")
    payload = _dump("web_crawl", {"url": f"{base}/", "max_pages": 1})
    assert rec.authed() == 0, "别家的凭据跟过来了：那是 §3-17 立案的那道边界"
    assert "auth" not in payload.get("identity_applied", ""), payload


def test_a_sitemap_map_that_fetched_no_pages_claims_no_identity(site):
    """只走 sitemap 的那趟一页都没取，所以它不许说自己带着谁的身份跑过。"""
    base, rec = site
    _store_token()
    payload = _dump("web_discover", {"url": f"{base}/", "sitemap": "on"})
    assert payload.get("pages_crawled") == 0, payload
    assert "auth" not in payload.get("identity_applied", ""), \
        f"一张只读了 sitemap 的地图声称带过凭据: {payload.get('identity_applied')!r}"


# ─── 2 · 不发凭据的两扇门，各自说破 ──────────────────────────────────────────

def test_a_feed_with_your_token_stored_says_it_used_none(site):
    base, rec = site
    _store_token()
    payload = _dump("feed_read", {"urls": [f"{base}/feed.xml"]})
    row = (payload.get("feeds") or [{}])[0]
    note = row.get("note", "")
    assert rec.authed() == 0, "feed 这道门开始发凭据了 —— 那这条句子与手册都得改"
    assert "feed_read sent none of it" in note, note
    assert TOKEN not in note, note
    assert "web_read" in note, f"说破不一致要同时给出那扇真的会发的门: {note}"


def test_a_feed_on_a_host_with_nothing_stored_stays_clean(site):
    base, _rec = site
    payload = _dump("feed_read", {"urls": [f"{base}/feed.xml"]})
    row = (payload.get("feeds") or [{}])[0]
    assert "cred_defaults" not in (row.get("note") or ""), row


@pytest.mark.parametrize("sitemap_body", [None, EMPTY_URLSET],
                         ids=["no-sitemap-at-all", "sitemap-read-but-empty"])
def test_every_no_map_answer_names_the_credential_it_did_not_use(sitemap_body, request):
    """两张"地图是空的"的脸都要挂那半句 —— 一条要 token 的站回你的可能是一页 HTML，
    parse 出来正好是"空"，而读者看到的是一句关于站点的断言。

    反向一起测：没存凭据时那半句一个字都不许多，基础那句不许被改动。
    """
    site = request.getfixturevalue("site")
    site[1].sitemap_body = sitemap_body
    base, _rec = site
    url = f"{base}/"
    plain = _dump("web_discover", {"url": url, "sitemap": "on"})
    assert "cred_defaults" not in str(plain.get("summary")), plain
    _store_token()
    held = _dump("web_discover", {"url": url, "sitemap": "on"})
    base_summary = str(plain.get("summary"))
    assert base_summary in str(held.get("summary")), \
        "新加的那半句把原来那句盖掉了 —— 它是接上去的，不是替换"
    assert "neither robots.txt nor the sitemap was fetched with it" in \
        str(held.get("summary")), held.get("summary")
    assert "identity_applied" not in held or "auth" not in str(
        held.get("identity_applied")), held


# ─── 3 · 计划担心的那一件事，今天不成立（钉成守卫）───────────────────────────

def test_the_stored_allowance_does_not_stay_open_after_the_call(site):
    """放行不许漏成一次调用之外的一层作用域：存量是存量，作用域是作用域。

    计划里那句"为进程里后续所有无关调用撑开了栅栏"量的就是这个形状，而它问的对象
    换了（评审 R02-141 · F122）：存下来的 `allow_private` **本来就该**对后续每一次
    调用生效（§3-17「set once, every later call reads them back」），所以"合并之后的
    答案在调用之外仍是开的"不再是缺陷，而是那颗参数的定义。真正的缺陷是另一件事 ——
    有一层作用域活过了它自己的调用。R02-141 之前这一问由 `_ALLOW_PRIVATE` 那一次
    push 承载（门自己从存储读一次再推），所以问合并答案与问作用域是同一个问题；
    今天守卫自己读存量，两者必须分开问：

      · 作用域那一层（`_ALLOW_PRIVATE`）在进用例前后都必须是空的 —— 它一旦漏出去，
        就是本仓库为这颗 ContextVar 记过的那次"20 条守卫全绿变全红"；
      · 合并后的门只由存量决定：最后那三行 `unset` 之后立刻回拒，证明开的不是残留。

    **问的时刻要在同一条协程里**：`_dispatch` 是直接 await 处理函数的，所以它与调用方
    共用同一个 context，漏下来的写入在这里看得见；而 `asyncio.run` 起的是新任务，任务
    拿到的是 context 的**副本**，外面再问也问不到里面写了什么 —— 那样这条守卫会假绿。
    """
    import asyncio as _a

    from dhole_mcp import security

    url = f"{site[0]}/"
    assert security._ALLOW_PRIVATE.get() == (frozenset(), False), \
        "进用例之前作用域那一层就开着：那是别处的进程级写入"

    async def _sequence():
        opened = await MasterFetchServer()._dispatch(
            "web_read", {"url": url, "cache_ttl": -1})
        payload = opened[1] if isinstance(opened, tuple) else {}
        return payload, security._ALLOW_PRIVATE.get()

    payload, after = _a.run(_sequence())
    assert not payload.get("error"), f"存了放行，这一次就该读得到回环: {payload.get('error')}"
    assert after == (frozenset(), False), \
        f"放行漏在本次调用之外（那是进程级那一扇门）: {after}"

    identity.unset(["allow_private"])
    refused = _dump("web_read", {"url": url, "cache_ttl": -1})
    assert "private IP" in (refused.get("error") or ""), \
        f"清掉存储后仍放行：那道门不在调用作用域里，而在进程里: {refused.get('error')}"


@pytest.mark.parametrize("tool", ["web_read", "web_request", "web_crawl",
                                  "web_discover", "feed_read", "doc_parse"])
def test_nothing_on_a_call_can_open_the_private_door(tool):
    """抓取面没有这颗参数（§3-17 把它搬进了 cred_defaults 的 `set`）—— 计划前提的正面。"""
    from dhole_mcp import server as srv

    assert "allow_private" not in set(srv._TOP_LEVEL_ARGS.get(tool) or ()), tool
    assert "allow_private" in identity.KEYS


def test_the_receipt_credits_the_store_only_for_what_the_store_supplied():
    """计划要的"区分来源"早在了：手传的 `cdp_url=` 不给存储记功。"""
    _vals, note = MasterFetchServer()._identity_for(
        "https://example.com/", cdp_url="http://127.0.0.1:9222/x")
    assert "cdp" not in note, (note, "调用方自己传的那一颗，不该记在存储头上")


# ─── 4 · 手册得跟着事实走 ────────────────────────────────────────────────────

def test_the_manual_stops_claiming_one_credential_rule_for_every_tool():
    from dhole_mcp import guide

    _h, behavior = guide.read("behavior")
    assert "`feed_read` is the exception" in behavior, "手册还写着'同一条规则适用于每个工具'"
    assert "web_crawl" in behavior and "identity_applied" in behavior
    _h2, fields = guide.read("fields")
    assert "(crawl envelope)" in fields, "爬取信封上那一位没写进字段字典"
    assert "`note`" in fields, "feed 那格的第二个主语没写进字段字典"
