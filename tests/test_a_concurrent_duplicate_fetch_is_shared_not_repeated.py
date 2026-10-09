"""同一发取回，不因为同时来了几个人就发四遍。

2026-10-09 独立审计 D8（本机，冷缓存，真 socket 靶站）：

    4 个并发的 web_read(<同一个 URL>)
      → 源站被打了 **4 次**，回来 4 份回执

缓存是**事后**才写进去的，而 4 个调用在写进去之前都已经各自过了"查缓存"那一步。
缓存对同时到达的调用一点用都没有 —— 它的价值全在"先后"，而并发请求没有先后。

修法的形状值得写下来，因为它是这份文件里大部分用例的由来：**不共享回执对象，
让缓存去传递答案**。同一请求只有一发真去取，其余等它落地，然后**用自己的那套参数
重查一次缓存**。

为什么不是共享那一份回执：回执里一堆东西长在**调用自己的** ContextVar 上 ——
robots 姿态、focus 的筛选结果、参数钳制的注记。借来的对象带的是**别人的**那一份，
而"借来之后再按我的参数重算"等于把整个 finalize 拆成两半。走缓存就没有这个问题：
跟随者拿到的每一份回执都是它自己那套参数经 `_cache_hit_response` 从缓存里还原的。

这条路的代价，也要说清楚：**不可缓存的页面合不了流**（JS 壳、验证墙、错误状态都
不写缓存）。跟随者醒来、缓存仍空、各自去取 —— 也就是合并之前的行为。这不是漏：
"共享的媒介是缓存"这句话本身就意味着没有可共享的东西时就没有共享。
"""

from __future__ import annotations

import asyncio
import http.server
import socket
import threading
import time

import pytest

from dhole_mcp import identity
from dhole_mcp import server as server_mod

#: 真解析器必须在**导入时**抓住：conftest 的 `_offline_dns`（autouse）把
#: `socket.getaddrinfo` 钉成公网地址，夹具里现取拿到的是**已经被钉住的那个**，
#: 于是 127.0.0.1 被判成公网 —— 靶站连不上，用例测的东西整个消失。
_REAL_GETADDRINFO = socket.getaddrinfo

HITS = {"page": 0, "long": 0, "slow": 0, "other": 0}

_PAGE = (b"<html><body><h1>one page</h1><p>"
         b"a reasonably long body so it is not a shell</p></body></html>")
_SLOW = (b"<html><body><h1>slow page</h1><p>"
         b"a reasonably long body so it is not a shell</p></body></html>")

#: 两个主题各占一半的段落，好让 focus 的取舍**看得见**（见
#: `test_a_follower_filters_by_its_own_focus`）。焦点词挑的是只在一半段落里出现的
#: 词，否则 BM25 打分两边一样，断言就退化成"长度差不多"。
_PARAS = "".join(
    (f"<p>alpha 甲主题第{i}段：关于苹果与橙子的详细说明，这一段只谈甲。</p>"
     if i % 2 == 0 else
     f"<p>beta 乙主题第{i}段：关于桥梁与隧道的详细说明，这一段只谈乙。</p>")
    for i in range(30))
_LONG = f"<html><body><h1>两个主题</h1>{_PARAS}</body></html>".encode()


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/robots.txt":
            body, ctype = b"User-agent: *\n", "text/plain"
        elif path == "/page":
            HITS["page"] += 1
            body, ctype = _PAGE, "text/html; charset=utf-8"
        elif path == "/long":
            HITS["long"] += 1
            body, ctype = _LONG, "text/html; charset=utf-8"
        elif path == "/slow":
            HITS["slow"] += 1
            time.sleep(1.2)
            body, ctype = _SLOW, "text/html; charset=utf-8"
        elif path == "/other":
            HITS["other"] += 1
            body, ctype = _PAGE, "text/html; charset=utf-8"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def site(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _REAL_GETADDRINFO)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def server(site):
    identity.set_value("allow_private", True)
    for k in HITS:
        HITS[k] = 0
    return server_mod.MasterFetchServer()


def _burst(srv, path, n=4, **kw):
    """n 个**同时**发出的同一请求。"""
    async def main():
        return await asyncio.gather(*[srv.web_read(path, **kw) for _ in range(n)])
    return asyncio.run(main())


# ─── 1 · D8 的原形状 ─────────────────────────────────────────────

def test_four_identical_cold_reads_hit_the_origin_once(server, site):
    """判据就是那个数：源站被打了几次。"""
    rs = _burst(server, f"{site}/page")
    assert HITS["page"] == 1, (
        f"源站被打了 {HITS['page']} 次 —— 合流没生效（合并前实测就是 4 次）")
    assert [r.status for r in rs] == [200] * 4


def test_every_caller_still_gets_a_usable_answer(server, site):
    """省下的必须是重复的**取回**，不是任何一个人的答案。"""
    rs = _burst(server, f"{site}/page")
    bodies = ["\n".join(r.content) for r in rs]
    assert all("one page" in b for b in bodies), bodies
    assert all(r.content_ok for r in rs), [r.content_ok for r in rs]
    assert len(set(bodies)) == 1, "同一个请求的四个答案不该各不相同"
    assert all(r.total_extracted_chars for r in rs)


def test_the_followers_say_where_their_answer_came_from(server, site):
    """冷缓存上的 `cached=True` 读起来像个 bug，所以那句话必须自己说清。

    这也顺带钉住 `duration_ms`：跟随者确实等了墙钟时间，报 0 就是账不对。
    """
    rs = _burst(server, f"{site}/page")
    leader = [r for r in rs if r.fetcher_used != "cache"]
    followers = [r for r in rs if r.fetcher_used == "cache"]
    assert len(leader) == 1 and len(followers) == 3
    assert leader[0].duration_ms > 0
    for f in followers:
        assert f.cached is True
        assert "concurrent call for this same request" in f.summary, f.summary
        assert "no request was sent for this call" in f.summary, f.summary
        assert f.duration_ms > 0, (
            "等了却报 duration_ms=0 —— 一份等了半秒的回执不该说这次调用没花时间")


def test_a_follower_filters_by_its_own_focus(server, site):
    """合流之后最容易坏掉的一条：跟随者拿到**领头那一发筛过的**正文。

    两个调用同 URL、同缓存键、不同 focus。它们会合流（缓存键相同），而各自必须按
    **自己的**焦点词筛 —— 走缓存那条路天然做到这一点，这条用例把它钉住。
    """
    async def main():
        return await asyncio.gather(
            server.web_read(f"{site}/long", focus="苹果"),
            server.web_read(f"{site}/long", focus="桥梁"),
        )
    rs = asyncio.run(main())
    assert HITS["long"] == 1, f"合流没生效：{HITS['long']} 次源站"
    by_focus = {}
    for r in rs:
        body = "\n".join(r.content)
        by_focus[("甲主题" in body and body.count("甲主题") > body.count("乙主题"))] = body
    focused_on_jia = [r for r in rs if "苹果" in "".join(r.content)][0]
    assert focused_on_jia.fetcher_used == "http", "第一个是领头"
    other = [r for r in rs if r is not focused_on_jia][0]
    assert other.fetcher_used == "cache"
    jia = "\n".join(focused_on_jia.content)
    yi = "\n".join(other.content)
    assert jia.count("甲主题") > jia.count("乙主题"), jia[:200]
    assert yi.count("乙主题") > yi.count("甲主题"), yi[:200]
    assert by_focus, "两个方向都要看得见取舍"


# ─── 2 · 什么时候**不**合流 ──────────────────────────────────────

def test_cache_ttl_zero_is_never_merged(server, site):
    """cache_ttl=0 是"这次不读也不写缓存"：没有可共享的媒介，也就没有合流。"""
    rs = _burst(server, f"{site}/page", cache_ttl=0)
    assert HITS["page"] == 4, HITS["page"]
    assert {r.fetcher_used for r in rs} == {"http"}


def test_a_different_url_is_a_different_request(server, site):
    async def main():
        return await asyncio.gather(
            server.web_read(f"{site}/page"),
            server.web_read(f"{site}/other"),
        )
    rs = asyncio.run(main())
    assert HITS["page"] == 1 and HITS["other"] == 1, HITS
    assert [r.status for r in rs] == [200, 200]


def test_a_leader_that_times_out_does_not_hold_the_follower(server, site):
    """领头那一发没落地（超时，于是什么都没写进缓存），跟随者必须被放走去自己取。

    这是"等待有上限"那条纪律的判据：上限就是跟随者自己那份取回预算，等到了就自己
    去取 —— 也就是合并之前的行为。而且它拿到的预算**已经扣掉了等待的那一段**，
    所以整次调用仍限在自己的预算里，不是"等一段再跑一整份"。
    """
    async def main():
        return await asyncio.gather(
            # 领头：1.2s 的页面配 1s 预算 —— 注定超时，也就注定不写缓存。
            server.web_read(f"{site}/slow", timeout=1000),
            # 跟随者：预算够（4s），扣掉 ~1s 的等待还剩 3s，够取完这页。
            server.web_read(f"{site}/slow", timeout=4000),
        )
    rs = asyncio.run(main())
    assert HITS["slow"] == 2, (
        f"源站被打了 {HITS['slow']} 次：跟随者要么没等到上限，要么根本没去取")
    assert rs[0].status == 0, f"领头那一发本来就该超时，实际 {rs[0].status}"
    assert rs[1].status == 200, (
        f"跟随者被领头那一发拖死了：status={rs[1].status} error={rs[1].error!r}")
    assert "slow page" in "\n".join(rs[1].content)


# ─── 3 · 收尾：放人、清场、不跨循环 ──────────────────────────────

def test_a_leader_that_dies_does_not_take_the_followers_with_it(server, site,
                                                                monkeypatch):
    """领头那一发炸了，等它的人必须被放走，然后自己去取。"""
    real = server_mod.MasterFetchServer._auto_escalate
    calls = {"n": 0}

    async def die_once(self, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("the leader exploded")
        return await real(self, *a, **k)

    monkeypatch.setattr(server_mod.MasterFetchServer, "_auto_escalate", die_once)

    async def main():
        return await asyncio.gather(
            *[server.web_read(f"{site}/page") for _ in range(3)],
            return_exceptions=True)
    rs = asyncio.run(main())
    errors = [r for r in rs if isinstance(r, BaseException)]
    oks = [r for r in rs if not isinstance(r, BaseException)]
    assert len(errors) == 1, "只有领头那一发该炸"
    assert len(oks) == 2 and all(r.status == 200 for r in oks), oks
    assert HITS["page"] == 2, f"跟随者没能自己去取：{HITS['page']}"
    assert server_mod._merge_registry() == {}, "登记处没清干净"


def test_the_registry_is_empty_after_a_burst(server, site):
    _burst(server, f"{site}/page", n=3)
    assert server_mod._merge_registry() == {}


def test_the_registry_does_not_survive_its_event_loop(server, site):
    """`asyncio.Event` 绑在创建它的循环上。

    模块级只放一本登记处的话，上一轮 `asyncio.run()` 留下的 Event 会被下一轮
    await —— 而本仓库的套件在一个进程里跑几千次 `asyncio.run()`。这条用例连跑两轮，
    第二轮必须拿到一本**新的**。
    """
    _burst(server, f"{site}/page", n=2)
    first_loop_registry = server_mod._MERGE_INFLIGHT_LOOP
    _burst(server, f"{site}/page", n=2)
    assert server_mod._MERGE_INFLIGHT_LOOP is not first_loop_registry, (
        "登记处跟着旧的（已关闭的）事件循环留了下来")


def test_a_second_burst_still_reuses_the_cache(server, site):
    """第二轮不该再打源站：缓存里已经有了。这条守的是"合流没把缓存写坏"。"""
    _burst(server, f"{site}/page", n=4)
    assert HITS["page"] == 1
    rs = _burst(server, f"{site}/page", n=4)
    assert HITS["page"] == 1, HITS["page"]
    assert all(r.fetcher_used == "cache" for r in rs), [r.fetcher_used for r in rs]


# ─── 4 · 合流键（单元）──────────────────────────────────────────

class TestTheMergeKey:
    """合流键刻意就是缓存键：本仓库对"同一个请求"的既有答案。

    正确性不靠它（跟随者会用自己的参数重查缓存），所以这里钉的是"它没另发明一套
    判据"，而不是"它覆盖了每一个塑形参数"—— 后者是个证明不完的命题。
    """

    def test_it_is_the_cache_key(self):
        from dhole_mcp.cache import _cache_key
        got = server_mod._merge_key("https://x/a", "markdown", None, None, "")
        assert got == _cache_key("https://x/a", "markdown", None, None,
                                 "live", "fetch", "")

    def test_the_shape_bits_change_it(self):
        base = server_mod._merge_key("https://x/a", "markdown", None, None, "")
        assert server_mod._merge_key("https://x/b", "markdown", None, None, "") != base
        assert server_mod._merge_key("https://x/a", "text", None, None, "") != base
        assert server_mod._merge_key("https://x/a", "markdown", "#id", None, "") != base
        assert server_mod._merge_key("https://x/a", "markdown", None, "1-2", "") != base
        assert server_mod._merge_key("https://x/a", "markdown", None, None, "ck=1") != base

    def test_the_credentials_context_changes_it(self):
        """带凭据取回的那一份，不许被匿名请求合流到一起。"""
        from dhole_mcp.context import _cache_context
        anon = _cache_context({})
        with_auth = _cache_context({"auth": {"type": "bearer", "token": "t"}})
        assert anon != with_auth
        assert server_mod._merge_key("https://x/a", "markdown", None, None, anon) != \
            server_mod._merge_key("https://x/a", "markdown", None, None, with_auth)
