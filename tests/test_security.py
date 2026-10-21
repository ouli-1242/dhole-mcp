"""test_security.py —— 由 3 个单一行为文件合并

由 2026-10-21 的测试重组合并而来；每个来源文件的原首段说明保留为小节注释。
"""

from __future__ import annotations

import pytest
from dhole_mcp.security import validate_url, validate_css_selector, validate_headers, validate_proxy, validate_timeout, validate_search_query, redact_api_key, SecurityError, _normalize_ip_notation
import asyncio
import json
from dhole_mcp import identity
from dhole_mcp.server import MasterFetchServer, _REVALIDATE, _SCHEMA_SOURCE_MAX_CHARS, _schema_value_empty, _validate_schema_selectors
from dhole_mcp.structured import MAX_RECORDS_PER_FIELD, extract_structured
from dhole_mcp.models import ResponseModel


# ============================================================================
# 来源：test_security.py
# ============================================================================
# Adversarial security tests: URL validation, SSRF bypass vectors,
# CSS selector validation, header injection prevention, proxy validation,
# timeout validation, search query sanitization, API key redaction.

# ─── validate_url: SSRF bypass vectors ─────────────────────────────

class TestSSRFBypassVectors:
    """Every known SSRF bypass vector must be rejected."""

    @pytest.mark.parametrize("url", [
        "http://127.0.0.1/admin",
        "http://10.0.0.1/api",
        "http://192.168.1.1/",
        "http://172.16.0.1/",
        "http://0.0.0.0/",
        "http://[::1]:8080/path",
        "http://169.254.169.254/latest/meta-data/",
        "http://169.254.169.254/computeMetadata/v1/",
        "http://[fd00:ec2::254]/latest/meta-data/",
        "http://100.100.100.200/latest/meta-data/",   # Aliyun IMDS (CGNAT 100.64.0.0/10)
        "http://1684301000/latest/meta-data/",        # decimal -> 100.100.100.200
        "http://metadata/latest/meta-data/",          # bare metadata hostname
    ])
    def test_rejects_private_and_metadata_ips(self, url):
        with pytest.raises(SecurityError, match="internal|private"):
            validate_url(url)

    @pytest.mark.parametrize("url", [
        "http://0177.0.0.1/admin",        # octal per-octet -> 127.0.0.1
        "http://0x7f.0x0.0x0.0x1/admin", # hex per-octet -> 127.0.0.1
        "http://2130706433/admin",         # decimal integer -> 127.0.0.1
        "http://0x7f000001/admin",         # hex integer -> 127.0.0.1 (inet_aton)
        "http://127.1/admin",              # short-form -> 127.0.0.1
    ])
    def test_rejects_alternate_ip_notations(self, url):
        with pytest.raises(SecurityError, match="internal|private"):
            validate_url(url)

    def test_rejects_localhost_hostname(self):
        with pytest.raises(SecurityError, match="internal"):
            validate_url("http://localhost:3000/api")

    def test_rejects_cloud_metadata_hostname(self):
        with pytest.raises(SecurityError, match="internal"):
            validate_url("http://metadata.google.internal/computeMetadata/v1/")

    @pytest.mark.parametrize("url", [
        "http://evil.10.0.0.1.nip.io/",
        "http://127.0.0.1.sslip.io/",
        "http://evil.192.168.1.1.xip.io/",
    ])
    def test_rejects_dns_rebinding_services(self, url):
        with pytest.raises(SecurityError, match="DNS rebinding"):
            validate_url(url)

    def test_rejects_backslash_in_url(self):
        # CVE-2025-0454: backslash causes parser confusion
        with pytest.raises(SecurityError, match="backslash"):
            validate_url("http://evil.com\\@127.0.0.1/")

    def test_rejects_file_scheme(self):
        with pytest.raises(SecurityError, match="not allowed|not supported"):
            validate_url("file:///etc/passwd")

    @pytest.mark.parametrize("url", [
        "ftp://evil.com/file",
        "data:text/html,<script>alert(1)</script>",
        "javascript:alert(1)",
        "gopher://evil.com/1",
        "vbscript:msgbox(1)",
        "chrome://settings",
    ])
    def test_rejects_dangerous_schemes(self, url):
        with pytest.raises(SecurityError):
            validate_url(url)

    def test_rejects_malformed_bracketed_host(self):
        # CVE-2024-11168: invalid bracketed host
        with pytest.raises(SecurityError, match="bracketed|Malformed"):
            validate_url("http://[not-an-ipv6]/path")

    def test_accepts_valid_ipv6_bracketed(self):
        result = validate_url("http://[2001:db8::1]/path")
        assert result == "http://[2001:db8::1]/path"

    def test_rejects_ipv4_mapped_ipv6_loopback(self):
        # ::ffff:127.0.0.1 is IPv4-mapped IPv6 -> must extract and reject
        with pytest.raises(SecurityError, match="IPv4-mapped"):
            validate_url("http://[::ffff:127.0.0.1]/")


class TestURLValidation:
    """Normal URL validation behavior."""

    def test_accepts_valid_https(self):
        assert validate_url("https://example.com/page") == "https://example.com/page"

    def test_accepts_valid_http(self):
        assert validate_url("http://example.com/page") == "http://example.com/page"

    def test_accepts_url_with_port(self):
        assert validate_url("https://example.com:8080/path") == "https://example.com:8080/path"

    @pytest.mark.parametrize("url", [
        "http://example.com:70000/",   # port > 65535
        "http://example.com:0/",       # port 0 is invalid
        "http://example.com:abc/",     # non-numeric port
        "http://127.0.0.1:99999/",     # oversized on private IP
    ])
    def test_rejects_invalid_ports(self, url):
        with pytest.raises(SecurityError, match="port"):
            validate_url(url)

    def test_rejects_empty_string(self):
        with pytest.raises(SecurityError, match="non-empty"):
            validate_url("")

    def test_rejects_none(self):
        with pytest.raises(SecurityError, match="non-empty"):
            validate_url(None)  # type: ignore

    def test_rejects_non_string(self):
        with pytest.raises(SecurityError, match="non-empty"):
            validate_url(123)  # type: ignore

    def test_rejects_oversized_url(self):
        url = "https://example.com/" + "a" * 9000
        with pytest.raises(SecurityError, match="maximum length"):
            validate_url(url)

    def test_rejects_url_without_scheme(self):
        with pytest.raises(SecurityError, match="Only http"):
            validate_url("example.com/path")

    def test_allow_internal_flag_permits_private_ip(self):
        result = validate_url("http://127.0.0.1/admin", allow_internal=True)
        assert result == "http://127.0.0.1/admin"


class TestNormalizeIPNotation:
    """The IP notation normalizer must correctly resolve every curl-accepted format."""

    @pytest.mark.parametrize("input_str,expected", [
        ("0177.0.0.1", "127.0.0.1"),       # octal per-octet
        ("0x7f.0x0.0x0.0x1", "127.0.0.1"), # hex per-octet
        ("2130706433", "127.0.0.1"),        # decimal integer
        ("0x7f000001", "127.0.0.1"),        # hex integer (inet_aton form)
        ("127.1", "127.0.0.1"),             # 2-part short form
        ("127.0.1", "127.0.0.1"),           # 3-part short form
    ])
    def test_resolves_to_dotted_decimal(self, input_str, expected):
        assert _normalize_ip_notation(input_str) == expected

    def test_rejects_non_ip_string(self):
        assert _normalize_ip_notation("example.com") is None

    def test_rejects_empty(self):
        assert _normalize_ip_notation("") is None


# ─── CSS selector validation ───────────────────────────────────────

class TestCSSSelectorValidation:

    def test_valid_selector_accepted(self):
        assert validate_css_selector("div.content > p") == "div.content > p"

    def test_none_returns_none(self):
        assert validate_css_selector(None) is None

    def test_empty_returns_none(self):
        assert validate_css_selector("") is None

    def test_whitespace_only_returns_none(self):
        assert validate_css_selector("   ") is None

    def test_rejects_non_string(self):
        with pytest.raises(SecurityError, match="must be a string"):
            validate_css_selector(123)  # type: ignore

    def test_rejects_oversized(self):
        with pytest.raises(SecurityError, match="maximum length"):
            validate_css_selector("div > " * 3000)

    def test_rejects_excessive_nesting(self):
        with pytest.raises(SecurityError, match="nesting depth"):
            validate_css_selector(" > ".join(["div"] * 101))

    @pytest.mark.parametrize("dangerous", [
        "div<script>alert(1)</script>",
        "a[href='javascript:alert(1)']",
        "div[style*='expression(alert(1))']",
    ])
    def test_rejects_dangerous_content(self, dangerous):
        with pytest.raises(SecurityError, match="dangerous"):
            validate_css_selector(dangerous)


# ─── Header validation ─────────────────────────────────────────────

class TestHeaderValidation:

    def test_valid_headers_accepted(self):
        result = validate_headers({"X-Custom": "value", "Accept": "text/html"})
        assert result == {"X-Custom": "value", "Accept": "text/html"}

    def test_none_returns_none(self):
        assert validate_headers(None) is None

    def test_empty_dict_returns_empty(self):
        result = validate_headers({})
        assert result == {}

    def test_rejects_non_dict(self):
        with pytest.raises(SecurityError, match="dictionary"):
            validate_headers("not a dict")  # type: ignore

    def test_rejects_newline_in_value(self):
        with pytest.raises(SecurityError, match="newline"):
            validate_headers({"X-Header": "value\r\nInjected: true"})

    def test_rejects_newline_in_name(self):
        with pytest.raises(SecurityError, match="newline"):
            validate_headers({"X-Evil\nHeader": "value"})

    def test_rejects_backslash_in_name(self):
        with pytest.raises(SecurityError, match="backslash"):
            validate_headers({"X-Evil\\Header": "value"})

    @pytest.mark.parametrize("forbidden", ["host", "content-length", "transfer-encoding", "connection"])
    def test_rejects_forbidden_headers(self, forbidden):
        with pytest.raises(SecurityError, match="managed internally"):
            validate_headers({forbidden: "evil"})

    def test_rejects_too_many_headers(self):
        headers = {f"X-Header-{i}": "value" for i in range(60)}
        with pytest.raises(SecurityError, match="Too many"):
            validate_headers(headers)

    def test_rejects_oversized_value(self):
        with pytest.raises(SecurityError, match="max length"):
            validate_headers({"X-Big": "a" * 9000})


# ─── Proxy validation ─────────────────────────────────────────────

class TestProxyValidation:

    def test_none_returns_none(self):
        assert validate_proxy(None) is None

    @pytest.mark.parametrize("proxy", [
        "http://proxy.example.com:8080",
        "https://proxy.example.com:8080",
        "socks5://proxy.example.com:1080",
        "socks5h://proxy.example.com:1080",
    ])
    def test_accepts_valid_schemes(self, proxy):
        assert validate_proxy(proxy) == proxy

    def test_rejects_invalid_scheme(self):
        with pytest.raises(SecurityError, match="scheme"):
            validate_proxy("ftp://proxy.example.com")

    def test_dict_proxy_accepted(self):
        proxy = {"server": "http://proxy.example.com:8080"}
        assert validate_proxy(proxy) == proxy

    def test_dict_proxy_with_socks5(self):
        proxy = {"server": "socks5://proxy.example.com:1080", "username": "u", "password": "p"}
        assert validate_proxy(proxy) == proxy

    def test_rejects_dict_with_extra_keys(self):
        with pytest.raises(SecurityError, match="Unknown"):
            validate_proxy({"server": "http://p.com:80", "extra": "bad"})

    def test_rejects_empty_string(self):
        assert validate_proxy("") is None

    def test_rejects_non_string_non_dict(self):
        with pytest.raises(SecurityError, match="must be"):
            validate_proxy(123)  # type: ignore


# ─── Timeout validation ───────────────────────────────────────────

class TestTimeoutValidation:

    def test_valid_int_accepted(self):
        assert validate_timeout(30) == 30

    def test_valid_float_accepted(self):
        assert validate_timeout(30.5) == 30.5

    def test_rejects_zero(self):
        with pytest.raises(SecurityError, match="positive"):
            validate_timeout(0)

    def test_rejects_negative(self):
        with pytest.raises(SecurityError, match="positive"):
            validate_timeout(-5)

    def test_rejects_non_number(self):
        with pytest.raises(SecurityError, match="must be a number"):
            validate_timeout("30")  # type: ignore

    def test_rejects_exceeds_max(self):
        with pytest.raises(SecurityError, match="maximum"):
            validate_timeout(200_000)


# ─── Search query validation ──────────────────────────────────────

class TestSearchQueryValidation:

    def test_valid_query_accepted(self):
        assert validate_search_query("python async") == "python async"

    def test_rejects_empty(self):
        with pytest.raises(SecurityError, match="non-empty"):
            validate_search_query("")

    def test_rejects_none(self):
        with pytest.raises(SecurityError, match="non-empty"):
            validate_search_query(None)  # type: ignore

    def test_rejects_whitespace_only(self):
        with pytest.raises(SecurityError, match="empty"):
            validate_search_query("   ")

    def test_strips_control_characters(self):
        result = validate_search_query("hello\x00world\x07test")
        assert "\x00" not in result and "\x07" not in result

    def test_rejects_oversized(self):
        with pytest.raises(SecurityError, match="maximum length"):
            validate_search_query("a" * 3000)


# ─── API key redaction ────────────────────────────────────────────

class TestAPIKeyRedaction:

    def test_redacts_tinyfish_key(self):
        assert "REDACTED" in redact_api_key("sk-tinyfish-abc123xyz")

    def test_redacts_generic_sk_key(self):
        assert "REDACTED" in redact_api_key("sk-abc123def456ghi789jkl012")

    def test_redacts_proxy_credentials_http(self):
        result = redact_api_key("http://user:pass@proxy.com:8080")
        assert "CREDENTIALS_REDACTED" in result
        assert "user:pass" not in result

    def test_redacts_proxy_credentials_socks5(self):
        result = redact_api_key("socks5://user:pass@proxy.com:1080")
        assert "CREDENTIALS_REDACTED" in result
        assert "user:pass" not in result

    def test_preserves_non_sensitive_text(self):
        text = "Error connecting to https://example.com/api"
        assert redact_api_key(text) == text


class TestHostsPinExemptionIsByValue:
    """hosts 豁免要看钉到哪个值，不能只看名字在不在文件里。

    广告/跟踪屏蔽类 hosts 把整批域名钉到 0.0.0.0，而 0.0.0.0 作为连接目标在
    Windows/macOS 上等同回环 —— 按存在性豁免等于让这些域名成为访问本机服务的
    入口，且攻击者只需挑一个已经在 hosts 里的名字。
    """

    @pytest.mark.parametrize("ip,expected", [
        ("0.0.0.0", True), ("::", True), ("0::0", True), ("::0", True),
        (" 0.0.0.0 ", True),
        ("127.0.0.1", False), ("172.16.253.73", False), ("8.8.8.8", False),
        ("", False), ("junk", False), ("localhost", False),
    ])
    def test_blackhole_detector(self, ip, expected):
        from dhole_mcp.security import _is_blackhole_pin
        assert _is_blackhole_pin(ip) is expected

    def test_blackhole_pin_is_no_longer_exempt(self, monkeypatch):
        from dhole_mcp import security as sec
        monkeypatch.setattr(sec, "_hosts_file_pin", lambda h: "0.0.0.0")
        monkeypatch.setattr(sec, "_tunnel_exempt_engaged", lambda: False)
        sec._DNS_CHECK_CACHE.clear()
        verdict = sec._resolves_to_internal("ads.example-tracker.com")
        assert verdict and "blackhole" in verdict
        assert sec.url_targets_internal("http://ads.example-tracker.com:6379/") is True
        sec._DNS_CHECK_CACHE.clear()

    def test_loopback_dev_pin_stays_exempt(self, monkeypatch):
        """钉到 127.0.0.1 是"我要打到本机这个服务"的显式决定，与黑洞意图相反。

        这台开发机就把 github.com / huggingface.co 钉到 127.0.0.1（见 conftest 的
        _offline_dns 说明）；把回环钉位一起拒掉会直接打断用户自己的配置。
        """
        from dhole_mcp import security as sec
        monkeypatch.setattr(sec, "_hosts_file_pin", lambda h: "127.0.0.1")
        monkeypatch.setattr(sec, "_tunnel_exempt_engaged", lambda: False)
        sec._DNS_CHECK_CACHE.clear()
        assert sec._resolves_to_internal("huggingface.co") is None
        sec._DNS_CHECK_CACHE.clear()

    def test_public_pin_stays_exempt(self, monkeypatch):
        from dhole_mcp import security as sec
        monkeypatch.setattr(sec, "_hosts_file_pin", lambda h: "93.184.216.34")
        sec._DNS_CHECK_CACHE.clear()
        assert sec._resolves_to_internal("example.com") is None
        sec._DNS_CHECK_CACHE.clear()


class TestUrlTargetsInternalPredicate:
    """给 tcp_preflight / 浏览器复校验共用的那一个谓词。"""

    @pytest.mark.parametrize("url", [
        "http://127.0.0.1:9200/", "http://localhost/admin", "http://0.0.0.0/",
        "http://169.254.169.254/latest/meta-data/", "http://10.0.0.5/",
        "http://0177.0.0.1/", "http://2130706433/", "http://0x7f000001/",
        "http://[::ffff:127.0.0.1]/", "http://metadata.google.internal/",
        "http://127.0.0.1.nip.io/", "not a url at all", "http://[bad",
    ])
    def test_flags_internal_and_unparseable(self, url):
        from dhole_mcp.security import url_targets_internal
        assert url_targets_internal(url) is True, url

    def test_allows_a_public_url(self):
        from dhole_mcp.security import url_targets_internal
        assert url_targets_internal("https://example.com/docs") is False

    def test_allow_internal_flag_is_honoured(self):
        from dhole_mcp.security import url_targets_internal
        assert url_targets_internal("http://127.0.0.1/", allow_internal=True) is False

    def test_never_raises_on_junk(self):
        from dhole_mcp.security import url_targets_internal
        for junk in ["", "   ", "http://", "file:///etc/passwd", "x" * 5000]:
            assert url_targets_internal(junk) is True


class TestTcpPreflightIsNotAPortOracle:
    """预探测本身是一次裸 TCP 连接，且分类会原样回报给 agent。

    它**不拨**内网地址，且对所有内网目标同形返回 —— 这两条是"不做端口扫描 oracle"
    的全部依据。曾经它还顺手把这件事说成 `dns_failure`，而 10.1.2.3 是一颗从没被问
    过的字面 IP：掩盖geo不是防oracle的手段，同形返回才是。现在它说自己是什么。
    """

    @pytest.mark.parametrize("url", [
        "http://127.0.0.1:9999/", "http://[::1]/", "http://169.254.169.254/",
        "http://10.1.2.3:6379/", "http://0x7f000001/",
    ])
    def test_internal_targets_never_connect(self, url, monkeypatch):
        import socket
        from dhole_mcp.fetcher import tcp_preflight

        def _boom(*a, **k):
            raise AssertionError(f"tcp_preflight connected to {a!r}")
        monkeypatch.setattr(socket, "create_connection", _boom)
        ok, category = tcp_preflight(url, timeout=0.2)
        assert ok is False
        # 一个标签，覆盖开着的和死着的：端口状态一个字都没漏
        assert category == "private_target_blocked"

    def test_the_internal_verdict_is_the_same_whether_the_port_is_open(
            self, monkeypatch):
        """oracle 检查：同一个内网地址，"有人听"与"没人听"必须给出同一个回执。

        上面那条只测了"不拨"。这一条把 create_connection 换成**成功**再比一次 ——
        如果哪天有人把这一支改成先拨后判，那才是真的把端口表交出去了。
        """
        import socket
        from dhole_mcp.fetcher import tcp_preflight

        class _Sock:
            def close(self):
                pass

        monkeypatch.setattr(socket, "create_connection",
                            lambda addr, timeout=None: _Sock())
        assert tcp_preflight("http://10.1.2.3:6379/", timeout=0.2) == (
            False, "private_target_blocked")

    def test_public_target_still_probes(self, monkeypatch):
        import socket
        from dhole_mcp.fetcher import tcp_preflight

        calls = []

        class _Sock:
            def close(self):
                pass

        def _fake(addr, timeout=None):
            calls.append(addr)
            return _Sock()
        monkeypatch.setattr(socket, "create_connection", _fake)
        ok, category = tcp_preflight("https://example.com/x", timeout=0.2)
        assert ok is True and category == ""
        assert calls and calls[0][0] == "example.com"

    def test_refused_port_still_reports_refused(self, monkeypatch):
        import socket
        from dhole_mcp.fetcher import tcp_preflight

        def _refused(addr, timeout=None):
            raise ConnectionRefusedError
        monkeypatch.setattr(socket, "create_connection", _refused)
        assert tcp_preflight("https://example.com/") == (False, "connection_refused")


# ============================================================================
# 来源：test_nested_schema_g14.py
# ============================================================================
# G14 回归：容器选择器 + 每项子 schema（重复记录不并被压平）。

QUOTES = """<html><body>
<div class="quote"><span class="text">First</span><small class="author">Alice</small>
  <div class="tags"><a class="tag">time</a><a class="tag">change</a></div></div>
<div class="quote"><span class="text">Second</span><small class="author">Bob</small>
  <div class="tags"><a class="tag">mirror</a></div></div>
</body></html>"""


def _schema(container="quote", child="text"):
    return {"properties": {
        "items": {"type": "array", "selector": f".{container}",
                  "properties": {child: {"selector": f".{child}"}}}}}


class TestRecordsKeepTheirGrouping:

    def test_each_container_becomes_its_own_record(self):
        out = extract_structured(QUOTES, {
            "properties": {"quotes": {
                "type": "array", "selector": ".quote",
                "properties": {"text": {"selector": ".text"},
                               "author": {"selector": ".author"}}}}})

        assert out["quotes"] == [{"text": "First", "author": "Alice"},
                                 {"text": "Second", "author": "Bob"}]

    def test_a_repeated_child_belongs_to_its_own_record(self):
        """这一条就是报告的那次复现：tags 不能是两页合并后的四个。"""
        out = extract_structured(QUOTES, {"properties": {"quotes": {
            "type": "array", "selector": ".quote",
            "properties": {"tags": {"type": "array", "selector": ".tag"}}}}})

        assert [q["tags"] for q in out["quotes"]] == [["time", "change"], ["mirror"]]
        assert "time" not in out["quotes"][1]["tags"], "第二个容器的记录里混进了第一个的标签"

    def test_two_levels_deep(self):
        html = """<html><body>
        <section class="row"><h2>R1</h2>
          <span class="cell">a</span><span class="cell">b</span></section>
        <section class="row"><h2>R2</h2><span class="cell">c</span></section>
        </body></html>"""
        out = extract_structured(html, {"properties": {"rows": {
            "type": "array", "selector": ".row",
            "properties": {"title": {"selector": "h2"},
                           "cells": {"type": "array", "selector": ".cell"}}}}})

        assert out["rows"] == [{"title": "R1", "cells": ["a", "b"]},
                               {"title": "R2", "cells": ["c"]}]

    def test_the_json_schema_spelling_of_items_is_accepted(self):
        out = extract_structured(QUOTES, {"properties": {"quotes": {
            "selector": ".quote",
            "items": {"properties": {"text": {"selector": ".text"}}}}}})

        assert [q["text"] for q in out["quotes"]] == ["First", "Second"]

    def test_a_single_container_field_gives_one_object_not_a_list(self):
        out = extract_structured(QUOTES, {"properties": {"first": {
            "selector": ".quote",
            "properties": {"text": {"selector": ".text"}}}}})

        assert out["first"] == {"text": "First"}

    def test_grouping_without_a_container_stays_in_the_same_scope(self):
        """没有 selector 的 properties 只是给字段分组，作用域跟着父级。"""
        out = extract_structured('<html><body><h1>H</h1></body></html>', {
            "properties": {"head": {"properties": {"title": {"selector": "h1"}}}}})

        assert out["head"] == {"title": "H"}

    def test_an_attribute_can_be_read_inside_a_record(self):
        html = """<html><body>
        <div class="item"><a href="/1">one</a></div>
        <div class="item"><a href="/2">two</a></div></body></html>"""
        out = extract_structured(html, {"properties": {"items": {
            "type": "array", "selector": ".item",
            "properties": {"url": {"selector": "a", "attribute": "href"}}}}})

        assert [i["url"] for i in out["items"]] == ["/1", "/2"]

    def test_records_do_not_borrow_page_metadata(self):
        """页面级兜底（OG/JSON-LD）回答的是「这个字段在页面上被叫作什么」；记录里
        用它填一个没命中的子字段，等于把别处的值当成这一行的答案。"""
        out = extract_structured('<html><head><meta property="og:title" content="Page">'
                                 "</head><body><div class='row'></div></body></html>",
                                 {"properties": {"rows": {
                                     "type": "array", "selector": ".row",
                                     "properties": {"title": {}}}}},
                                 metadata={"og:title": "Page"})

        assert out["rows"][0]["title"] != "Page", out["rows"]


class TestEmptyAndBounded:

    def test_a_container_that_is_not_there_gives_an_empty_list(self):
        out = extract_structured(QUOTES, _schema(container="product"))

        assert out["items"] == []

    def test_containers_present_but_fields_absent_are_still_no_answer(self):
        """外壳在、内容全空：这不是「抽到了 N 条」，schema_no_match 必须还能发现。"""
        out = extract_structured('<html><body><div class="quote"></div>'
                                 '<div class="quote"></div></body></html>',
                                 _schema())

        assert all(_schema_value_empty(r) for r in out["items"]), out["items"]

    def test_the_emptiness_check_recurses(self):
        assert _schema_value_empty([{"text": ""}, {"text": "  "}]) is True
        assert _schema_value_empty([{"text": "real"}]) is False
        assert _schema_value_empty({"a": {"b": ""}}) is True
        assert _schema_value_empty({"a": {"b": "x"}}) is False

    def test_the_record_count_is_capped_and_visible(self):
        html = "<html><body>" + "".join(
            f'<div class="quote"><span class="text">q{i}</span></div>'
            for i in range(MAX_RECORDS_PER_FIELD + 50)) + "</body></html>"

        out = extract_structured(html, _schema())

        assert len(out["items"]) == MAX_RECORDS_PER_FIELD
        assert out["items"][-1]["text"] == f"q{MAX_RECORDS_PER_FIELD - 1}"


class TestNestedSelectorsAreStillGuarded:
    """守卫本身拒什么由 security 的测试管；这里要证的是**走到子层**：过去循环只
    遍历第一层，嵌套字段的选择器是没被看过一眼就交给 lxml 的。

    所以每个用例都拿一个「一定被拒」的输入（超长 / 非字符串）放在不同的嵌套位置上。
    """

    def test_a_bad_nested_selector_is_reached_and_rejected(self):
        schema = {"properties": {"rows": {
            "type": "array", "selector": ".row",
            "properties": {"x": {"selector": "div" * 4000}}}}}

        with pytest.raises(SecurityError):
            _validate_schema_selectors(schema["properties"])

    def test_a_bad_doubly_nested_selector_is_rejected(self):
        schema = {"properties": {"rows": {"selector": ".row", "properties": {
            "cells": {"type": "array", "selector": ".cell",
                      "properties": {"v": {"selector": ".c" * 4000}}}}}}}

        with pytest.raises(SecurityError):
            _validate_schema_selectors(schema["properties"])

    def test_the_items_spelling_is_guarded_too(self):
        schema = {"properties": {"rows": {"selector": ".row", "items": {
            "properties": {"v": {"selector": 123}}}}}}

        with pytest.raises(SecurityError):
            _validate_schema_selectors(schema["properties"])

    def test_an_unbounded_nesting_is_refused_rather_than_recursed_forever(self):
        deep: dict = {}
        node = deep
        for _ in range(30):
            node["selector"] = ".x"
            node["properties"] = {"kid": {}}
            node = node["properties"]

        with pytest.raises(ValueError):
            _validate_schema_selectors(deep)

    def test_a_valid_nested_schema_passes_untouched(self):
        schema = {"properties": {"quotes": {"type": "array", "selector": ".quote",
                                            "properties": {"t": {"selector": ".text"}}}}}
        _validate_schema_selectors(schema["properties"])
        assert schema["properties"]["quotes"]["selector"] == ".quote"


NESTED_QUOTES = {"properties": {"quotes": {
    "type": "array", "selector": ".quote",
    "properties": {"text": {"selector": ".text"},
                   "tags": {"type": "array", "selector": ".tag"}}}}}


def _stub_tier(monkeypatch, seen: dict) -> dict:
    """Stand in for the fetch tier, binding to its REAL signature.

    A stub that swallows ``*args, **kwargs`` accepts any misordering, which is how
    the second half of G14 stayed invisible: every existing schema test replaces
    ``_auto_escalate`` itself, so the call site can hand ``cookies`` an int and the
    suite still goes green.
    """
    import inspect

    sig = inspect.signature(MasterFetchServer._auto_escalate)

    async def fake(self, *args, **kwargs):
        bound = dict(sig.bind(self, *args, **kwargs).arguments)
        bound.pop("self", None)
        seen.update(bound)
        return ResponseModel(
            status=200, content=[QUOTES], url=bound.get("url", ""),
            fetcher_used="http", extracted_type="html", content_type="text/html",
        )

    monkeypatch.setattr(MasterFetchServer, "_auto_escalate", fake)
    return seen


class TestTheSchemaBranchCallsTheTierInOrder:
    """G14 的另一半不在抽取器里，在 ``web_read`` 的 schema 分支：那次
    ``_auto_escalate`` 调用漏了一个位置参数（``use_trafilatura``，17.0 已删），之后每个参数集体
    错一位 —— ``cookies`` 因此收到 ``_SCHEMA_SOURCE_MAX_CHARS``，被
    ``_safe_cookie_dict`` 当成 cookie 列表遍历。症状是**任何**带 schema 的调用都回
    ``Error: 'int' object is not iterable``，报告把它记成了「嵌套 schema 的 bug」。

    这一族（参数错位、抽取器被喂了被截断的 HTML）过去全靠真网络才发现，所以这里把
    参数名逐个钉住：错一位就有一条断言红，而不是静默地把 int 送到 cookie 的位置上。
    """

    def test_every_tier_parameter_gets_its_own_value(self, monkeypatch):
        """17.0 §3-17 之后这一条读的是**存储 → 取回层**那一段。

        `cookies` / `proxy` 不再是 web_read 的参数：调用方递不进来，但 `_auto_escalate`
        那 18 个位置一个没少（内部梯子仍然要它们），值改由 cred_defaults 的存储按 host
        回答。参数错位在公共面上少了一个入口，不等于这一族断言可以撤 —— 所以这里把
        cookie 存进 example.com 的 scope，再断言它原样抵达取回层。
        """
        cookies = [{"name": "sid", "value": "v"}]
        identity.set_value("cookies", cookies, scope="example.com")
        identity.set_value("proxy", "http://proxy.test:8080")
        seen = _stub_tier(monkeypatch, {})

        asyncio.run(MasterFetchServer().web_read(
            url="https://example.com", schema=NESTED_QUOTES, cache_ttl=0,
            anti_detect="basic", timeout=12345,
        ))

        assert seen["extraction_type"] == "html", "schema 分支必须抓原始 HTML"
        assert seen["cookies"] == cookies, "存进 host scope 的 cookie 没抵达取回层"
        assert seen["proxy"] == "http://proxy.test:8080"
        # extra_headers 今天没有任何来源（web_read 里它被写死成 None）：凭据形状的
        # 头走 auth（它会说清自己的 host），其余是梯子的事。名字仍然占着它自己的
        # 那一位 —— 一位不错，才轮到下面那些断言说话。
        assert seen["extra_headers"] is None
        assert seen["timeout"] == 12345
        # 原来的 `assert seen["wait"] == 7` 断的是渲染时机；B5-13d 之后时机不再是
        # 参数，那 5 秒成了浏览器层认出壳页才付的钱，证人搬到 tests/test_render_settle_b5.py。
        # B5-13a 之后这一档不再有"每颗一个独一值"的公共口：UA/伪装旋钮由
        # anti_detect 一步解析，公开传 useragent 已经是不支持的参数。所以这里
        # 读的是**档位解析后的形状** —— basic 档是四档里唯一
        # "画布+WebRTC 开、CF 关、资源不全省"的组合，错位一样会红；而
        # useragent 必须是 None（UA 跟着指纹档，不跟着调用方）。
        assert seen["hide_canvas"] is True and seen["block_webrtc"] is True
        assert seen["solve_cloudflare"] is False
        assert seen["load_everything"] is False
        assert seen["useragent"] is None
        # 抽取器要看到整篇文档，所以喂给取回层的不是调用方的返回上限（见
        # test_live_audit_regressions）；分页 offset 由 JSON 的 re-chunk 负责，
        # 取回层拿到 0。
        assert seen["max_chars"] == _SCHEMA_SOURCE_MAX_CHARS
        assert seen["offset"] == 0

    def test_a_conditional_schema_fetch_still_bypasses_the_cache(self, monkeypatch):
        """带版本标记的那一趟 schema 抓取问的是源站，不是 dhole 的记忆。

        17.0 §6 之后标记不再是参数（`if_modified_since` 下了线），通道是 `_REVALIDATE`；
        17.0 收尾后这条通道连生产端也没有了（喂它的 `web_watch` 随四颗状态工具下线），
        所以这里直接驱动它 —— 钉的是「条件请求不许读缓存」，与谁发起无关。
        """
        seen = _stub_tier(monkeypatch, {})

        tok = _REVALIDATE.set({"last_modified": "2020-01-01"})
        try:
            asyncio.run(MasterFetchServer().web_read(
                url="https://example.com", schema=NESTED_QUOTES, cache_ttl=3600,
            ))
        finally:
            _REVALIDATE.reset(tok)

        assert seen["conditional"]
        assert seen["cache_ttl"] == 0, "条件请求问的是源站，不是 dhole 的记忆"

    def test_the_nested_schema_comes_back_as_grouped_records(self, monkeypatch):
        """整条链路的验收：抽取器的单测全绿时，工具仍可能一个字节都返不回来。"""
        _stub_tier(monkeypatch, {})

        out = asyncio.run(MasterFetchServer().web_read(
            url="https://example.com", schema=NESTED_QUOTES, cache_ttl=0,
        ))

        assert out.error == "", out.error
        assert out.extracted_type == "structured"
        payload = json.loads(out.content[0])
        assert [q["tags"] for q in payload["quotes"]] == [["time", "change"], ["mirror"]]


# ============================================================================
# 来源：test_a_selector_the_engine_cannot_compile_is_refused.py
# ============================================================================
# R2刀2（P2-4）：编译不了的 CSS 选择器曾被静默丢弃，200 + 整页未过滤内容回来。

BROKEN = "a >> b {;}"
URL = "https://example.test/article"

HTML = ("<html><head><title>Widget</title></head><body>"
        "<article><h1>Widgets</h1><p>body text long enough to extract here</p>"
        "</article></body></html>")


def _let_it_fetch(monkeypatch):
    """把取回那一步换成记账的替身：验证到底有没有发生在发包之前，一眼可见。

    `_auto_escalate(self, url, extraction_type, css_selector, ...)` 是按位置传的，
    替身得照同一个顺序接下来，否则测的是签名而不是行为。
    """
    seen: list = []

    async def fake(self, url, extraction_type=None, css_selector=None, *a, **tier):
        seen.append({"url": url, "css_selector": css_selector,
                     "schema": tier.get("schema")})
        return ResponseModel(url=url, status=200, content=[HTML],
                             content_ok=True)

    monkeypatch.setattr(MasterFetchServer, "_auto_escalate", fake)
    return seen


class TestTheGuardItself:
    """安全层的新增判决：只给抽取层用，报错点名参数与解析器的原话。"""

    def test_an_uncompilable_selector_is_refused(self):
        from dhole_mcp.security import validate_html_css_selector
        with pytest.raises(SecurityError):
            validate_html_css_selector(BROKEN)

    def test_the_message_names_the_parameter_and_the_parser(self):
        from dhole_mcp.security import validate_html_css_selector
        with pytest.raises(SecurityError) as exc:
            validate_html_css_selector(BROKEN)
        text = str(exc.value)
        assert "css_selector" in text, text
        assert BROKEN in text, text

    def test_the_param_name_follows_the_caller(self):
        """同一条守卫服务三颗参数，报错点名的是**这一颗**。"""
        from dhole_mcp.security import validate_html_css_selector
        with pytest.raises(SecurityError) as exc:
            validate_html_css_selector(BROKEN, param="nested_selector")
        assert "nested_selector" in str(exc.value)

    def test_the_injection_guard_still_wins(self):
        """危险内容型的拒绝零回归：注入守卫先判，编译检查只是补上另一类。"""
        from dhole_mcp.security import validate_html_css_selector
        with pytest.raises(SecurityError, match="dangerous"):
            validate_html_css_selector("a[href='javascript:alert(1)']")

    @pytest.mark.parametrize("selector", [
        "article", ".main-content", "div.content > p", 'a[href*="/works/"]',
        "#x li:nth-child(2)", "div:has(> button)",
    ])
    def test_ordinary_extraction_selectors_still_pass(self, selector):
        from dhole_mcp.security import validate_html_css_selector
        assert validate_html_css_selector(selector) == selector

    def test_none_and_empty_stay_none(self):
        from dhole_mcp.security import validate_html_css_selector
        assert validate_html_css_selector(None) is None
        assert validate_html_css_selector("   ") is None

    def test_the_browser_tier_is_not_lxml_judged(self):
        """`:visible` 是 Chromium 认、lxml 不认的选择器。

        `wait_selector` / actions 的 selector 活在浏览器层，用抽取层的尺判它，
        等于把一颗本来能用的能力判死。这条钉住两侧的分界。
        """
        assert validate_css_selector("div:visible") == "div:visible"


class TestTheCallRefusesBeforeFetching:

    @pytest.mark.asyncio
    async def test_a_broken_css_selector_is_an_invalid_request(self, monkeypatch):
        seen = _let_it_fetch(monkeypatch)
        out = await MasterFetchServer().web_read(url=URL, css_selector=BROKEN)
        assert out.error.startswith("invalid_request"), out.error
        assert "css_selector" in out.error, out.error
        assert out.content_ok is False and out.content == []
        assert seen == [], "发请求前就该拒掉"

    @pytest.mark.asyncio
    async def test_a_working_selector_still_reaches_the_fetcher(self, monkeypatch):
        seen = _let_it_fetch(monkeypatch)
        out = await MasterFetchServer().web_read(url=URL, css_selector="article")
        assert not out.error, out.error
        assert seen[0]["css_selector"] == "article"

    @pytest.mark.asyncio
    async def test_a_schema_selector_gets_the_same_gate(self, monkeypatch):
        """schema 的 selector 走 schema 那条既有信封（前缀已由别的测试钉住）；这里要钉
        的是编译不过的选择器**在这里也被拒**，而不是等到提取期静默失效。"""
        seen = _let_it_fetch(monkeypatch)
        out = await MasterFetchServer().web_read(
            url=URL, schema={"properties": {"title": {"selector": BROKEN}}})
        assert out.status == 0 and out.content_ok is False
        assert "selector" in out.error and BROKEN in out.error, out.error
        assert "title" in out.error, out.error
        assert seen == [], "发请求前就该拒掉"

    @pytest.mark.asyncio
    async def test_a_nested_selector_gets_the_same_gate(self, monkeypatch):
        _let_it_fetch(monkeypatch)
        out = await MasterFetchServer().web_read(
            url=URL, nested_selector=BROKEN,
            nested_fields=[{"name": "作品", "selector": ".work-item a"}])
        assert out.error.startswith("invalid_request"), out.error

    @pytest.mark.asyncio
    async def test_a_nested_field_selector_gets_the_same_gate(self, monkeypatch):
        _let_it_fetch(monkeypatch)
        out = await MasterFetchServer().web_read(
            url=URL, nested_selector=".work-item a",
            nested_fields=[{"name": "作品", "selector": BROKEN}])
        assert out.error.startswith("invalid_request"), out.error


class TestTheExtractionTimeEdge:
    """编译已经前移，提取期仍可能出事（lxml 在真文档上抛的边角）。那种情况下正文是
    整页的，响应必须说出来 —— 与 XML content-type 那条同款措辞（"was NOT applied"）。"""

    def test_a_runtime_selector_failure_records_the_note(self, monkeypatch):
        import lxml.cssselect

        from dhole_mcp.context import _arg_notes_scope
        from dhole_mcp.trafilatura_extractor import extract_with_trafilatura

        class _Boom:
            def __init__(self, selector, *a, **kw):
                self.selector = selector

            def __call__(self, tree):
                raise RuntimeError("boom at match time")

        # 构造成功（校验过的选择器走到这里），匹配时才炸 —— 校验期测不出的那一类
        monkeypatch.setattr(lxml.cssselect, "CSSSelector", _Boom)

        class _Page:
            url = "https://example.test/article"
            status = 200
            content_type = "text/html"
            encoding = "utf-8"
            body = HTML.encode()
            content = HTML

        with _arg_notes_scope() as notes:
            out = extract_with_trafilatura(_Page(), extraction_type="markdown",
                                           css_selector="article")

        assert any("was NOT applied" in n and "article" in n for n in notes), notes
        assert out, "整页正文仍然带回：要说的是「没过滤」，不是「没内容」"

    def test_a_working_selector_records_nothing(self):
        from dhole_mcp.context import _arg_notes_scope
        from dhole_mcp.trafilatura_extractor import extract_with_trafilatura

        class _Page:
            url = "https://example.test/article"
            status = 200
            content_type = "text/html"
            encoding = "utf-8"
            body = HTML.encode()
            content = HTML

        with _arg_notes_scope() as notes:
            extract_with_trafilatura(_Page(), extraction_type="markdown",
                                     css_selector="article")

        assert not any("NOT applied" in n for n in notes), notes
