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
    monkeypatch.setattr(paths, "_legacy_migrate_done", True)


@pytest.fixture(autouse=True)
def _no_real_robots_fetch(monkeypatch):
    """Keep the suite off the network for robots.txt.

    v16.0 made ``smart_fetch`` consult robots.txt before every tier. Any test
    that monkeypatches the tier functions (the usual way to test the fetch
    pipeline) would otherwise add a REAL ``GET /robots.txt`` per origin — slow,
    network-dependent, and a source of flakes. ``DHOLE_IGNORE_ROBOTS=1`` makes
    ``robots.check()`` short-circuit to "allowed", which is exactly the
    pre-v16.0 behaviour those tests were written against.

    ``tests/test_robots.py`` owns the compliance behaviour itself: it deletes
    this env var locally and injects a fake fetcher into ``RobotsCache``. The
    cache is reset on teardown so a test that primed it cannot leak rules into
    an unrelated test.
    """
    monkeypatch.setenv("DHOLE_IGNORE_ROBOTS", "1")
    yield
    from dhole_mcp.robots import reset_robots_cache
    reset_robots_cache()


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


@pytest.fixture(autouse=True)
def _no_real_home_state_writes(request, monkeypatch, tmp_path):
    """测试绝不把产品状态写进真实的 ``~/.dhole``。

    驱动真 ``metasearch()`` 的用例（连接冷却、产出统计）会落盘。实测抓到过：测试把
    一条 brightdata 记录写进了用户真实的 ``~/.dhole/engine_stats.json``。

    三个状态文件都是惰性求值（函数而非常量），所以在这里指到临时目录就能整体接管。
    也不动 ``paths.home`` 本身：DHOLE_HOME 那组用例要在它的语义上断言。

    ``real_state_paths`` 标记的用例是**故意**验这些路径由 paths.py 派生的，接管理会
    让它们验不到东西 —— 让它们选择退出。
    """
    if request.node.get_closest_marker("real_state_paths"):
        yield
        return
    from dhole_mcp import search_metasearch as ms

    monkeypatch.setattr(ms, "_engine_stats_file", lambda: str(tmp_path / "engine_stats.json"))
    monkeypatch.setattr(ms, "_circuit_state_file", lambda: str(tmp_path / "circuit_breaker.json"))
    from dhole_mcp import search as _search
    monkeypatch.setattr(_search, "_feedback_file", lambda: str(tmp_path / "search_feedback.json"))
    monkeypatch.setattr(ms, "_ENGINE_YIELD", {})
    monkeypatch.setattr(ms, "_engine_stats_last_save", 0.0)
    # The proxy pool config is the fourth writable state file. `dhole proxy add`
    # and any test that exercises add/remove/clear would otherwise write real
    # credentials into the user's ~/.dhole/search_proxies.json.
    from dhole_mcp import search_proxy as _proxy
    monkeypatch.setattr(_proxy, "_config_path", lambda: tmp_path / "search_proxies.json")
    # The cookie jar (G7) is the fifth state file, and the only one holding
    # credentials: without this, a test that fetches with options.session_id
    # writes a real Set-Cookie value into the real ~/.dhole/sessions.db, where it
    # then outlives the test and gets sent by the next unrelated call.
    # reset_for_tests() closes the module-level connection because the DB path is
    # resolved lazily — a connection opened before this redirect would keep
    # writing to the real file.
    from dhole_mcp import sessions as _sessions
    _sessions.reset_for_tests()
    monkeypatch.setattr(_sessions, "_db_path", lambda: str(tmp_path / "sessions.db"))
    # The fetch cache is the sixth state file, and it broke from the other
    # direction: not written by tests so much as READ by them. Ordinary live use
    # fills ~/.dhole/cache.db with real pages, so a test that stubs the fetcher for
    # https://example.com/ gets a cache HIT and never reaches its own stub — two
    # tests in test_bug_report2_regressions flipped exactly that way once the DB
    # happened to hold an example.com entry. A test's view of the cache should
    # depend only on what that test put into it.
    #
    # No connection to close here (unlike sessions.db): every cache operation opens
    # aiosqlite fresh and _db_initialized is keyed by path, and tmp_path is unique
    # per test. Models live under paths.cache_dir() by a separate route, so this
    # does not move the ~90MB reranker tree.
    from dhole_mcp import cache as _cache
    monkeypatch.setattr(_cache, "_CACHE_DIR", tmp_path)
    yield
    _sessions.reset_for_tests()


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
