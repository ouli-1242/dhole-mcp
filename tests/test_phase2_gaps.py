"""v16.0 Phase 2 的缺口回归（G2 / G18 / G21 / G9 / G12）。

这些项的共同点：**文档承诺了一种行为，代码做了另一种**。所以每条测试都去量
「实际发生了什么」（发了几个请求、正文有没有被丢掉、错误分类是什么），而不是
只看返回结构的形状。
"""

from __future__ import annotations

import pytest

from dhole_mcp import crawl as crawl_mod
from dhole_mcp.server import ResponseModel


def _page_html(n_links: int = 30) -> str:
    return "".join(f'<a href="/p/{i}">P{i}</a>' for i in range(n_links))


class _CountingServer:
    """Fake server that records every smart_fetch it is asked to perform."""

    def __init__(self, body: str | None = None, status: int = 200,
                 content_ok: bool = True):
        self.calls: list[str] = []
        self._body = body if body is not None else _page_html()
        self._status = status
        self._content_ok = content_ok

    async def smart_fetch(self, url, **kwargs):
        self.calls.append(url)
        return ResponseModel(url=url, status=self._status, content=[self._body],
                             content_type="text/html", fetcher_used="http",
                             content_ok=self._content_ok)


# ─── G2: discover_only 的代价必须可见且可控 ──────────────────────────

class TestDiscoverOnlyIsHonestAboutItsCost:
    """报告 G2：文档说 "URL map only, no page content"，实测打了 10 个完整请求。

    map 模式要展开链接就必须抓页面 —— 那不是 bug。bug 是**代价不可见**，
    以及 summary 谎报 "10 content_ok"（那 10 页从未抽取正文）。
    """

    @pytest.mark.asyncio
    async def test_the_fetch_count_is_exactly_pages_crawled(self):
        server = _CountingServer()

        out = await crawl_mod.smart_crawl(
            server, "https://example.com/", max_pages=10, max_depth=2, discover_only=True)

        assert len(server.calls) == 10, "请求数必须等于 pages_crawled"
        assert out.pages_crawled == 10
        assert out.pages_discovered > out.pages_crawled, "BFS 应该展开出更多 URL"

    @pytest.mark.asyncio
    async def test_map_mode_returns_no_content_and_claims_none(self):
        """`content_ok=true` 曾经是 map 模式的谎：字段定义是「取到并抽取了正文」。"""
        out = await crawl_mod.smart_crawl(
            _CountingServer(), "https://example.com/", max_pages=3, max_depth=1,
            discover_only=True)

        assert out.pages, "map 模式仍应返回 URL 列表"
        for p in out.pages:
            assert p.content == []
            assert p.content_chars == 0
            assert p.content_ok is False, f"{p.url} 没有正文却说 content_ok"
            assert p.page_type == "discover_only"

    @pytest.mark.asyncio
    async def test_the_summary_states_the_fetch_count_and_not_a_content_claim(self):
        out = await crawl_mod.smart_crawl(
            _CountingServer(), "https://example.com/", max_pages=4, max_depth=1,
            discover_only=True)

        assert "4 page fetch(es)" in out.summary, out.summary
        assert "no content extracted" in out.summary, out.summary
        assert "content_ok" not in out.summary, "map 模式不该报 content_ok"

    @pytest.mark.asyncio
    async def test_max_pages_1_is_the_one_request_map(self):
        """一次请求拿到起始页的链接图 —— 这是「只想发现链接」的正确姿势。"""
        server = _CountingServer()

        out = await crawl_mod.smart_crawl(
            server, "https://example.com/", max_pages=1, max_depth=1, discover_only=True)

        assert len(server.calls) == 1
        assert out.pages_discovered == 31, "起始页的 30 条链接 + 自身"
        assert out.pages_crawled == 1

    @pytest.mark.asyncio
    async def test_next_action_names_the_cost_and_the_one_request_escapes(self):
        """代价披露要落在**可达**的分支里。

        map 模式下 `discovered > crawled` 必然意味着被 max_pages 截断，所以真正会
        被读到的是「stopped early」那条；专门的 map 分支只在跨域重定向的边缘情形
        可达。这条测试钉住的是前者。
        """
        out = await crawl_mod.smart_crawl(
            _CountingServer(), "https://example.com/", max_pages=3, max_depth=1,
            discover_only=True)

        assert "stopped early" in out.next_action, out.next_action
        assert "one request per page" in out.next_action, out.next_action
        assert "max_pages=1" in out.next_action
        assert "sitemap=true" in out.next_action

    @pytest.mark.asyncio
    async def test_map_mode_is_not_diagnosed_as_an_unreachable_site(self):
        """`ok == 0` 在 map 模式是预期形态，不该被读成「站点可能挂了」。

        这条复现的是一条真实的错位：content_ok 现在诚实地为 False，若不做区分，
        「全部页面都没有正文」的诊断会在一次成功的 map 调用上触发。
        """
        out = await crawl_mod.smart_crawl(
            _CountingServer(), "https://example.com/", max_pages=1, max_depth=0,
            discover_only=True)

        assert "may be unreachable" not in out.next_action, out.next_action
        assert "returned no content" not in out.next_action, out.next_action

    @pytest.mark.asyncio
    async def test_content_mode_still_reports_content_ok(self):
        """回归守卫：非 map 模式的 content_ok 语义没被这次改动带偏。"""
        para = "This is a real article paragraph about Python packaging. " * 12
        body = f"<html><body><article><h1>T</h1><p>{para}</p><p>{para}</p></article></body></html>"
        server = _CountingServer(body=body)

        out = await crawl_mod.smart_crawl(
            server, "https://example.com/", max_pages=1, max_depth=0)

        assert out.pages[0].content_ok is True
        assert out.pages[0].content_chars > 0


# ─── G12: css_selector 对非 HTML 不能静默忽略 ────────────────────────

def _xml_response() -> ResponseModel:
    return ResponseModel(
        url="https://example.com/feed", status=200,
        content=["<rss><channel><item><title>Hello</title></item></channel></rss>"],
        content_type="application/xml", fetcher_used="http",
    )


class TestCssSelectorOnNonHtml:
    """报告 G12：`httpbin/xml` + `css_selector:'h1'` 返回整段 XML，选择器被忽略且无提示。

    修法是**说清楚**而不是报错：XML/JSON 里本来就没有 h1，整份文档可能正是调用方
    要的东西，把 content_ok 翻成 False 会让它不敢用可用的内容。
    """

    def test_a_selector_on_non_html_is_reported_in_the_summary(self, monkeypatch):
        from dhole_mcp import server as server_mod

        monkeypatch.setattr(server_mod, "_CSS_SELECTOR",
                            server_mod._CSS_SELECTOR)
        token = server_mod._CSS_SELECTOR.set("h1")
        try:
            out = server_mod._with_agent_hints(_xml_response())
        finally:
            server_mod._CSS_SELECTOR.reset(token)

        assert "css_selector 'h1' was NOT applied" in out.summary, out.summary
        assert "application/xml" in out.summary, out.summary
        assert out.content_ok is True, "文档本身可用，不该被标成不可信"

    @pytest.mark.parametrize("ctype", ["text/html", "text/html; charset=utf-8"])
    def test_html_responses_are_not_flagged(self, ctype):
        from dhole_mcp import server as server_mod

        token = server_mod._CSS_SELECTOR.set("h1")
        try:
            result = _xml_response()
            result.content_type = ctype
            out = server_mod._with_agent_hints(result)
        finally:
            server_mod._CSS_SELECTOR.reset(token)

        assert "was NOT applied" not in out.summary, out.summary

    def test_no_selector_means_no_note(self):
        from dhole_mcp import server as server_mod

        out = server_mod._with_agent_hints(_xml_response())
        assert "css_selector" not in out.summary

    def test_an_unknown_content_type_is_not_accused(self):
        """拿不到 content-type 就不能断言选择器失效 —— 未知不等于非 HTML。"""
        from dhole_mcp import server as server_mod

        token = server_mod._CSS_SELECTOR.set("h1")
        try:
            result = _xml_response()
            result.content_type = ""
            out = server_mod._with_agent_hints(result)
        finally:
            server_mod._CSS_SELECTOR.reset(token)

        assert "was NOT applied" not in out.summary

    @pytest.mark.asyncio
    async def test_the_selector_reaches_the_summary_through_a_real_fetch(self, monkeypatch):
        """接线：装饰器必须把 css_selector 放进上下文，否则这条提示永远不会出现。"""
        import dhole_mcp.fetcher as fetcher
        from dhole_mcp.server import MasterFetchServer

        async def fake_get(*a, **k):
            return ResponseModel(
                url="https://example.com/feed", status=200,
                content=["<rss><channel><item><title>Hello</title></item></channel></rss>"],
                content_type="application/xml", fetcher_used="http", content_ok=True,
            )

        monkeypatch.setattr(fetcher, "tcp_preflight", lambda url, timeout=2.0: (True, ""))
        server = MasterFetchServer()
        server.get = fake_get

        out = await server.smart_fetch("https://example.com/feed",
                                       css_selector="h1", cache_ttl=0)

        assert "css_selector 'h1' was NOT applied" in out.summary, out.summary


# ─── G9: 私网 / 本机 allowlist（默认关闭，点名放行）─────────────────


def _verdict(url: str) -> str:
    from dhole_mcp.security import SecurityError, validate_url

    try:
        validate_url(url)
        return "allowed"
    except SecurityError:
        return "blocked"


@pytest.fixture
def allow_private():
    """Push a per-call allowlist and reset it when the test ends."""
    from dhole_mcp import security as sec

    tokens = []

    def _set(value):
        tokens.append(sec.set_allow_private(value))

    yield _set
    for token in reversed(tokens):
        sec._ALLOW_PRIVATE.reset(token)


class TestSsrfGuardIsStillTheDefault:

    @pytest.mark.parametrize("url", [
        "http://127.0.0.1:8899/secret.txt",
        "http://192.168.1.1/",
        "http://10.0.0.1/api",
        "http://169.254.169.254/latest/meta-data/",
        "http://localhost:3000/",
        "http://0177.0.0.1/",
    ])
    def test_private_targets_are_refused_without_an_allowlist(self, url):
        assert _verdict(url) == "blocked"

    @pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://x/y", "data:text/html,x"])
    def test_blocked_schemes_stay_blocked(self, allow_private, url):
        from dhole_mcp.security import SecurityError, validate_url

        allow_private(True)
        with pytest.raises(SecurityError) as e:
            validate_url(url)
        assert "scheme" in str(e.value).lower()


class TestAllowPrivateIsNarrow:

    def test_true_opens_loopback_only(self, allow_private):
        """`true` 不是「开整个局域网」—— 它有明确边界（只有本机）。"""
        allow_private(True)

        assert _verdict("http://127.0.0.1:8899/secret.txt") == "allowed"
        assert _verdict("http://localhost:3000/") == "allowed"
        assert _verdict("http://127.0.0.2:999/") == "allowed"      # 整段 127.0.0.0/8
        assert _verdict("http://[::1]:8080/") == "allowed"
        # 局域网与元数据不在 `true` 的范围里
        assert _verdict("http://192.168.1.1/") == "blocked"
        assert _verdict("http://10.0.0.1/api") == "blocked"

    def test_a_named_list_those_hosts_only(self, allow_private):
        # 只用默认被拦的**内网地址**断言：像 my-service.local 这样的主机名在
        # conftest 的假 DNS 下解析到公网 IP，默认就是放行的，拿它断言等于什么都没测。
        allow_private(["192.168.1.50", "10.0.0.5"])

        assert _verdict("http://192.168.1.50/") == "allowed"
        assert _verdict("http://10.0.0.5:8080/api") == "allowed"
        assert _verdict("http://192.168.1.51/") == "blocked"
        assert _verdict("http://127.0.0.1:8899/") == "blocked"

    @pytest.mark.parametrize("value", [True, ["169.254.169.254"], "169.254.169.254"])
    def test_cloud_metadata_is_never_allowed(self, allow_private, value):
        """SSRF 的第一目标。点名也不放行 —— 那不属于「我的 dev 服务」这类诉求。"""
        allow_private(value)

        assert _verdict("http://169.254.169.254/latest/meta-data/") == "blocked"
        assert _verdict("http://metadata.google.internal/") == "blocked"

    def test_a_named_host_is_matched_literally(self, allow_private):
        """字面匹配：allowlist 写了 127.0.0.1 不代表八进制写法也被放行。

        （`true` 走的是另一条路 —— 按解析后的地址判定 127.0.0.0/8 —— 见
        test_true_opens_loopback_only 里的 127.0.0.2。）
        """
        allow_private(["127.0.0.1"])

        assert _verdict("http://127.0.0.1:8899/") == "allowed"
        assert _verdict("http://0177.0.0.1/") == "blocked"

    def test_dns_rebinding_services_have_no_escape_hatch(self, allow_private):
        """这类服务把任意域名解析到任意 IP，放行它等于把内网判定交给外部解析器。"""
        allow_private(["foo.1u.ms", "127.0.0.1.nip.io"])

        assert _verdict("http://foo.1u.ms/") == "blocked"
        assert _verdict("http://127.0.0.1.nip.io/") == "blocked"

    def test_the_env_var_works_process_wide(self, monkeypatch):
        monkeypatch.setenv("DHOLE_ALLOW_PRIVATE_HOSTS", "10.0.0.5, 192.168.1.50")

        assert _verdict("http://10.0.0.5/") == "allowed"
        assert _verdict("http://192.168.1.50/") == "allowed"
        assert _verdict("http://10.0.0.6/") == "blocked"

    def test_the_allowlist_can_be_reset_so_it_does_not_leak(self):
        """一次调用点名放行的主机，不能变成下一次调用的默认。"""
        from dhole_mcp import security as sec

        token = sec.set_allow_private(["192.168.1.50"])
        try:
            assert _verdict("http://192.168.1.50/") == "allowed"
        finally:
            sec._ALLOW_PRIVATE.reset(token)
        assert _verdict("http://192.168.1.50/") == "blocked"

    @pytest.mark.asyncio
    async def test_the_option_reaches_the_guard_through_a_real_call(self, monkeypatch):
        """接线：装饰器必须把 options.allow_private 送进 guard，否则这个开关不存在。"""
        import dhole_mcp.fetcher as fetcher
        from dhole_mcp.server import MasterFetchServer, ResponseModel

        hit: list[str] = []

        async def fake_get(url, *a, **k):
            hit.append(url)
            return ResponseModel(url=url, status=200, content=["dev server body"],
                                 content_type="text/plain", fetcher_used="http",
                                 content_ok=True)

        monkeypatch.setattr(fetcher, "tcp_preflight", lambda url, t=2.0: (True, ""))
        server = MasterFetchServer()
        server.get = fake_get

        # allow_private lives in the options bag; the MCP dispatcher promotes it to
        # a keyword, so calling the method directly takes it as one.
        target = "http://127.0.0.1:8899/secret.txt"
        out = await server.smart_fetch(target, allow_private=True,
                                       cache_ttl=0, timeout=6000)
        assert hit == [target], out.error
        assert out.content == ["dev server body"]

    @pytest.mark.asyncio
    async def test_without_the_option_the_same_url_is_refused(self, monkeypatch):
        """回归守卫：默认仍然是拒，且拒在任何请求之前。"""
        from dhole_mcp.server import MasterFetchServer

        async def _never(*a, **k):
            raise AssertionError("被 SSRF 守卫拒绝的 URL 不该发出请求")

        server = MasterFetchServer()
        server.get = _never
        server.stealthy_fetch = _never

        out = await server.smart_fetch("http://127.0.0.1:8899/secret.txt",
                                       cache_ttl=0, timeout=6000)
        assert "internal/private" in out.error, out.error
        assert out.content == []

    def test_an_unusable_value_is_reported_not_silently_ignored(self):
        """安全相关的选项被静默忽略是最糟的形态 —— 必须说。"""
        from dhole_mcp.security import parse_allow_private

        # 无法理解的值解析为空集，由调用方写进 summary
        assert parse_allow_private(42) == (frozenset(), False)
        assert parse_allow_private(None) == (frozenset(), False)
        assert parse_allow_private("true")[1] is True
        assert parse_allow_private("a.local,b.local")[0] == {"a.local", "b.local"}


# ─── G18: 错误响应的 body ────────────────────────────────────────────
#
# 报告原文「4xx/5xx 的 body 被整体丢弃」**证伪**：httpbin 的 /status/* 本来就是空
# body，而服务器真回了 body 时 dhole 是保留的（实测 404 + JSON body -> content 保留、
# error=http_error_404）。真正存在的缺口是另一件事：一旦 archive.org 有该 URL 的
# 快照，活的响应体会被 2019 年的页面**顶替**掉，且 error 被清空 —— 服务器刚给出的
# 答案消失且没有痕迹。这一组测试钉的是后者。


def _error_result(status: int, body: str, ctype: str) -> ResponseModel:
    return ResponseModel(url="https://api.example.com/items/42", status=status,
                         content=[body] if body else [], content_type=ctype,
                         fetcher_used="http", total_size_bytes=len(body))


class TestErrorBodyIsKept:

    def test_a_4xx_body_survives_the_pipeline(self):
        """回归守卫：dhole 从来不丢 error body（报告 G18 的字面结论是错的）。"""
        from dhole_mcp.server import _with_agent_hints

        out = _with_agent_hints(_error_result(404, '{"error":"not found"}', "application/json"))
        assert out.content == ['{"error":"not found"}']
        assert out.content_ok is False

    def test_an_api_error_body_is_not_replaced_by_a_snapshot(self):
        from dhole_mcp.server import _should_try_archive

        assert _should_try_archive(
            _error_result(404, '{"error":"not found","hint":"use /v2/items"}', "application/json")
        ) is False
        assert _should_try_archive(
            _error_result(500, "<error><code>42</code></error>", "application/xml")
        ) is False

    def test_an_html_error_page_still_falls_back(self):
        """对照：页没了的时候快照常常比错误页有用，那条回退不能一起收走。"""
        from dhole_mcp.server import _should_try_archive

        assert _should_try_archive(
            _error_result(404, "<html><body>404 Not Found</body></html>", "text/html")
        ) is True

    def test_an_empty_body_still_falls_back(self):
        """没有 body 时快照是唯一的内容 —— 该回退还是得回退。"""
        from dhole_mcp.server import _should_try_archive

        assert _should_try_archive(_error_result(404, "", "application/json")) is True

    @pytest.mark.asyncio
    async def test_the_live_api_error_reaches_the_caller_even_with_a_snapshot(self, monkeypatch):
        import dhole_mcp.fetcher as fetcher
        from dhole_mcp.server import MasterFetchServer

        live = '{"error":"item 42 not found","hint":"use /v2/items"}'

        async def fake_get(url, *a, **k):
            return ResponseModel(url=url, status=404, content=[live],
                                 content_type="application/json", fetcher_used="http",
                                 total_size_bytes=len(live), content_ok=True)

        async def fake_archive(*a, **k):
            return ResponseModel(url="https://api.example.com/items/42", status=200,
                                 content=["wayback snapshot from 2019"],
                                 content_type="text/html", fetcher_used="archive",
                                 metadata={"source": "archive", "archived_at": "2019-05-01"})

        async def no_stealthy(*a, **k):
            raise AssertionError("404 不升级浏览器")

        monkeypatch.setattr(fetcher, "tcp_preflight", lambda url, t=2.0: (True, ""))
        server = MasterFetchServer()
        server.get = fake_get
        server.stealthy_fetch = no_stealthy
        monkeypatch.setattr(server, "_fetch_from_archive", fake_archive)

        out = await server.smart_fetch("https://api.example.com/items/42",
                                       cache_ttl=0, timeout=6000)

        assert out.content == [live], out.content
        assert out.status == 404
        assert out.error.startswith("http_error_404"), out.error
        assert (out.metadata or {}).get("source") != "archive"

@pytest.fixture
def proxy_socket(monkeypatch):
    """Drive ``proxy_preflight``'s socket call deterministically.

    conftest replaces ``socket.getaddrinfo`` with a fixed PUBLIC ip so the suite
    never depends on the machine's resolver — which also means
    ``create_connection(('127.0.0.1', port))`` does NOT reach loopback under
    pytest. A test that connects to a real local listener would therefore pass (or
    fail) for the wrong reason, so the socket is stubbed here and the REAL OS
    behaviour is verified outside pytest (recorded in the changelog).

    What these tests pin is the decision: which failures count as definitive, that
    an accepting endpoint is never rejected, and that a failure is not reported as
    a slow host.
    """
    import socket as _socket
    import types

    state = {"mode": "accept", "calls": []}

    def _fake(address, timeout=None):
        state["calls"].append((address, timeout))
        mode = state["mode"]
        if mode == "refused":
            raise ConnectionRefusedError(10061, "actively refused")
        if mode == "timeout":
            raise TimeoutError("timed out")
        return types.SimpleNamespace(close=lambda: None)

    monkeypatch.setattr(_socket, "create_connection", _fake)
    return state


class TestProxyPreflight:

    def test_a_refused_proxy_endpoint_is_reported(self, proxy_socket):
        from dhole_mcp.fetcher import proxy_preflight

        proxy_socket["mode"] = "refused"
        assert proxy_preflight("http://127.0.0.1:9/", timeout=3.0) == \
            (False, "proxy_unreachable")

    def test_a_timed_out_endpoint_is_also_definitive(self, proxy_socket):
        """过滤端口（SYN 被丢）同样不能"再试试" —— 那正是要消灭的静默 30s。"""
        from dhole_mcp.fetcher import proxy_preflight

        proxy_socket["mode"] = "timeout"
        assert proxy_preflight("http://10.255.255.1:3128/", timeout=3.0) == \
            (False, "proxy_unreachable")

    def test_an_accepting_endpoint_is_not_rejected(self, proxy_socket):
        """只判「有没有接受连接」。代理不答 HTTP 是另一回事，不能在这里杀掉。"""
        from dhole_mcp.fetcher import proxy_preflight

        assert proxy_preflight("http://127.0.0.1:7890/") == (True, "")
        assert proxy_socket["calls"] == [(("127.0.0.1", 7890), 5.0)]

    def test_an_unparseable_proxy_does_not_block_the_call(self, proxy_socket):
        """探测不出来就别拦 —— 未知不等于不可达。"""
        from dhole_mcp.fetcher import proxy_preflight

        assert proxy_preflight("not a url") == (True, "")
        assert proxy_socket["calls"] == [], "解析不出来就不该去连"

    def test_the_endpoint_label_never_carries_credentials(self):
        from dhole_mcp.server import _proxy_endpoint_label

        label = _proxy_endpoint_label("http://user:sekret@127.0.0.1:1080/")
        assert label == "127.0.0.1:1080"
        assert "sekret" not in label and "user" not in label

    def test_a_proxy_failure_is_not_answered_with_a_wayback_snapshot(self):
        """我们从未接触过目标站，快照会被当成那个页面读，而且是另一个问题的答案。"""
        from dhole_mcp.server import ResponseModel, _should_try_archive

        result = ResponseModel(url="https://example.com/", status=0, content=[],
                               error="proxy_unreachable: nothing accepted a TCP connection at 127.0.0.1:9")
        assert _should_try_archive(result) is False

        # 对照：真正的网络失败仍然该去查快照
        plain = ResponseModel(url="https://example.com/", status=0, content=[],
                              error="network_error: dns_failure (TCP preflight)")
        assert _should_try_archive(plain) is True


class TestDeadProxyFailsFast:

    @pytest.mark.asyncio
    async def test_it_returns_proxy_unreachable_without_burning_the_budget(
            self, proxy_socket, monkeypatch):
        """报告 G21 的实测形态：30s 预算烧光，报成「慢主机，调大 timeout」。"""
        from dhole_mcp.server import MasterFetchServer

        proxy_socket["mode"] = "refused"

        async def _never(*a, **k):
            raise AssertionError("死代理不该走到抓取 tier")

        server = MasterFetchServer()
        server.get = _never
        server.stealthy_fetch = _never
        monkeypatch.setattr(server, "_fetch_from_archive", _never)

        import time
        started = time.monotonic()
        out = await server.smart_fetch(
            "https://example.com/", proxy="http://127.0.0.1:9/",
            cache_ttl=0, timeout=30000)
        elapsed = time.monotonic() - started

        assert out.status == 0
        assert out.error.startswith("proxy_unreachable"), out.error
        assert out.fetcher_used == "none"
        assert "proxy_unreachable" in out.escalation_path
        assert elapsed < 10.0, f"仍然烧了 {elapsed:.1f}s"
        # 归因不能指向「慢主机」——那会让人去调 timeout，而问题不在那儿
        assert "raise timeout" not in out.next_action.lower(), out.next_action
        assert "no request was sent" in out.error

    @pytest.mark.asyncio
    async def test_a_pinned_tier_refuses_a_dead_proxy_too(self, proxy_socket):
        """`force_fetcher` returns from `_force_fetch`, which never asked about
        the proxy — the preflight lived only in `_auto_escalate`, the path a
        pinned call skips. Measured live: `force_fetcher='http'` + a dead proxy
        spent 6s and came back `os error 10061` in the local language, which
        reads as "the SITE refused us", for a proxy that never accepted a
        connection. The option description's promise is unconditional, so the
        check has to be too."""
        from dhole_mcp.server import MasterFetchServer

        proxy_socket["mode"] = "refused"

        async def _never(*a, **k):
            raise AssertionError("钉层也不该让死代理发出请求")

        server = MasterFetchServer()
        server.get = _never
        server.stealthy_fetch = _never
        for pinned in ("http", "stealthy"):
            out = await server.smart_fetch(
                "https://example.com/", proxy="http://127.0.0.1:9/",
                force_fetcher=pinned, cache_ttl=0)
            assert out.error.startswith("proxy_unreachable"), f"{pinned}: {out.error}"
            assert out.fetcher_used == "none"

    @pytest.mark.asyncio
    async def test_credentials_never_reach_the_response(self, proxy_socket):
        from dhole_mcp.server import MasterFetchServer

        proxy_socket["mode"] = "refused"
        server = MasterFetchServer()

        out = await server.smart_fetch(
            "https://example.com/", proxy="http://user:sekret@127.0.0.1:9/",
            cache_ttl=0, timeout=10000)

        assert "sekret" not in out.error
        assert "127.0.0.1:9" in out.error

    @pytest.mark.asyncio
    async def test_a_live_proxy_endpoint_is_not_rejected(self, proxy_socket, monkeypatch):
        """回归守卫：本地代理（Clash 等）是常态，不能因为探测就把它们全杀掉。"""
        from dhole_mcp.server import MasterFetchServer, ResponseModel

        proxy_socket["mode"] = "accept"
        hit: list[str] = []

        async def fake_get(url, *a, **k):
            hit.append(url)
            return ResponseModel(url=url, status=200, content=["body"],
                                 content_type="text/html", fetcher_used="http",
                                 content_ok=True)

        server = MasterFetchServer()
        server.get = fake_get

        out = await server.smart_fetch(
            "https://example.com/", proxy="http://127.0.0.1:7890/",
            cache_ttl=0, timeout=5000)

        assert hit == ["https://example.com/"], "接受连接的代理不该被前置探测拦下"
        assert not out.error.startswith("proxy_unreachable")
