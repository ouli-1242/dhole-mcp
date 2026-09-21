"""Pytest fixtures for Dhole tests."""

import socket

import pytest
import tempfile
from pathlib import Path


@pytest.fixture(autouse=True)
def _no_real_home_migration(monkeypatch):
    """Keep tests from ever touching the REAL user home via the migration.

    ``migrate_legacy_cache_dir()`` fires on the first cache access / model
    lookup, and running the suite would otherwise move a real
    ``~/.dhole_mcp_cache`` into the real ``~/.dhole``. That is product behavior,
    not a test side effect — so it is globally disabled here, and
    ``tests/test_paths.py`` resets the flag itself to exercise the migration
    against a fake home.
    """
    from dhole_mcp import paths
    monkeypatch.setattr(paths, "_legacy_migrated", True)


@pytest.fixture(autouse=True)
def _offline_dns(monkeypatch, request):
    """Keep the suite independent of the machine's resolver.

    validate_url() rechecks DNS by default, so a developer whose hosts file (or a
    polluted resolver) points a test host at 127.0.0.1 would see unrelated tests
    fail — this machine pins github.com and huggingface.co to 127.0.0.1, and both
    appear in tests and in the product's own model download. Tests that exercise
    the check itself patch getaddrinfo locally, which overrides this.

    ``live``-marked tests are the exception: they hit real engines, and a fake
    resolver would make them assert nothing (validate_url would judge a made-up
    public IP instead of the real answer).
    """
    if request.node.get_closest_marker("live"):
        from dhole_mcp import security as _security
        _security._DNS_CHECK_CACHE.clear()
        yield
        _security._DNS_CHECK_CACHE.clear()
        return
    monkeypatch.setattr(
        socket, "getaddrinfo",
        lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "",
                          ("93.184.216.34", 0))],
    )
    from dhole_mcp import security as _security
    _security._DNS_CHECK_CACHE.clear()
    yield


def pytest_addoption(parser):
    """Engine-fixture controls. Deliberately opt-in twice over: ``-m live`` AND
    ``--engine-fixtures``, so a bare ``pytest -m live`` can never hammer five
    search engines from an unsuspecting laptop."""
    parser.addoption(
        "--engine-fixtures", action="store", default=None,
        choices=[None, "check", "capture"],
        help="check = compare live SERP parses against the captured fixtures; "
             "capture = (re)write tests/engine_fixtures/*.html from live engines. "
             "Both require -m live.",
    )


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test artifacts."""
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


@pytest.fixture
def sample_urls():
    """Sample URLs for testing."""
    return {
        "valid_http": "https://example.com/page",
        "valid_https": "https://api.github.com/repos",
        "with_path": "https://example.com/path/to/page?q=1&b=2",
        "with_port": "https://example.com:8080/path",
        "internal_ipv4": "http://127.0.0.1/admin",
        "internal_ipv4_10": "http://10.0.0.1/api",
        "internal_ipv4_192": "http://192.168.1.1/",
        "internal_ipv4_172": "http://172.16.0.1/",
        "internal_ipv6": "http://[::1]:8080/path",
        "localhost": "http://localhost:3000/api",
        "metadata": "http://169.254.169.254/latest/meta-data/",
        "file_scheme": "file:///etc/passwd",
        "javascript_scheme": "javascript:alert(1)",
        "data_scheme": "data:text/html,<script>alert(1)</script>",
        "gopher_scheme": "gopher://evil.com/1",
        "no_scheme": "example.com/path",
        "malformed": "not a url at all",
        "empty": "",
        "oversized": "https://example.com/" + "a" * 9000,
    }


@pytest.fixture
def sample_selectors():
    """Sample CSS selectors for testing."""
    return {
        "valid": "div.content > p",
        "complex": "div.container article.main p.text",
        "oversized": "div > " * 3000,
        "with_script": "script[src='evil.js']",
        "with_style": "style > *",
        "with_js_protocol": "a[href='javascript:alert(1)']",
        "valid_none": None,
        "empty": "",
    }


@pytest.fixture
def sample_headers():
    """Sample HTTP headers for testing."""
    return {
        "valid": {"X-Custom": "value", "Accept": "text/html"},
        "with_newline_name": {"X-Evil\nHeader": "value"},
        "with_newline_value": {"X-Header": "value\r\nInjected: true"},
        "forbidden": {"Host": "evil.com"},
        "empty": {},
        "none": None,
        "too_many": {f"X-Header-{i}": "value" for i in range(100)},
    }
