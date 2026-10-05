"""HTTP 传输面（G25 评审）：rebinding 防护 + 可选 bearer 门。

SDK 的 `TransportSecuritySettings` 缺省显式关闭 Host/Origin 校验，而空列表的
settings 会把一切 Host 拒掉（421）——所以 allowed_hosts/allowed_origins 由
serve() 按 `--host` 显式构建。`DHOLE_HTTP_TOKEN` 未设时传输保持今天的开放
行为，设了即强制：让「要不要鉴权」从「开 HTTP 就全开」变成一次主动选择。

判据（机械的）：serve() 的 HTTP 分支必须真的把 security_settings 传进
StreamableHTTPSessionManager——这层防线忘接线时，其余测试都还全绿，和
B5-13c 那次「假件比真件宽容」的静默失败是同一个形状。
"""

from __future__ import annotations

import inspect

from dhole_mcp.server import (
    MasterFetchServer,
    _http_allowed_hosts,
    _http_allowed_origins,
    _http_token_ok,
)


class TestTheHostAllowlist:

    def test_loopback_names_are_always_allowed(self):
        hosts = _http_allowed_hosts("127.0.0.1")
        for name in ("127.0.0.1", "localhost", "[::1]"):
            assert name in hosts, hosts
            assert f"{name}:*" in hosts, hosts

    def test_a_named_bind_host_is_allowed_with_any_port(self):
        hosts = _http_allowed_hosts("192.168.1.5")
        assert "192.168.1.5" in hosts and "192.168.1.5:*" in hosts, hosts

    def test_a_wildcard_bind_adds_nothing_guessable(self):
        """0.0.0.0 上客户端的 Host 无法预知：名单防不住 rebinding 之外的事，
        所以它自己也不进名单——那个配置该靠 DHOLE_HTTP_TOKEN。"""
        assert "0.0.0.0" not in _http_allowed_hosts("0.0.0.0")

    def test_origins_are_the_http_form_of_the_same_names(self):
        origins = _http_allowed_origins("127.0.0.1", 8765)
        assert "http://127.0.0.1:8765" in origins
        assert "http://localhost:8765" in origins


class TestTheBearerGate:

    def test_the_right_bearer_passes(self):
        assert _http_token_ok({"authorization": "Bearer s3cret"}, "s3cret")

    def test_the_scheme_is_case_insensitive(self):
        assert _http_token_ok({"authorization": "bearer s3cret"}, "s3cret")

    def test_the_wrong_or_absent_token_fails(self):
        assert not _http_token_ok({"authorization": "Bearer nope"}, "s3cret")
        assert not _http_token_ok({}, "s3cret")

    def test_a_prefix_of_the_token_is_not_enough(self):
        assert not _http_token_ok({"authorization": "Bearer s3cre"}, "s3cret")

    def test_the_header_alternative_passes_and_is_trimmed(self):
        assert _http_token_ok({"x-dhole-token": " s3cret "}, "s3cret")

    def test_an_unset_token_never_authenticates(self):
        assert not _http_token_ok({"authorization": "Bearer "}, "")
        assert not _http_token_ok({"x-dhole-token": "anything"}, "")


class TestTheGuardIsActuallyWired:

    def test_serve_hands_security_settings_to_the_session_manager(self):
        src = inspect.getsource(MasterFetchServer.serve)
        assert "security_settings=TransportSecuritySettings(" in src, (
            "HTTP 传输的 rebinding 防护没接线：settings=None 时 SDK 显式关闭校验")

    def test_the_token_gate_wraps_the_app_only_when_a_token_is_set(self):
        src = inspect.getsource(MasterFetchServer.serve)
        assert "if http_token:" in src and "_TokenGate(app, http_token)" in src

    def test_a_non_loopback_bind_without_a_token_prints_a_warning(self):
        src = inspect.getsource(MasterFetchServer.serve)
        assert "DHOLE_HTTP_TOKEN" in src
        assert "WARNING" in src
