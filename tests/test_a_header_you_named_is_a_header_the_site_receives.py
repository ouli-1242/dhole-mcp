"""W1：调用方点名的头必须真的到线上，而到不了线上的名字必须当场具名退回。

缺陷的原文是一句人的话："我没设过的头"。一次本地 echo 抓取量到，一次普通的
`web_read` 往站点发 **15 颗** 头，一颗都不是调用方点的：`referer:
https://www.google.com/`（编出来的来路）、browserforge 生成的 UA/`sec-ch-ua*`
（本次实测出现 `user-agent` 说 macOS 而 `sec-ch-ua-platform` 说 Windows 的一发），
以及一枚错值的 `sec-fetch-site: "?1"`（真 Chrome 在这一位发 none / same-origin /
cross-site）。而参数面上没有任何一颗能改它：`extra_headers` 是被 §3-17 具名拒绝的
旧拼写（它当时搬走的是**身份**），`referer` 硬编在两处。

同一份实测还推翻了一条写在计划里的话：`sec-purpose` 与 `upgrade-insecure-requests`
曾被记成"primp 编译层之下、dhole 的字典覆盖不掉"。**量下来是覆盖得掉的** ——
primp 只补字典里没有的名字。所以本文件没有"技术上做不到"那类拒绝，只有三条设计
边界，每一条都把自己那道门的地址一起交出去（params.py：拒必须具名）：

* `cookie` / `authorization` / `proxy-authorization` → `cred_defaults`（按主机存的，
  跨源重定向会被剥掉；逐次传等于把这两条保证一起丢掉）
* `user-agent` → 它与 TLS 画像是配对的（`impersonate_os` 在你这份字典合流**之前**
  就由 dhole 自己的基线 UA 定了）
* `if-*` 校验子 → 公开的那扇门只有 `extraction_type='check'`（浏览器层带不动校验子）

验收刀9-1 之后，上面那句"往站点发 15 颗头"只对**公网**成立：靶站是回环，而回环/内网
目标现在收不到 dhole 合成的那一份（一枚编造的 google `referer` 落进用户自己的服务日志
不是反爬，是污染）。所以本文件留下的两条"默认档仍发模板"的对照搬到了能判公网的位置上
（`_build_headers()` 与替身层的 `stealthy_headers`），回环那一侧的新语义在
`test_a_private_target_gets_no_invented_referrer.py`。"你点名的每一颗都到线上"这一半
不受影响 —— 摘的是 dhole 合成的，从来不是你点的。

真 socket 组测的是**抵达**（不发一封就谈不上赢）；替身组测形状、指纹、批量与两处
描述。摘掉修复的对照写在提交信息里。
"""

from __future__ import annotations

import asyncio
import http.server
import json
import re
import socket
import threading

import pytest

from dhole_mcp import identity
from dhole_mcp import server as server_mod
from dhole_mcp.server import MasterFetchServer

URL = "https://example.com/"
PAGE = (b"<html><head><title>T</title></head><body><p>"
        + b"Real body text so the extractor has something to chew on. " * 4
        + b"</p></body></html>")

# conftest 的 `_offline_dns`（autouse）把 getaddrinfo 钉成公网地址。靶站是真 socket，
# 真解析器要在**导入时**抓住，否则夹具里拿到的是被钉住的那个（先例见
# test_a_private_sitemap_is_read_when_the_door_is_open.py 的同一句话）。
_REAL_GETADDRINFO = socket.getaddrinfo


class _Recorder:
    """一个把每一发的头记下来的最小站点。"""

    def __init__(self):
        self.received: list[tuple[str, dict]] = []

    def handler(self):
        rec = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _answer(self):
                rec.received.append((self.path,
                                     {k.lower(): v for k, v in self.headers.items()}))
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(PAGE)))
                self.end_headers()
                self.wfile.write(PAGE)

            def do_GET(self):
                self._answer()

            def do_POST(self):
                self._answer()
        return Handler

    def page_headers(self) -> dict:
        """落在页面那一发的头（robots.txt / 预检各发自己的一份，不算这一发）。"""
        hits = [h for p, h in self.received if p.split("?")[0] == "/"]
        assert hits, f"页面从未被请求：{[p for p, _ in self.received]}"
        return hits[-1]

    def paths(self) -> list[str]:
        return [p for p, _ in self.received]


@pytest.fixture
def site(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _REAL_GETADDRINFO)
    rec = _Recorder()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), rec.handler())
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    # 只走**存下来的**那一份放行。conftest 把 identity.db 重定向进每用例自己的
    # tmp_path，所以它出不了这一格；而直接推 `_ALLOW_PRIVATE` 那颗 ContextVar 会
    # 漏出用例之外（实测：同一套件里后面 20 条"私网必须被拒"的守卫全绿变全红 ——
    # 门在进程级别被我自己撑开了）。工具每次调用都从存储重推作用域，所以存储这一份
    # 就够了，多的那一推只有副作用。
    identity.set_value("allow_private", True)
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}/", rec
    finally:
        srv.shutdown()
        srv.server_close()


def _call(tool: str, args: dict):
    out = asyncio.run(MasterFetchServer()._dispatch(tool, args))
    if isinstance(out, tuple) and len(out) == 2:
        return out[1]
    return {}


def _tier_seen(monkeypatch, tool="web_read", **args):
    """抓 HTTP 层那一发实际收到的参数（`get` 是两条整篇读路的汇点）。"""
    seen: dict = {}

    async def fake(self, url, **kwargs):
        seen.update(kwargs)
        seen["url"] = url
        return server_mod.ResponseModel(url=url, status=200, content=["body text"],
                                         fetcher_used="http", content_ok=True,
                                         content_type="text/html")

    monkeypatch.setattr(MasterFetchServer, "get", fake)
    args.setdefault("url", URL)
    _call(tool, args)
    return seen


def _stub_tiers(monkeypatch):
    """两层都替掉，按先后记下每发实际收到的参数（浏览器层不许在这条路上起来）。"""
    calls: list[dict] = []

    async def fake_get(self, url, **kwargs):
        calls.append(dict(kwargs, tier="http", url=url))
        return server_mod.ResponseModel(url=url, status=200, content=["body text"],
                                        fetcher_used="http", content_ok=True,
                                        content_type="text/html")

    async def fake_browser(self, url, **kwargs):
        calls.append(dict(kwargs, tier="stealthy", url=url))
        return server_mod.ResponseModel(url=url, status=200, content=["body text"],
                                        fetcher_used="stealthy", content_ok=True,
                                        content_type="text/html")

    monkeypatch.setattr(MasterFetchServer, "get", fake_get)
    monkeypatch.setattr(MasterFetchServer, "stealthy_fetch", fake_browser)
    # 浏览器层的入口先要一台已起的会话；真浏览器在默认那条路上被 conftest 拦着，
    # 而这一刀要读的是"浏览器层收到了什么参数"，不是浏览器本身（先例见
    # test_anti_detect_b5.py 的 `_tier_kwargs`，同一句理由）。
    async def fake_session(self, **kw):
        return "auto-under-test"

    monkeypatch.setattr(MasterFetchServer, "_ensure_auto_session", fake_session)
    return calls


# ─────────────── 抵达：真线上看到的就是点名的那一颗 ───────────────

def test_a_named_header_arrives_at_the_site_verbatim(site):
    """点名的头不是"记在回执里"，是站点收到的那一个值。"""
    url, rec = site
    _call("web_read", {"url": url, "force_fetcher": "http", "cache_ttl": 0,
                       "ignore_robots": True,
                       "headers": {"x-dhole-probe": "as-written",
                                   "accept-language": "de-DE,de;q=0.9"}})
    got = rec.page_headers()
    assert got["x-dhole-probe"] == "as-written", got
    assert got["accept-language"] == "de-DE,de;q=0.9", got


def test_a_named_header_wins_over_dholes_own_synthesized_value(site):
    """那枚实测发错的 `sec-fetch-site: "?1"`：点名它的人拿到的是他写的值。

    这一条是"覆盖得掉吗"那个问题的答案，也是把计划里那句错话（"primp 编译层覆盖
    不掉"）留在原地的东西 —— 它现在按实测写着：字典在场时 primp 让路。
    """
    url, rec = site
    _call("web_read", {"url": url, "force_fetcher": "http", "cache_ttl": 0,
                       "ignore_robots": True,
                       "headers": {"sec-fetch-site": "same-origin"}})
    assert rec.page_headers()["sec-fetch-site"] == "same-origin", rec.page_headers()


def test_the_template_still_rides_when_you_only_add_to_it(site, monkeypatch):
    """`headers=` 是**加**，不是**换**：默认那一档的模板仍在你那份之下垫着。

    这句话有两个读者。一个是以为"点了头就等于我控制这一发"的人 —— 公网目标照样收到
    dhole 自己的模板，它是过墙的承重件，删它会开始失败；另一个是以为 `off` 档
    什么都没关的人 —— 下面两条测的就是它。

    靶站是回环，而回环从验收刀9-1 起**不再**收到那枚编造的来路（那一句由
    `test_a_private_target_is_sent_your_headers_only.py` 测）。所以这一条测"加不是换"
    的**公网**那一半用替身层量（模板在不在，看交给 HTTP 层的 `stealthy_headers`），
    真 socket 这一半只测点名的那几颗到不到 —— 那正是这条命令原本要回答的问题。
    """
    url, rec = site
    _call("web_read", {"url": url, "force_fetcher": "http", "cache_ttl": 0,
                       "ignore_robots": True, "headers": {"x-dhole-probe": "1"}})
    assert rec.page_headers()["x-dhole-probe"] == "1", rec.page_headers()

    calls = _stub_tiers(monkeypatch)
    out = _call("web_read", {"url": URL, "force_fetcher": "http", "cache_ttl": 0,
                             "headers": {"x-dhole-probe": "1"}})
    assert calls[0]["stealthy_headers"] is True, calls[0]
    assert calls[0]["headers"] == {"x-dhole-probe": "1"}, calls[0]
    assert "wins key by key over dhole" in (out.get("summary") or ""), out.get("summary")

    url2, rec2 = site
    _call("web_read", {"url": url2, "force_fetcher": "http", "cache_ttl": 0,
                       "ignore_robots": True})
    synthesized = rec2.page_headers()
    assert "referer" not in synthesized, (
        f"回环目标还收到编造的来路（验收刀9-1 之后不该有）: {synthesized}")


def test_the_off_rung_sends_no_dhole_synthesized_header(site):
    """`anti_detect='off'` 在 HTTP 层第一次真的关掉合成（此前它是两颗字面量）。

    断的是可复现的性质而不是某一天的名单：`referer` 缺席、点名的那一颗到了、
    `sec-fetch-site` 落在真 Chrome 会用的那几个值里（同一份 16 发的抽样里 `auto` 档
    有 13 发带着 `"?1"` —— 那是 `Sec-Fetch-User` 的值）。
    """
    url, rec = site
    _call("web_read", {"url": url, "force_fetcher": "http", "cache_ttl": 0,
                       "ignore_robots": True, "anti_detect": "off",
                       "headers": {"x-dhole-probe": "alone"}})
    got = rec.page_headers()
    assert got["x-dhole-probe"] == "alone", got
    assert "referer" not in got, f"off 档还发编造的来路: {got}"
    assert got.get("sec-fetch-site") in (None, "none", "same-origin", "same-site",
                                         "cross-site"), \
        f"off 档带着 dhole 模板那个错值: {got.get('sec-fetch-site')!r}"


def test_the_off_rung_note_reaches_the_auto_path_too(monkeypatch):
    """自动升级那条路（不针层）也说得出这一发没带合成头。

    这条不是重复上一条：那一刀替下来的两颗字面量住在**两个**调用点上（针层那一条
    与自动升级那一条）。只测其中一个时，另一个摘回 `stealthy_headers=True` 整套
    守卫一条都不红 —— 本刀的 E2 对照实测就是这么一条 GREEN 自己把自己指出来的。
    """
    calls = _stub_tiers(monkeypatch)
    out = _call("web_read", {"url": URL, "cache_ttl": 0, "anti_detect": "off"})
    assert [c["tier"] for c in calls][:1] == ["http"], calls
    assert calls[0]["stealthy_headers"] is False, calls[0]
    assert "no dhole-synthesized headers" in (out.get("summary") or ""), out.get("summary")


def test_the_default_rung_does_not_claim_a_switch_that_never_flipped(monkeypatch):
    """反向：`auto`（默认）那一发不许带这句话 —— 说了就是给一次没发生的改动做证。"""
    calls = _stub_tiers(monkeypatch)
    out = _call("web_read", {"url": URL, "cache_ttl": 0})
    assert calls[0]["stealthy_headers"] is True, calls[0]
    assert "no dhole-synthesized headers" not in (out.get("summary") or ""), out


@pytest.mark.live
def test_the_auto_path_sends_no_invented_referrer_when_the_rung_is_off(site):
    """真 socket 上的同一条对照：不针层、只点档位，站点就收不到那枚编造的来路。"""
    url, rec = site
    _call("web_read", {"url": url, "cache_ttl": 0, "ignore_robots": True,
                       "anti_detect": "off", "headers": {"x-dhole-probe": "auto"}})
    got = rec.page_headers()
    assert "referer" not in got, f"自动升级那条路还在发合成头: {got}"
    assert got["x-dhole-probe"] == "auto", got


def test_the_default_rung_still_synthesizes_the_template():
    """零回归的那一半：`auto`（默认）那一发仍然把 dhole 自己的模板垫在你那份之下。

    编造的 google `referer` 是过墙的承重件，它在公网上**留着** —— 这一条不是为它辩护，
    是把"默认没动"钉住，防止下一次有人把它当漏掉的分支删了。

    量的是 `_build_headers()` 这一层（先例：`test_a_machine_without_browserforge_builds_
    what_it_always_built` 也直接读它）。真 socket 那半边量不了了：靶站是回环，而回环从
    验收刀9-1 起由 dhole 自己把模板摘掉（那一句在
    `test_a_private_target_gets_no_invented_referrer.py` 里测）。这里也不许断言"线上没有
    user-agent"：primp 自己那份 Chrome 画像照样在发，摘掉的只是 dhole 合成的那一份。
    """
    from dhole_mcp.fetcher import HTTPSession

    assert HTTPSession(stealthy_headers=True)._build_headers()["referer"] == \
        "https://www.google.com/"
    merged = HTTPSession(stealthy_headers=True)._build_headers({"x-dhole-probe": "1"})
    assert merged["x-dhole-probe"] == "1", merged
    assert merged["referer"] == "https://www.google.com/", merged
    # 模板被摘掉的那一发（`off` 档与回环共用这一位）只剩你点名的那几颗。
    assert HTTPSession(stealthy_headers=False)._build_headers({"x-dhole-probe": "1"}) \
        == {"x-dhole-probe": "1"}


def test_the_browser_tier_is_given_the_named_headers(monkeypatch):
    """浏览器层拿到的是同一份字典（`extra_headers` 本来就是它的参数）。"""
    calls = _stub_tiers(monkeypatch)
    _call("web_read", {"url": URL, "force_fetcher": "stealthy", "cache_ttl": 0,
                       "headers": {"x-dhole-probe": "browser-tier"}})
    assert [c["tier"] for c in calls] == ["stealthy"], calls
    assert calls[0]["extra_headers"] == {"x-dhole-probe": "browser-tier"}, calls[0]


@pytest.mark.live
def test_a_real_browser_sends_the_named_header(site):
    """真浏览器那一发确实带着它（量过一次才敢替这一层说话）。"""
    url, rec = site
    out = _call("web_read", {"url": url, "force_fetcher": "stealthy", "cache_ttl": 0,
                             "ignore_robots": True,
                             "headers": {"x-dhole-probe": "browser-tier",
                                         "accept-language": "nl-NL"}})
    assert out["status"] == 200, out.get("error")
    got = rec.page_headers()
    assert got["x-dhole-probe"] == "browser-tier", got
    assert got["accept-language"] == "nl-NL", got


# ─────────────── 具名拒绝：三族名字，各自那扇门的地址 ───────────────

@pytest.mark.parametrize("name,door", [
    ("cookie", "cred_defaults"),
    ("Cookie", "cred_defaults"),
    (" authorization ", "cred_defaults"),
    ("authorization", "cred_defaults"),
    ("Proxy-Authorization", "cred_defaults"),
    ("user-agent", "TLS"),
    ("If-None-Match", "extraction_type='check'"),
    ("if-modified-since", "extraction_type='check'"),
])
def test_a_name_that_belongs_elsewhere_is_refused_with_its_door(site, name, door):
    """拒绝必须点名是哪一颗头、为什么、去哪 —— 且一发都不许发出去。"""
    url, rec = site
    out = _call("web_read", {"url": url, "ignore_robots": True, "headers": {name: "x"}})
    blob = json.dumps(out, ensure_ascii=False)
    assert "invalid_request" in blob, blob[:300]
    assert name.strip().lower() in blob, f"没点名被拒的那一颗: {blob[:300]}"
    assert door in blob, f"没把那条路的地址说出去（要找 {door!r}）: {blob[:400]}"
    assert rec.received == [], f"被拒的调用发过请求: {rec.paths()}"


def test_the_shape_gate_is_the_one_every_header_path_already_used(site):
    """形状走的是既有那道 `validate_headers`：值必须是字符串、不许换行、不许数到 51。"""
    url, rec = site
    for bad, needle in (({"x-a": 5}, "string"),
                        ({"x-a": "one\r\ntwo"}, "newline"),
                        ({f"k{i}": "v" for i in range(51)}, "50"),
                        ({"connection": "close"}, "internally")):
        out = _call("web_read", {"url": url, "ignore_robots": True, "headers": bad})
        blob = json.dumps(out, ensure_ascii=False)
        assert "headers=" in blob and needle in blob, (bad, blob[:260])
    assert rec.received == [], rec.paths()


def test_a_serialized_object_is_accepted_and_a_list_is_named_by_name(monkeypatch):
    """序列化客户端把整段 JSON 发过来是常态（既有的 dict 参数都这样收）；
    数组不是头字典 —— 那句话要说得出是哪一颗参数。"""
    seen = _tier_seen(monkeypatch, force_fetcher="http", headers='{"x-a": "1"}')
    assert seen["headers"] == {"x-a": "1"}, seen
    out = _call("web_read", {"url": URL, "headers": ["x-a"]})
    blob = json.dumps(out, ensure_ascii=False)
    assert "headers" in blob and "object" in blob, blob[:300]


# ─────────────── 不许静默：落点说得出这次骑在哪一层 ───────────────

def test_the_summary_says_your_keys_win_over_the_template(monkeypatch):
    """`headers=` 不替换 dhole 的模板，只压过它 —— 这句话不许只在源码里。

    措辞说的是**机制**而不是"这一发"：同一条路也走缓存命中，而那一发没有包可发，
    一句"你的头骑在模板之上"就成了一句关于没发生过的事的断言。
    """
    seen = _tier_seen(monkeypatch, force_fetcher="http",
                      headers={"x-a": "1"})
    assert seen["headers"] == {"x-a": "1"}, seen
    out = _call("web_read", {"url": URL, "force_fetcher": "http", "cache_ttl": 0,
                             "headers": {"x-a": "1"}})
    summary = out.get("summary") or ""
    assert re.search(r"wins key by key over dhole.s own browser template", summary), summary
    assert "anti_detect='off'" in summary, "没说出那把真正的开关: " + summary


def test_an_empty_dict_is_not_silently_the_same_as_no_parameter(monkeypatch):
    """`headers={}` 读起来像"这次不发多余的"，实际什么都没改 —— 那就说出来。"""
    _tier_seen(monkeypatch, force_fetcher="http", headers={})
    out = _call("web_read", {"url": URL, "force_fetcher": "http", "cache_ttl": 0,
                             "headers": {}})
    assert "names no header" in (out.get("summary") or ""), out.get("summary")


def test_the_rung_note_lands_where_the_request_actually_ran(monkeypatch):
    """`off` 的说法住在跑过 HTTP 层的那条路上，不住在参数解析那一层。

    反过来的写法会把这句话发到根本没过 HTTP 的调用上（`force_fetcher='stealthy'`），
    而读者会去找一台没发出去的请求。
    """
    calls = _stub_tiers(monkeypatch)
    off = _call("web_read", {"url": URL, "force_fetcher": "http", "cache_ttl": 0,
                             "anti_detect": "off"})
    assert [c["tier"] for c in calls] == ["http"], calls
    assert "no dhole-synthesized headers" in (off.get("summary") or ""), off.get("summary")
    calls.clear()
    browser_only = _call("web_read", {"url": URL, "force_fetcher": "stealthy",
                                      "cache_ttl": 0, "anti_detect": "off"})
    assert [c["tier"] for c in calls] == ["stealthy"], calls
    assert "no dhole-synthesized headers" not in (browser_only.get("summary") or ""), \
        browser_only.get("summary")


# ─────────────── 缓存与批量：一颗会改变答案的参数不该被回放 ───────────────

def test_headers_change_what_comes_back_so_they_change_the_key(site):
    """`accept-language` 换的是**正文**。指纹里没有它，缓存就把别人的答复回放给你。"""
    url, rec = site
    args = {"cache_ttl": 600, "ignore_robots": True}
    _call("web_read", {"url": url + "?ck=1", "headers": {"x-dhole-probe": "one"}, **args})
    rec.received.clear()
    _call("web_read", {"url": url + "?ck=1", "headers": {"x-dhole-probe": "two"}, **args})
    assert rec.page_headers()["x-dhole-probe"] == "two", \
        f"换了点名的头却回放了上一份: {rec.paths()}"
    rec.received.clear()
    _call("web_read", {"url": url + "?ck=1", "headers": {"x-dhole-probe": "two"}, **args})
    assert rec.received == [], (
        f"同一份头、同一个 ttl 却没有命中缓存（{rec.paths()}）—— 这条对照不成立时，"
        "上面那条『不回放』是空的：谁都不命中也叫通过")


def test_a_batch_carries_the_named_headers_to_every_row(monkeypatch):
    """`urls=[...]` 是第二条路。先例：`test_the_rung_survives_the_bulk_fanout`。"""
    rows: list = []

    async def fake(self, url, **kwargs):
        rows.append(kwargs)
        return server_mod.ResponseModel(url=url, status=200, content=["body"],
                                        fetcher_used="http", content_ok=True,
                                        content_type="text/html")

    monkeypatch.setattr(MasterFetchServer, "get", fake)
    _call("web_read", {"urls": ["https://a.example.com/", "https://b.example.com/"],
                       "force_fetcher": "http", "cache_ttl": 0,
                       "headers": {"accept-language": "ja"}})
    assert len(rows) == 2, rows
    assert [r["headers"] for r in rows] == [{"accept-language": "ja"}] * 2, rows


def test_web_request_sends_the_named_headers_on_the_write():
    """`web_request` 从前连 `extra_headers` 都不下传 —— 签名里七颗键没有这一颗。"""
    seen: dict = {}

    async def fake(self, **kwargs):
        seen.update(kwargs)
        return server_mod.ResponseModel(url=kwargs.get("url", ""), status=200,
                                        content=[])

    original = MasterFetchServer.web_read
    MasterFetchServer.web_read = fake
    try:
        _call("web_request", {"url": "https://httpbin.org/post", "method": "POST",
                              "body": {"a": 1}, "confirm": True,
                              "headers": {"x-dhole-probe": "post"}})
    finally:
        MasterFetchServer.web_read = original
    assert seen.get("headers") == {"x-dhole-probe": "post"}, seen


# ─────────────── 边界没有跟着门一起搬：§3-17 那条还在 ───────────────

def test_the_identity_spellings_are_still_refused_as_top_level_names():
    """新门开的是"你说的话"，不是"你是谁"：旧拼写照样以名字被拒。"""
    for key in ("auth", "cookies", "proxy", "extra_headers", "allow_private"):
        with pytest.raises(ValueError) as exc:
            asyncio.run(MasterFetchServer()._dispatch("web_read", {"url": URL, key: "x"}))
        msg = str(exc.value)
        assert "Unsupported argument" in msg and key in msg, msg
        assert key not in server_mod._TOP_LEVEL_ARGS["web_read"]
    assert "headers" in server_mod._TOP_LEVEL_ARGS["web_read"]


def test_a_credential_name_inside_headers_is_not_a_second_door_back():
    """`headers={"authorization": …}` 不是 `auth=` 换个马甲：拒的句子里要有那扇门。"""
    out = _call("web_read", {"url": URL, "headers": {"Authorization": "Bearer t"}})
    blob = json.dumps(out, ensure_ascii=False)
    assert "cred_defaults" in blob, blob[:300]
    assert "host" in blob, f"没说清那扇门是按主机存的: {blob[:300]}"
    assert "invalid_request" in blob, blob[:200]


# ─────────────── wire 面与手册：只读一处也路由得动 ───────────────

def _props(tool):
    td = next(d for d in MasterFetchServer._TOOL_DEFS if d["name"] == tool)
    return td["inputSchema"]["properties"]


def test_both_doors_advertise_the_parameter_and_one_author_names_the_doors():
    """web_read 说全三族与各自的门；web_request 指着那一处作者（同 anti_detect 的
    短指针那条例外：一份阶梯两处够得着，第二份是纯重复的字符税）。"""
    read = _props("web_read")["headers"]["description"]
    assert len(read) <= 296, len(read)
    for needle in ("cookie", "authorization", "proxy-authorization", "user-agent",
                   "if-*", "cred_defaults", "extraction_type='check'",
                   "anti_detect='off'", "win"):
        assert needle in read, (needle, read)
    write = _props("web_request")["headers"]["description"]
    assert len(write) <= 296, len(write)
    assert "web_read.headers" in write, write
    assert "refused" in write and "key by key" in write, write


def test_the_manual_names_the_caps_and_the_measured_asymmetry():
    from dhole_mcp import guide
    _header, behavior = guide.read("behavior")
    for needle in ("`headers=`", "50 names", "8KB", "sec-fetch-site"):
        assert needle in behavior, needle
    assert "Adding is not replacing" in behavior, "没说清这是加，不是换"
    _h, trouble = guide.read("troubleshooting")
    # "not one coherent machine" 是 W1 那句话的形状；W3 把这条限制量掉了一半（手机 UA
    # 配桌面平台那半边），文字改成"曾经是三台机器 + 现在留下的那一位"，针跟着走。
    # 留着的仍然是同一条主张：这套头不是**你这台**浏览器的头。
    for needle in ("sec-purpose", "upgrade-insecure-requests",
                   "referer", "not one coherent machine"):
        assert needle in trouble, needle
