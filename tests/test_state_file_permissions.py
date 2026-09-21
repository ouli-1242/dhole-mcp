"""KB-6：状态文件/目录的权限收紧必须真的发生在写入路径上。

背景（D-06，已实测复核）：README#269 承诺"POSIX 下 ``~/.dhole`` 建为 0700、
状态文件 0600"，`paths.ensure_private_dir` / `paths.harden_file` 也确实存在且被
``cache.py`` / ``reranker.py`` / ``search_metasearch.py`` 用着 —— 但覆盖不全，
**恰好漏掉了含明文凭据的 `search_proxies.json`**（实测落盘 mode 受 umask 决定，
本机读到 0o666），`usage.jsonl`、`search_feedback.json`、`last_version` 以及
若干"先建 home 再写文件"的目录也都漏了。

这里冻结的是修复后的规则：**任何写 dhole home 下状态文件的路径，都要把目录收到
0700、把文件收到 0600**；而用户用 ``DHOLE_USAGE_LOG`` 显式指定的外部路径，
**一个 chmod 都不许发**。

平台口径与 ``tests/test_paths.py`` 的 ``TestPermissions`` 一致：本机（Windows）
chmod 只能改只读位，所以能验证的是"调用发生了、参数是 0o700/0o600"；
真正落到 inode 上的位只在 POSIX 上由 ``test_posix_*`` 那组断言。
"""

from __future__ import annotations

import json
import os
import stat

import pytest


@pytest.fixture
def chmod_spy(monkeypatch):
    """Record every ``os.chmod`` call as ``(path, mode)`` instead of performing it."""
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(os, "chmod", lambda p, m: calls.append((str(p), oct(m))))
    return calls


# ─── 辅助函数本身 ────────────────────────────────────────────────────────

class TestHardenDir:
    def test_requests_owner_only_mode(self, tmp_path, chmod_spy):
        from dhole_mcp import paths

        d = tmp_path / "home"
        d.mkdir()
        paths.harden_dir(d)
        assert chmod_spy == [(str(d), oct(0o700))]

    def test_does_not_create_the_directory(self, tmp_path, chmod_spy):
        """与 ensure_private_dir 的分工：本函数只收紧已存在的目录。

        调用方各自保留自己的 mkdir，这样"建目录失败"仍旧照原样抛错，
        不会被这里静默吞掉 —— 这是错误语义的一部分。
        """
        from dhole_mcp import paths

        missing = tmp_path / "nope"
        paths.harden_dir(missing)
        assert not missing.exists()

    def test_never_raises(self, tmp_path, monkeypatch):
        from dhole_mcp import paths

        def _boom(*a, **k):
            raise OSError("no posix here")

        monkeypatch.setattr(os, "chmod", _boom)
        paths.harden_dir(tmp_path)  # 不抛
        paths.harden_dir(tmp_path / "missing")  # 不存在也不抛


# ─── 明文凭据：本轮的原始缺陷 ────────────────────────────────────────────

class TestProxyConfigPermissions:
    def test_save_proxies_tightens_both_dir_and_file(self, chmod_spy, monkeypatch, tmp_path):
        from dhole_mcp import search_proxy

        monkeypatch.setattr(search_proxy, "_config_path",
                            lambda: tmp_path / "search_proxies.json")
        search_proxy.save_proxies(["http://user:secret@10.0.0.1:8080"])

        assert (str(tmp_path), oct(0o700)) in chmod_spy, chmod_spy
        assert (str(tmp_path / "search_proxies.json"), oct(0o600)) in chmod_spy, chmod_spy

    def test_add_remove_clear_go_through_save_proxies(self, monkeypatch, tmp_path):
        """CLI 的三个入口都必须落到同一条收紧路径上。"""
        from dhole_mcp import search_proxy

        monkeypatch.setattr(search_proxy, "_config_path",
                            lambda: tmp_path / "search_proxies.json")
        hardened: list[str] = []
        real = search_proxy.save_proxies

        def spy(proxies):
            hardened.append("save_proxies")
            real(proxies)

        monkeypatch.setattr(search_proxy, "save_proxies", spy)
        search_proxy.add_proxy("http://10.0.0.1:8080")
        search_proxy.clear_proxies()          # non-empty, so it rewrites
        search_proxy.add_proxy("http://10.0.0.2:8080")
        search_proxy.remove_proxy(0)
        assert hardened == ["save_proxies"] * 4, hardened

    def test_content_is_unchanged(self, monkeypatch, tmp_path):
        """只改模式，不改字节。"""
        from dhole_mcp import search_proxy

        target = tmp_path / "search_proxies.json"
        monkeypatch.setattr(search_proxy, "_config_path", lambda: target)
        search_proxy.save_proxies(["http://user:secret@10.0.0.1:8080"])

        assert json.loads(target.read_text(encoding="utf-8")) == {
            "proxies": ["http://user:secret@10.0.0.1:8080"],
        }
        assert target.read_text(encoding="utf-8").endswith("\n") is False

    def test_directory_failure_still_raises(self, monkeypatch, tmp_path):
        """建目录失败的错误语义没变：仍旧抛，不因为加了收紧就被吞掉。"""
        from dhole_mcp import search_proxy

        blocker = tmp_path / "blocker"
        blocker.write_text("i am a file", encoding="utf-8")
        monkeypatch.setattr(search_proxy, "_config_path",
                            lambda: blocker / "sub" / "search_proxies.json")
        with pytest.raises(OSError):
            search_proxy.save_proxies(["http://10.0.0.1:8080"])


# ─── usage.jsonl ────────────────────────────────────────────────────────

class TestUsageLogPermissions:
    def test_owned_log_tightens_home_and_file(self, chmod_spy, monkeypatch, tmp_path):
        from dhole_mcp import server

        home = tmp_path / "home"
        monkeypatch.setenv("DHOLE_HOME", str(home))
        monkeypatch.setenv("DHOLE_USAGE_LOG", "1")
        server._log_tool_call("smart_fetch", True, 12.3)

        assert (str(home), oct(0o700)) in chmod_spy, chmod_spy
        assert (str(home / "usage.jsonl"), oct(0o600)) in chmod_spy, chmod_spy

    def test_user_supplied_path_is_left_alone(self, chmod_spy, monkeypatch, tmp_path):
        """DHOLE_USAGE_LOG 指到外面时，那是用户自己的文件，dhole 不碰它的模式。"""
        from dhole_mcp import server

        external = tmp_path / "elsewhere" / "calls.jsonl"
        monkeypatch.setenv("DHOLE_HOME", str(tmp_path / "home"))
        monkeypatch.setenv("DHOLE_USAGE_LOG", str(external))
        server._log_tool_call("smart_fetch", True, 12.3)

        assert external.exists()
        assert chmod_spy == [], chmod_spy

    def test_appends_and_keeps_content(self, monkeypatch, tmp_path):
        from dhole_mcp import server

        home = tmp_path / "home"
        monkeypatch.setenv("DHOLE_HOME", str(home))
        monkeypatch.setenv("DHOLE_USAGE_LOG", "1")
        server._log_tool_call("smart_fetch", True, 1.0)
        server._log_tool_call("smart_search", False, 2.0, "boom")

        lines = (home / "usage.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["tool"] == "smart_fetch"
        assert json.loads(lines[1])["error"] == "boom"


# ─── 另外三个状态文件 ────────────────────────────────────────────────────

class TestOtherStateFiles:
    def test_search_feedback_tightens_both(self, chmod_spy, monkeypatch, tmp_path):
        from dhole_mcp import search

        target = tmp_path / "search_feedback.json"
        monkeypatch.setattr(search, "_feedback_file", lambda: str(target))
        monkeypatch.setenv("DHOLE_SEARCH_FEEDBACK", "1")
        search.record_search_feedback("https://example.org/page")

        assert (str(tmp_path), oct(0o700)) in chmod_spy, chmod_spy
        assert (str(target), oct(0o600)) in chmod_spy, chmod_spy

    def test_circuit_state_tightens_dir(self, chmod_spy, monkeypatch, tmp_path):
        from dhole_mcp import search_metasearch as ms

        target = tmp_path / "circuit_breaker.json"
        monkeypatch.setattr(ms, "_circuit_state_file", lambda: str(target))
        ms._BACKEND_HEALTH["bing"] = ms.time() + 60
        ms._save_circuit_state()

        assert (str(tmp_path), oct(0o700)) in chmod_spy, chmod_spy
        assert (str(target), oct(0o600)) in chmod_spy, chmod_spy

    def test_engine_stats_tightens_dir(self, chmod_spy, monkeypatch, tmp_path):
        from dhole_mcp import search_metasearch as ms

        target = tmp_path / "engine_stats.json"
        monkeypatch.setattr(ms, "_engine_stats_file", lambda: str(target))
        monkeypatch.setattr(ms, "_engine_stats_last_save", 0.0)
        ms._ENGINE_YIELD["bing"] = {"ts": ms.time(), "status": "ok",
                                    "last_nodes": 5, "last": 3}
        ms._save_engine_stats()

        assert (str(tmp_path), oct(0o700)) in chmod_spy, chmod_spy
        assert (str(target), oct(0o600)) in chmod_spy, chmod_spy

    def test_last_version_is_tightened(self, chmod_spy, monkeypatch, tmp_path):
        from dhole_mcp import updater

        home = tmp_path / "home"
        monkeypatch.setenv("DHOLE_HOME", str(home))
        updater._write_last_version("14.7")

        assert (str(home / "last_version"), oct(0o600)) in chmod_spy, chmod_spy
        assert (home / "last_version").read_text(encoding="utf-8") == "14.7"


# ─── POSIX 上真正落到 inode 的位 ─────────────────────────────────────────
# 本机（Windows）chmod 只能改只读位，stat 报出的 mode 一律是 0o777/0o666 —— 连
# 已经用了 harden_file 的 circuit_breaker.json 也一样（refactor_artifacts/
# analysis/kb6_before.txt 的对照组实测）。所以真正的权限位只能由 POSIX 断言。

posix_only = pytest.mark.skipif(
    os.name != "posix", reason="Windows 的 chmod 不设 POSIX 权限位（README 明说）"
)


@posix_only
class TestRealModeOnPosix:
    def test_proxy_config_lands_0600(self, monkeypatch, tmp_path):
        from dhole_mcp import search_proxy

        target = tmp_path / "home" / "search_proxies.json"
        monkeypatch.setattr(search_proxy, "_config_path", lambda: target)
        search_proxy.save_proxies(["http://user:secret@10.0.0.1:8080"])

        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700

    def test_usage_log_lands_0600(self, monkeypatch, tmp_path):
        from dhole_mcp import server

        home = tmp_path / "home"
        monkeypatch.setenv("DHOLE_HOME", str(home))
        monkeypatch.setenv("DHOLE_USAGE_LOG", "1")
        server._log_tool_call("smart_fetch", True, 1.0)

        assert stat.S_IMODE((home / "usage.jsonl").stat().st_mode) == 0o600
        assert stat.S_IMODE(home.stat().st_mode) == 0o700

    def test_search_feedback_lands_0600(self, monkeypatch, tmp_path):
        from dhole_mcp import search

        target = tmp_path / "home" / "search_feedback.json"
        monkeypatch.setattr(search, "_feedback_file", lambda: str(target))
        monkeypatch.setenv("DHOLE_SEARCH_FEEDBACK", "1")
        search.record_search_feedback("https://example.org/page")

        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700


def test_python_is_actually_from_src():
    """守卫 KB-8：不带 PYTHONPATH=src 时 site-packages 里躺着 14.6 的旧轮子，
    整套断言会"通过"但验的是旧代码。"""
    import dhole_mcp

    assert "site-packages" not in dhole_mcp.__file__, dhole_mcp.__file__
    assert os.path.abspath(dhole_mcp.__file__).startswith(
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
    ), dhole_mcp.__file__
