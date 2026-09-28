"""G23 回归：内容变更监控 —— 条件请求与 feed 的增量。

改造前实测（本地 loopback 服务器实现真正的条件 GET）：带着 ``If-None-Match`` 去问
「变了吗」，服务器答 304（按设计没有正文），dhole 把空正文读成 JS 壳，于是升级去
开浏览器渲染一个**定义上没有正文**的响应，最后回 ``all_tiers_failed (HTTP status 0)``
——把「站点正常回答了」报成「站点把你挡了」。

现在 304 是一次**成功**：not_modified=true、content_ok=true、error 为空、content
为空，并且明确说「你手上那份就是最新的」。校验子（etag / last_modified）在每次
200 上都回吐，监控循环才闭得上。

feed 的「增量」是同一个问题的另一半：只留下比 since 新的条目，并把被过滤掉的条数
说出来 —— 空列表既可能是「没有新东西」也可能是「你的日期写错了」。
"""

from __future__ import annotations

import http.server
import json
import socketserver
import threading

import pytest

from dhole_mcp import feed as feed_mod
from dhole_mcp.server import (
    MasterFetchServer,
    ResponseModel,
    _conditional_headers,
    _stealthy_only_call,
)

ETAG = '"v7"'
LM = "Sun, 27 Sep 2026 00:00:00 GMT"


class TestTheHeaderTranslation:

    def test_nothing_asked_nothing_added(self):
        assert _conditional_headers(None, None) == ({}, "")
        assert _conditional_headers("", "") == ({}, "")

    def test_an_http_date_is_passed_through(self):
        headers, why = _conditional_headers(LM, None)
        assert why == ""
        assert headers == {"If-Modified-Since": LM}

    @pytest.mark.parametrize("iso", [
        "2026-09-27",
        "2026-09-27T00:00:00Z",
        "2026-09-27T00:00:00+00:00",
    ])
    def test_iso_dates_become_an_http_date(self, iso):
        headers, why = _conditional_headers(iso, None)
        assert why == ""
        assert headers == {"If-Modified-Since": LM}

    def test_a_timed_iso_keeps_its_time(self):
        headers, _ = _conditional_headers("2026-09-27T08:30:00Z", None)
        assert headers["If-Modified-Since"] == "Sun, 27 Sep 2026 08:30:00 GMT"

    def test_an_unreadable_date_is_refused_with_the_two_accepted_shapes(self):
        headers, why = _conditional_headers("last tuesday", None)
        assert headers == {}
        assert "neither an HTTP-date" in why and "ISO-8601" in why

    @pytest.mark.parametrize("bad", [12345, ["2026-09-27"], True])
    def test_a_non_string_date_is_refused(self, bad):
        headers, why = _conditional_headers(bad, None)
        assert headers == {}
        assert why

    def test_a_bare_etag_gets_its_quotes(self):
        """调用方从 cache_validators 里拿到的常常是去掉引号的那一半。"""
        headers, why = _conditional_headers(None, "v7")
        assert why == ""
        assert headers == {"If-None-Match": ETAG}

    @pytest.mark.parametrize("given", [ETAG, 'W/"v7"', "*"])
    def test_an_etag_already_in_shape_is_left_alone(self, given):
        headers, why = _conditional_headers(None, given)
        assert why == ""
        assert headers == {"If-None-Match": given}

    def test_header_injection_in_an_etag_is_refused(self):
        headers, why = _conditional_headers(None, 'x"\r\nX-Evil: y')
        assert headers == {}
        assert why

    def test_an_absurdly_long_etag_is_refused(self):
        headers, why = _conditional_headers(None, "z" * 300)
        assert headers == {}
        assert "too long" in why

    def test_both_options_at_once(self):
        headers, why = _conditional_headers(LM, ETAG)
        assert why == ""
        assert set(headers) == {"If-Modified-Since", "If-None-Match"}


class TestTheStealthyPredicate:
    """条件请求只能问 HTTP 层：浏览器不接受这个问题。"""

    @pytest.mark.parametrize("ff,actions,want", [
        (None, None, False),
        ("http", None, False),
        ("stealthy", None, True),
        ("dynamic", None, True),
        (None, [{"click": "x"}], True),
    ])
    def test_only_a_browser_call_is_browser_only(self, ff, actions, want):
        assert _stealthy_only_call(ff, actions) is want


# ─── 端到端：一个真会答 304 的服务器 ───────────────────────────────────────────

class _RevalidateHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.hits += 1  # type: ignore[attr-defined]
        if self.headers.get("if-none-match") == ETAG or \
                self.headers.get("if-modified-since") == LM:
            self.send_response(304)
            self.send_header("etag", ETAG)
            self.send_header("last-modified", LM)
            self.end_headers()
            return
        body = json.dumps({"reading": 42}).encode()
        ctype = "application/pdf" if self.path.endswith(".pdf") else "application/json"
        if ctype == "application/pdf":
            body = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
        self.send_response(200)
        self.send_header("content-type", ctype)
        self.send_header("etag", ETAG)
        self.send_header("last-modified", LM)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def origin():
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _RevalidateHandler)
    srv.hits = 0
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/state", srv
    srv.shutdown()


@pytest.fixture(autouse=True)
def _throwaway_cache(tmp_path, monkeypatch):
    """每个用例一份空缓存：条件请求「不许读缓存」这条断言，只有在缓存里真的
    有过一次 200 时才算数，也只有在不串用例时才可知。"""
    from dhole_mcp import cache as cache_mod
    monkeypatch.setattr(cache_mod, "_CACHE_DIR", tmp_path)
    cache_mod._db_initialized.clear()
    yield
    cache_mod._db_initialized.clear()


class TestTheConditionalCall:

    @pytest.mark.asyncio
    async def test_a_plain_answer_reports_the_validators_it_used(self, origin):
        url, srv = origin
        r = await MasterFetchServer().smart_fetch(url, allow_private=True, cache_ttl=0)
        assert r.status == 200
        assert r.cache_validators == {"etag": ETAG, "last_modified": LM}

    @pytest.mark.asyncio
    async def test_a_cached_answer_still_carries_the_markers_to_revalidate(self, origin):
        """命中缓存时 etag 必须还在：条件轮询的入参正是从这里取的。

        写入侧与读出侧都没带 cache_validators，于是缓存命中的响应回的是
        `"cache_validators":{}` —— 空对象把这个缺口藏住了：重新验证只对先
        `cache_ttl=0` 强制刷新的调用方可行，而轮询者恰恰是复用缓存的那一个。
        往返钉在这里，并让拿到的 etag 真的换来一个 304。
        """
        url, _ = origin
        fresh = await MasterFetchServer().smart_fetch(url, allow_private=True,
                                                      cache_ttl=600)
        assert fresh.cache_validators == {"etag": ETAG, "last_modified": LM}

        hit = await MasterFetchServer().smart_fetch(url, allow_private=True,
                                                    cache_ttl=600)
        assert hit.cached is True, "这一条测的就是缓存命中那条路径"
        assert hit.cache_validators == {"etag": ETAG, "last_modified": LM}, \
            "缓存命中丢掉了版本标记，条件轮询无从下手"

        again = await MasterFetchServer().smart_fetch(
            url, allow_private=True, cache_ttl=600,
            if_none_match=hit.cache_validators["etag"])
        assert (again.status, again.not_modified) == (304, True)

    @pytest.mark.asyncio
    async def test_a_304_is_a_success_with_no_body(self, origin):
        url, _ = origin
        r = await MasterFetchServer().smart_fetch(
            url, allow_private=True, cache_ttl=0, if_none_match=ETAG)
        assert r.status == 304
        assert r.not_modified is True
        assert r.content_ok is True, f"a 304 must not read as a failure: {r.error}"
        assert r.error == ""
        assert r.content == []
        assert "not modified" in r.next_action
        assert r.page_type != "js_shell"
        assert "unchanged" in r.summary

    @pytest.mark.asyncio
    async def test_a_304_on_a_document_is_still_marked_as_unchanged(self, origin):
        """Two paths returned before the 304 branch, and the live call hit the
        second one: the document early-return in `_auto_escalate`, and the pinned
        tier in `_force_fetch` (measured live against arxiv
        `/pdf/1706.03762`, where watching a paper for changes is exactly what the
        etag is for).

        The field's own description promises True on a 304, and compaction only
        ships a field that differs from its default, so the false was the whole
        answer: a caller reading `not_modified` sees "changed" for the one
        response that proves it did not. Hence the flag is set in the shared
        tail, where no path can forget it.
        """
        url, _ = origin
        pdf = url.replace("/state", "/paper.pdf")
        for pinned in (None, "http"):
            r = await MasterFetchServer().smart_fetch(
                pdf, allow_private=True, cache_ttl=0, if_none_match=ETAG,
                **({"force_fetcher": pinned} if pinned else {}))
            assert (r.status, r.not_modified) == (304, True), f"force_fetcher={pinned}"
            assert r.content_ok is True and r.error == ""
            assert "not modified" in r.next_action

    @pytest.mark.asyncio
    async def test_a_304_never_buys_a_browser_launch(self, origin, monkeypatch):
        """升级去渲染一个按设计没有正文的响应，是这一项原来最贵的错误。"""
        url, _ = origin
        calls = []

        async def fail(self, *a, **kw):
            calls.append(a)
            return ResponseModel(url=url, status=200, content=["<p>x</p>"],
                                 fetcher_used="stealthy")

        monkeypatch.setattr(MasterFetchServer, "stealthy_fetch", fail)
        await MasterFetchServer().smart_fetch(url, allow_private=True, cache_ttl=0,
                                              if_modified_since="2026-09-27")
        assert calls == []

    @pytest.mark.asyncio
    async def test_a_bare_etag_from_cache_validators_is_enough(self, origin):
        url, _ = origin
        r = await MasterFetchServer().smart_fetch(
            url, allow_private=True, cache_ttl=0, if_none_match="v7")
        assert (r.status, r.not_modified) == (304, True)

    @pytest.mark.asyncio
    async def test_a_conditional_call_asks_the_origin_not_the_cache(self, origin):
        """先缓存一份 200，再问「变了吗」：必须真的去问站点，不能拿记忆作答。"""
        url, srv = origin
        await MasterFetchServer().smart_fetch(url, allow_private=True, cache_ttl=600)
        before = srv.hits
        r = await MasterFetchServer().smart_fetch(
            url, allow_private=True, cache_ttl=600, if_none_match=ETAG)
        assert srv.hits == before + 1, "the conditional question never reached the origin"
        assert r.cached is False
        assert r.not_modified is True

    @pytest.mark.asyncio
    async def test_an_unreadable_date_dials_nothing(self, origin):
        url, srv = origin
        before = srv.hits
        r = await MasterFetchServer().smart_fetch(
            url, allow_private=True, cache_ttl=0, if_modified_since="someday")
        assert r.error.startswith("invalid_request:")
        assert "if_modified_since" in r.error
        assert srv.hits == before, "a refused date must not cost a request"

    @pytest.mark.asyncio
    async def test_a_browser_call_says_the_conditionals_were_dropped(self, origin,
                                                                    monkeypatch):
        url, _ = origin

        async def fake_stealthy(self, *a, **kw):
            hdrs = kw.get("extra_headers") or {}
            assert "If-None-Match" not in hdrs, list(hdrs)
            return ResponseModel(url=url, status=200, content=["<p>body</p>"],
                                 content_type="text/html", fetcher_used="stealthy")

        monkeypatch.setattr(MasterFetchServer, "stealthy_fetch", fake_stealthy)
        r = await MasterFetchServer().smart_fetch(
            url, allow_private=True, cache_ttl=0, if_none_match=ETAG,
            force_fetcher="stealthy")
        assert r.not_modified is False
        assert "dropped" in r.summary and "force_fetcher='http'" in r.summary


# ─── feed 的增量过滤 ─────────────────────────────────────────────────────────

def _feed_xml(dates: list[str]) -> str:
    items = "".join(
        f"<item><title>entry {i}</title><link>https://f.example/{i}</link>"
        + (f"<pubDate>{d}</pubDate>" if d else "")
        + "</item>"
        for i, d in enumerate(dates))
    return ('<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>'
            + items + "</channel></rss>")


async def _fetch_with(xml: str, **kw):
    """Run fetch_feed with the network replaced by a canned body (and a call log)."""
    calls: list[str] = []

    async def fake_grab(url, timeout, headers=None):
        calls.append(url)
        return xml.encode(), 200, "utf-8", "", {}

    orig = feed_mod._grab
    feed_mod._grab = fake_grab
    try:
        out = await feed_mod.fetch_feed("https://f.example/feed", **kw)
    finally:
        feed_mod._grab = orig
    out._grab_calls = list(calls)  # type: ignore[attr-defined]
    return out


async def _poll_with(xml: str, status: int = 200, hdrs: dict | None = None, **kw):
    """fetch_feed against a canned response, recording the headers we actually sent."""
    sent: dict = {}

    async def fake_grab(url, timeout, headers=None):
        sent.update(headers or {})
        return (b"" if status == 304 else xml.encode()), status, "utf-8", "", (hdrs or {})

    orig = feed_mod._grab
    feed_mod._grab = fake_grab
    try:
        out = await feed_mod.fetch_feed("https://f.example/feed", **kw)
    finally:
        feed_mod._grab = orig
    out._sent = dict(sent)  # type: ignore[attr-defined]
    return out


class TestAConditionalThatWasIgnoredSaysSo:
    """站方用 200 + 全文回答「变了吗」，等于没回答 —— 而沉默会被读成「变了」。

    smart_fetch 早就为同一件事写了 validators_ignored（它的注释里点名的就是
    arXiv 对每个 PDF etag 都这样）；feed 这一侧此前什么都没有，于是轮询者分不清
    "内容变了" 和 "这源站不支持再验证"，而后者意味着每次轮询都要重下整个 feed。
    """

    QUIET = ('<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>'
             "<lastBuildDate>Sun, 27 Sep 2026 04:00:00 +0000</lastBuildDate>"
             "<skipDays><day>Saturday</day></skipDays></channel></rss>")

    @pytest.mark.asyncio
    async def test_a_200_answer_to_a_conditional_names_the_refusal(self):
        out = await _poll_with(_feed_xml(["Sat, 27 Sep 2026 10:00:00 +0000"]),
                               if_none_match="v7")
        assert out._sent.get("If-None-Match") == '"v7"', "裸 token 要补上引号"
        assert out.not_modified is False
        assert "If-None-Match" in out.note
        assert "does not honour" in out.note
        assert "NOT evidence" in out.note, "必须说清这不是内容变了的证据"

    @pytest.mark.asyncio
    async def test_a_real_304_still_needs_no_note(self):
        out = await _poll_with("", status=304, if_none_match="v7")
        assert (out.not_modified, out.items, out.note) == (True, [], "")

    @pytest.mark.asyncio
    async def test_a_plain_request_says_nothing_about_validators(self):
        out = await _poll_with(_feed_xml(["Sat, 27 Sep 2026 10:00:00 +0000"]))
        assert out.note == "", "没问过就不该报告答复方式"

    @pytest.mark.asyncio
    async def test_two_explanations_share_the_field_without_clobbering(self):
        """空文档 + 被无视的条件请求：两条都是对同一个空列表的解释。"""
        out = await _poll_with(self.QUIET, if_modified_since="2026-09-20")
        assert "skipDays=Saturday" in out.note
        assert "If-Modified-Since" in out.note
        assert " | " in out.note, "第二条不该把第一条挤掉"

    @pytest.mark.asyncio
    async def test_the_note_reaches_the_wire_for_one_feed_in_a_batch(self):
        """批量里只有被问过的那一行该带说明。"""
        from dhole_mcp.server import _wire_json, _wire_prune

        def row(result):
            return _wire_prune(
                {k: getattr(result, k) for k in type(result).model_fields},
                feed_mod.FeedResult)

        quiet = await _poll_with(self.QUIET, if_none_match="v7")
        plain = await _poll_with(_feed_xml(["Sat, 27 Sep 2026 10:00:00 +0000"]))
        _text, payload = _wire_json({"feeds": [row(quiet), row(plain)]})
        assert "does not honour" in payload["feeds"][0]["note"]
        assert "note" not in payload["feeds"][1], "空 note 不上线"


class TestTheFeedCutoff:


    def test_the_accepted_formats(self):
        dt, iso, why = feed_mod._since_cutoff("2026-09-20")
        assert why == "" and iso.startswith("2026-09-20")
        assert dt.tzinfo is not None
        _dt, iso, _why = feed_mod._since_cutoff("Sat, 20 Sep 2026 08:00:00 +0000")
        assert iso.startswith("2026-09-20T08:00")
        assert feed_mod._since_cutoff("") == (None, "", "")
        assert feed_mod._since_cutoff(None)[0] is None

    def test_an_unreadable_cutoff_is_a_reason_not_a_no_op(self):
        dt, iso, why = feed_mod._since_cutoff("last tuesday")
        assert dt is None and why

    @pytest.mark.asyncio
    async def test_older_entries_are_dropped_and_counted(self):
        out = await _fetch_with(_feed_xml([
            "Sat, 27 Sep 2026 10:00:00 +0000",
            "Fri, 26 Sep 2026 10:00:00 +0000",
            "Mon, 15 Sep 2026 10:00:00 +0000",
        ]), since="2026-09-26")
        assert [i.title for i in out.items] == ["entry 0", "entry 1"]
        assert out.items_older_than_since == 1
        assert out.since.startswith("2026-09-26")

    @pytest.mark.asyncio
    async def test_undated_entries_are_kept_and_named(self):
        """没有日期的条目不是「旧」，是「判不了」——悄悄丢掉就等于替站点撒了谎。"""
        out = await _fetch_with(_feed_xml([
            "Sat, 27 Sep 2026 10:00:00 +0000",
            "",
        ]), since="2026-09-26")
        assert [i.title for i in out.items] == ["entry 0", "entry 1"]
        assert out.items_without_date == 1
        assert out.items_older_than_since == 0

    @pytest.mark.asyncio
    async def test_the_filter_runs_before_the_cap(self):
        """30 条旧的 + 2 条新的：先截断再过滤会把新条目留在截断窗口外。"""
        from datetime import datetime, timedelta, timezone
        from email.utils import format_datetime
        day = datetime(2026, 9, 25, 10, tzinfo=timezone.utc)
        old = [format_datetime(day - timedelta(days=i), usegmt=True) for i in range(30)]
        dates = ["Sat, 26 Sep 2026 10:00:00 +0000",
                 "Sun, 27 Sep 2026 10:00:00 +0000"] + old
        out = await _fetch_with(_feed_xml(dates), since="2026-09-26", max_items=20)
        assert [i.title for i in out.items] == ["entry 1", "entry 0"]  # newest first
        assert out.items_older_than_since == 30

    @pytest.mark.asyncio
    async def test_nothing_new_is_an_answer_you_can_act_on(self):
        out = await _fetch_with(_feed_xml([
            "Mon, 01 Sep 2026 10:00:00 +0000"]), since="2026-09-20")
        assert out.items == []
        assert out.error == ""
        assert out.items_older_than_since == 1

    @pytest.mark.asyncio
    async def test_a_bad_cutoff_costs_no_request(self):
        out = await _fetch_with(_feed_xml(["Sat, 27 Sep 2026 10:00:00 +0000"]),
                                since="whenever")
        assert out.error.startswith("since=")
        assert out._grab_calls == []

    @pytest.mark.asyncio
    async def test_without_since_nothing_is_reported_as_filtered(self):
        out = await _fetch_with(_feed_xml(["Sat, 27 Sep 2026 10:00:00 +0000"]))
        assert (out.since, out.items_older_than_since, out.items_without_date) == ("", 0, 0)

    def test_the_projection_shows_the_receipts(self):
        """feed_fetch 返回的是自己拼的 dict：模型上的新字段不投影就等于不存在。

        判据是「这个工具的 wire 条目里有没有」而非「有没有写在描述里」：收据清单
        现在归在 since 参数的说明里（一个事实一处），只查描述会误报成信息丢失。
        """
        tools = {t["name"]: t for t in MasterFetchServer._TOOL_DEFS}
        text = json.dumps(tools["feed_fetch"], ensure_ascii=False).lower()
        assert "items_older_than_since" in text and "since" in text
        assert "since" in tools["feed_fetch"]["inputSchema"]["properties"]
