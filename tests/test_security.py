"""Adversarial security tests: URL validation, SSRF bypass vectors,
CSS selector validation, header injection prevention, proxy validation,
timeout validation, search query sanitization, API key redaction.

Every SSRF bypass vector from public CVEs and CTFs is tested against the real
validate_url function. No mocks. The function either accepts or rejects each
input; we assert the correct outcome for every vector.
"""

import pytest
from dhole_mcp.security import (
    validate_url, validate_css_selector, validate_headers, validate_proxy,
    validate_timeout, validate_search_query, redact_api_key, SecurityError,
    _normalize_ip_notation,
)


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
        monkeypatch.setattr(sec, "_dns_recheck_enabled", lambda: True)
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
        monkeypatch.setattr(sec, "_dns_recheck_enabled", lambda: True)
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
    """预探测本身是一次裸 TCP 连接，且分类会原样回报给 agent。"""

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
        # 与"真的不可达"同形：不暴露端口开/关
        assert category == "dns_failure"

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
