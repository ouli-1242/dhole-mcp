"""两轮扩展报告的回归：BUG-1 mojibake、BUG-5 since 掉包、N2 引擎未观测、
问题-3 缓存丢判定、问题-4 非英文的墙、N1 click 不等导航、N4 验证器被无视。

每条的根因都不在报告指的地方，而且多数是同一类：**两条路径长得不一样，而只有一条被测过**。
- BUG-1 / 问题-3 在解码与缓存这两条"另一半没测到"的链上；
- BUG-5 在 dispatcher 这道缝上（单元测试直调门方法，看不见参数掉包）；
- N1 / N2 / N4 是把已经存在的信号（引擎产出计数、条件请求、click 的后果）在有界时间内
  如实报出去。
"""


from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from dhole_mcp import feed as feed_mod
from dhole_mcp.envelope import detect_page_type
from dhole_mcp.fetcher import Response
from dhole_mcp.server import (
    MasterFetchServer, ResponseModel, _detect_content_issue, _is_js_shell,
)
from dhole_mcp.structured import extract_structured

# APOSTROPHE = U+2019; its UTF-8 bytes read as windows-1252 are "â€™", as latin-1 "â"+控制符.
TYPED = "Saturn\u2019s icy moons \u2013 report"
MOJIBAKE_MARKS = ("\xe2", "\xc3", "\xc2", "\ufffd")


def _page(meta: str | None) -> bytes:
    """One document, utf-8 bytes, whose <meta charset> says whatever `meta` says.

    `None` is the common real-world shape: HN ships no meta charset at all and
    relies on the HTTP header, which is exactly where libxml2 falls back to the
    host locale (the tester's console then eats the two control bytes, which is
    why the report shows a bare "â").
    """
    tag = f"<meta charset='{meta}'>" if meta else ""
    return ("<html><head>" + tag + "<title>t</title></head><body>"
            f"<div class='story'><a class='title'>{TYPED}</a></div>"
            "</body></html>").encode("utf-8")


SCHEMA = {"properties": {"stories": {
    "type": "array", "selector": ".story",
    "properties": {"title": {"selector": ".title"}}}}}


class TestTheSchemaPathDecodesExactlyOnce:
    """BUG-1 本体：解码之后不许再有第二个字符集裁判。"""

    @pytest.mark.parametrize("meta", [None, "utf-8", "iso-8859-1", "windows-1252"])
    def test_a_meta_charset_cannot_remangle_bytes_we_already_decoded(self, meta):
        out = extract_structured(_page(meta).decode("utf-8"), SCHEMA)

        assert out["stories"][0]["title"] == TYPED
        assert not any(m in json.dumps(out, ensure_ascii=False) for m in MOJIBAKE_MARKS), \
            f"meta charset={meta} reintroduced the report's mojibake"

    @pytest.mark.parametrize("meta", [None, "iso-8859-1", "windows-1252"])
    def test_the_response_css_path_uses_the_header_charset_too(self, meta):
        """`page.css()` 是同一个洞的另一半：它曾经把**原始字节**交给 libxml2。"""
        page = Response(url="https://example.test/", body=_page(meta), status=200,
                             headers={}, encoding="utf-8")

        assert page.css(".title")[0].text_content() == TYPED

    def test_a_lying_header_is_still_overridden_by_the_bytes(self):
        """修 mojibake 不能把 16.0 那头的修好方向弄丢：header 说谎时字节要赢。"""
        page = Response(url="https://example.test/", body=_page(None), status=200,
                             headers={}, encoding="iso-8859-1")

        assert page.content.count("\u2019") == 1
        assert page.css(".title")[0].text_content() == TYPED

    def test_a_true_latin1_body_is_not_forced_through_utf8(self):
        latin = ("<html><body><div class='story'><a class='title'>café "
                 "naïve</a></div></body></html>").encode("iso-8859-1")
        page = Response(url="https://example.test/", body=latin, status=200,
                             headers={}, encoding="iso-8859-1")

        assert page.css(".title")[0].text_content() == "café naïve"


def _dispatch(tool: str, args: dict):
    return asyncio.run(MasterFetchServer()._dispatch(tool, args))


def _record(monkeypatch, method: str, result=None) -> dict:
    """Stub a tool method; return the kwargs the dispatcher actually passed it.

    Bound against the real signature, so a value handed to the wrong parameter
    shows up under the wrong name instead of being filed away under the right one.
    """
    import inspect
    sig = inspect.signature(getattr(MasterFetchServer, method))
    seen: dict = {}

    async def fake(self, **kwargs):
        bound = sig.bind(self, **kwargs)
        seen.update(bound.arguments)
        seen.pop("self")
        return result

    monkeypatch.setattr(MasterFetchServer, method, fake)
    return seen


def _shape_for(tool: str):
    """Whatever each dispatch branch expects back, so the stub is not the failure."""
    if tool == "feed_fetch":
        return []
    if tool == "resolve_url":
        return {}
    return ResponseModel(url="https://example.test/", status=200, content=["x"])


class TestTheWireReadsEveryArgumentItAdvertises:
    """BUG-5 本体，外加一条不再靠人记得的守卫。"""

    def test_since_reaches_the_method(self, monkeypatch):
        seen = _record(monkeypatch, "feed_fetch")
        _dispatch("feed_fetch", {"urls": ["https://example.test/feed.xml"],
                                 "since": "2026-09-27T11:00:00Z"})

        assert seen.get("since") == "2026-09-27T11:00:00Z"

    @pytest.mark.parametrize("tool,call", [
        ("feed_fetch", {"urls": ["https://example.test/feed.xml"], "max_items": 3,
                        "timeout": 7, "since": "2026-09-27T11:00:00Z"}),
        ("resolve_url", {"url": "https://example.test/x", "timeout": 7}),
        ("parse", {"file_path": "x.csv", "cwd": ".", "encoding": "gbk"}),
        ("cache_clear", {"all": True, "engine_state": True}),
    ])
    def test_every_advertised_property_is_forwarded(self, monkeypatch, tool, call):
        """schema 声明即承诺：写了却没读 = 对调用方说谎。

        报告的复现是「结果与不传 since 完全一致」。只有把每个声明的参数都送一遍、再核对
        门方法收到了什么，这类掉包才会红 —— 单测 `_since_cutoff` 或直调 `self.feed_fetch`
        都看不见它。带 options 袋的四个工具由 test_options_are_canonical_g22 同样地钉住。
        """
        defs = {d["name"]: d for d in MasterFetchServer._enabled_tool_defs()}
        props = set(defs[tool]["inputSchema"]["properties"])

        seen = _record(monkeypatch, tool, result=_shape_for(tool))
        _dispatch(tool, call)

        unread = sorted(props - set(seen))
        assert not unread, f"{tool} advertises {unread} but the dispatcher drops them"


class TestSinceActuallyFilters:
    """门方法往下的那半段也要从 wire 走一遍，不然又只剩单元测试。"""

    FEED = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel><title>t</title>
  <item><title>new</title><link>https://example.test/n</link>
        <pubDate>Sun, 27 Sep 2026 12:30:00 +0000</pubDate></item>
  <item><title>old-one</title><link>https://example.test/o1</link>
        <pubDate>Sun, 27 Sep 2026 10:26:00 +0000</pubDate></item>
  <item><title>old-two</title><link>https://example.test/o2</link>
        <pubDate>Sat, 26 Sep 2026 21:00:00 +0000</pubDate></item>
  <item><title>undated</title><link>https://example.test/u</link></item>
</channel></rss>""".encode("utf-8")

    @pytest.fixture
    def served(self, monkeypatch):
        async def fake_grab(url, timeout, headers=None):  # noqa: ARG001
            # (body, status, encoding, error, response headers) - the headers are
            # what P2-10 asked for: without them a poll re-downloads the feed.
            return self.FEED, 200, "utf-8", "", {"etag": '"feed-etag-1"'}

        monkeypatch.setattr(feed_mod, "_grab", fake_grab)
        return fake_grab

    def test_entries_before_the_cutoff_are_dropped_and_counted(self, served):
        _content, structured = _dispatch("feed_fetch", {
            "urls": ["https://example.test/feed.xml"], "since": "2026-09-27T11:00:00Z"})

        feed = structured["feeds"][0]
        assert [i["title"] for i in feed["items"]] == ["new", "undated"]
        assert feed["items_older_than_since"] == 2
        assert feed["items_without_date"] == 1
        assert feed["since"] == "2026-09-27T11:00:00+00:00"

    def test_without_since_the_same_feed_comes_back_whole(self, served):
        """对照：有 since 与没有 since 必须**不一样**，那才是过滤器在跑的证据。"""
        _content, structured = _dispatch("feed_fetch", {
            "urls": ["https://example.test/feed.xml"]})

        assert len(structured["feeds"][0]["items"]) == 4
        assert structured["feeds"][0]["items_older_than_since"] == 0


class _ServedPdf:
    """A loopback server that answers one URL with a real text PDF.

    Real extraction, real sqlite cache, real `_finalize_result` — 问题-3 的两半
    （写入带不带 content_ok、命中读不读它）都在这条链上，桩位打在任何一端都测不到它。
    """

    def __enter__(self):
        import http.server
        import socketserver
        import threading

        pdf = (Path(__file__).parent / "background_checks.pdf").read_bytes()

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Length", str(len(pdf)))
                self.end_headers()
                self.wfile.write(pdf)

            def log_message(self, *args):
                pass

        self.srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.srv.server_address[1]}/paper.pdf"

    def __exit__(self, *exc):
        self.srv.shutdown()
        self.srv.server_close()
        return False


def _fetch(url: str, **options) -> dict:
    """One smart_fetch through the real dispatcher (loopback hosts need the opt-in)."""
    return asyncio.run(MasterFetchServer()._dispatch(
        "smart_fetch",
        {"url": url, "allow_private": True, "timeout": 60000, **options}))[1]


class TestACachedPdfKeepsTheExtractorsVerdict:
    """问题-3：缓存命中的那份 PDF 报 content_ok=false，而 content 是非空的。

    `_agent_hints` 在 quality_score>0 时把判断权交给抽取器（乱码 PDF 必须留 false，
    那是 P3 的修法），于是它读的 `result.content_ok` 必须随行。缓存写入存了
    quality_score 却没存 content_ok，命中路径就把它落在默认值 False 上 —— 同一份
    内容，第一次抓是 true、第二次抓是 false，而且第二次看着更像失败。
    """

    def test_a_real_pdf_round_trips_one_verdict(self):
        pytest.importorskip("pdfplumber")
        with _ServedPdf() as url:
            first, second = _fetch(url), _fetch(url)

        assert first["error"] == "", f"抓取本身失败了：{first['error'][:120]}"
        assert (first.get("cached", False), second["cached"]) == (False, True), \
            f"第二次没走缓存：{second['error'][:80]}"
        assert first["quality_score"] > 0, "这条用例要靠 quality_score>0 走抽取器判定分支"
        assert first["content_ok"] is True
        assert second["content_ok"] == first["content_ok"], (
            "同一份内容两次调用给了两个 content_ok —— 报告说的「易误导调用方判失败」")
        assert any(c.strip() for c in second["content"]), "content 非空却 content_ok=false"

    def test_a_stored_garbled_verdict_is_not_promoted(self):
        """反向也要钉住：恢复 content_ok 不能把乱码 PDF 洗成 true（那是 P3 当年的错）。"""
        import asyncio as aio

        from dhole_mcp.cache import set_cached

        async def seed(url):
            await set_cached(url, "markdown", ["[cid garbage] " * 60], 200,
                             content_type="application/pdf", ttl=3600,
                             envelope={"quality_score": 0.4, "content_ok": False,
                                       "page_type": "pdf"})

        with _ServedPdf() as url:
            aio.run(seed(url))
            hit = _fetch(url)

        assert hit["cached"] is True
        assert hit["quality_score"] == 0.4
        assert hit["content_ok"] is False


class _VirtualClock:
    """`time` as actions.py sees it, with waits that move the clock instead of sleeping."""

    def __init__(self):
        self.t = 0.0

    def monotonic(self) -> float:
        return self.t


class _FakePage:
    """The slice of the playwright page that a click touches.

    `snapshots` is what the page reports after each poll; the list is consumed in
    order, and a None entry stands for "no execution context" (a document being
    replaced). `loads` records the wait-for-navigation calls, which is the thing
    the report says never happened.
    """

    def __init__(self, snapshots, start_href="https://news.ycombinator.test/",
                 clock=None, click_exc=None):
        self._snapshots = list(snapshots)
        self.href = start_href
        self.polls = 0
        self.loads: list[tuple] = []
        self.clicked = False
        self.click_exc = click_exc
        self.clock = clock or _VirtualClock()

    async def evaluate(self, script):
        self.polls += 1
        if not self._snapshots:
            return [self.href, 10, 100]
        snap = self._snapshots.pop(0)
        if snap is None:
            raise RuntimeError("Execution context was destroyed")
        href, nodes, chars = snap
        self.href = href
        return [href, nodes, chars]

    async def wait_for_timeout(self, ms: int) -> None:
        self.clock.t += ms / 1000.0

    async def wait_for_load_state(self, state="load", timeout=None):
        self.loads.append((state, timeout))

    def locator(self, selector):
        page = self

        class _First:
            async def click(self, timeout=None):
                if page.click_exc is not None:
                    raise page.click_exc
                page.clicked = True

        class _Loc:
            first = _First()

        return _Loc()


def _run_click(page, monkeypatch, actions_mod):
    async def clock_monotonic():
        return page.clock.t

    monkeypatch.setattr(actions_mod, "time", page.clock)
    action = actions_mod.build_page_action([{"click": "a.more-link"}])
    asyncio.run(action(page))
    return page


class TestAClickWaitsForWhatItCaused:
    """N1：click 派发完就返回，抽取发生在导航完成之前，拿到的是原页面。"""

    def test_a_navigation_after_the_click_is_waited_for(self, monkeypatch):
        from dhole_mcp import actions as actions_mod

        base = "https://news.ycombinator.test/"
        page = _FakePage([
            (base, 100, 1000),                      # before the click
            (base, 100, 1000),                      # nothing yet
            ("https://news.ycombinator.test/?p=2", 140, 1800),   # the new page lands
        ])
        _run_click(page, monkeypatch, actions_mod)

        assert page.clicked
        assert page.loads, "点击造成了导航却没有等文档就绪 —— 抽取会停在原页面"
        assert page.loads[0][0] == "domcontentloaded"

    def test_a_detached_context_counts_as_a_navigation(self, monkeypatch):
        """playwright 换文档时 evaluate 直接抛 —— 那正是该等的时候，不是失败。"""
        from dhole_mcp import actions as actions_mod

        base = "https://news.ycombinator.test/"
        page = _FakePage([(base, 100, 1000), None])
        _run_click(page, monkeypatch, actions_mod)

        assert page.loads and page.loads[0][0] == "domcontentloaded"

    def test_a_page_that_grew_in_place_is_not_a_navigation(self, monkeypatch):
        """展开器（本页变长，不换 URL）：等它停下来就行，不该去等新文档。"""
        from dhole_mcp import actions as actions_mod

        base = "https://news.ycombinator.test/"
        page = _FakePage([
            (base, 100, 1000), (base, 100, 1000),
            (base, 120, 1600), (base, 120, 1600), (base, 120, 1600),
        ])
        _run_click(page, monkeypatch, actions_mod)

        assert page.loads == []
        assert page.polls >= 4, "本页变长的形状没被等到"

    def test_a_click_nothing_answers_stays_fast(self, monkeypatch):
        """封顶的另一半：4s 的额度不能变成每个空点击的固定开销。"""
        from dhole_mcp import actions as actions_mod

        base = "https://news.ycombinator.test/"
        page = _FakePage([(base, 100, 1000)] * 40)
        _run_click(page, monkeypatch, actions_mod)

        assert page.polls <= 6, f"空点击花了 {page.polls} 轮轮询"
        assert page.clock.t * 1000 <= actions_mod.CLICK_SETTLE_MIN_MS + 600


class _ServedValidator:
    """A loopback origin with one path that honours If-None-Match and one that doesn't.

    arxiv answers every conditional request on a PDF with 200 + the whole file
    (report N4). Without a second path in the same server, a test of "the flag is
    absent on a real 304" would be asserting nothing about the 304 branch.
    """

    def __enter__(self):
        import http.server
        import socketserver
        import threading

        body = (b"<html><body><article><h1>Doc</h1><p>"
                + b"loopback validator probe body text. " * 30
                + b"</p></article></body></html>")

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path.startswith("/honoured") and self.headers.get("if-none-match"):
                    self.send_response(304)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("ETag", '"probe-etag"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        return f"{base}/ignored", f"{base}/honoured"

    def __exit__(self, *exc):
        self.srv.shutdown()
        self.srv.server_close()
        return False


class TestAValidatorTheOriginIgnoresIsNamed:
    """N4：验证器被原样无视时，响应不能读成「内容变了」。"""

    def test_a_200_answered_to_a_conditional_says_so(self):
        with _ServedValidator() as (ignored_url, honoured_url):
            plain = _fetch(ignored_url)
            ignored = _fetch(ignored_url, if_none_match="stale-etag")
            honoured = _fetch(honoured_url, if_none_match="stale-etag")

        assert plain["metadata"].get("validators_ignored") is None, \
            "没发验证器的普通抓取不该出现这个标记"
        note = ignored["metadata"].get("validators_ignored")
        assert note, "200 + 全文就是对 If-None-Match 的无视，这里必须说明"
        assert "If-None-Match" in note
        assert not ignored.get("not_modified", False)
        assert honoured["not_modified"] is True
        assert honoured["metadata"].get("validators_ignored") is None, \
            "真 304 是验证器起了作用，不该标成被无视"


# 实测抓回来的文本（本机 2026-09-27，问题-4）：
#   GET https://www.google.com/search?q=quantum+entanglement
#   -> 200, http 层, 1053 字符, content_ok=True, page_type=unknown
GOOGLE_ZH_JS_WALL = (
    "启用 JavaScript 才能使用搜索功能\n\n"
    "# 开启 JavaScript 才能继续搜索\n\n"
    "您使用的浏览器已关闭 JavaScript。若要继续搜索，请开启 JavaScript。\n\n"
    "了解如何在 [Google Chrome](https://support.google.com/chrome/answer/114662) "
    "中进行操作\n\n"
    "1. 点击 ，然后点击**设置**。\n2. 点击**隐私和安全**。\n3. 点击**网站设置**。"
    "\n4. 点击 **JavaScript**。\n5. 选择**网站**。\n")


class TestAWallInAnotherLanguageIsStillAWall:
    """问题-4：墙按访客的出口语言出文案，而信号表整张是英文。"""

    def _result(self, text: str, **kw):
        return ResponseModel(url="https://www.google.com/search?q=x", status=200,
                             content=[text], fetcher_used="http", **kw)

    def test_the_localized_google_wall_is_not_content(self):
        assert _is_js_shell(self._result(GOOGLE_ZH_JS_WALL))
        assert _detect_content_issue(self._result(GOOGLE_ZH_JS_WALL)).startswith(
            "js_shell_detected")

    def test_a_chinese_tutorial_about_javascript_is_not_a_wall(self):
        """长度闸门：正文里出现「开启 JavaScript」的长文不是墙（与 bot-wall 同一道理）。"""
        body = ("开启 JavaScript 后，浏览器会执行页面里的脚本。" * 60)

        assert len(body) > 1500
        assert not _is_js_shell(self._result(body))


class TestTheResponseSaysHowRobotsWasDecided:
    """两轮报告从两边要同一件事：调用方/审计无法从响应区分「站点允许」与「全局绕过」。

    本机默认就是 DHOLE_IGNORE_ROBOTS=1（conftest 为了不让测试打 /robots.txt 而设），
    所以这条链在这里正好是它要暴露的那一格。
    """

    def test_the_three_ways_to_get_permission_are_distinguishable(self, monkeypatch):
        with _ServedValidator() as (ignored_url, _honoured):
            url = ignored_url.split("?")[0]
            monkeypatch.setenv("DHOLE_IGNORE_ROBOTS", "1")
            by_env = _fetch(url, options={"include_links": False})
            monkeypatch.delenv("DHOLE_IGNORE_ROBOTS")
            by_param = _fetch(url, options={"ignore_robots": True,
                                            "cache_ttl": 0, "include_links": False})
            complying = _fetch(url + "#", options={"cache_ttl": 0, "include_links": False})

        assert "DHOLE_IGNORE_ROBOTS" in by_env["metadata"]["robots"], \
            f"进程级绕过没有如实写出：{by_env['metadata']}"
        assert "ignore_robots=true" in by_param["metadata"]["robots"]
        assert by_env["metadata"]["robots"] != by_param["metadata"]["robots"]
        assert complying["metadata"]["robots"] == "complied"


class TestAnActionThatNeverRanSaysSo:
    """验证报告把 N1 记成「仍未修复」的那一次，真相是动作根本没跑。

    `click a.more-link` 打的是 HN 不存在的 class（它是 `morelink`）：playwright 10s
    超时，异常被 page_action 的 except 吃掉，只留一条 logger.warning —— 于是响应是
    「第 1 页的正常内容 + 一切成功」，读起来就是「click 不等导航」。计时那一半上一轮
    已经修了；这一半（动作的收执）才是让下一次报告不再指错地方的东西。
    """

    def _run(self, monkeypatch, click_exc=None, selector="a.more-link"):
        """One click action, run against the fake page through the real builder."""
        from dhole_mcp import actions as actions_mod

        base = "https://news.ycombinator.test/"
        page = _FakePage([(base, 100, 1000)], click_exc=click_exc)
        runner = actions_mod.build_page_action([{"click": selector}])
        monkeypatch.setattr(actions_mod, "time", page.clock)
        asyncio.run(runner(page))
        return runner, page

    def test_a_timed_out_click_is_reported_with_its_selector(self, monkeypatch):
        runner, _page = self._run(
            monkeypatch,
            RuntimeError("Timeout 10000ms exceeded.\nCall log: waiting for locator('a.more-link')"))

        assert runner.outcomes == [{
            "action": "click 'a.more-link'", "ok": False,
            "error": runner.outcomes[0]["error"]}]
        assert "matched no element" in runner.outcomes[0]["error"], \
            "超时必须分出「选择器没命中」这一格，否则下一步仍是猜"

    def test_a_successful_action_reports_no_failure(self, monkeypatch):
        runner, page = self._run(monkeypatch, selector="a.morelink")

        assert page.clicked
        assert runner.outcomes == [{"action": "click 'a.morelink'", "ok": True}]

    def test_the_receipt_reaches_the_response(self):
        from dhole_mcp.server import _report_action_outcomes

        result = ResponseModel(url="https://x.test/", status=200,
                               content=["page one content" * 20])
        result.content_ok = True

        class _Runner:
            outcomes = [{"action": "click 'a.more-link'", "ok": True},
                        {"action": "scroll x5", "ok": False, "error": "detached"}]

        _report_action_outcomes(result, _Runner())

        assert len(result.metadata["actions"]) == 2
        assert "1 of 2 actions did not run" in result.summary
        assert "scroll x5" in result.summary
        assert "never happened" in result.next_action

    def test_an_all_green_sequence_adds_no_complaint(self):
        from dhole_mcp.server import _report_action_outcomes

        result = ResponseModel(url="https://x.test/", status=200,
                               content=["page one content" * 20])

        class _Runner:
            outcomes = [{"action": "click 'a.morelink'", "ok": True}]

        _report_action_outcomes(result, _Runner())

        assert result.metadata["actions"] == _Runner.outcomes
        assert result.next_action == ""


def _fake_page(content_type: str, body: bytes, url: str = "https://example.test/x"):
    class Page:
        status = 200
        encoding = "utf-8"
    page = Page()
    page.body = body
    page.url = url
    page.headers = {"content-type": content_type}
    return page


SITEMAP = (b'<?xml version="1.0" encoding="UTF-8"?>'
           b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           b'<url><loc>https://docusaurus.dev/lander</loc>'
           b'<lastmod>2026-09-27</lastmod></url></urlset>')


class TestADataXmlDocumentKeepsItsTags:
    """P2-7：sitemap / feed / XML API 的数据就在标签结构里，压平即丢失。"""

    @pytest.mark.parametrize("ct", ["application/xml", "text/xml",
                                    "application/rss+xml", "application/sitemap+xml",
                                    "application/soap+xml"])
    def test_the_elements_survive(self, ct):
        from dhole_mcp.server import _translate_response

        text = "".join(_translate_response(_fake_page(ct, SITEMAP), "markdown", None,
                                           False, False, "http", 1).content)
        assert "<urlset" in text and "<loc>" in text and "2026-09-27" in text, text[:160]

    def test_xhtml_is_still_an_html_document(self):
        """application/xhtml+xml 是 HTML 穿了 XML 的媒体类型，不能原样吐出去。"""
        from dhole_mcp.server import _is_xml_content_type

        assert not _is_xml_content_type("application/xhtml+xml")
        assert not _is_xml_content_type("image/svg+xml")
        assert not _is_xml_content_type("text/html; charset=utf-8")
        assert _is_xml_content_type("application/rss+xml; charset=utf-8")

    def test_the_structure_verdict_names_it(self):
        from dhole_mcp.server import _apply_envelope

        assert detect_page_type("", "https://a.test/sitemap.xml", "application/xml") == "xml"
        assert detect_page_type("", "https://a.test/f", "application/rss+xml") == "xml"
        assert detect_page_type("<html><body>x</body></html>", "https://a.test/f",
                               "application/xhtml+xml") != "xml"

        # The response's own verdict comes from _apply_envelope's content_type
        # override, not from the structural fallback above — patching only one of
        # the two leaves page_type=unknown next to a body full of tags.
        result = ResponseModel(url="https://a.test/sitemap.xml", status=200,
                               content=["<urlset/>"], content_type="application/xml")
        _apply_envelope(result)
        assert result.page_type == "xml"


class TestAPollCanAskAnythingNewWithoutDownloading:
    """P2-10：轮询者拿不到 feed 自己的 ETag，就只能整份下载后自己比。"""

    FEED = (b'<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>'
            b'<item><title>new</title><link>https://example.test/n</link>'
            b'<pubDate>Sun, 27 Sep 2026 12:30:00 +0000</pubDate></item>'
            b'</channel></rss>')

    def _stub(self, monkeypatch, hdrs, status=200, body=None):
        from dhole_mcp import feed as feed_mod

        sent: dict = {}

        async def fake_grab(url, timeout, headers=None):
            sent["headers"] = headers or {}
            if status == 304:
                return b"", 304, "", "", hdrs
            return (self.FEED if body is None else body), status, "utf-8", "", hdrs

        monkeypatch.setattr(feed_mod, "_grab", fake_grab)
        return sent

    def test_the_servers_validators_travel_back_to_the_caller(self, monkeypatch):
        self._stub(monkeypatch, {"etag": '"feed-1"', "last-modified": "Sun, 27 Sep 2026 12:00:00 GMT"})
        _content, structured = _dispatch("feed_fetch", {
            "urls": ["https://example.test/feed.xml"]})

        feed = structured["feeds"][0]
        assert feed["cache_validators"] == {"etag": '"feed-1"',
                                           "last_modified": "Sun, 27 Sep 2026 12:00:00 GMT"}
        # 线格式不上默认值，所以缺席就是说「不是 304」
        assert not feed.get("not_modified", False)

    def test_a_304_is_a_successful_poll_not_an_empty_feed(self, monkeypatch):
        sent = self._stub(monkeypatch, {"etag": '"feed-1"'}, status=304)
        _content, structured = _dispatch("feed_fetch", {
            "urls": ["https://example.test/feed.xml"], "if_none_match": "feed-1"})

        assert sent["headers"] == {"If-None-Match": '"feed-1"'}, \
            "裸 token 要补上引号：引号属于 header，不属于标签"
        feed = structured["feeds"][0]
        assert feed["not_modified"] is True
        assert feed["items"] == [] and feed.get("error", "") == ""

    def test_one_set_of_markers_cannot_be_asked_of_several_feeds(self, monkeypatch):
        self._stub(monkeypatch, {"etag": '"feed-1"'})
        _content, structured = _dispatch("feed_fetch", {
            "urls": ["https://example.test/a.xml", "https://example.test/b.xml"],
            "if_none_match": "feed-1"})

        assert structured["feeds"] == []
        assert "ONE feed" in structured["error"]

    def test_a_bare_etag_token_is_quoted_and_a_line_break_is_refused(self):
        from dhole_mcp.feed import _feed_conditional_headers

        hdrs, why = _feed_conditional_headers("", 'W/"abc"')
        assert why == "" and hdrs == {"If-None-Match": 'W/"abc"'}, "弱标签原样保留"
        hdrs, why = _feed_conditional_headers("", "*")
        assert why == "" and hdrs == {"If-None-Match": "*"}

        _h, why = _feed_conditional_headers("", 'abc\r\nX-Evil: 1')
        assert why and _h == {}, "换行必须被拒：那是在往请求里塞第二个 header"
        _h3, why3 = _feed_conditional_headers("Sun, 27 Sep 2026", "")
        assert why3 == "" and _h3 == {"If-Modified-Since": "Sun, 27 Sep 2026"}


class TestRotatedPdfTextIsNotBodyText:
    """P2-8：arxiv 页边那行旋转 90° 的标识被按阅读序读成 1v12052.9062:viXra。

    实测（报告自己那篇 2609.25021）：page 1 有 39 个 upright=False 的字符，正文
    3032 个 upright=True，而那 39 个正是报告引用的 guA / ]GL.sc[ / viXra 三行。
    """

    def _chars(self, upright_n: int, tilted_n: int):
        return ([{"object_type": "char", "upright": True, "text": "a"}] * upright_n
                + [{"object_type": "char", "upright": False, "text": "b"}] * tilted_n)

    def test_a_few_rotated_chars_are_the_artifact(self):
        from dhole_mcp.pdf_extractor import _rotated_is_artifact

        class Page:
            chars = self._chars(3032, 39)

        assert _rotated_is_artifact(Page) is True

    def test_a_page_that_IS_rotated_text_keeps_its_chars(self):
        """竖排中文 / 整页侧倒：旋转就是内容本身，剔除等于把文档删了。"""
        from dhole_mcp.pdf_extractor import _rotated_is_artifact

        class Page:
            chars = self._chars(20, 200)

        assert _rotated_is_artifact(Page) is False

    def test_a_page_without_chars_decides_nothing(self):
        from dhole_mcp.pdf_extractor import _rotated_is_artifact

        class Empty:
            chars = []

        class Broken:
            @property
            def chars(self):  # noqa: ANN401
                raise RuntimeError("no char layer")

        assert _rotated_is_artifact(Empty()) is False
        assert _rotated_is_artifact(Broken()) is False

    @pytest.mark.live
    def test_the_report_pdf_loses_its_sidebar_noise(self):
        """联网那条：拿报告引用的那篇真 PDF 验一遍（默认跑不执行）。"""
        import urllib.request

        from dhole_mcp.pdf_extractor import extract_pdf

        req = urllib.request.Request("https://arxiv.org/pdf/2609.25021",
                                     headers={"User-Agent": "Mozilla/5.0"})
        data = urllib.request.urlopen(req, timeout=120).read()
        text = "\n".join(extract_pdf(data, pages="1").content)

        for noise in ("viXra", "guA", "]GL.sc["):
            assert noise not in text, f"旋转页边仍被读进正文：{noise}"
        assert "Chat Template" in text
