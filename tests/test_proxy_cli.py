"""Tests for `dhole proxy` and the pool-management helpers.

The rotation side (``ProxyPool``) has its own coverage elsewhere; what matters
here is the write side. Every failure mode must be LOUD - a duplicate, an
unsupported scheme, an out-of-range index and a full pool all have to raise or
exit non-zero rather than quietly doing nothing. A user who thinks a proxy was
added and finds out three searches later that it never was has no way to tell.

Credentials are the other half: they are stored in plaintext (documented), so
nothing the CLI prints may contain the password.
"""

import json

import pytest

from dhole_mcp import search_proxy


def _run(argv: list[str]) -> int:
    from dhole_mcp.server import _cmd_proxy
    return _cmd_proxy(argv)


class TestAddProxy:
    def test_returns_running_total(self):
        assert search_proxy.add_proxy("socks5://10.0.0.1:1080") == 1
        assert search_proxy.add_proxy("http://10.0.0.2:3128") == 2

    def test_writes_expected_json_shape(self):
        search_proxy.add_proxy("socks5://10.0.0.1:1080")
        data = json.loads(search_proxy._config_path().read_text(encoding="utf-8"))
        assert data == {"proxies": ["socks5://10.0.0.1:1080"]}

    def test_creates_the_config_file(self):
        path = search_proxy._config_path()
        assert not path.exists()
        search_proxy.add_proxy("socks5://10.0.0.1:1080")
        assert path.exists()

    def test_duplicate_raises(self):
        search_proxy.add_proxy("socks5://10.0.0.1:1080")
        with pytest.raises(ValueError, match="already configured"):
            search_proxy.add_proxy("socks5://10.0.0.1:1080")

    @pytest.mark.parametrize("bad", [
        "ftp://10.0.0.1:21",   # unsupported scheme
        "10.0.0.1:8080",       # no scheme at all
        "not a proxy",
        "",
        "   ",
    ])
    def test_invalid_entry_raises(self, bad):
        with pytest.raises(ValueError, match="Invalid proxy"):
            search_proxy.add_proxy(bad)

    def test_pool_full_raises(self):
        for i in range(search_proxy.MAX_PROXIES):
            search_proxy.add_proxy(f"socks5://10.0.0.{i}:1080")
        with pytest.raises(ValueError, match="pool is full"):
            search_proxy.add_proxy("socks5://10.0.0.99:1080")

    def test_max_proxies_is_the_documented_number(self):
        assert search_proxy.MAX_PROXIES == 20


class TestRemoveAndClear:
    def test_remove_returns_the_removed_entry(self):
        search_proxy.add_proxy("socks5://10.0.0.1:1080")
        search_proxy.add_proxy("socks5://10.0.0.2:1080")
        assert search_proxy.remove_proxy(0) == "socks5://10.0.0.1:1080"
        assert search_proxy.list_proxies() == ["socks5://10.0.0.2:1080"]

    def test_remove_out_of_range_raises(self):
        search_proxy.add_proxy("socks5://10.0.0.1:1080")
        with pytest.raises(IndexError, match="out of range"):
            search_proxy.remove_proxy(5)

    def test_remove_on_empty_pool_raises(self):
        with pytest.raises(IndexError, match="No proxies configured"):
            search_proxy.remove_proxy(0)

    def test_clear_reports_count_and_empties_the_file(self):
        search_proxy.add_proxy("socks5://10.0.0.1:1080")
        search_proxy.add_proxy("socks5://10.0.0.2:1080")
        assert search_proxy.clear_proxies() == 2
        assert search_proxy.list_proxies() == []

    def test_clear_on_empty_pool_returns_zero(self):
        assert search_proxy.clear_proxies() == 0


class TestRedaction:
    def test_credentials_are_masked(self):
        assert search_proxy._redact("http://user:pass@10.0.0.1:3128") == \
            "http://***:***@10.0.0.1:3128"

    def test_credential_free_proxy_is_unchanged(self):
        assert search_proxy._redact("socks5://10.0.0.1:1080") == \
            "socks5://10.0.0.1:1080"

    def test_user_only_credentials_are_masked(self):
        assert "user" not in search_proxy._redact("http://user@10.0.0.1:3128")


class TestEnvProxySource:
    def test_dhole_var_wins(self, monkeypatch):
        monkeypatch.setenv("DHOLE_SEARCH_PROXY", "socks5://1.1.1.1:1080")
        monkeypatch.setenv("HTTPS_PROXY", "http://2.2.2.2:8080")
        assert search_proxy._env_proxy_source() == "DHOLE_SEARCH_PROXY"

    def test_falls_back_to_https_proxy(self, monkeypatch):
        monkeypatch.delenv("DHOLE_SEARCH_PROXY", raising=False)
        monkeypatch.setenv("HTTPS_PROXY", "http://2.2.2.2:8080")
        assert search_proxy._env_proxy_source() == "HTTPS_PROXY"

    def test_empty_when_nothing_is_set(self, monkeypatch):
        for name in ("DHOLE_SEARCH_PROXY", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY"):
            monkeypatch.delenv(name, raising=False)
        assert search_proxy._env_proxy_source() == ""

    def test_source_name_matches_what_the_loader_reads(self, monkeypatch):
        """The name reported must be the one actually feeding the pool."""
        monkeypatch.delenv("DHOLE_SEARCH_PROXY", raising=False)
        monkeypatch.delenv("HTTP_PROXY", raising=False)
        monkeypatch.delenv("ALL_PROXY", raising=False)
        monkeypatch.setenv("HTTPS_PROXY", "socks5://3.3.3.3:1080")
        assert search_proxy._env_proxy_source() == "HTTPS_PROXY"
        assert search_proxy._read_env_var() == ["socks5://3.3.3.3:1080"]


class TestResetPool:
    def test_reset_drops_the_cached_singleton(self, monkeypatch):
        monkeypatch.setenv("DHOLE_SEARCH_PROXY", "socks5://1.1.1.1:1080")
        assert search_proxy.get_proxy_pool() is not None
        search_proxy.reset_pool()
        assert search_proxy._pool is None

    def test_pool_is_rebuilt_on_next_access(self, monkeypatch):
        monkeypatch.setenv("DHOLE_SEARCH_PROXY", "socks5://1.1.1.1:1080")
        first = search_proxy.get_proxy_pool()
        search_proxy.reset_pool()
        second = search_proxy.get_proxy_pool()
        assert second is not None and second is not first


class TestProxyCli:
    """The CLI is a thin wrapper - assert exit codes and safety, not layout."""

    def test_list_on_empty_pool_says_so(self, capsys):
        assert _run(["list"]) == 0
        assert "none configured" in capsys.readouterr().out

    def test_list_is_the_default_action(self, capsys):
        assert _run([]) == 0
        assert "proxy pool" in capsys.readouterr().out

    def test_add_then_list_shows_the_proxy(self, capsys):
        assert _run(["add", "socks5://10.0.0.1:1080"]) == 0
        assert _run(["list"]) == 0
        assert "10.0.0.1:1080" in capsys.readouterr().out

    def test_add_without_arguments_is_usage_error(self, capsys):
        assert _run(["add"]) == 2
        assert "usage" in capsys.readouterr().out

    def test_add_all_invalid_exits_nonzero(self, capsys):
        assert _run(["add", "ftp://10.0.0.1:21"]) == 2
        assert search_proxy.list_proxies() == []

    def test_bulk_add_skips_bad_entries_without_aborting(self, capsys):
        rc = _run(["add", "ftp://bad", "socks5://10.0.0.1:1080"])
        assert rc == 0
        assert search_proxy.list_proxies() == ["socks5://10.0.0.1:1080"]

    def test_remove_by_index(self, capsys):
        search_proxy.add_proxy("socks5://10.0.0.1:1080")
        assert _run(["remove", "0"]) == 0
        assert search_proxy.list_proxies() == []

    def test_remove_non_numeric_index_is_usage_error(self, capsys):
        assert _run(["remove", "abc"]) == 2

    def test_remove_out_of_range_exits_nonzero(self, capsys):
        search_proxy.add_proxy("socks5://10.0.0.1:1080")
        assert _run(["remove", "9"]) == 2

    def test_clear_empties_the_pool(self, capsys):
        search_proxy.add_proxy("socks5://10.0.0.1:1080")
        assert _run(["clear"]) == 0
        assert search_proxy.list_proxies() == []

    def test_unknown_subcommand_exits_nonzero(self, capsys):
        assert _run(["frobnicate"]) == 2

    def test_password_never_reaches_stdout(self, capsys):
        """The file is plaintext by design; the terminal must not be."""
        search_proxy.add_proxy("http://user:secretpw@10.0.0.1:3128")
        assert _run(["list"]) == 0
        out = capsys.readouterr().out
        assert "secretpw" not in out
        assert "***" in out

    def test_password_not_printed_by_add_either(self, capsys):
        _run(["add", "http://user:secretpw@10.0.0.1:3128"])
        assert "secretpw" not in capsys.readouterr().out

    def test_env_sourced_proxy_is_labelled_not_written(self, monkeypatch, capsys):
        monkeypatch.setenv("HTTPS_PROXY", "http://9.9.9.9:8080")
        assert _run(["list"]) == 0
        out = capsys.readouterr().out
        assert "HTTPS_PROXY" in out
        assert search_proxy.list_proxies() == []
