"""Pytest fixtures for Dhole tests."""

import socket

import pytest
import tempfile
from pathlib import Path


@pytest.fixture(autouse=True)
def _doctor_off_the_network(request, monkeypatch):
    """doctor 的第 5 项与第 10 项分别会真起一个 stdio 子进程、真发一次搜索与抓取
    —— 计划里的 12 项就有这两条，它们不是可选项。

    套件里默认关掉：跑一次几秒，而结论取决于当场的网络，那不是关于产品的断言。
    关掉之后 doctor 仍然印出一行"skipped by the test harness"，所以这条路不会静默
    消失；它接得上由 tests/test_doctor.py 里两条打桩用例证明，`-m live` 那一条则
    在真网络上跑整条路径。
    """
    if request.node.get_closest_marker("live"):
        return
    from dhole_mcp import updater
    monkeypatch.setattr(updater, "LIVE_CHECKS", False)


class _RealBrowserInstall(BaseException):
    """套件里"真的去下浏览器"用的跳闸类型。

    它**不能**是 `Exception` 的子孙：`ensure_chromium` 为了让一次坏掉的子进程不拖垮
    抓取，会吞掉一切 Exception（生产里这是对的）。一道会被产品代码吞掉的闸等于没有闸
    —— 实测过一次：改成 Exception 之后用例只报"DID NOT RAISE"，而下载照样开始。
    """


@pytest.fixture(autouse=True)
def _nobody_installs_a_browser(monkeypatch):
    """17.0 B3 · 套件永远不许真的去下 chromium（默认那一份就有 119MB）。

    首用安装挂在"需要浏览器档"的那几条路上，而测试里有一批用例会把
    `PLAYWRIGHT_BROWSERS_PATH` 指到空目录去模拟"二进制没装" —— 那条判定现在正好会
    触发一次真的 `python -m patchright install chromium`。这不是假设：上一次跑套件
    就同时起了三个安装子进程，套件停在 26%，三条下载互相抢带宽。

    所以钉成死闸：任何子进程命令里出现 patchright/playwright + install 就立刻红，并且
    说清本该打什么桩。`tests/test_ensure_chromium.py` 自己的用例随后再覆盖这一层
    （monkeypatch 后设者胜），它每次都把 `subprocess.run` 换成桩，所以量不到真下载。
    """
    from dhole_mcp import browser

    real_run = browser.subprocess.run

    def guarded_run(cmd, *args, **kwargs):
        argv = [str(x) for x in (cmd if isinstance(cmd, (list, tuple)) else [cmd])]
        if any("patchright" in a or "playwright" in a for a in argv) \
                and "install" in argv:
            raise _RealBrowserInstall(
                "a test reached the real browser installer - stub "
                "browser.subprocess.run (or server.ensure_chromium_async) instead "
                f"of downloading ~119MB: {argv}")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(browser.subprocess, "run", guarded_run)


@pytest.fixture(autouse=True)
def _no_real_robots_fetch(monkeypatch):
    """Keep the suite off the network for robots.txt.

    v16.0 made ``web_read`` consult robots.txt before every tier. Any test
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
    monkeypatch.setattr(ms, "_ENGINE_YIELD", {})
    monkeypatch.setattr(ms, "_engine_stats_last_save", 0.0)
    # 17.0 B2: 代理池（第四个可写状态文件 search_proxies.json）连同它的重定向一起
    # 删了 —— 现在代理只来自环境变量，没有落盘可污染。
    # The cookie jar (G7) is the state file that still holds credentials, and
    # the only one whose path is resolved lazily: without reset_for_tests(), a
    # connection opened before this redirect keeps writing to the real
    # ~/.dhole/sessions.db, where a Set-Cookie value then outlives the test and
    # gets sent by the next unrelated call.
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
    # The persisted config file (W11) is the seventh state file, and it leaks in
    # both directions: READING the real ~/.dhole/config.json lets one machine's
    # knobs decide what a test asserts, and WRITING it leaves residue in the file
    # that decides how that user's own dhole behaves. This is not hypothetical -
    # the first run of tests/test_config_store.py wrote `["DHOLE_USAGE_LOG"]` into
    # the real home, because this fixture redirects six paths but not paths.home().
    from dhole_mcp import env_registry as _envreg
    monkeypatch.setattr(_envreg, "config_path", lambda: tmp_path / "config.json")
    _envreg.invalidate_file_cache()
    # The CDP authorize store (§3-16 先问再记) is the eighth state file, and it
    # decides whether a first attach executes: pointing it at the real
    # ~/.dhole/cdp_hosts.json would let one test's confirm=true authorize a host
    # for every later test AND for the developer's own live server. Routed
    # through paths.file itself so BOTH readers agree — server's
    # _cdp_hosts_file() and updater.doctor()'s stdlib mirror.
    from dhole_mcp import paths as _paths
    _orig_file = _paths.file
    monkeypatch.setattr(_paths, "file",
                        lambda name: tmp_path / name if name == "cdp_hosts.json"
                        else _orig_file(name))
    from dhole_mcp import server as _srv
    _srv._cdp_hosts_cache, _srv._cdp_hosts_stamp = frozenset(), None
    yield
    _srv._cdp_hosts_cache, _srv._cdp_hosts_stamp = frozenset(), None
    _sessions.reset_for_tests()
    _envreg.invalidate_file_cache()


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
