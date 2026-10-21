"""test_server_4.py —— 由 3 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import asyncio
import pytest
from dhole_mcp import browser as _browser
from dhole_mcp import params
from dhole_mcp import server as server_mod
from dhole_mcp.server import MasterFetchServer as Server
import http.server
import socket
import threading
from dhole_mcp import identity
from dhole_mcp.server import MasterFetchServer
import json
from dhole_mcp import context as CTX
from dhole_mcp import crawl as CRAWL
from dhole_mcp import quality as Q
from dhole_mcp import search as SEARCH
from dhole_mcp import server as SRV


# ============================================================================
# 来源：test_anti_detect_b5.py
# ============================================================================
# B5-13a：反爬伪装收成一颗 `anti_detect` 四档（§3-19）。

URL = "https://example.com/"

RETIRED = ("headless", "real_chrome", "hide_canvas", "block_webrtc",
           "solve_cloudflare", "useragent")

# 17.0 B5-13 之前 web_read 签名里那六颗的默认值 —— 逐位抄下来，
# 用来证明 auto 档没动行为。
OLD_DEFAULTS = {"headless": True, "real_chrome": False, "hide_canvas": True,
                "block_webrtc": True, "solve_cloudflare": True, "useragent": None}
# 第七位（W1 的 `stealthy_headers`）不在这张表里：它从前不是签名上的一颗，是 HTTP 层
# 两个调用点上的字面量 `True`，而 `_tier_kwargs` 的汇点在浏览器层 —— 拿它当对照会
# 测到 `None`（那颗根本没经过这条路）。逐位对照住在下面的
# test_the_rung_decides_whether_dhole_synthesizes_request_headers 里，同样证明
# `auto` 一位没动。


def _tier_kwargs(monkeypatch, **call):
    """走一次真实分发，抓浏览器层实际收到的参数。

    两条路径（force_fetcher='stealthy' 与 http→stealthy 自动升级）最后都汇到
    `stealthy_fetch`，所以断言打在汇点上，一次覆盖两条路。
    """
    seen: dict = {}

    async def fake(self, url, **kwargs):
        seen.update(kwargs)
        seen["url"] = url
        from dhole_mcp.server import ResponseModel
        return ResponseModel(url=url, status=200, content=["body"],
                             fetcher_used="stealthy")

    monkeypatch.setattr(Server, "stealthy_fetch", fake)
    # 自动会话会启动一台真浏览器；这一刀要读的是"浏览器层收到了什么参数"，
    # 不是浏览器本身。
    async def fake_session(self, **kw):
        return "auto-under-test"

    monkeypatch.setattr(Server, "_ensure_auto_session", fake_session)
    # cache_ttl=0：每一次都要真的走到浏览器层。第一档把 example.com 存进缓存之后，
    # 第二档会从缓存返回，`seen` 就成了空的 —— 这种假失败比真失败更难查。
    args = {"url": URL, "force_fetcher": "stealthy", "cache_ttl": 0}
    args.update(call)
    asyncio.run(Server()._dispatch("web_read", args))
    return seen


def _defs():
    return {td["name"]: td for td in Server._TOOL_DEFS}


def _http_tier_kwargs(monkeypatch, **call):
    """走一次真实分发，抓 **HTTP 层** 那一发实际收到的参数。

    `_tier_kwargs` 的汇点在浏览器层，而档位表第五位（W1 的 `stealthy_headers`）只有
    这一层读得到 —— 两位分开测，是因为它们本来就走两条不同的路：强制浏览器层的调用
    根本不经过 HTTP 层，那句"这一档关掉了合成头"就不该出现在它的回执里。
    """
    seen: dict = {}

    async def fake(self, url, **kwargs):
        seen.update(kwargs)
        seen["url"] = url
        from dhole_mcp.server import ResponseModel
        return ResponseModel(url=url, status=200, content=["body"],
                             fetcher_used="http", content_ok=True,
                             content_type="text/html")

    monkeypatch.setattr(Server, "get", fake)
    args = {"url": URL, "force_fetcher": "http", "cache_ttl": 0}
    args.update(call)
    asyncio.run(Server()._dispatch("web_read", args))
    return seen


# ─── 公开面：一颗换掉六颗 ──────────────────────────────────────────

def test_web_read_advertises_one_level_param_and_no_hardening_knobs():
    props = _defs()["web_read"]["inputSchema"]["properties"]
    assert "anti_detect" in props, sorted(props)
    # 17.0 B6-7：档位不再由 schema 的 enum 守，改由 dhole 的拒绝守 —— 因为宿主那条
    # "must be equal to one of the allowed values" 既不列合法值、也不给恢复方向。
    # 表还是同一份，只是读它的人从 schema 换成了 params._coerce_anti_detect_arg。
    assert "enum" not in props["anti_detect"], "enum 数组回来了"
    assert set(server_mod._ANTI_DETECT) == {"off", "basic", "auto", "aggressive"}
    assert len(props["anti_detect"]["description"]) <= 200
    gone = sorted(k for k in RETIRED if k in props)
    assert not gone, f"档位收了这几颗却还留在公开面：{gone}"
    assert "anti_detect" in server_mod._TOP_LEVEL_ARGS["web_read"]
    assert not set(RETIRED) & server_mod._TOP_LEVEL_ARGS["web_read"]
    # 六颗换一颗，这一刀的净账是 40 → 35；B5-13c 再把 auth / cookies / proxy /
    # extra_headers / allow_private 五颗搬进 cred_defaults，净账 35 → 30；B5-13d
    # 又把渲染时机那两颗（wait / network_idle）收成浏览器层的一条规矩，30 → 28；
    # B5-14a 把写操作那三颗（method / body / content_type）搬去 web_request，28 → 25；
    # B5-14b 再把动作链那一颗搬去 web_act，25 → 24。
    # B5-14d 加了两层扇出的三颗（nested_selector / nested_fields / nested_max），24 → 27：
    # 它们不是又一组装饰，而是"一次调用替 agent 发 8 次"这件事的接口 —— 没有它们，
    # 列表页→详情页只能靠 agent 自己排队，而排队的那 8 次里没人保证 robots 逐条查过。
    # §3-12 的 ≤20 已由作者裁决**放宽成棘轮**（"能力优先"，与作废 ≤26,000 同一句
    # 话）：这 7 颗里没有一颗是"没人用的装饰"——cdp_url / confirm / check_login 是
    # §3-16 那道门（"逐次留着"是定过的决定），nested_* 是扇出，剩的是读法本身。守卫
    # 在 tests/test_wire_shape.py，数字钉在当天的最大值、只准往下。
    # W1 是 27 → 28 的那一颗 `headers`。它不是又一个装饰：这批缺陷的原文是"我没设过
    # 的头"—— 调用方看见站点收到一整套自己没点名的头（实测 15 颗，含一枚编造的
    # google referer），而参数面上**没有任何一颗**能改它：`extra_headers` 是 §3-17
    # 具名拒绝的旧拼写，`referer` 硬编在两处。加它买到的不是旋钮，是"我说的话能说出去"。
    # 同一次还把 `stealthy_headers` 那两颗字面量交给档位表读（`rungs.py`），公开面
    # 因此**没有**多第二颗伪装旋钮。
    assert len(server_mod._TOP_LEVEL_ARGS["web_read"]) == 28, \
        len(server_mod._TOP_LEVEL_ARGS["web_read"])


def test_the_two_axes_stayed_two_axes():
    """两条轴不许混：伪装收进档位，时机收成规矩 —— 谁也不许替对方做决定。

    13a 的时候时机还留在参数面（那条测试钉的是"它俩没被吸进 anti_detect"）。13d
    之后时机那颗也没了：`wait` 彻底删，`network_idle` 变成认出壳页才付的 5 秒，
    判定的证人搬到 tests/test_render_settle_b5.py。这里守的是另一半：档位的枚举
    里不许混进时机（一个决定"像不像机器人"的旋钮不该同时决定"读不读得到内容"）。
    """
    props = set(_defs()["web_read"]["inputSchema"]["properties"])
    assert not {"wait", "network_idle"} & props, sorted(props)
    # 档位的合法值现在只印在两处：描述里的例子，和 dhole 拒本里的整份列表。两处都不
    # 许混进时机旋钮 —— 判据从 "enum == {...}" 换成 "能被接受的集合 == {...}"，因为
    # 后者才是真正被读的那份（params._coerce_anti_detect_arg 的 `_ANTI_DETECT`）。
    accepted = set()
    for level in ("off", "basic", "auto", "aggressive", "wait", "network_idle"):
        try:
            params._coerce_anti_detect_arg(level)
        except ValueError:
            continue
        accepted.add(level)
    assert accepted == {"off", "basic", "auto", "aggressive"}, accepted


@pytest.mark.parametrize("knob", RETIRED)
def test_a_retired_knob_raises_instead_of_being_ignored(knob):
    """§3-20 的静默忽略只管已注销的 DHOLE_* 环境变量。

    参数面上「未知键必须抛」是守卫（`_reject_unknown_args`），删掉的旋钮也是陌生键：
    悄悄吞掉一次 `real_chrome=true`，调用方就会相信那一档开过。
    """
    with pytest.raises(ValueError, match="Unsupported argument"):
        asyncio.run(Server()._dispatch("web_read", {"url": URL, knob: True}))


def test_an_unknown_level_names_the_four():
    # web_read 的坏参数走的是信封而不是异常（与 urls 那条同一条口径），
    # 所以这里读的是回包里那句话，不是 raise。
    content, structured = asyncio.run(
        Server()._dispatch("web_read", {"url": URL, "anti_detect": "paranoid"}))
    blob = str(content) + str(structured)
    assert "anti_detect" in blob, blob[:200]
    for level in server_mod._ANTI_DETECT:
        assert level in blob, f"报错没列出 {level} 这一档"


# ─── 行为账：auto 必须逐位等于删之前的默认 ─────────────────────────

def test_auto_is_bit_for_bit_the_pre_refactor_default(monkeypatch):
    seen = _tier_kwargs(monkeypatch)
    for name, was in OLD_DEFAULTS.items():
        assert seen.get(name) == was, f"auto 档把 {name} 从 {was!r} 改成了 {seen.get(name)!r}"


@pytest.mark.parametrize("level,expected", [
    ("off", {"hide_canvas": False, "block_webrtc": False, "solve_cloudflare": False}),
    ("basic", {"hide_canvas": True, "block_webrtc": True, "solve_cloudflare": False}),
    ("auto", {"hide_canvas": True, "block_webrtc": True, "solve_cloudflare": True}),
    ("aggressive", {"hide_canvas": True, "block_webrtc": True, "solve_cloudflare": True}),
])
def test_each_level_sets_the_hardening_it_claims(monkeypatch, level, expected):
    seen = _tier_kwargs(monkeypatch, anti_detect=level)
    for name, want in expected.items():
        assert seen.get(name) is want, f"{level} 档的 {name} 不是 {want}"


@pytest.mark.parametrize("level,want", [
    ("off", False), ("basic", True), ("auto", True), ("aggressive", True),
])
def test_the_rung_decides_whether_dhole_synthesizes_request_headers(monkeypatch,
                                                                    level, want):
    """档位表的第五位（W1）：`off` 从此在 HTTP 层也真的关得掉东西。

    这一位从前是两个调用点上的字面量 `stealthy_headers=True`，所以那一档在纯 HTTP 的
    路上**什么都不关**：一次零点名的调用实测发 15 颗头，其中 `referer:
    https://www.google.com/` 是编出来的来路，`sec-fetch-site: "?1"` 是一个错值（真
    Chrome 在这一位发 none / same-origin / cross-site）。`auto` 那一位必须仍是 True
    —— 它与上面那条 OLD_DEFAULTS 的逐位对照同一条命：红了就是在改反爬效果，不是在
    改参数名。
    """
    seen = _http_tier_kwargs(monkeypatch, anti_detect=level)
    assert seen.get("stealthy_headers") is want, (
        f"{level} 档把 HTTP 层的合成头设成了 {seen.get('stealthy_headers')!r}")


def test_aggressive_stops_trimming_resources(monkeypatch):
    """真浏览器不裁资源：这是 aggressive 相对 auto 唯一说得清的另一件事。

    强度不该靠"强制某个 channel"来表达（见下面那条 artifact 测试），而
    `disable_resources` 是既有管线里唯一一个"更像真浏览器"的开关。
    """
    assert _tier_kwargs(monkeypatch, anti_detect="auto")["disable_resources"] is True
    assert _tier_kwargs(monkeypatch,
                        anti_detect="aggressive")["disable_resources"] is False


def test_no_level_asks_for_an_artifact_the_machine_does_not_have(monkeypatch):
    """干净环境 + aggressive：不许去要它没装的东西，也不许触发新下载。

    `launch_artifact(real_chrome=True)` 回的是 SYSTEM_CHROME（"不用下载"），真正
    会痛的是起不来：channel='chrome' 在没装 Chrome 的机器上是硬失败。所以四档一律
    不传它。这里把"这台机器有没有 Chrome"钉成没有 —— 不钉的话，这条在装了 Chrome
    的开发机上读的是另一台机器。
    """
    monkeypatch.setattr(_browser, "_chrome_channel_cache", "chromium")
    for level in server_mod._ANTI_DETECT:
        seen = _tier_kwargs(monkeypatch, anti_detect=level)
        assert seen["real_chrome"] is False, f"{level} 档把 channel 钉死了"
        assert _browser.launch_artifact(seen["real_chrome"]) == "chromium", \
            f"{level} 档要的 artifact 不是自带 chromium"


def test_the_ladder_does_not_need_the_chrome_knob_because_detection_has_it(monkeypatch):
    """装了 Chrome 的机器本来就在用它，不必有人传 `real_chrome=true`。

    删掉这颗旋钮不是少了一种能力：`_launch_channel(False)` 走
    `_detect_chrome_channel()`，装了就用；传 True 只是把"没装"那种情况从降级
    变成硬失败。
    """
    monkeypatch.setattr(_browser, "_chrome_channel_cache", "chrome")
    seen = _tier_kwargs(monkeypatch, anti_detect="aggressive")
    assert seen["real_chrome"] is False
    assert _browser._launch_channel(seen["real_chrome"]) == "chrome"


def test_every_level_leaves_the_ua_with_its_profile(monkeypatch):
    """手传 UA 会与 TLS/指纹配套冲突：档位面必须根本没有这个口。"""
    for level in server_mod._ANTI_DETECT:
        assert _tier_kwargs(monkeypatch, anti_detect=level)["useragent"] is None


# ─── 扇出面：bulk 是第二条路 ───────────────────────────────────────

def test_the_rung_survives_the_bulk_fanout(monkeypatch):
    """`urls=[...]` 走的是 `_smart_fetch_bulk`，一条和单 URL 平行的路。

    这一条是本批唯一真红过的地方：档位参数换掉六颗时，bulk 的签名与转发都还是
    老样子，于是 `anti_detect` 在 `_fetch_one` 里 NameError，被那一层的
    `except Exception` 吞成一行 status=0 的错误 —— 回包里看不出任何事，只有
    "每个 URL 都失败"。单 URL 测试全绿也照不出来。
    """
    seen: list = []

    async def fake(self, url, **kwargs):
        seen.append(kwargs)
        from dhole_mcp.server import ResponseModel
        return ResponseModel(url=url, status=200, content=["body"],
                             fetcher_used="stealthy")

    async def fake_session(self, **kw):
        return "auto-under-test"

    monkeypatch.setattr(Server, "stealthy_fetch", fake)
    monkeypatch.setattr(Server, "_ensure_auto_session", fake_session)
    # 只给 urls=：这一条测的是批量路径。从前这里把 URL 又按位置传了一遍，而
    # 实现让 urls 赢、url 静默消失，于是断言（2 行而不是 3 行）照样绿 —— 一条
    # 手写的测试都能把"少了一个地址"吞下去，这正是那个静默能活到今天的原因。
    out = asyncio.run(Server().web_read(
        "", urls=[URL, "https://other.example.org/"],
        force_fetcher="stealthy", cache_ttl=0, anti_detect="aggressive"))
    assert out.successful == out.total == len(seen) == 2, \
        f"bulk 里有一行没走到浏览器层：{[r.error for r in out.results]}"
    for kw in seen:
        assert kw["disable_resources"] is False
        assert kw["hide_canvas"] is True and kw["block_webrtc"] is True
        assert kw["useragent"] is None


# ============================================================================
# 来源：test_a_crawl_that_ran_as_you_says_it_ran_as_you.py
# ============================================================================
# D1：带着你的凭据爬回来的二十页，得说那是你登录后的样子，而不是公开的样子。

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


# ============================================================================
# 来源：test_a_rejected_url_is_not_reported_as_a_missing_one.py
# ============================================================================
# 同一颗坏 URL 在各扇门上必须是同一位裁判 —— 搜索门那条 find_similar 路今天不是（评审 R02-131）

TABLE_CODES = {e[1] for e in Q._DIAGNOSIS_PREFIXES} | {e[1] for e in Q._DIAGNOSIS_CONTAINS}
FIXTURE = "http://127.0.0.1:8399/index.html"

#: 五种必然在发包之前被守卫挡下的 `url=`（全是本机/非法形状，外网一个名字都不查）
BAD_URLS = [
    ("loopback", FIXTURE, "internal/private IP"),
    ("private-ip", "http://10.1.2.3/x", "internal/private IP"),
    ("metadata", "http://169.254.169.254/latest/meta-data", "cloud metadata"),
    ("no-scheme", "not a url", "Only http and https"),
    ("javascript", "javascript:alert(1)", "'javascript' is not allowed"),
]

#: 每扇门的必填锚点：缺了锚点就先撞"缺参数"那面，量到的不是 URL 守卫这一面。
#: 新长出一扇收 `url=` 的门而这里没有它，第 ① 格会点名报错 —— 那是尺具在催下一位作者，不是漏量。
ANCHORS = {"web_read": {}, "web_request": {"method": "POST"},
           "web_act": {"actions": [{"click": "#nope"}]},
           "web_crawl": {}, "web_discover": {}, "feed_read": {},
           "web_search": {"mode": "find_similar"}}

SEARCH_REFUSALS = [
    ("mode 里的陌生词", {"mode": "bogus-mode", "query": "r131"}),
    ("缺词也缺 url", {"query": ""}),
    ("名册里没有的引擎", {"query": "r131", "engines": ["no-such-engine"]}),
    ("freshness 的陌生词", {"query": "r131", "freshness": "nonsense"}),
    ("page 是负数", {"query": "r131", "page": -2}),
    # R02-136 把首词那把尺的名单改成扫出来之后，这三面第一次到场（前四张是 F113 数的）：
    # 两颗是参数拒绝（同一扇门里，别颗交 `invalid_request` 而它们交措辞发明的词），
    # 一颗是 find_similar 读不到源页（那颗**不是**参数拒绝，见下面单独一枚腿）。
    ("find_similar 没交 url", {"mode": "find_similar", "url": ""}),
    ("共识地板踩在下限下", {"query": "r02-139", "fetch_min_consensus": 1}),
    ("共识地板没开取正文", {"query": "r02-139", "fetch_min_consensus": 3}),
]


async def _call(tool: str, args: dict) -> dict:
    from mcp import ClientSession
    from mcp.client._memory import InMemoryTransport

    transport = InMemoryTransport(MasterFetchServer()._mcp_server())
    async with transport as streams:
        async with ClientSession(streams[0], streams[1]) as session:
            await session.initialize()
            res = await session.call_tool(tool, args)
    text = "".join(getattr(b, "text", "") for b in res.content)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"_prose": text[:400]}


def _err(p: dict) -> str:
    return str(p.get("error") or p.get("_prose") or "")


URL_DOORS = sorted(d for d, args in SRV._TOP_LEVEL_ARGS.items() if "url" in args and d in ANCHORS)


@pytest.mark.parametrize("door", URL_DOORS)
def test_the_url_doors_are_all_here(door):
    """尺具自己的名单：每扇收 `url=` 的门都得有锚点，否则这一族的跨门比较缺一角。"""
    assert door in ANCHORS, door


@pytest.mark.parametrize("door", URL_DOORS)
@pytest.mark.parametrize("shape,url,needle", BAD_URLS)
@pytest.mark.asyncio
async def test_a_rejected_url_is_judged_the_same_way_on_every_door(door, shape, url, needle):
    """判据 ①：同一颗坏 URL 在所有门上同一颗 code、同一段出路正文（只差本门的动词）。"""
    args = {"url": url}
    args.update(ANCHORS[door])
    p = await _call(door, args)
    err = _err(p)
    assert err, (door, shape, sorted(p))
    assert needle in err, (door, shape, err[:160])
    dia = p.get("diagnosis")
    assert isinstance(dia, dict), f"{door}/{shape} 的把手整格缺席：{sorted(p)}"
    assert dia.get("code") == "invalid_request", (door, shape, dia)
    assert dia.get("retryable") is False, (door, shape, dia)
    clause = CTX.nothing_went_out(door)
    na = str(p.get("next_action") or "")
    assert na.startswith(clause), (door, shape, na[:120])
    body = na[len(clause):].strip()
    assert body, (door, shape, na[:120])
    # 出路正文的**第一位作者**是 security 那四段共用文案，门名只进动词；这里比较正文的开头，
    # 因为 URL_SHAPE_RECOVERY 里插了 `{tool}`（那是唯一一处按门换字的共用文案，允许不同）。
    if "Absolute http(s)" in body or "takes an absolute http(s)" in body:
        pass          # 形状族那一句本来就点名本门（`tool=` 一位作者）
    if door != "web_read":
        peer = await _call("web_read", {"url": url})
        peer_body = str(peer.get("next_action") or "")[len(CTX.nothing_went_out("web_read")):].strip()
        same_family = (body.split(".")[0][:40] == peer_body.split(".")[0][:40]
                       or "absolute http(s)" in body.lower())
        assert same_family, (door, shape, body[:120], peer_body[:120])


@pytest.mark.parametrize("door", URL_DOORS)
@pytest.mark.asyncio
async def test_the_gate_still_closes(door):
    """判据 ④：这一把不推门 —— 私网那两种形状在每扇上都一个包都没发。"""
    for url in (FIXTURE, "http://10.1.2.3/x", "http://169.254.169.254/latest/meta-data"):
        args = {"url": url}
        args.update(ANCHORS[door])
        p = await _call(door, args)
        assert _err(p), (door, url, sorted(p))
        assert p.get("engines_used") in (None, [], {}), (door, url, p.get("engines_used"))
        assert p.get("fetcher_used") in (None, "none"), (door, url, p.get("fetcher_used"))
        assert not (p.get("results") or p.get("pages") or p.get("content")
                    or p.get("entries") or p.get("feeds")), (door, url)


@pytest.mark.parametrize("shape,url,needle", BAD_URLS)
@pytest.mark.asyncio
async def test_a_rejected_url_is_not_reported_as_a_missing_one(shape, url, needle):
    """判据 ②：find_similar 收到被守卫挡下的 URL，要说守卫说的原因，不许说"你没传"。"""
    p = await _call("web_search", {"mode": "find_similar", "url": url})
    err = _err(p)
    assert needle in err, (shape, err[:170])
    assert "requires a url" not in err, (shape, "传了的 URL 被说成没传：" + err[:150])


@pytest.mark.asyncio
async def test_a_genuinely_missing_url_still_says_so():
    """正控制：什么都没传时那句真话留着 —— 这一把修的是**吞掉原因**，不是把那句话删了。"""
    p = await _call("web_search", {"mode": "find_similar", "url": ""})
    assert "requires a url" in _err(p), _err(p)[:170]


@pytest.mark.asyncio
async def test_a_source_page_that_cannot_be_read_is_named_by_a_registered_token(monkeypatch):
    """判据 ⑤（R02-137 · 现场 r276）：find_similar 读不到源页那一面，code 不许由措辞决定。

    `fetch_source_for_similar` 交回空 ⇒ 门里那句 `could not fetch the source URL for
    find_similar (blocked or offline).` 是 **dhole 自己写的**句子，从前判据表里没有它的格子，
    上线的 `code` 是小写首词兜底发明的 `could`（那条兜底按 quality.py:2153 的注释是给"消息写在
    dhole 之外"的句子留的）。这一枚钉的是：这颗 token 在册、住在表里，而不是被首词顶替。

    零发包：源页取词被桩住（真请求根本不会发生），重排器加载也桩住（它会去碰磁盘/网络），
    URL 用本机靶站那颗带 `captcha` 字样的页 —— 这正是门里那条 blocked 判据认的形状，
    放开只对本机回环生效（`DHOLE_ALLOW_PRIVATE_HOSTS=127.0.0.1`，与仓库里其余本地量具同一惯例）。
    """
    from unittest.mock import AsyncMock

    monkeypatch.setenv("DHOLE_ALLOW_PRIVATE_HOSTS", "127.0.0.1")

    async def _no_source(url, **kwargs):
        # 桩交的是**看见的那一件事**（R02-138 的修法形状）：靶站 /captcha 那页命中 blocked。
        return {"title": "", "body": "", "status": 200,
                "reason": "blocked", "detail": ""}

    monkeypatch.setattr(SEARCH, "fetch_source_for_similar", _no_source)
    monkeypatch.setattr(SEARCH, "ensure_reranker", AsyncMock(return_value=None))
    p = await _call("web_search", {"mode": "find_similar",
                                   "url": "http://127.0.0.1:8399/captcha"})
    err = _err(p)
    assert "source page" in err and "find_similar" in err, err[:170]  # R02-137 的本职：句首带着在册的名字，说的是源页这一面
    dia = p.get("diagnosis")
    assert isinstance(dia, dict), f"这面戴 error 没把手：{sorted(p)}"
    assert dia.get("code") != "could", f"code 是首词兜底发明的：{dia}"
    assert dia.get("code") in TABLE_CODES, (dia, sorted(TABLE_CODES))
    assert str(dia.get("recover_with") or "").strip(), dia


@pytest.mark.asyncio
async def test_the_advice_and_the_verdict_agree_on_retrying_that_page(monkeypatch):
    """D5 那一法的建议位形状（R02-137 的另一半）：叫人事先 Retry 的回执不许写 retryable=false。

    现场读数是 `('could', False, '')` 配 `next_action="Retry, or web_read the source URL first…"`
    —— 同一封回执里两只手互相否决。这一枚不规定哪一只是对的（判决归谁由 R02-138 那张账判），
    只钉住两只手不能互相否决。
    """
    from unittest.mock import AsyncMock

    monkeypatch.setenv("DHOLE_ALLOW_PRIVATE_HOSTS", "127.0.0.1")

    async def _no_source(url, **kwargs):
        # 桩交的是**看见的那一件事**（R02-138 的修法形状）：靶站 /captcha 那页命中 blocked。
        return {"title": "", "body": "", "status": 200,
                "reason": "blocked", "detail": ""}

    monkeypatch.setattr(SEARCH, "fetch_source_for_similar", _no_source)
    monkeypatch.setattr(SEARCH, "ensure_reranker", AsyncMock(return_value=None))
    p = await _call("web_search", {"mode": "find_similar",
                                   "url": "http://127.0.0.1:8399/captcha"})
    na = str(p.get("next_action") or "")
    dia = p.get("diagnosis") or {}
    if "retry" in na.lower():
        assert dia.get("retryable") is True, (na[:120], dia)
    else:
        assert dia.get("retryable") is False, (na[:120], dia)



@pytest.mark.parametrize("label,args", SEARCH_REFUSALS)
@pytest.mark.asyncio
async def test_the_search_doors_own_arg_refusals_hands_over_a_code(label, args):
    """判据 ③：`Invalid <arg>: …` 这一族从此在册（`invalid_request` 一颗 code，名字与别扇同一位）。"""
    p = await _call("web_search", args)
    err = _err(p)
    assert err, (label, sorted(p))
    dia = p.get("diagnosis")
    assert isinstance(dia, dict), f"{label} 的把手整格缺席：{sorted(p)}"
    assert dia.get("code") == "invalid_request", (label, dia)
    assert dia.get("code") in TABLE_CODES, (label, dia)
    assert str(dia.get("recover_with") or "").strip(), (label, dia)
    assert str(dia.get("recover_with")) != str(dia.get("code")), (label, dia)


def test_the_repair_hint_and_the_code_are_one_author_per_fact():
    """出路那一句的作者没换：`_param_repair_hint` 仍按 `Invalid <arg>:` 表点名参数，
    而 code 由判据表给 —— 两件事各有各的作者，不许并成一个（并了就会有人去改 hint 表顺手改 code）。"""
    for prefix in ("Invalid mode:", "Invalid engine:", "Invalid freshness:", "Invalid page:",
                   "Invalid site filter:", "Invalid exclude_sites entry:",
                   "Invalid location:", "Invalid language:"):
        assert any(k[0] == prefix for k in SEARCH._PARAM_REPAIR_HINTS), prefix
        code, retryable, recover = Q.classify_error_text(prefix + " 'x' (whatever)")
        assert code == "invalid_request", (prefix, code)
        assert retryable is False and str(recover).strip(), (prefix, code, recover)


@pytest.mark.asyncio
async def test_the_codes_come_from_the_table_not_from_the_door(monkeypatch):
    """归属锁（F93）：哨兵换掉判据表唯一的读者 ⇒ 被扫的每一张脸跟着变（含 crawl / search 两副回执）。"""
    monkeypatch.setattr(Q, "classify_error_text", lambda err: ("SENTINEL", False, "from the table"))
    faces = [("web_search", {"mode": "bogus-mode", "query": "r131"}),
             ("web_search", {"mode": "find_similar", "url": "http://10.1.2.3/x"}),
             ("web_crawl", {"url": "http://10.1.2.3/x"}),
             ("web_discover", {"url": "not a url"}),
             ("web_read", {"url": "http://10.1.2.3/x"})]
    for tool, args in faces:
        p = await _call(tool, args)
        dia = p.get("diagnosis") or {}
        assert dia.get("code") == "SENTINEL", (tool, args, dia)
        assert str(dia.get("recover_with") or "").startswith("from the table"), (tool, args, dia)


def test_the_crawl_and_search_receipts_have_the_slot():
    """模型前提（与 R02-130 那格同法）：这两副回执从此有这一格，而线上只在有 error 时上线。"""
    assert "diagnosis" in CRAWL.CrawlResponseModel.model_fields
    assert "diagnosis" in SEARCH.SearchResponseModel.model_fields
    # 干净形状不带它：直接造一副没有 error 的回执，按 wire 那一份序列化看键
    from dhole_mcp import wire
    clean = SEARCH.SearchResponseModel(query="r131", results=[], total_results=0)
    assert "diagnosis" not in wire._wire_dump(clean), sorted(wire._wire_dump(clean))
    refused = SEARCH.SearchResponseModel(query="r131", results=[], total_results=0,
                                         error="Invalid mode: 'x' (auto|neural|find_similar)")
    dumped = wire._wire_dump(refused)
    assert dumped.get("diagnosis", {}).get("code") == "invalid_request", dumped
