"""外部测试报告（dhole_fix_prompt / 第二轮报告）挖出的缺陷守卫

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

import json
import os
import pytest
from dhole_mcp.server import ResponseModel, MasterFetchServer, _ARCHIVE_FALLBACK_STATUSES, _agent_hints, _apply_chunking, _annotate_quality, _with_agent_hints, _backfill_article_json, _blocked_path_prefix, _detect_content_issue, _invalid_request_result, _is_bot_wall, _is_js_shell, _never_escalate, _resolve_local_path, _should_try_archive, _translate_response
import asyncio
from pathlib import Path
import dhole_mcp.actions as actions_mod
from dhole_mcp import server as server_mod


# ============================================================================
# 来源：test_bug_report_regressions.py
# ============================================================================
# Regressions for the bugs found in the external dhole MCP test report.

def _result(**kwargs):
    defaults = dict(
        status=200, content=["Real article body text about Python packaging."],
        url="https://example.com/article", fetcher_used="http",
        content_type="text/html", total_size_bytes=4000,
        extracted_type="markdown",
    )
    defaults.update(kwargs)
    return ResponseModel(**defaults)


# `_auto_escalate` 这一层的必填自变量：除 url 与预算外，其余都是本题不收的值。
# 以前 6 条测试各自把它们抄成一串位置参数（17 个），13d 删掉 wait / network_idle
# 之后位置整体错位而不报错 —— timeout 被喂成 None、max_chars 被喂成 None，有的测试
# 只是碰巧还没走到用到那两个值的那一步。改成按名字传，且只在这一处传。
_TIER_BASE = dict(
    extraction_type="markdown", css_selector=None, cache_ttl=0, offset=0,
    headless=True, real_chrome=False, proxy=None, solve_cloudflare=False,
    block_webrtc=False, hide_canvas=False, extra_headers=None,
    useragent=None, cookies=None,
)


async def _escalate(server, url, timeout_ms):
    """走真的升级阶梯，网络在 self.get 那一层被换掉。

    会话那一层也要换掉：升级链拿 `stealthy_fetch` 之前先 `_ensure_auto_session`，而那颗
    函数真的 launch 一台浏览器。这一刀读的是阶梯与文案，不是浏览器 —— 批次 1 的 conftest
    启动闸把这种"顺带起一次 chromium"的用例抓了出来（`tests/test_anti_detect_b5.py:63`
    是同一刀的先例）。
    """
    async def _no_session(**kw):
        return "auto-under-test"

    server._ensure_auto_session = _no_session
    return await server._auto_escalate(url, timeout=timeout_ms, **_TIER_BASE)


# ─── BUG-5: error / placeholder text must never be the page body ───────────

class TestFailureContentIsEmpty:

    def test_failed_fetch_has_no_placeholder_body(self):
        """A DNS failure used to return content ["[No more content.]"], which a
        caller reading content[0] took for the page."""
        failed = _result(status=0, content=[], fetcher_used="none",
                         error="network_error: dns_failure (TCP preflight)")
        out = _apply_chunking(failed)
        assert out.content == []
        assert out.total_extracted_chars == 0

    def test_error_text_is_not_counted_as_extracted_content(self):
        """Measured: a TLS failure reported total_extracted_chars 87 - the length
        of the error string, presented as page content."""
        failed = _result(status=0, content=[], fetcher_used="http",
                         error="error sending request for url > tls handshake eof")
        out = _apply_chunking(failed)
        assert out.total_extracted_chars == 0
        assert out.content_ok is False

    # 15.0 实测（软 404 频率探针顺带挖到）：图片与「.pdf 却回了 HTML」两条分支
    # 仍在把占位文本写进 content。content_ok 是 false、error 也有说明，但正文里
    # 不该有错误文本 —— 调用方读 content[0] 就会把 "[Image page - OCR failed: ...]"
    # 当成页面正文（BUG-5 的同类，遗留在这两条分支）。

    @staticmethod
    def _image_page(content_type: str = "image/svg+xml", body: bytes = b"<svg/>"):
        class FakePage:
            status = 200
            body = b""
            encoding = "utf-8"
            headers = {"content-type": content_type}
        page = FakePage()
        page.body = body
        page.url = "https://cdn.example.com/logo.svg"
        return page

    def test_image_ocr_failure_leaves_the_body_empty(self, monkeypatch):
        import dhole_mcp.ocr as ocr

        monkeypatch.setattr(ocr, "ocr_available", lambda: True)
        monkeypatch.setattr(ocr, "ocr_image_bytes",
                            lambda b: (_ for _ in ()).throw(ValueError("cannot identify image file")))
        r = _translate_response(self._image_page(), "markdown", None, "http", 1)
        assert r.content == [], "OCR 失败的占位文本又回到正文里了"
        assert r.content_ok is False
        assert r.error.startswith("image_ocr_failed")
        assert "OCR failed" not in " ".join(r.content)

    def test_image_without_ocr_extras_keeps_the_advice_in_the_error(self, monkeypatch):
        """正文清空后，「装 OCR 依赖」这句指路必须留在 error 里 —— 否则调用方
        只拿到一个 image_ocr_unavailable 代码，不知道该做什么。"""
        import dhole_mcp.ocr as ocr

        monkeypatch.setattr(ocr, "ocr_available", lambda: False)
        r = _translate_response(self._image_page(), "markdown", None, "http", 1)
        assert r.content == []
        assert "image_ocr_unavailable" in r.error
        assert "dhole-mcp[all]" in r.error

    def test_image_with_no_text_leaves_the_body_empty(self, monkeypatch):
        import dhole_mcp.ocr as ocr

        monkeypatch.setattr(ocr, "ocr_available", lambda: True)
        monkeypatch.setattr(ocr, "ocr_image_bytes", lambda b: "")
        r = _translate_response(self._image_page(), "markdown", None, "http", 1)
        assert r.content == []
        assert r.error.startswith("image_ocr_empty")

    def test_pdf_url_that_returned_html_leaves_the_body_empty(self):
        """URL 以 .pdf 结尾却回了 HTML（登录/错误页）：那页 HTML 不是正文，也不是
        一个可以引用的错误说明。"""
        class FakePage:
            status = 200
            encoding = "utf-8"
            headers = {"content-type": "text/html; charset=utf-8"}
        page = FakePage()
        page.body = b"<html><body>Please sign in to download</body></html>"
        page.url = "https://example.com/paper.pdf"
        r = _translate_response(page, "markdown", None, "http", 1)
        assert r.content == []
        assert r.error.startswith("auth_required")
        assert "not_a_pdf" not in " ".join(r.content)

    @staticmethod
    def _pdf_page(status: int, body: bytes):
        class FakePage:
            encoding = "utf-8"
            headers = {"content-type": "text/html; charset=utf-8"}
        page = FakePage()
        page.status = status
        page.body = body
        page.url = "https://example.com/paper.pdf"
        return page

    def test_a_pdf_url_behind_a_403_challenge_is_a_wall_not_a_bad_pdf(self):
        """2026-10-28 本地受控靶：403 + cf-challenge 曾被判成 not_a_pdf
        （retryable=false，出路是“检查 URL”），而浏览器档其实能过这面墙。"""
        page = self._pdf_page(403, b"<html><head><title>Just a moment...</title>"
                                   b"</head><body>cf-challenge</body></html>")
        r = _translate_response(page, "markdown", None, "http", 1)
        assert r.content == []
        assert r.status == 403
        assert r.error.startswith("bot_wall_detected"), r.error
        annotated = _with_agent_hints(_annotate_quality(r))
        assert annotated.diagnosis and annotated.diagnosis["code"] == "bot_wall"
        assert "does not send .pdf" in annotated.diagnosis["recover_with"], (
            "浏览器档在 PDF 上是**按类型故意不升**，不是这台机器起不来；"
            f"诊断却写成后者：{annotated.diagnosis}")
        assert "unavailable on this machine" not in annotated.next_action, annotated.next_action
        assert "switch source" in annotated.next_action, annotated.next_action

    def test_a_pdf_challenge_that_the_browser_already_rendered_is_not_sent_back(self):
        """force_fetcher='stealthy' 跑过之后，回执必须说“已经渲染过”，
        而不是“浏览器档不可用”或“再升级一次”。"""
        page = self._pdf_page(403, b"<html><head><title>Just a moment...</title>"
                                   b"</head><body>cf-challenge</body></html>")
        r = _translate_response(page, "markdown", None, "stealthy", 1)
        r.escalation_path = "direct:stealthy"
        annotated = _with_agent_hints(_annotate_quality(r))
        assert annotated.diagnosis and annotated.diagnosis["code"] == "bot_wall"
        assert "already rendered" in annotated.diagnosis["recover_with"], annotated.diagnosis
        assert "already rendered" in annotated.next_action, annotated.next_action
        assert "unavailable on this machine" not in annotated.next_action

    def test_a_pdf_url_behind_a_plain_403_reports_the_status(self):
        """没有挑战页特征时，403 本身仍是比“URL 不像 PDF”更接近事实的诊断。"""
        page = self._pdf_page(403, b"<html><body>Forbidden</body></html>")
        r = _translate_response(page, "markdown", None, "http", 1)
        assert r.content == []
        assert r.error.startswith("http_error_403"), r.error
        assert r.error != "not_a_pdf: URL ends in .pdf but the response is HTML, not a PDF (possibly a redirect/error page). Try the direct PDF link."
        annotated = _with_agent_hints(_annotate_quality(r))
        assert annotated.diagnosis and annotated.diagnosis["code"] == "http_error_403"
        assert annotated.diagnosis["retryable"] is False
        assert ("force_fetcher" in annotated.next_action
                or "switch source" in annotated.next_action), annotated.next_action

    def test_a_pdf_url_returning_plain_html_is_still_not_a_pdf(self):
        page = self._pdf_page(200, b"<html><body>A landing page about widgets.</body></html>")
        r = _translate_response(page, "markdown", None, "http", 1)
        assert r.content == []
        assert r.error.startswith("not_a_pdf"), r.error

    def test_empty_image_body_is_not_a_js_shell_worth_a_browser(self):
        """空正文 + 200 正是 _is_js_shell 升级的条件 —— 图片页必须被排除在外，
        否则 OCR 失败会换来一次 30~40s 的浏览器渲染，结果还是同一张图。"""
        assert _is_js_shell(_result(status=200, content=[], content_type="image/svg+xml")) is True, \
            "前提变了：空 200 正文已不再触发升级，这条守卫可以退休了"
        assert _never_escalate(_result(status=200, content=[], content_type="image/svg+xml"),
                               "https://cdn.example.com/logo.svg") is True

    @pytest.mark.asyncio
    async def test_empty_ocr_image_does_not_trigger_a_browser_run(self, monkeypatch):
        """正文清空后的 200 图片 = 空壳形态，必须挡在升级之前。

        没有这道守卫时这条会红：空正文 + 200 让 _is_js_shell 返回 True，
        should_escalate 成立，于是 agent 等 30~40s 换来同一张图（还可能是空结果）。
        """
        import dhole_mcp.fetcher as fetcher

        async def http_image(*a, **k):
            return _result(status=200, content=[], content_type="image/png",
                           fetcher_used="http", total_size_bytes=4096,
                           error="image_ocr_failed: cannot identify image file")

        async def browser(*a, **k):
            raise AssertionError("空正文的图片响应不该升级浏览器")

        monkeypatch.setattr(fetcher, "tcp_preflight", lambda url, timeout=2.0: (True, ""))
        server = MasterFetchServer()
        server.get = http_image
        server.stealthy_fetch = browser
        out = await _escalate(server, "https://cdn.example.com/logo.svg", 20000)
        assert out.error.startswith("image_ocr_failed")
        assert out.fetcher_used == "http", "结果应该仍是 HTTP 层的，说明没走浏览器"

    @pytest.mark.asyncio
    async def test_gone_image_url_skips_the_browser_but_still_tries_the_archive(self, monkeypatch):
        """守卫只该挡住浏览器层，不该顺手没收 archive 回退。

        图片分支清空正文后，空正文 + 200 会被 _is_js_shell 判成壳 → 白烧一次浏览器。
        但如果把守卫写成「早期返回」，404 的图片 URL 就连 Wayback 也不查了 —— 而
        失效的图片链接正是最需要快照的那种。
        """
        import dhole_mcp.fetcher as fetcher

        async def http_404(*a, **k):
            return _result(status=404, content=[], content_type="image/png",
                           fetcher_used="http", total_size_bytes=0)

        async def browser(*a, **k):
            raise AssertionError("图片响应不该升级浏览器")

        asked: list[str] = []

        async def fake_archive(url, *a, **k):
            asked.append(url)
            return _result(status=200, content=["snapshot body from 2019"],
                           content_type="text/html", source="archive")

        monkeypatch.setattr(fetcher, "tcp_preflight", lambda url, timeout=2.0: (True, ""))
        server = MasterFetchServer()
        server.get = http_404
        server.stealthy_fetch = browser
        monkeypatch.setattr(server, "_fetch_from_archive", fake_archive)
        out = await _escalate(server, "https://cdn.example.com/gone.png", 20000)
        assert asked == ["https://cdn.example.com/gone.png"], "404 的图片 URL 没去查 Wayback"
        assert "snapshot body" in "\n".join(out.content)

    def test_image_ocr_failure_tells_the_agent_what_to_do_next(self):
        """正文清空后恢复建议落在 next_action —— 而且只挂在这条错误上。

        OCR 成功的图片同样 page_type="image"：按 page_type 分流会对着一段真读出来的
        文字说「这图读不出字」。
        """
        failed = _with_agent_hints(_result(status=200, content=[], content_type="image/png",
                                           error="image_ocr_failed: cannot identify image file"))
        assert failed.content_ok is False
        assert "vision-capable" in failed.next_action

        ok = _with_agent_hints(_result(status=200, content=["recognized text here"],
                                       content_type="image/png", page_type="image"))
        assert ok.content_ok is True
        assert "vision-capable" not in (ok.next_action or "")

    def test_never_escalate_only_covers_binary_responses(self):
        assert _never_escalate(_result(content_type="image/png"), "https://x.test/a") is True
        # .pdf 判定看 URL（体是 HTML 的情况也要拦住），图片判定看 content-type
        assert _never_escalate(_result(content_type="text/html"),
                               "https://x.test/paper.pdf?v=2") is True
        assert _never_escalate(_result(content_type="text/html"),
                               "https://x.test/page") is False
        # 扫描件 PDF 不在内：它的正文里带着明确说明（test_pdf_real 钉住了这条有意行为），
        # 本来就不满足升级条件。
        assert _never_escalate(_result(content_type="application/pdf"), "https://x.test/scan") is False

    @pytest.mark.asyncio
    async def test_all_tiers_failed_keeps_the_advice_out_of_the_body(self, monkeypatch):
        """The both-tiers-failed path used to write a six-line advisory block into
        content[0], where a caller reading the body found it."""
        import dhole_mcp.fetcher as fetcher

        async def http_fail(*a, **k):
            return _result(status=503, content=[], error="blocked")

        async def browser_fail(*a, **k):
            return _result(status=503, content=[], error="blocked",
                           fetcher_used="stealthy")

        monkeypatch.setattr(fetcher, "tcp_preflight", lambda url, timeout=2.0: (True, ""))
        monkeypatch.setattr("dhole_mcp.server._browser_deps_available", lambda: True)

        # archive.org 是这条路径的最后一跳，原来真的打网络：2026-09-22 的一次运行里
        # 它超时了，于是错误文本变成 "timeout: ... during the archive.org lookup"，
        # 断言随网络抖动而红。桩掉快照查询（返回非 200 = 没有快照），这条测的才是
        # "两层都失败时提示写进 error 字段而不是正文"。
        class _NoSnapshot:
            status = 502
            body = b""

        async def no_snapshot(*a, **k):
            return _NoSnapshot()

        monkeypatch.setattr("dhole_mcp.server._fallback_http_get", no_snapshot)
        server = MasterFetchServer()
        server.get = http_fail
        server.stealthy_fetch = browser_fail
        out = await _escalate(server, "https://blocked.example.com", 20000)
        assert out.content == []
        assert out.error.startswith("all_tiers_failed:")
        # 13d 只改了这句的措辞：代理不再是一颗参数，而是 cred_defaults 里存的凭据。
        # 本题钉的仍是 BUG-5 那一半 —— 恢复建议在 error 里、不在正文里。
        assert "switch source" in out.error or "Retry once" in out.error
        assert "the proxy parameter" not in out.error, "退役的 proxy 参数措辞复活了"
        assert out.content_ok is False
        # 2026-10-08 实连实测（D-20）：这里报的是 `unknown (HTTP status 0)` 并叫读者
        # "先去查 DNS / 试个代理"——可是站点明明回了 503，只是浏览器渲染非 200 时自己
        # 报 0。用一个档位的渲染结果去否认另一个档位收到的答复，是把事实说反。
        # 现在钉的是：状态号来自源站真正回的那个，且文案不再把它说成够不着。
        assert "HTTP status 503" in out.error, "报出去的是渲染码，不是站点的答案"
        assert "unknown" not in out.error.split("\n")[0]
        assert "No HTTP response was received" not in out.error, \
            "站点回了 503，不能宣称没收到响应"
        assert "Try a different URL on the same domain (some paths have lower protection)" \
            not in out.error, "5xx 被当成反爬墙，指的是下一步做不成的事"
        assert "A 5xx is not a bot block" in out.error
        assert out.diagnosis["code"] == "all_tiers_failed"

    def test_exhausted_pagination_still_says_so(self):
        """The placeholder is correct - and expected - when a real page was read
        to the end and the agent asked for a chunk past it."""
        page = _result(content=["a" * 2000])
        out = _apply_chunking(page, offset=5000)
        assert out.content == ["[No more content.]"]
        assert out.total_extracted_chars == 2000

    @pytest.mark.asyncio
    async def test_bulk_failure_carries_error_not_body(self):
        """Measured: content ["[Fetch error: error sending request for url ... >
        tls handshake eof]"] with total_extracted_chars 87 - the exception text
        presented as the page body, and counted as its length."""
        import dhole_mcp.fetcher as fetcher

        class Boom(Exception):
            pass

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, url, **kwargs):
                raise Boom("error sending request for url > tls handshake eof")

        monkey = pytest.MonkeyPatch()
        monkey.setattr(fetcher, "HTTPSession", lambda *a, **k: FakeSession())
        try:
            bulk = await MasterFetchServer.bulk_get(urls=["https://example.com/x"])
        finally:
            monkey.undo()
        r = bulk.results[0]
        assert r.status == 0
        assert r.content == []
        assert "tls handshake eof" in r.error
        assert r.total_extracted_chars == 0
        assert r.content_ok is False


# ─── BUG-6 / BUG-12: walls on HTTP 200 ─────────────────────────────────────

_SOGOU_CAPTCHA = (
    "IP：112.49.187.249访问时间：2026.09.21 23:54:14VerifyCode：b95fc2fcd78a"
    "From：weixin.sogou.com\n\n此验证码用于确认这些请求是您的正常行为而不是"
    "自动程序发出的，需要您协助验证。\n\n验证码：\n\n提交 提交后没解决问题？欢迎反馈。"
)


class TestWallDetectionOn200:

    def test_captcha_page_is_detected(self):
        r = _result(url="https://weixin.sogou.com/antispider/?from=%2Flink",
                    content=[_SOGOU_CAPTCHA])
        assert _is_bot_wall(r) is True
        assert _detect_content_issue(r).startswith("bot_wall_detected")

    def test_captcha_page_is_not_content_ok(self):
        """Measured through the MCP tool: the same page came back
        content_ok:true, and the agent cited a captcha as an article."""
        r = _with_agent_hints(_result(url="https://weixin.sogou.com/antispider/",
                                      content=[_SOGOU_CAPTCHA]))
        annotated = _annotate_quality(_result(url="https://weixin.sogou.com/antispider/",
                                              content=[_SOGOU_CAPTCHA]))
        hinted = _with_agent_hints(annotated)
        assert hinted.content_ok is False
        assert hinted.page_type == "captcha"
        assert "do NOT cite" in hinted.next_action
        assert r.content_ok is not None  # sanity: hints ran

    def test_english_challenge_detected(self):
        r = _result(content=["Please verify you are a human. Checking your browser "
                             "before accessing the site."])
        assert _is_bot_wall(r) is True

    def test_article_about_captchas_is_not_a_wall(self):
        """The guard the old 403/503-only check relied on, kept: long prose that
        happens to mention captchas is content."""
        body = ("A history ofCompletely unrelated but lengthy discussion of " * 40)
        r = _result(content=[body + " The CAPTCHA was invented to tell humans apart."])
        assert _is_bot_wall(r) is False

    def test_login_wall_sets_page_type(self):
        """BUG-12: error said auth_wall_detected while page_type said unknown."""
        r = _result(
            url="https://www.zhihu.com/signin?next=%2F",
            content=["验证码登录 密码登录 获取短信验证码 忘记密码 登录/注册 登录"],
        )
        annotated = _with_agent_hints(_annotate_quality(r))
        assert annotated.page_type == "auth_wall"
        assert annotated.content_ok is False
        assert "login/sign-in wall" in annotated.next_action

    def test_wall_hint_reachable_without_content_ok(self):
        """The page_type block in _agent_hints only runs when content_ok is true,
        so wall advice has to come from the error branch or never fire at all."""
        summary, next_action, content_ok = _agent_hints(
            _result(error="auth_wall_detected: page is a login/sign-in wall, not content")
        )
        assert content_ok is False
        assert next_action


# ─── BUG-16: input validation returns a FetchResult ────────────────────────

class TestStructuredInputErrors:

    def test_shape_matches_the_fetch_contract(self):
        r = _invalid_request_result("", "Either 'url' or 'urls' must be provided")
        assert r.status == 0
        assert r.content == []
        assert r.content_ok is False
        assert r.error.startswith("invalid_request:")
        assert "no request was made" in r.next_action

    @pytest.mark.asyncio
    async def test_missing_url_does_not_raise(self):
        """Measured: the MCP call came back as a protocol error instead of a
        FetchResult, unlike every other web_read failure."""
        server = MasterFetchServer()
        content, structured = await server._dispatch("web_read", {})
        assert structured["error"].startswith("invalid_request:")
        assert structured["content"] == []

    @pytest.mark.asyncio
    async def test_unsupported_scheme_returns_structured_result(self):
        server = MasterFetchServer()
        result = await server.web_read(url="example.com")
        assert result.status == 0
        assert result.content == []
        assert "invalid_request" in result.error

    @pytest.mark.asyncio
    async def test_actions_in_bulk_mode_reported_per_url(self):
        server = MasterFetchServer()
        bulk = await server.web_read(
            url="", urls=["https://a.example.com", "https://b.example.com"],
            actions=[{"click": "button"}],
        )
        assert bulk.successful == 0
        assert all("invalid_request" in r.error for r in bulk.results)
        assert all(r.content == [] for r in bulk.results)


# BUG-19 的形状（`options` 袋被当成字符集合遍历）随袋子一起消失了：17.0 B4 起
# 参数只有顶层一个家，没有第二份字符串要解析。同一族事故现在由
# test_params_reach_the_tool.py（每个参数抵达）与 test_wire_arg_type_regressions.py
# （JSON-stringify 的值要认下来）守着。


# ─── BUG-8: css_selector in the default markdown mode ──────────────────────

_SELECTOR_PAGE_HTML = """
<html><head><title>Doc</title></head><body>
<nav><a href="/a">nav link a</a><a href="/b">nav link b</a></nav>
<h1>Selected heading text</h1>
<blockquote class="abstract">The abstract sentence that the caller asked for.</blockquote>
<article><p>Body paragraph that must be dropped by the selector. """ + ("Filler prose of the article body. " * 12) + """</p></article>
<footer>footer noise that goes on for a while and should not appear in the narrowed output at all</footer>
</body></html>
"""


def _fake_page(html: str):
    class FakePage:
        status = 200
        body = html.encode("utf-8")
        encoding = "utf-8"
        url = "https://example.com/doc"
        headers = {"content-type": "text/html"}
    return FakePage()


class TestCssSelectorEveryMode:

    # "html" is deliberately absent: the real pipeline routes html mode through
    # extractor.extract_content (which narrows on its own), never through this
    # function - asking for raw markup here returns prose instead.
    @pytest.mark.parametrize("mode", ["markdown", "text", "article"])
    def test_selector_narrows_the_output(self, mode):
        """Measured in markdown mode: css_selector="h1" returned the whole 183
        -char page, and "blockquote.abstract" returned 4051 chars. Only html mode
        honored it, because the narrowing path checked hasattr(page,'css') on a
        Response object that has no such method and silently fell back."""
        from dhole_mcp.trafilatura_extractor import extract_with_trafilatura
        parts = extract_with_trafilatura(_fake_page(_SELECTOR_PAGE_HTML),
                                         extraction_type=mode,
                                         css_selector="blockquote.abstract")
        text = "\n".join(parts)
        assert "abstract sentence" in text
        # The narrowing has to be visible: everything else on the page is gone.
        assert "footer noise" not in text
        assert "nav link" not in text
        assert "Body paragraph that must be dropped" not in text
        assert len(text) < 300, f"output is not narrowed: {len(text)} chars"

    @pytest.mark.parametrize("mode", ["markdown", "text"])
    def test_single_tag_selector(self, mode):
        """The report's own case: h1 must yield just the heading."""
        from dhole_mcp.trafilatura_extractor import extract_with_trafilatura
        parts = extract_with_trafilatura(_fake_page(_SELECTOR_PAGE_HTML),
                                         extraction_type=mode, css_selector="h1")
        text = "\n".join(parts)
        assert "Selected heading text" in text
        assert "abstract sentence" not in text

    def test_multiple_matches_are_all_kept(self):
        from dhole_mcp.trafilatura_extractor import extract_with_trafilatura
        parts = extract_with_trafilatura(_fake_page(_SELECTOR_PAGE_HTML),
                                         extraction_type="markdown",
                                         css_selector="a")
        text = "\n".join(parts)
        assert "nav link a" in text and "nav link b" in text

    def test_selector_matching_nothing_falls_back_to_the_page(self):
        """Existing behavior, kept: no match widens rather than returning empty."""
        from dhole_mcp.trafilatura_extractor import extract_with_trafilatura
        full = "\n".join(extract_with_trafilatura(
            _fake_page(_SELECTOR_PAGE_HTML), extraction_type="markdown", css_selector=None))
        narrowed = "\n".join(extract_with_trafilatura(
            _fake_page(_SELECTOR_PAGE_HTML), extraction_type="markdown",
            css_selector="div.does-not-exist"))
        assert narrowed.strip()
        assert narrowed == full

    def test_bad_selector_does_not_raise(self):
        from dhole_mcp.trafilatura_extractor import extract_with_trafilatura
        parts = extract_with_trafilatura(_fake_page(_SELECTOR_PAGE_HTML),
                                         extraction_type="markdown",
                                         css_selector="<<<not a selector")
        assert parts and any(p.strip() for p in parts)


# ─── BUG-15 / BUG-4 / BUG-14: article JSON + extracted_type ────────────────

class TestArticleMetadataBackfill:

    def test_author_and_date_filled_from_metadata(self):
        """Measured on a Verge article: metadata carried both, the article JSON
        returned empty strings for both."""
        body = json.dumps({"title": "T", "author": "", "date": "",
                           "body": "text", "description": "", "url": "u",
                           "categories": [], "tags": []}, indent=2)
        out = _backfill_article_json(
            [body], {"author": "Emma Roth",
                    "published_time": "2026-09-21T12:41:42+00:00"})
        data = json.loads(out[0])
        assert data["author"] == "Emma Roth"
        assert data["date"] == "2026-09-21T12:41:42+00:00"

    def test_existing_values_are_not_overwritten(self):
        body = json.dumps({"author": "Trafilatura found", "date": "2020"}, indent=2)
        out = _backfill_article_json([body], {"author": "OG person",
                                              "published_time": "2026"})
        data = json.loads(out[0])
        assert (data["author"], data["date"]) == ("Trafilatura found", "2020")

    def test_non_json_content_untouched(self):
        assert _backfill_article_json(["# Heading\nprose"], {"author": "X"}) == \
            ["# Heading\nprose"]

    def test_modified_time_used_when_no_publication_date(self):
        body = json.dumps({"author": "", "date": ""}, indent=2)
        out = _backfill_article_json([body], {"modified_time": "2026-01-02"})
        assert json.loads(out[0])["date"] == "2026-01-02"


class TestExtractedTypeLabel:

    def _page(self, html: str, url: str = "https://example.com/a"):
        class FakePage:
            status = 200
            body = html.encode("utf-8")
            encoding = "utf-8"
            headers = {"content-type": "text/html; charset=utf-8"}
        p = FakePage()
        p.url = url
        return p

    def test_article_mode_reports_article(self):
        html = ("<html><head><title>Only title</title></head><body>"
                "<article><p>" + ("Some article prose here. " * 20) + "</p></article>"
                "</body></html>")
        r = _translate_response(self._page(html), "article", None, "http", 1)
        assert r.extracted_type == "article"

    def test_markdown_fallback_is_not_labelled_article(self):
        """Saying "article" about a prose fallback is the same lie in the other
        direction; the label must describe what came back."""
        html = "<html><body><div>" + ("lorem ipsum dolor sit amet " * 30) + "</div></body></html>"
        r = _translate_response(self._page(html), "structured", None, "http", 1)
        assert r.extracted_type in ("structured", "markdown")
        if r.extracted_type == "markdown":
            assert not r.content[0].lstrip().startswith("{")

    def test_html_mode_reports_html(self):
        html = "<html><body><h1>Hi</h1></body></html>"
        r = _translate_response(self._page(html), "html", None, "http", 1)
        assert r.extracted_type == "html"


# ─── NEW-1 / NEW-2 / BUG-13: what web_search says when IT dropped the hits ─

def _raw(title: str, url: str, snippet: str = "", source: str = "bing"):
    from dhole_mcp.search_engines import RawResult
    return RawResult(title=title, url=url, snippet=snippet, source=source,
                     position=1, consensus=1, sources=(source,))


def _patch_engines(monkeypatch, results, reports=None, calls=None):
    from dhole_mcp import search as S
    from dhole_mcp.search_engines import EngineReport

    async def fake_multi_search(query, max_results, **kwargs):
        if calls is not None:
            calls.append({"query": query, "max_results": max_results, **kwargs})
        return list(results), (reports or [EngineReport(name="bing", ok=True, status="ok")])

    async def fake_get_cached(*a, **k):
        return None

    async def fake_set_cached(*a, **k):
        return None

    async def fake_ensure_reranker(*a, **k):
        return None

    monkeypatch.setattr(S, "multi_search", fake_multi_search)
    monkeypatch.setattr(S, "get_cached", fake_get_cached)
    monkeypatch.setattr(S, "set_cached", fake_set_cached)
    monkeypatch.setattr(S, "ensure_reranker", fake_ensure_reranker)
    monkeypatch.setattr(
        S, "_rank",
        lambda q, ranked, mode, **kw: (
            ranked, [1.0] * len(ranked), "merge", ""),
    )
    return S


class TestOffTopicFilterIsVisible:

    @pytest.mark.asyncio
    async def test_all_results_dropped_is_reported(self, monkeypatch):
        """Measured: bing delivered 10 usable results, dhole discarded every one,
        and the response read total_results:0 / error:"" / engines_used:["bing"]
        with next_action telling the agent to rephrase."""
        S = _patch_engines(monkeypatch, [
            _raw("Bilibili", "https://www.bilibili.com/", "视频网站"),
            _raw("Other", "https://other.example/", "unrelated text"),
        ])
        out = await S.web_search(None, "how does dns resolution work", 6)
        assert out.results == []
        assert "dhole dropped them rather than return noise" in out.error
        assert "rephrasing will not help" in out.error
        assert out.engines_used == ["bing"]

    @pytest.mark.asyncio
    async def test_partial_drop_leaves_a_note(self, monkeypatch):
        """Fewer results than the engines delivered is itself a silent-degradation
        shape unless it is said out loud."""
        rows = [_raw(f"DNS resolution doc {i}", f"https://e{i}.example/",
                     "dns resolution walkthrough") for i in range(5)]
        S = _patch_engines(monkeypatch, rows)
        scores = [1.0, 0.4, 0.3, 0.05, 0.02]
        monkeypatch.setattr(
            S, "_rank",
            lambda q, ranked, mode, **kw: (
                ranked, scores[:len(ranked)], "merge", ""),
        )
        out = await S.web_search(None, "dns resolution", 6)
        assert len(out.results) < len(rows)
        # 教训不变（部分丢弃必须留痕），句子换了形状：体验修复刀2-S4 把
        # "dropped as off-topic/low-relevance" 这一句合并账拆成逐因带数，并点名
        # 是哪把尺判的 low —— 抬 min_relevance 治不了"没命中查询词"，两因合并成
        # 一句就等于让人猜该拧哪颗旋钮。
        assert "engine result(s) dropped:" in out.fetch_hint
        assert "labelled low" in out.fetch_hint, out.fetch_hint
        assert out.error == ""

    @pytest.mark.asyncio
    async def test_a_cjk_round_is_dropped_only_with_two_signatures(self, monkeypatch):
        """旧期望「分词器只认拉丁，所以 CJK 一律放行」正是 R02-046 那条缺陷本身。

        它守的东西一条都没减，只是换了守法：**两根桩**（词面全不命中 + 模型也没说哪一行值得取）
        都在才清空整轮，而清空必须是看得见的 —— 这一类（`TestOffTopicFilterIsVisible`）
        守的一直是后半句。中文有词面这一维之后，两句都要重新钉一遍。
        """
        S = _patch_engines(monkeypatch, [_raw("标题", "https://example.com/a", "正文")])
        out = await S.web_search(None, "大模型 推理优化", 6)
        assert out.results == [], out.results
        stated = (out.fetch_hint or "") + " " + (out.error or "")
        assert "matched no query term" in stated, stated
        assert "标题" in (out.error or ""), out.error  # 丢了哪几行，调用方自己核得上

        # 同一支查询，行上带着「值得取」的判决时，词面一根桩不许单独清空整轮
        S2 = _patch_engines(monkeypatch, [_raw("大模型推理优化实践", "https://example.com/b", "正文")])
        out2 = await S2.web_search(None, "大模型 推理优化", 6)
        assert len(out2.results) == 1, out2.results


class TestSiteFilterSurvivesRewrite:

    @pytest.mark.asyncio
    async def test_rewrite_keeps_the_site_restriction(self, monkeypatch):
        """Measured: site=theverge.com + a query that found nothing returned
        trustpilot/g2/pissedconsumer results, because the rewrite round passed
        site=None and dropped the caller's explicit domain constraint."""
        calls: list[dict] = []
        S = _patch_engines(monkeypatch, [], calls=calls)
        await S.web_search(None, "Googlebooks Android review", 3, site="theverge.com")
        assert len(calls) >= 2, "expected a rewritten second round"
        assert all(c.get("site") == "theverge.com" for c in calls), \
            "the site filter must not be lifted silently on the rewrite round"

    @pytest.mark.asyncio
    async def test_empty_with_site_says_the_filter_was_respected(self, monkeypatch):
        S = _patch_engines(monkeypatch, [])
        out = await S.web_search(None, "nothing at all here", 3, site="example.invalid")
        assert out.results == []
        assert "site=example.invalid" in out.error


class TestMaxResultsClampIsVisible:

    @pytest.mark.asyncio
    async def test_out_of_range_is_announced(self, monkeypatch):
        S = _patch_engines(monkeypatch, [_raw("A", "https://a.example/")])
        out = await S.web_search(None, "dns resolution test", 100)
        # `summary`, not `fetch_hint`: the same sentence lands in the same field on
        # all three clamping tools (see search._search_summary).
        assert "max_results=100 is outside the supported 1-50 range" in out.summary


# ─── BUG-9: no invented install instructions ───────────────────────────────

class TestRerankHintWording:

    def test_no_install_advice_when_nothing_failed(self, monkeypatch):
        from dhole_mcp import search as S
        monkeypatch.setattr(S, "unavailable_reason", lambda: "")
        reason = S._rerank_absent_reason()
        assert "install dhole-mcp[all] and retry" not in reason
        assert "dhole -v" in reason

    def test_recorded_reason_is_passed_through(self, monkeypatch):
        """BUG-9 的承诺：记下的原因**逐字**交回，并且不许编安装指引。

        R02-F33 给这句话加了主语，所以「逐字」从全等改成「以原话开头」—— 这是加账不是
        降口径：三种形状（没记下耗时 / 这一发付的那次 / 引用别人付过的账）都断言原话在前、
        `install dhole-mcp[all] and retry` 不在内。顺带把这把锁自己的坑填了：耗时它从
        `reranker` 的账里读，从前那本账没钉，全等式只是**恰好**这一发没人 append 过东西
        才成立（同进程里另一条测试真跑过一次加载时，1563 ms 就挂在句子尾巴上）。
        """
        from dhole_mcp import search as S
        from dhole_mcp import reranker as R
        monkeypatch.setattr(S, "unavailable_reason", lambda: "model file missing")
        monkeypatch.setattr(R, "last_attempt_ms", lambda: 0.0)
        assert S._rerank_absent_reason() == "model file missing"
        monkeypatch.setattr(R, "last_attempt_ms", lambda: 1563.4)
        for paid_here in (True, False):
            said = S._rerank_absent_reason(paid_here)
            assert said.startswith("model file missing"), said
            assert "install dhole-mcp[all] and retry" not in said, said


# ─── BUG-1: related_queries mined from one site's boilerplate ──────────────

class TestRelatedQueries:

    def _sr(self, title, snippet, url):
        from dhole_mcp.search import SearchResult
        return SearchResult(title=title, url=url, snippet=snippet, source="bing",
                            position=1, relevance_score=1.0, fetch_relevance="high",
                            engines_consensus="1 of 1", source_type="other")

    def test_one_domain_repeating_a_phrase_is_not_a_topic(self):
        """An author bio under five articles used to mint five 'suggestions'."""
        from dhole_mcp import search as S
        rows = [self._sr(f"Article {i}", "Antonio is a reviewer covering laptops "
                         "and the occasional gadget", "https://theverge.com/a%d" % i)
                for i in range(5)]
        assert S._related_queries("antonio verge", rows) == []

    def test_two_independent_domains_do_make_a_suggestion(self):
        from dhole_mcp import search as S
        rows = [
            self._sr("GPT scoring", "tokenizer overhead dominates inference latency",
                     "https://one.example/"),
            self._sr("LLM notes", "inference latency grows with tokenizer overhead",
                     "https://two.example/"),
        ]
        out = S._related_queries("large language models", rows)
        assert any("inference latency" in p or "tokenizer overhead" in p for p in out)

    def test_sentence_fragments_are_rejected(self):
        from dhole_mcp import search as S
        rows = [
            self._sr("Bio one", "he spent years before joining The Verge as deals writer",
                     "https://one.example/"),
            self._sr("Bio two", "she worked before joining The Verge as deals writer too",
                     "https://two.example/"),
        ]
        out = S._related_queries("verge people", rows)
        assert not any(p.split()[0] in ("before", "joining", "as") for p in out)

    def test_no_results_returns_empty(self):
        from dhole_mcp import search as S
        assert S._related_queries("q", []) == []


# ─── NEW-3: a list page's next_action targets ──────────────────────────────

class TestListPageTargets:

    def test_chrome_links_are_not_offered_as_targets(self):
        """Measured on theverge.com/news: "Top targets: /, /auth/login,
        /subscribe" - the header, in DOM order, instead of the articles."""
        from dhole_mcp.server import _best_list_targets
        cits = [
            {"url": "https://www.theverge.com/", "text": "The Verge"},
            {"url": "https://www.theverge.com/auth/login?returnPath=/", "text": "Login"},
            {"url": "https://www.theverge.com/subscribe?itm_source=navigation", "text": "Subscribe"},
            {"url": "https://www.theverge.com/rss/index.xml", "text": "RSS"},
            {"url": "https://www.theverge.com/tech/998146/meta-subsea-cable", "text": ""},
            {"url": "https://www.theverge.com/policy/997805/press-credentials", "text": "credentials"},
        ]
        top = _best_list_targets(cits, "https://www.theverge.com/news")
        assert all("login" not in u and "subscribe" not in u and "rss" not in u
                   for u in top), top
        assert any("998146" in u for u in top)

    def test_anchor_typed_links_win_over_bare_ones(self):
        from dhole_mcp.server import _best_list_targets
        cits = [
            {"url": "https://ex.com/blog/2026/a-post", "text": ""},
            {"url": "https://ex.com/blog/2026/b-post", "text": "B post title"},
        ]
        top = _best_list_targets(cits, "https://ex.com/blog")
        assert top[0].endswith("b-post")

    def test_no_content_links_returns_nothing(self):
        from dhole_mcp.server import _best_list_targets
        assert _best_list_targets([{"url": "https://ex.com/", "text": "home"}],
                                 "https://ex.com/news") == []


# ─── BUG-17: engine state on disk, and the lever to clear it ───────────────

class TestEngineState:

    @pytest.fixture(autouse=True)
    def _ms(self, monkeypatch):
        from dhole_mcp import search_metasearch as ms
        monkeypatch.setattr(ms, "_BACKEND_HEALTH", {})
        monkeypatch.setattr(ms, "_CONN_FAIL_COUNTS", {})
        monkeypatch.setattr(ms, "_ENGINE_YIELD", {})
        return ms

    def test_expired_cooldowns_are_pruned_from_disk_on_load(self, _ms, tmp_path):
        """The report read circuit_breaker.json, saw timestamps from 17 minutes
        ago, and concluded the block never expires. It does - in memory. The file
        just kept lying about it."""
        import json
        import time
        path = _ms._circuit_state_file()
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"brave": time.time() - 60, "yahoo": time.time() + 600}, f)
        _ms._load_circuit_state()
        assert list(_ms._BACKEND_HEALTH) == ["yahoo"]
        on_disk = json.load(open(path, encoding="utf-8"))
        assert on_disk == {"yahoo": _ms._BACKEND_HEALTH["yahoo"]}
        assert "brave" not in on_disk

    def test_sweep_releases_and_persists(self, _ms):
        import json
        import time
        _ms._BACKEND_HEALTH.update({"brave": time.time() - 1, "bing": time.time() + 300})
        assert _ms.sweep_expired_cooldowns() == 1
        assert "brave" not in _ms._BACKEND_HEALTH
        assert "brave" not in json.load(open(_ms._circuit_state_file(), encoding="utf-8"))
        assert _ms.cooldowns().get("bing", 0) > 290

    def test_reset_clears_memory_and_both_files(self, _ms):
        import json
        import time
        _ms._record_block("brave")
        _ms._record_conn_failure("brave")
        _ms._record_conn_failure("brave")
        _ms._record_conn_failure("brave")   # -> 10 min cooldown
        _ms._ENGINE_YIELD["brave"] = {"n": 3, "status": "preempted", "ts": time.time()}
        _ms._save_engine_stats()
        info = _ms.engine_state_reset()
        assert _ms._BACKEND_HEALTH == {}
        assert _ms._ENGINE_YIELD == {}
        assert _ms.cooldowns() == {}
        assert info["released_cooldowns"].get("brave", 0) > 0
        assert info["engines_forgotten"] == 1
        assert json.load(open(_ms._circuit_state_file(), encoding="utf-8")) == {}
        assert json.load(open(_ms._engine_stats_file(), encoding="utf-8")) == {}

    def test_snapshot_carries_cooldown_seconds(self, _ms):
        import time
        _ms._BACKEND_HEALTH["brave"] = time.time() + 42.0
        _ms._ENGINE_YIELD["brave"] = {"n": 1, "status": "ok", "last": 5,
                                      "last_nodes": 5, "last_kept": 5, "mean": 5,
                                      "zero": 0, "drift": 0, "ts": time.time()}
        snap = _ms.engine_state_snapshot()
        assert 35 < snap["brave"]["cooldown_seconds_left"] <= 42
        assert snap["brave"]["verdict"] == "healthy"

    @pytest.mark.asyncio
    async def test_circuit_open_report_says_when_the_engine_is_retried(self, monkeypatch):
        """An engine skipped as "circuit open" gave no way to tell a permanent
        block from a 90-second cooldown."""
        from dhole_mcp import search_engines as se
        from dhole_mcp import search_metasearch as ms

        async def fake_meta(query, max_results, **kwargs):
            return [], {"brave": "circuit_open", "bing": "preempted"}

        monkeypatch.setattr(se, "_metasearch", fake_meta)
        monkeypatch.setattr(ms, "cooldowns", lambda: {"brave": 90.4})
        _, reports = await se.multi_search("q", 5)  # type: ignore[misc]
        by = {r.name: r for r in reports}
        assert "retried in 90s" in by["brave"].error
        assert "not a block" in by["bing"].error

    @pytest.mark.asyncio
    async def test_the_state_tool_can_reset_engine_state(self, _ms):
        _ms._record_block("brave")
        server = MasterFetchServer()
        out = await server.sys_state(clear=["engine_state"])
        assert out.engine_state_reset is True
        assert "cooldown" in out.message.lower()
        assert _ms.cooldowns() == {}
        # The snapshot is taken BEFORE the reset, so it reports what was just
        # released. It used to be taken after, which made the field provably
        # always {} - previously hidden by an "or == {}" escape hatch here.
        assert "brave" in out.engine_health, out.engine_health
        assert out.engine_health["brave"]["cooldown_seconds_left"] > 0

    def test_cli_engines_reset_clears_the_files(self, _ms):
        from dhole_mcp.ops import _cmd_engines
        _ms._record_block("brave")
        _ms._ENGINE_YIELD["brave"] = {"n": 1, "status": "ok", "ts": 0}
        assert _cmd_engines(["reset"]) == 0
        assert _ms._BACKEND_HEALTH == {}
        assert _cmd_engines(["list"]) == 0
        assert _cmd_engines(["bogus"]) == 2


# ─── NEW-4: the call budget is a wall, not a suggestion ────────────────────

class TestCallBudget:

    @pytest.mark.asyncio
    async def test_backstop_returns_a_fetch_result_inside_the_budget(self):
        """Measured: example.com took 24.1s and 25.0s on a default 30s budget
        (retries × redirect hops), and the report's slow hosts got killed by the
        MCP client (-32001) with no FetchResult at all."""
        import asyncio
        import time

        async def hang():
            await asyncio.sleep(30)

        server = MasterFetchServer()
        t0 = time.monotonic()
        out = await server._within_call_budget(hang(), "https://slow.example.com",
                                               400, "HTTP tier")
        took = time.monotonic() - t0
        assert took < 3.0, f"budget not enforced: {took:.1f}s"
        assert out.status == 0
        assert out.content == []
        assert out.content_ok is False
        assert "timeout" in out.error and "HTTP tier" in out.error
        assert "Raise timeout" in out.next_action

    @pytest.mark.asyncio
    async def test_escalation_reports_which_tier_ran_out(self, monkeypatch):
        import asyncio

        async def slow_get(*args, **kwargs):
            await asyncio.sleep(5)
            return _result()

        import dhole_mcp.fetcher as fetcher
        monkeypatch.setattr(fetcher, "tcp_preflight", lambda url, timeout=2.0: (True, ""))
        server = MasterFetchServer()
        server.get = slow_get
        out = await _escalate(server, "https://slow.example.com", 800)
        assert out.status == 0
        assert out.content == []
        assert "HTTP tier" in out.error
        assert out.duration_ms < 3000

    @pytest.mark.asyncio
    async def test_http_timeout_cannot_exceed_the_caller_budget(self, monkeypatch):
        """_adaptive_timeout learns [5s, 60s]; a 1s call must not inherit 60s."""
        import asyncio

        seen: dict = {}

        async def capture_get(*args, **kwargs):
            seen["timeout"] = kwargs.get("timeout")
            await asyncio.sleep(5)
            return _result()

        import dhole_mcp.fetcher as fetcher
        monkeypatch.setattr(fetcher, "tcp_preflight", lambda url, timeout=2.0: (True, ""))
        monkeypatch.setattr("dhole_mcp.server._adaptive_timeout",
                            lambda url, default_ms: 60000)
        server = MasterFetchServer()
        server.get = capture_get
        await _escalate(server, "https://slow.example.com", 1500)
        assert seen.get("timeout", 99) <= 2


# ─── BUG-10 / BUG-12: parse path resolution and envelope ───────────────────

class TestParsePathResolution:

    def test_relative_path_resolves_against_cwd(self, tmp_path, monkeypatch):
        (tmp_path / "report.csv").write_text("a,b\n1,2\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        target, tried = _resolve_local_path("report.csv")
        assert target == str(tmp_path / "report.csv")
        assert tried[0] == target

    def test_retired_workdir_knob_changes_nothing(self, tmp_path, monkeypatch):
        """17.0：``DHOLE_WORKDIR`` 已注销 —— 设了它不报错，也不许参与解析。
        相对路径的每次调用入口只剩 ``cwd`` 参数一个。"""
        work = tmp_path / "project"
        work.mkdir()
        (work / "notes.html").write_text("<h1>x</h1>", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DHOLE_WORKDIR", str(work))
        target, tried = _resolve_local_path("notes.html")
        assert target == ""
        assert not any(p.startswith(str(work)) for p in tried), "退役旋钮不许进入候选路径"

        target, _ = _resolve_local_path("notes.html", cwd=str(work))
        assert target == str(work / "notes.html")

    def test_miss_lists_every_directory_tried(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        target, tried = _resolve_local_path("nope.csv")
        assert target == ""
        assert any(p.endswith("nope.csv") for p in tried)
        assert len(tried) >= 2

    def test_absolute_path_that_exists(self, tmp_path):
        f = tmp_path / "x.csv"
        f.write_text("a\n", encoding="utf-8")
        target, _ = _resolve_local_path(str(f))
        assert target == str(f)

    def test_cwd_argument_wins_over_every_other_root(self, tmp_path, monkeypatch):
        """cwd 是唯一不依赖 host 的通道，所以它必须最优先。

        背景：宿主进程的 cwd 是它自己的安装目录（实测 D:\\Program Files\\Qoder），
        MCP roots 在 SDK 里已弃用（SEP-2577），``DHOLE_WORKDIR`` 在 17.0 注销。
        剩下「agent 从 system prompt 读到工作目录再传进来」这条路 —— 传了就必须
        赢过进程 cwd 和 home，否则这个参数等于没加。
        """
        session = tmp_path / "session"
        elsewhere = tmp_path / "elsewhere"
        for d, body in ((session, "session\n"), (elsewhere, "elsewhere\n")):
            d.mkdir()
            (d / "notes.csv").write_text(body, encoding="utf-8")
        monkeypatch.chdir(elsewhere)
        target, tried = _resolve_local_path("notes.csv", cwd=str(session))
        assert target == str(session / "notes.csv")
        assert tried[0] == target, "cwd 的候选必须排在最前，否则报错信息会误导"

    def test_cwd_without_the_file_falls_through_and_is_still_listed(self, tmp_path, monkeypatch):
        """cwd 指错地方不是错误 —— 继续按既有顺序往下找，并且把试过的路径都列出来。"""
        here = tmp_path / "here"
        here.mkdir()
        (here / "notes.html").write_text("<h1>x</h1>", encoding="utf-8")
        empty = tmp_path / "empty"
        empty.mkdir()
        monkeypatch.chdir(here)
        target, tried = _resolve_local_path("notes.html", cwd=str(empty))
        assert target == str(here / "notes.html")
        assert str(empty / "notes.html") in tried

    def test_absolute_path_ignores_cwd(self, tmp_path):
        """绝对路径不受 cwd 影响（cwd 只描述相对路径的基准）。"""
        f = tmp_path / "x.csv"
        f.write_text("a\n", encoding="utf-8")
        target, tried = _resolve_local_path(str(f), cwd=str(tmp_path / "nowhere"))
        assert target == str(f)
        assert tried == [str(f)]

    def test_nonexistent_cwd_is_not_fatal(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "here.csv").write_text("a\n", encoding="utf-8")
        target, _ = _resolve_local_path("here.csv", cwd=str(tmp_path / "deleted-mid-session"))
        assert target == str(tmp_path / "here.csv")

    def test_blocked_prefix_still_applies(self, tmp_path, monkeypatch):
        monkeypatch.setattr(os.path, "expanduser", lambda p: str(tmp_path / p.replace("~/", "")))
        assert _blocked_path_prefix(str(tmp_path / ".ssh" / "id_rsa")) != ""
        assert _blocked_path_prefix(str(tmp_path / "ok.csv")) == ""


class TestParseEnvelope:

    @pytest.mark.asyncio
    async def test_success_fills_the_stats_fields(self, tmp_path, monkeypatch):
        """Measured: a successful parse returned content with data while
        total_extracted_chars, summary, content_type and fetched_at were empty."""
        f = tmp_path / "doc.html"
        f.write_text("<html><head><title>T</title></head><body><h1>Heading</h1>"
                     "<p>Paragraph body text.</p></body></html>", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        server = MasterFetchServer()
        r = await server.doc_parse("doc.html")
        assert r.status == 200
        assert r.content_ok is True
        assert r.total_extracted_chars == len("\n".join(r.content)) > 0
        assert r.content_type == "text/html"
        assert r.fetched_at
        assert r.summary
        assert r.duration_ms >= 0
        assert r.url.startswith("file:///")

    @pytest.mark.asyncio
    async def test_failure_says_what_was_tried(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        server = MasterFetchServer()
        r = await server.doc_parse("missing.html")
        assert r.status == 0
        assert r.content == []
        assert "File not found" in r.error
        assert "missing.html" in r.error
        # 17.0：报错不再提 DHOLE_WORKDIR（旋钮已注销），只给真正的入口 cwd 参数
        assert "DHOLE_WORKDIR" not in r.error
        assert "cwd=" in r.error

    @pytest.mark.asyncio
    async def test_cwd_argument_changes_which_file_is_parsed(self, tmp_path, monkeypatch):
        """端到端：同一个相对名，两个目录里内容不同 —— cwd 决定读到哪一份。

        这是这个参数存在的全部意义（宿主进程 cwd 是安装目录时，相对路径本无解），
        所以断言正文而不只是「没报错」。
        """
        session = tmp_path / "session"
        session.mkdir()
        (session / "doc.html").write_text(
            "<html><body><h1>Session copy</h1><p>Body text from the session dir.</p>"
            "</body></html>", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        server = MasterFetchServer()
        r = await server.doc_parse("doc.html", cwd=str(session))
        assert r.status == 200, r.error
        assert "Session copy" in "\n".join(r.content)
        assert r.url.startswith("file:///")


# ─── KB-9: 410 真的走 archive 回退（元组与 gate 不许再漂移） ────────────────

class TestArchiveFallback:

    def test_every_fallback_status_is_accepted_by_the_gate(self):
        """元组里的每个状态都必须过 gate：被 gate 拒绝的状态 = 永远走不到的分支。

        KB-9：410 曾列在元组里而 ``_should_try_archive()`` 一直返回 False，
        于是 410 Gone 的页面从不去 Wayback 找快照，与代码的字面意图不符。
        """
        for status in _ARCHIVE_FALLBACK_STATUSES:
            assert _should_try_archive(_result(status=status, content=[])), (
                f"{status} 在 _ARCHIVE_FALLBACK_STATUSES 里却被 gate 拒绝")

    def test_network_failure_is_not_short_circuited_to_archive(self):
        """status=0 不在元组里：它该升级到浏览器层，而不是在这里拿旧快照了事。"""
        assert 0 not in _ARCHIVE_FALLBACK_STATUSES
        assert _should_try_archive(
            _result(status=0, content=[], fetcher_used="none",
                    error="network_error: dns_failure (TCP preflight)"))

    @pytest.mark.asyncio
    async def test_410_is_answered_from_the_archive(self, monkeypatch):
        """端到端：HTTP 层拿到 410 时，回退确实会去取 archive 快照。"""
        import dhole_mcp.fetcher as fetcher
        monkeypatch.setattr(fetcher, "tcp_preflight", lambda url, timeout=2.0: (True, ""))
        server = MasterFetchServer()

        async def gone(*args, **kwargs):
            return _result(status=410, content=[], fetcher_used="http",
                           error="HTTP status 410")

        server.get = gone
        asked: list[str] = []

        async def fake_archive(url, *args, **kwargs):
            asked.append(url)
            return _result(status=200, content=["Archived snapshot body."],
                           fetcher_used="archive.org", url=url)

        monkeypatch.setattr(server, "_fetch_from_archive", fake_archive)
        out = await _escalate(server, "https://gone.example.com/x", 8000)
        assert asked == ["https://gone.example.com/x"], "410 没走到 archive 回退"
        assert out.content == ["Archived snapshot body."]
        assert out.content_ok is True


# ============================================================================
# 来源：test_bug_report2_regressions.py
# ============================================================================
# Regressions for the second external test report (dhole_fix_prompt.md).

# The helpers under test are reached through the module, not by name: a name
# import of something a fix ADDS makes the whole file fail collection against
# the pre-fix revision, which hides which tests are actually red.
_safe_cookie_dict = server_mod._safe_cookie_dict

# A page whose anchors are assets and links, same-domain, no assets to trip the
# crawler's asset filter.
_LINKED_PAGE = (
    "<html><head><title>Start</title></head><body>"
    "<p>" + "Real body text so the extractor has something to chew on. " * 3 + "</p>"
    '<a href="/a">Alpha</a> <a href="/b">Beta</a> <a href="/c">Gamma</a>'
    "</body></html>"
)


class _Stop(Exception):
    """Aborts a stubbed fetch tier once the call under test has been observed."""


def _stub_crawl(monkeypatch, **kwargs):
    """Run a real web_crawl against a stubbed fetch tier (no network)."""
    async def fake_smart_fetch(self, url=None, **ignored):
        return server_mod.ResponseModel(
            url=url or "", status=200, content=[_LINKED_PAGE],
            fetcher_used="http", content_ok=True, content_type="text/html",
        )

    monkeypatch.setattr(server_mod.MasterFetchServer, "web_read", fake_smart_fetch)
    srv = server_mod.MasterFetchServer()
    return asyncio.run(srv.web_crawl("https://example.com/", max_pages=5, cache_ttl=0, **kwargs))


class TestCookieShapes:
    """report BUG-5: ``cookies="sessionid=abc123"`` put no cookie on
    the wire at all - the string was iterated character by character, every
    entry was dropped, and the call still reported 200 + ``content_ok``.
    """

    def test_cookie_header_string_becomes_a_dict(self):
        assert _safe_cookie_dict("sessionid=abc123") == {"sessionid": "abc123"}

    def test_multiple_cookie_pairs(self):
        assert _safe_cookie_dict("a=1; b=2") == {"a": "1", "b": "2"}

    def test_plain_dict_and_documented_list_still_work(self):
        assert _safe_cookie_dict({"a": "b"}) == {"a": "b"}
        assert _safe_cookie_dict(
            [{"name": "n", "value": "v", "domain": "example.com"}]) == {"n": "v"}

    def test_junk_is_dropped_rather_than_raising(self):
        assert _safe_cookie_dict("not a cookie") is None
        assert _safe_cookie_dict("") is None

    def test_browser_tier_scopes_string_cookies_to_the_target_url(self):
        """The browser needs a domain; a Cookie header carries none."""
        assert server_mod._browser_cookies("a=1", "https://example.com/p") == [
            {"name": "a", "value": "1", "url": "https://example.com/p"}]

    def test_browser_tier_keeps_a_caller_supplied_list_untouched(self):
        """A list already carries its own domain/path scope - do not rewrite it."""
        jar = [{"name": "n", "value": "v", "domain": ".example.com"}]
        assert server_mod._browser_cookies(jar, "https://example.com/p") is jar


class TestUserAgentOverride:
    """report BUG-4: ``useragent`` was accepted and ignored - the HTTP
    tier always sent browserforge's rotating UA. The browser tier honoured it,
    so the same option worked or not depending on which tier answered.
    """

    @staticmethod
    def _headers(headers=None, useragent=None, stealthy=True):
        from dhole_mcp.fetcher import HTTPSession

        return HTTPSession(stealthy_headers=stealthy)._build_headers(headers, useragent)

    def test_explicit_useragent_replaces_the_generated_one(self):
        got = self._headers(useragent="DholeTestBot/1.0")
        assert [k for k in got if k.lower() == "user-agent"] == ["user-agent"]
        assert got["user-agent"] == "DholeTestBot/1.0"

    def test_extra_headers_user_agent_does_not_ride_along_as_a_second_header(self):
        """Header names are case-insensitive: a caller's "User-Agent" must
        replace browserforge's "user-agent", not sit next to it."""
        got = self._headers(headers={"User-Agent": "FromHeaders/1.0"})
        assert [v for k, v in got.items() if k.lower() == "user-agent"] == ["FromHeaders/1.0"]

    def test_the_option_outranks_extra_headers(self):
        got = self._headers(headers={"User-Agent": "FromHeaders/1.0"},
                            useragent="FromOption/1.0")
        assert [v for k, v in got.items() if k.lower() == "user-agent"] == ["FromOption/1.0"]

    def test_non_stealthy_session_still_honours_it(self):
        assert self._headers(useragent="Plain/1.0", stealthy=False) == {"user-agent": "Plain/1.0"}


class TestOptionsReachTheHttpTier:
    """report BUG-4/BUG-5 wiring: the option has to reach the HTTP request
    builder, not just the browser context."""

    def test_force_fetch_http_forwards_cookies_and_useragent(self, monkeypatch):
        from dhole_mcp import server as server_mod

        seen: dict = {}

        async def fake_get(url, **kwargs):
            seen.update(kwargs)
            raise _Stop()

        monkeypatch.setattr(server_mod.MasterFetchServer, "get", staticmethod(fake_get))
        srv = server_mod.MasterFetchServer()
        with pytest.raises(_Stop):
            asyncio.run(srv._force_fetch(
                url="https://example.com/p", force_fetcher="http",
                extraction_type="markdown", css_selector=None,
                cache_ttl=0,
                # 13d：渲染时机不再是参数（wait / network_idle 两颗删），这一层收的
                # 自变量随之少两个；本条钉的是 cookies/useragent 能不能走到 HTTP 层。
                offset=0, headless=True, real_chrome=False, proxy=None,
                timeout=30000, solve_cloudflare=True,
                block_webrtc=True, hide_canvas=True, extra_headers=None,
                useragent="DholeTestBot/1.0", cookies="sessionid=abc123",
            ))
        assert seen["cookies"] == {"sessionid": "abc123"}
        assert seen["useragent"] == "DholeTestBot/1.0"


class TestSchemaScalarAndAttribute:
    """report BUG-3 + BUG-9.

    BUG-3's diagnosis (a parser that chokes on large/nested HTML) was wrong: the
    same tree answers ``type: array`` fine, and ``.score`` works - what broke was
    the scalar path returning elements[0], whose text is empty whenever the first
    match is an image-only link. That is exactly the HN/iana repro.

    BUG-9: ``attribute`` was documented in neither schema path and implemented in
    neither.
    """

    _HTML = (
        "<html><body>"
        '<a href="/home"><img src="logo.png" alt="Home"></a>'
        '<a href="/next">Read more</a>'
        '<a href="/last">Last</a>'
        "</body></html>"
    )

    def _extract(self, **field):
        from dhole_mcp.structured import extract_structured

        return extract_structured(self._HTML, {"properties": {"x": field}})["x"]

    def test_scalar_selector_skips_an_image_only_first_match(self):
        """The first <a> wraps an <img>, so its text_content() is empty."""
        assert self._extract(selector="a") == "Read more"

    def test_text_is_still_returned_when_the_first_match_has_text(self):
        """The report's working case (example.com) must not regress."""
        from dhole_mcp.structured import extract_structured

        html = ('<html><body><a href="https://iana.org/domains/example">'
                "Learn more</a></body></html>")
        got = extract_structured(html, {"properties": {"x": {"selector": "a"}}})
        assert got["x"] == "Learn more"

    def test_attribute_returns_the_attribute_value(self):
        assert self._extract(selector="a", attribute="href") == "/home"

    def test_attribute_works_with_arrays(self):
        assert self._extract(selector="a", attribute="href", type="array") == [
            "/home", "/next", "/last"]

    def test_array_of_texts_still_works(self):
        assert self._extract(selector="a", type="array") == ["Read more", "Last"]

    def test_missing_attribute_falls_back_to_empty(self):
        assert self._extract(selector="a", attribute="data-nope") == ""

    def test_count_is_unaffected_by_attribute(self):
        assert self._extract(selector="a", type="count", attribute="href") == 3


class TestCrawlPathFilterAcceptsAString:
    """report BUG-6: ``path_exclude="/what/"`` was iterated character by
    character (``for p in path_exclude``), and ``startswith("/")`` holds for
    every path - so a string filter silently filtered the whole crawl away while
    the list form crawled normally.
    """

    def test_string_filter_does_not_drop_every_link(self, monkeypatch):
        result = _stub_crawl(monkeypatch, path_exclude="/what/")
        assert result.pages_crawled > 1, "一个字符串把整站都过滤掉了"

    def test_string_and_list_agree(self, monkeypatch):
        as_string = _stub_crawl(monkeypatch, path_exclude="/what/")
        as_list = _stub_crawl(monkeypatch, path_exclude=["/what/"])
        assert as_string.pages_crawled == as_list.pages_crawled == 4

    def test_the_filter_still_filters(self, monkeypatch):
        result = _stub_crawl(monkeypatch, path_exclude=["/a"])
        assert result.pages_crawled == 3
        assert not any(p.url.endswith("/a") for p in result.pages)
        assert any(p.url.endswith("/b") for p in result.pages)

    def test_string_include_only_keeps_matching_paths(self, monkeypatch):
        """The include filter had the same bug, in the opposite direction: the
        char-iterated string matched every path, so the filter was a no-op."""
        result = _stub_crawl(monkeypatch, path_include="/a")
        assert result.pages_crawled == 2
        assert not any(p.url.endswith("/b") for p in result.pages)


class TestCrawlSearchOptionAndSummary:
    """report BUG-7: ``search`` was documented for web_crawl but a call that
    carried it was rejected as an unsupported key.

    Found alongside it: when the filter matched nothing, the summary kept the
    PRE-filter stats - "crawled 1 page(s) ... 1 content_ok" next to
    ``pages_crawled: 0`` and ``pages: []``.
    """

    @staticmethod
    def _dispatched_search(monkeypatch, args):
        from dhole_mcp import server as server_mod
        from dhole_mcp.crawl import CrawlResponseModel

        seen: dict = {}

        async def fake_crawl(self, url=None, **kwargs):
            seen.update(kwargs)
            return CrawlResponseModel(start_url=url or "", pages=[])

        monkeypatch.setattr(server_mod.MasterFetchServer, "web_crawl", fake_crawl)
        asyncio.run(server_mod.MasterFetchServer()._dispatch("web_crawl", args))
        return seen.get("search")

    def test_search_is_accepted(self, monkeypatch):
        assert self._dispatched_search(monkeypatch, {
            "url": "https://example.com/", "search": "interpreter",
        }) == "interpreter"

    def test_filtered_away_summary_describes_the_pages_returned(self, monkeypatch):
        """report 的本意：summary 描述**实际交回了什么**。

        R01-006 轮改判据说明（2026-10-25 review/round-01）：旧断言钉的
        「crawled 0 page(s) … stopped: max_pages reached」正是被评的缺陷——没货
        读起来像爬满了（P02/P05 照它去抬 max_pages 白跑）。新 summary 说的是同一
        件事的诚实版：爬了几页、0 页带词、词长在正文里。
        """
        result = _stub_crawl(monkeypatch, search="nomatch")
        assert result.pages == [] and result.pages_crawled == 0
        assert "matched 0 of" in result.summary, result.summary
        assert "crawled page(s)" in result.summary, result.summary
        assert "max_pages reached" not in result.summary, result.summary
        assert "nomatch" in result.next_action


class TestLocalPdfReportsItsEnvelope:
    """report BUG-8: parse returned ``table_of_contents: []`` and ``metadata: {}``
    for a local PDF while web_read on the same PDF by URL returned both - the
    extractor already produced them and the parse path dropped them.
    """

    @pytest.fixture
    def pdf_with_outline(self, tmp_path):
        pytest.importorskip("pypdf")
        from pypdf import PdfReader, PdfWriter

        reader = PdfReader(str(Path(__file__).parent / "background_checks.pdf"))
        writer = PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        writer.add_outline_item("Intro", 0)
        writer.add_metadata({"/Producer": "dhole-test"})
        out = tmp_path / "outlined.pdf"
        with open(out, "wb") as fh:
            writer.write(fh)
        return out

    def test_parse_file_detailed_returns_toc_and_metadata(self, pdf_with_outline):
        pytest.importorskip("pdfplumber")
        from dhole_mcp.parse import parse_file_detailed

        content, error, extras = parse_file_detailed(str(pdf_with_outline))
        assert error == "" and content
        assert extras["table_of_contents"][0]["title"] == "Intro"
        assert extras["table_of_contents"][0]["page"] == 1
        assert extras["metadata"]["producer"] == "dhole-test"
        assert extras["quality_score"] > 0
        assert extras["content_ok"] is True

    def test_parse_file_keeps_its_two_tuple(self, pdf_with_outline):
        pytest.importorskip("pdfplumber")
        from dhole_mcp.parse import parse_file

        content, error = parse_file(str(pdf_with_outline))
        assert error == "" and content

    def test_the_response_envelope_carries_them(self, pdf_with_outline):
        pytest.importorskip("pdfplumber")

        out = asyncio.run(MasterFetchServer().doc_parse(file_path=str(pdf_with_outline)))
        assert out.table_of_contents[0]["title"] == "Intro"
        assert out.metadata["producer"] == "dhole-test"
        assert out.quality_score > 0
        # Carrying quality_score flips _agent_hints onto the "defer to the
        # extractor" branch, so content_ok has to travel with it: a healthy PDF
        # reported content_ok=False here, which reads to an agent as "do not cite".
        assert out.content_ok is True


class TestNoPerHostLane:
    """17.0 §6 删掉 reddit 特例，这一类钉的是"没有按 host 分叉的泳道"这件事本身。

    删掉的两样行为都是按 host 的例外：listing 的 URL 被改写成 old.reddit.com（少 7x
    字节），以及 reddit 跳过 HTTP 直接进 stealthy（省约 1 秒）。它们是这条链上**唯一**
    一组"某家主机自己占一条泳道"的例子，而例子消失之后最容易发生的事，就是下一个特例
    在没人注意的时候长出来。所以这里守的是形状：URL 原样送下去、人人都走
    `_auto_escalate`，而显式的 `force_fetcher` 仍然是最后一句。
    """

    def _lane(self, monkeypatch, url, **kw):
        """Which tier lane the call took, and the URL it was handed."""
        seen: dict = {}

        async def auto(self, u, extraction_type, *a, **k):
            seen["lane"] = "auto_escalate"
            seen["url"] = u
            raise _Stop()

        async def forced(self, u, force_fetcher, *a, **k):
            seen["lane"] = f"force:{force_fetcher}"
            seen["url"] = u
            raise _Stop()

        monkeypatch.setattr(MasterFetchServer, "_auto_escalate", auto)
        monkeypatch.setattr(MasterFetchServer, "_force_fetch", forced)
        with pytest.raises(_Stop):
            asyncio.run(MasterFetchServer().web_read(url=url, **kw))
        return seen

    def test_a_reddit_listing_takes_everyones_lane_with_its_own_url(self, monkeypatch):
        asked = "https://www.reddit.com/r/Python/"
        seen = self._lane(monkeypatch, asked)
        assert seen["lane"] == "auto_escalate", \
            f"reddit 又占了单独一条泳道：{seen['lane']}"
        assert seen["url"] == asked, f"URL 在路上被改写过：{seen['url']}"

    def test_an_explicit_pin_is_still_the_last_word(self, monkeypatch):
        asked = "https://old.reddit.com/r/Python/"
        seen = self._lane(monkeypatch, asked, force_fetcher="http")
        assert seen["lane"] == "force:http"
        assert seen["url"] == asked


class TestActionsTierBudget:
    """report BUG-2: actions force the stealthy tier but inherited the 30s
    HTTP-era default, so a cold browser start spent the whole budget on
    example.com (measured 30012ms).
    """

    def _budget_for_actions(self, monkeypatch, **kwargs):
        from dhole_mcp import server as server_mod

        seen: dict = {}

        async def fake_force_fetch(self, url, force_fetcher, **kw):
            # 本题只问预算：把 _force_fetch 的整串形参抄一遍（那时 23 个）等于让这条
            # 测试在任何一次无关的签名改动上变红 —— 13d 删掉 wait / network_idle
            # 就是这样。值从 kw 里取，签名再怎么变也只红在真正相关的那一行。
            seen["timeout"] = kw["timeout"]
            raise _Stop()

        monkeypatch.setattr(server_mod.MasterFetchServer, "_force_fetch", fake_force_fetch)
        with pytest.raises(_Stop):
            asyncio.run(server_mod.MasterFetchServer().web_read(
                url="https://example.com/", actions=[{"scroll": 300}], **kwargs))
        return seen["timeout"]

    def test_actions_get_a_host_safe_default(self, monkeypatch):
        # 批次13：动作链默认 25000 —— 宿主的 MCP 超时普遍 30s 且砍掉时什么都不回
        # （实测 web_act 首调死于裸 "Tool execution timed out"）。内部预算必须赶在
        # 宿主动手之前返回结构化超时；慢页面是调用方显式抬高 timeout 的事。
        assert self._budget_for_actions(monkeypatch) == 25000

    def test_an_explicit_timeout_still_wins(self, monkeypatch):
        assert self._budget_for_actions(monkeypatch, timeout=12345) == 12345

    def test_plain_fetches_keep_the_documented_30s(self, monkeypatch):
        from dhole_mcp import server as server_mod

        seen: dict = {}

        async def fake_auto_escalate(self, url, extraction_type, **kw):
            seen["timeout"] = kw["timeout"]
            raise _Stop()

        monkeypatch.setattr(server_mod.MasterFetchServer, "_auto_escalate", fake_auto_escalate)
        with pytest.raises(_Stop):
            asyncio.run(server_mod.MasterFetchServer().web_read(url="https://example.com/"))
        assert seen["timeout"] == 30000

    def test_the_dispatcher_leaves_the_default_to_smart_fetch(self, monkeypatch):
        """The MCP layer used to substitute 30000 itself, which is why the
        actions default could never take effect."""
        from dhole_mcp import server as server_mod

        seen: dict = {}

        async def fake_smart_fetch(self, **kwargs):
            seen.update(kwargs)
            return ResponseModel(url=kwargs.get("url", ""), status=0, content=[],
                                 error="stub")

        monkeypatch.setattr(server_mod.MasterFetchServer, "web_read", fake_smart_fetch)
        asyncio.run(server_mod.MasterFetchServer()._dispatch(
            "web_read", {"url": "https://example.com/"}))
        # Absent or None both mean the same thing here — the tool decides — and the
        # dispatcher now leaves it absent rather than handing over an explicit None.
        # What must never come back is a number substituted at this layer.
        assert seen.get("timeout") is None


class TestParseDecodesLegacyEncodings:
    """report BUG-3 (P0): parse read .csv/.html as UTF-8 with errors="replace",
    so a GBK file - Excel's default "CSV (逗号分隔)" export on a Chinese Windows
    box - came back as U+FFFD soup while content_ok stayed true and error stayed
    empty. Reproduced live through the MCP server on a 90-byte CSV:
    "| ���� | ���� |" with content_ok=true.

    The fix has to hold three separate lines, so there are three groups below:
    the bytes decode to the right text, a guess that went wrong is visible, and
    the caller can correct it.
    """

    GBK_CSV = "姓名,年龄,备注\n张三,30,工程师\n李四,25,设计师\n"

    def _write(self, tmp_path, name, text, encoding):
        path = tmp_path / name
        path.write_bytes(text.encode(encoding))
        return path

    # ── the bytes decode to the right text ──────────────────────────────

    def test_gbk_csv_is_not_mojibake(self, tmp_path):
        from dhole_mcp.parse import parse_file_detailed

        path = self._write(tmp_path, "gbk.csv", self.GBK_CSV, "gbk")
        content, error, extras = parse_file_detailed(str(path))
        assert error == ""
        # Exact text, not just "no replacement char": a decode that silently
        # produced the wrong CJK characters would pass a weaker assertion.
        assert "| 姓名 | 年龄 | 备注 |" in content
        assert "| 张三 | 30 | 工程师 |" in content
        assert "\ufffd" not in content
        assert extras["encoding"] == "gb18030"
        assert "decode_damage" not in extras

    def test_gbk_html_is_not_mojibake(self, tmp_path):
        pytest.importorskip("trafilatura")
        from dhole_mcp.parse import parse_file_detailed

        html = ("<html><body><h1>标题</h1>"
                "<p>这是一段中文正文，用来验证解码是否正确。</p></body></html>")
        path = self._write(tmp_path, "gbk.html", html, "gbk")
        content, error, extras = parse_file_detailed(str(path))
        assert error == ""
        assert "这是一段中文正文" in content
        assert "\ufffd" not in content

    def test_utf8_is_untouched(self, tmp_path):
        from dhole_mcp.parse import parse_file_detailed

        path = self._write(tmp_path, "utf8.csv", self.GBK_CSV, "utf-8")
        content, error, extras = parse_file_detailed(str(path))
        assert error == "" and "| 姓名 | 年龄 | 备注 |" in content
        assert extras["encoding"] == "utf-8"

    def test_a_bom_outranks_detection(self, tmp_path):
        from dhole_mcp.parse import parse_file_detailed

        for enc, expected in (("utf-8-sig", "utf-8-sig"), ("utf-16", "utf-16"),
                              ("utf-32", "utf-32")):
            path = self._write(tmp_path, f"bom-{expected}.csv", self.GBK_CSV, enc)
            content, error, extras = parse_file_detailed(str(path))
            assert error == "", f"{enc}: {error}"
            assert "姓名" in content, f"{enc} decoded to {content!r}"
            assert extras["encoding"] == expected

    def test_utf16_without_a_bom_is_still_read(self, tmp_path):
        """A stripped BOM used to mean GB18030 "succeeded" on the NUL bytes and
        returned text full of them - damage 0, content garbage."""
        from dhole_mcp.parse import parse_file_detailed

        path = self._write(tmp_path, "nobom.csv", self.GBK_CSV, "utf-16-le")
        content, error, extras = parse_file_detailed(str(path))
        assert error == "" and "姓名" in content
        assert "\x00" not in content

    def test_semicolon_delimited_csv_keeps_its_columns(self, tmp_path):
        """Excel in a comma-decimal locale writes ';'. Parsed as comma-
        separated, the whole row lands in column 1 - silently wrong, not empty.
        """
        from dhole_mcp.parse import parse_file_detailed

        path = self._write(tmp_path, "eu.csv", "姓名;年龄\n张三;30\n", "gbk")
        content, error, _ = parse_file_detailed(str(path))
        assert error == ""
        assert "| 姓名 | 年龄 |" in content

    # ── a wrong guess is visible, not silent ────────────────────────────

    def test_a_damaged_decode_is_reported_not_returned_as_content(self, tmp_path):
        """Big5 decoded as GB18030 comes out wrong. Measured: GB18030 maps the
        Big5 byte pairs into the Private Use Area, which is what makes it
        detectable - and a flagged answer beats a confident wrong one.
        """
        from dhole_mcp.parse import parse_file_detailed

        path = self._write(tmp_path, "big5.csv", "姓名,年齡,備註\n張三,30\n", "big5")
        _, _, extras = parse_file_detailed(str(path))
        assert extras["decode_damage"] > 0, "Big5-as-GB18030 should be flagged"

    def test_the_response_says_do_not_trust_this(self, tmp_path):
        from dhole_mcp.parse import parse_file_detailed  # noqa: F401

        path = self._write(tmp_path, "big5.csv", "姓名,年齡,備註\n張三,30\n", "big5")
        out = asyncio.run(MasterFetchServer().doc_parse(file_path=str(path)))
        assert out.error.startswith("encoding_undecodable")
        assert out.content_ok is False
        # The content still travels - it is what the caller needs to look at -
        # but status stays 200 because the file itself was read fine.
        assert out.content and out.status == 200
        # Without this branch the generic parse hint fired and sent the caller
        # off to check paths that were never the problem.
        assert "encoding=" in out.next_action

    def test_a_clean_decode_is_not_flagged(self, tmp_path):
        path = self._write(tmp_path, "gbk.csv", self.GBK_CSV, "gbk")
        out = asyncio.run(MasterFetchServer().doc_parse(file_path=str(path)))
        assert out.error == ""
        assert out.content_ok is True
        assert out.metadata["encoding"] == "gb18030"

    def test_an_unreadable_file_still_returns_no_content(self, tmp_path):
        """The damaged-decode branch keeps its content; a real failure must not
        start leaking an empty string as content."""
        out = asyncio.run(MasterFetchServer().doc_parse(file_path=str(tmp_path / "nope.csv")))
        assert out.status == 0 and out.content == [] and out.content_ok is False

    def test_prose_that_QUOTES_mojibake_is_not_an_undecodable_file(self, tmp_path):
        """A damage COUNT cannot tell a wrong charset from text that shows what a
        wrong charset looks like. Measured on this repo's own CHANGELOG: it
        documents the `â€™` failure mode once, and a 43k-character file that
        decodes byte-perfect as UTF-8 came back `encoding_undecodable` with
        content_ok=false - a false accusation against a clean file, and one the
        caller can do nothing about, since `encoding=` cannot un-quote it.
        """
        from dhole_mcp.parse import parse_file_detailed

        path = tmp_path / "quotes.md"
        path.write_bytes(("本仓库的更新日志正文。" * 400
                          + " 反例写法：â€™ 两个字符。\n").encode("utf-8"))
        content, error, extras = parse_file_detailed(str(path))
        assert extras["encoding"] == "utf-8"
        assert extras.get("decode_damage", 0) == 0, \
            "one quoted marker is not evidence that the charset guess was wrong"
        assert error == "" and "â€™" in content

        out = asyncio.run(MasterFetchServer().doc_parse(file_path=str(path)))
        assert out.content_ok is True and out.error == ""

    def test_a_file_that_MOSTLY_is_mojibake_is_still_flagged(self, tmp_path):
        """The bound the other way, on the rule itself: a real misdecode hits
        every non-ASCII character, so its markers are a share of the document
        rather than a quotation.

        Measured, not assumed: this cannot be tested through a file, because
        UTF-8 bytes of double-encoded Latin text decode as GB18030 into
        plausible Chinese with zero damage - the boundary charset.py documents
        (Shift_JIS/EUC-KR behave the same way), which is what `encoding=` is
        for. The marker branch decides the VERDICT, so that is where it is
        tested.
        """
        from dhole_mcp.charset import charset_is_in_doubt

        assert charset_is_in_doubt("Ã©Ã¨ " * 600), "a share of the text is markers"
        assert charset_is_in_doubt("x\ufffdy"), "a replacement char is a lost byte"
        assert charset_is_in_doubt("\ue000"), "PUA means GB18030 ate foreign bytes"
        assert not charset_is_in_doubt("正文" * 500 + " 反例写法：â€™")

    def test_the_message_names_the_charset_it_actually_used(self, tmp_path):
        """The old wording contradicted the same response's own metadata: the
        error said "could not determine this file's charset" while
        metadata.encoding named one."""
        path = self._write(tmp_path, "big5.csv", "姓名,年齡,備註\n張三,30\n", "big5")
        out = asyncio.run(MasterFetchServer().doc_parse(file_path=str(path)))
        assert out.error.startswith("encoding_undecodable")
        assert "could not determine" not in out.error
        assert out.metadata["encoding"] in out.error

    # ── the caller can correct it ───────────────────────────────────────

    def test_encoding_argument_overrides_detection(self, tmp_path):
        from dhole_mcp.parse import parse_file

        path = self._write(tmp_path, "latin.csv", "café,naïve\nrésumé,30\n", "latin-1")
        content, error = parse_file(str(path), "latin-1")
        assert error == ""
        assert "café" in content and "naïve" in content

    def test_a_lying_meta_charset_falls_through_to_detection(self, tmp_path):
        """The document says utf-8; the bytes are GBK. The declaration is a
        hint, so a failed strict decode hands over to detection instead of
        producing replacement soup."""
        pytest.importorskip("trafilatura")
        from dhole_mcp.parse import parse_file_detailed

        html = ("<html><head><meta charset='utf-8'></head><body>"
                "<p>中文正文内容，验证解码。</p></body></html>")
        path = self._write(tmp_path, "liar.html", html, "gbk")
        content, error, extras = parse_file_detailed(str(path))
        assert error == "" and "中文正文内容" in content
        assert extras["encoding"] == "gb18030"

    def test_the_encoding_argument_is_on_the_wire(self):
        tools = {t["name"]: t for t in MasterFetchServer._TOOL_DEFS}
        props = tools["doc_parse"]["inputSchema"]["properties"]
        assert "encoding" in props, "客户端看不到 encoding，就无法从乱码里恢复"
        assert "encoding" in tools["doc_parse"]["description"]

    def test_the_decoder_needs_no_fetch_stack(self):
        """parse.py promises to work without the [all] extra, and its fallback
        path used to import dhole_mcp.fetcher - which imports primp. Pinned
        structurally because the import only fires on the rare damaged-decode
        path, so an in-process test would pass on a machine that has primp."""
        import ast

        from dhole_mcp import charset as charset_mod

        tree = ast.parse(Path(charset_mod.__file__).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert "dhole_mcp" not in imported, (
            f"charset.py 不能依赖项目内模块（否则 parse 会拖进 primp）: {imported}"
        )


class TestCrawlPathScopeIsASubtree:
    """report BUG-1 (P0): path_include/path_exclude were applied as raw string
    prefixes (`path.startswith(p)`), which failed in four ways - all of them
    silent. Measured on one link set before the rewrite:

      path_include=["docs"]    -> 0 links kept (URL paths start with "/")
      path_include=["/docs/*"] -> 0 links kept (a glob is not a prefix)
      path_include=["/docs"]   -> kept /docs-old/legacy and /docsomething
      path_exclude=["docs"]    -> excluded nothing

    A crawl that returns fewer pages than the site has, for a reason the caller
    cannot see, is the failure this locks down. The contract now is a path
    subtree, matched at a segment boundary.
    """

    def test_a_slashless_pattern_keeps_the_subtree(self, monkeypatch):
        """The reported symptom: the most natural spelling scoped the whole
        crawl away. It used to keep 0 of 3 links, i.e. crawl only the start URL.
        """
        result = _stub_crawl(monkeypatch, path_include=["a"])
        assert result.pages_crawled == 2, "path_include=['a'] 又变成空爬了"
        assert any(p.url.endswith("/a") for p in result.pages)

    def test_every_spelling_of_one_subtree_agrees(self, monkeypatch):
        spellings = ["a", "/a", "/a/", "/a/*"]
        counts = {s: _stub_crawl(monkeypatch, path_include=[s]).pages_crawled
                  for s in spellings}
        assert len(set(counts.values())) == 1, f"同一子树的不同写法结果不一致: {counts}"
        assert counts["a"] == 2

    def test_a_slashless_exclude_pattern_excludes(self, monkeypatch):
        """The no-op direction: `path_exclude=["docs"]` excluded nothing, so the
        caller believed a crawl was scoped that had not been."""
        result = _stub_crawl(monkeypatch, path_exclude=["a"])
        assert result.pages_crawled == 3
        assert not any(p.url.endswith("/a") for p in result.pages)

    # ── the segment boundary, at unit level ─────────────────────────────

    def test_a_shared_prefix_is_not_a_subtree(self):
        from dhole_mcp.crawl import path_allowed

        for path in ("/docs", "/docs/", "/docs/x", "/docs/x/y"):
            assert path_allowed(path, ["/docs"]), f"{path} 应该在 /docs 子树里"
        for path in ("/docs-old", "/docs-old/legacy", "/docsomething", "/doc"):
            assert not path_allowed(path, ["/docs"]), f"{path} 只是前缀相同，不在 /docs 子树里"

    def test_root_and_star_mean_everything(self):
        from dhole_mcp.crawl import path_allowed

        for pattern in ("/", "*", "/*"):
            assert path_allowed("/anything/at/all", [pattern]), f"{pattern} 应表示全部"

    def test_exclude_wins_over_include(self):
        from dhole_mcp.crawl import path_allowed

        assert not path_allowed("/docs/x", ["/docs"], ["/docs/x"])

    def test_the_sitemap_path_uses_the_same_decision(self):
        """The sitemap map carried the identical `startswith` defect - the two
        call sites are now one implementation."""
        from dhole_mcp.crawl import _sitemap_passes_filters

        assert _sitemap_passes_filters("/docs/x", ["/docs"], None)
        assert not _sitemap_passes_filters("/docs-old/x", ["/docs"], None)
        assert not _sitemap_passes_filters("/docs/x", None, ["docs"])

    # ── a pattern the matcher cannot honour must not be silent ──────────

    def test_an_unsupported_glob_fails_the_call(self, monkeypatch):
        """A mid-path glob used to match nothing, so the caller saw an empty
        crawl instead of a bad pattern. Fail fast, before any fetching."""
        result = _stub_crawl(monkeypatch, path_include=["/api/*/v1"])
        assert result.pages_crawled == 0
        assert result.error.startswith("invalid path filter")
        assert "'/api/*/v1'" in result.error
        assert "path_include" in result.next_action, "下一步必须说清怎么改"

    def test_a_non_string_pattern_fails_the_call(self, monkeypatch):
        result = _stub_crawl(monkeypatch, path_exclude=[123])
        assert result.pages_crawled == 0
        assert "must be strings" in result.error

    def test_an_empty_pattern_fails_the_call(self, monkeypatch):
        """An empty string is not a scoping instruction; silently ignoring it
        would let a caller believe a filter was applied."""
        result = _stub_crawl(monkeypatch, path_include=[""])
        assert result.pages_crawled == 0
        assert "empty" in result.error

    def test_a_bad_pattern_does_not_claim_a_network_failure(self, monkeypatch):
        """The generic classify hint says "try a different source", which is
        nonsense here: nothing was fetched."""
        result = _stub_crawl(monkeypatch, path_include=["/api/*/v1"])
        assert "different source" not in result.next_action

    def test_the_wire_description_names_the_subtree_rule(self):
        tools = {t["name"]: t for t in MasterFetchServer._TOOL_DEFS}
        props = tools["web_crawl"]["inputSchema"]["properties"]
        desc = props["path_include"]["description"]
        assert "path subtree" in desc
        assert "NOT /docs-old" in desc, "描述必须点明前缀相同的兄弟目录不算"


class TestContentBudgetArgIsHonouredOrRejected:
    """report P1-5: ``max_content_chars`` silently ignored the caller's value.

    The old code was ``if not isinstance(v, int): v = MAX_CONTENT_CHARS``. Several
    MCP clients stringify numbers - the exact behaviour ``_coerce_arg_types``
    exists for - so ``max_content_chars="2000"`` silently became 40,000: a 20x
    context overspend reported as an ordinary 200. ``offset`` had the sibling
    defect that a negative value slices from the END (``text[-5:]`` returns the
    last 5 chars), i.e. a wrong result also delivered as an ordinary 200.

    Both are the "looks like it worked" family the project closes everywhere
    else, so the policy is: coerce what parses, raise on what cannot be an int,
    clamp only where the bound is a documented resource cap.
    """

    def _call(self, **args):
        return asyncio.run(MasterFetchServer()._dispatch("web_read", args))

    @staticmethod
    def _no_request(monkeypatch) -> list:
        """Fail the test if any request is attempted."""
        calls: list = []

        async def fake_get(url, **kwargs):
            calls.append(url)
            raise AssertionError(f"a request was attempted for {url}")

        monkeypatch.setattr(server_mod.MasterFetchServer, "get",
                            staticmethod(fake_get))
        return calls

    # ─── policy, at the unit level ───────────────────────────────────

    @pytest.mark.parametrize("value,expected", [
        (None, 40000),          # unset -> documented default
        ("2000", 2000),         # stringified number -> honoured, not defaulted
        ("  2000  ", 2000),     # clients pad
        (2000, 2000),
        (100, 500),             # below the floor -> clamped
        (999999, 200000),       # above the ceiling -> clamped
    ])
    def test_coerces_and_clamps(self, value, expected):
        assert server_mod._coerce_int_arg(
            value, "max_content_chars", lo=500, hi=200000,
            default=40000) == expected

    @pytest.mark.parametrize("value", [True, False, "abc", "2.5", 2.5, [2000],
                                       {"v": 2000}])
    def test_raises_on_what_cannot_be_an_int(self, value):
        """``True`` is an ``int`` subclass - without the bool guard it would
        silently mean 1 character."""
        with pytest.raises(ValueError):
            server_mod._coerce_int_arg(value, "max_content_chars", lo=500,
                                       hi=200000, default=40000)

    def test_clamp_can_be_turned_off_for_nonsense_bounds(self):
        """A negative offset has no sensible reading, so it raises rather than
        being quietly corrected to 0."""
        with pytest.raises(ValueError, match="between 0 and"):
            server_mod._coerce_int_arg(-1, "offset", lo=0, hi=2 ** 31,
                                       default=0, clamp=False)

    # ─── wiring: the rejection travels the normal contract ───────────

    @pytest.mark.parametrize("args,needle", [
        ({"url": "https://example.com/", "max_content_chars": "abc"},
         "max_content_chars"),
        ({"url": "https://example.com/", "max_content_chars": True},
         "max_content_chars"),
        ({"url": "https://example.com/", "offset": -1}, "offset"),
    ])
    def test_a_bad_value_is_rejected_before_any_request(self, monkeypatch, args,
                                                        needle):
        calls = self._no_request(monkeypatch)
        result = self._call(**args)[1]
        assert calls == [], "参数被拒绝时不该发出任何请求"
        assert result["status"] == 0
        assert result["error"].startswith("invalid_request:")
        assert needle in result["error"]
        # 参数错误必须给出对得上的下一步，不能复用「传绝对 URL」那句
        assert needle in result["next_action"]
        assert "absolute http(s) URL" not in result["next_action"]

    def test_the_rejection_does_not_claim_the_url_was_wrong(self, monkeypatch):
        """The default next_action is URL advice; a param error that reused it
        would send the caller to fix the one argument that was already right."""
        self._no_request(monkeypatch)
        result = self._call(url="https://example.com/",
                            max_content_chars="abc")[1]
        assert "no request was made" in result["next_action"]

    def test_a_stringified_number_reaches_the_fetch(self, monkeypatch):
        """The coercion half: ``"2000"`` must get past normalization (it used to
        be silently replaced, which also got past - so the point is that the
        value is honoured, which the unit test above pins)."""
        sentinel = _Stop()

        async def fake_get(url, **kwargs):
            raise sentinel

        monkeypatch.setattr(server_mod.MasterFetchServer, "get",
                            staticmethod(fake_get))
        with pytest.raises(_Stop):
            self._call(url="https://example.com/", max_content_chars="2000")

    # ─── the documented bound ────────────────────────────────────────

    def test_the_wire_documents_the_real_range(self):
        """The 200000 ceiling is a deliberate clamp, so it has to be stated -
        otherwise an agent asking for 500000 cannot tell why it got 200000."""
        tools = {t["name"]: t for t in MasterFetchServer._TOOL_DEFS}
        desc = tools["web_read"]["inputSchema"]["properties"]["max_content_chars"]["description"]
        assert "200000" in desc
        assert "500" in desc


class TestScreenshotErrorsAreActionableNotRawDriverLogs:
    """P1-3: ``screenshot`` did ``raise captured["error"]`` verbatim, so the
    agent received patchright's multi-line dump - a headline plus a "Call log:"
    block of driver internals ("waiting for fonts to load"). None of it names an
    argument the caller could change, and the whole reason an ``is_error``
    result is worth returning is that the caller can decide what to do next.
    """

    @staticmethod
    def _msg(exc: BaseException) -> str:
        return actions_mod._screenshot_error_message(exc)

    def test_the_call_log_never_reaches_the_agent(self):
        exc = Exception(
            "Page.screenshot: Timeout 30000ms exceeded.\n"
            "Call log:\n"
            "  - taking page screenshot\n"
            "    - waiting for fonts to load...\n"
            "    - fonts loaded\n")
        out = self._msg(exc)
        assert "Call log" not in out
        assert "fonts" not in out
        assert "taking page screenshot" not in out

    def test_the_headline_survives(self):
        """Dropping the log must not drop the one line that names the failure."""
        out = self._msg(Exception(
            "Page.screenshot: Timeout 30000ms exceeded.\nCall log:\n  - x\n"))
        assert "Timeout 30000ms exceeded" in out

    @pytest.mark.parametrize("raw,needle", [
        ("Page.screenshot: Timeout 30000ms exceeded.", "timeout"),
        ("Target page, context or browser has been closed", "sys_state"),
        ("browserType.launch: Executable doesn't exist at /x/chrome",
         "patchright install"),
    ])
    def test_each_known_failure_names_the_knob_that_fixes_it(self, raw, needle):
        assert needle in self._msg(Exception(raw))

    def test_an_unrecognised_failure_still_says_something(self):
        out = self._msg(Exception(
            "Protocol error (Page.captureScreenshot): Internal error"))
        assert "Protocol error" in out
        assert len(out) > 40

    def test_the_exception_type_is_kept(self):
        """The type is the cheapest signal the caller has; dropping it makes a
        timeout indistinguishable from a protocol error."""
        assert self._msg(TimeoutError("x")).startswith("TimeoutError:")

    def test_an_empty_message_does_not_crash(self):
        """``Exception()`` stringifies to ``""``. Without the fallback the type
        name would be the only content and the line would read
        ``"Exception:  - call again"`` - a blank field in the middle of the one
        sentence the agent gets. ``startswith("Exception:")`` alone does not
        catch that, hence the double-space check."""
        out = self._msg(Exception())
        assert out.startswith("Exception:")
        assert "(no message)" in out
        assert "  " not in out

    def test_a_runaway_headline_is_bounded(self):
        assert len(self._msg(Exception("E" * 5000))) <= 300

    def test_every_shape_fits_the_transport_cap(self):
        """``call_tool`` sends ``str(e)[:300]``. A message past that arrives cut
        mid-sentence - an unactionable error that still looks like a complete
        one, which is exactly the failure mode this fix exists to remove."""
        shapes = [
            Exception("Page.screenshot: Timeout 30000ms exceeded.\nCall log:\n"
                      + "  - step\n" * 40),
            Exception("Target page, context or browser has been closed"),
            Exception("E" * 5000),
            Exception(),
        ]
        for exc in shapes:
            assert len(self._msg(exc)) <= 300, repr(exc)

    def test_the_hint_reads_the_headline_only(self):
        """A call log mentioning "timeout" under a headline that is not a
        timeout must not produce timeout advice - a confidently wrong next step
        is worse than no next step."""
        exc = Exception(
            "Protocol error (Page.captureScreenshot): Internal error\n"
            "Call log:\n"
            "  - retrying after timeout\n")
        out = self._msg(exc)
        assert "raise timeout" not in out
        assert "Protocol error" in out

    def test_the_dispatched_error_is_the_sanitised_one(self, monkeypatch):
        """End to end through ``_dispatch``: the raw dump used to be what
        ``call_tool`` stringified into the ``is_error`` payload."""

        class _Timeout(Exception):
            pass

        class _Page:
            url = "https://example.com/"

            async def screenshot(self, **kwargs):
                raise _Timeout(
                    "Page.screenshot: Timeout 30000ms exceeded.\n"
                    "Call log:\n"
                    "  - taking page screenshot\n"
                    "    - waiting for fonts to load...\n")

        class _Session:
            _is_alive = True

            async def fetch(self, url, **kwargs):
                await kwargs["page_action"](_Page())

        entry = server_mod._SessionEntry(session=_Session(), session_type="stealthy")

        async def fake_get_session(self, session_id, expected_type):
            return entry

        async def fake_ensure(self, **kw):
            return "auto"

        monkeypatch.setattr(server_mod, "_browser_deps_available", lambda: True)
        monkeypatch.setattr(server_mod.MasterFetchServer, "_get_session",
                            fake_get_session)
        monkeypatch.setattr(server_mod.MasterFetchServer, "_ensure_auto_session",
                            fake_ensure)

        # A1：捕获住进动作链后，"派发级的净化"落在了执行器的行内错误上 ——
        # 失败是一张 ok=False 的回执行，不再是异常。承诺不变：Call log 到不了
        # agent，能改的旋钮名字要到。
        pa = actions_mod.build_page_action(
            [{"screenshot": {"selector": "img.captcha"}}])

        class _Page2:
            def locator(self, sel):
                class _L:
                    first = None

                    async def count(self):
                        return 1

                    async def screenshot(self, **kw):
                        nl = chr(10)
                        raise Exception(nl.join([
                            "Page.screenshot: Timeout 30000ms exceeded.",
                            "Call log:",
                            "  - taking page screenshot",
                            "    - waiting for fonts to load..."]))

        async def _capture():
            await pa(_Page2())

        asyncio.run(_capture())
        (row,) = pa.outcomes
        assert row["ok"] is False
