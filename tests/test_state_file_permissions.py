"""KB-6：状态文件/目录的权限收紧必须真的发生在写入路径上。

背景（D-06，已实测复核）：README#269 承诺"POSIX 下 ``~/.dhole`` 建为 0700、
状态文件 0600"，`paths.ensure_private_dir` / `paths.harden_file` 也确实存在且被
``cache.py`` / ``reranker.py`` / ``search_metasearch.py`` 用着 —— 但当初覆盖不全：
凡是"自己 open 再写"的路径都按 umask 落盘（本机实测 0o666），而这里面装着
``sessions.db``（cookie 罐 = 凭据）与 ``cdp_hosts.json``（授权围栏），另有
`usage.jsonl`、`last_version` 和若干"先建 home 再写文件"的目录也漏了。

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


# ─── usage.jsonl ────────────────────────────────────────────────────────

class TestUsageLogPermissions:
    def test_owned_log_tightens_home_and_file(self, chmod_spy, monkeypatch, tmp_path):
        from dhole_mcp import server

        home = tmp_path / "home"
        monkeypatch.setenv("DHOLE_HOME", str(home))
        monkeypatch.setenv("DHOLE_USAGE_LOG", "1")
        server._log_tool_call("web_read", True, 12.3)

        assert (str(home), oct(0o700)) in chmod_spy, chmod_spy
        assert (str(home / "usage.jsonl"), oct(0o600)) in chmod_spy, chmod_spy

    def test_user_supplied_path_is_left_alone(self, chmod_spy, monkeypatch, tmp_path):
        """DHOLE_USAGE_LOG 指到外面时，那是用户自己的文件，dhole 不碰它的模式。"""
        from dhole_mcp import server

        external = tmp_path / "elsewhere" / "calls.jsonl"
        monkeypatch.setenv("DHOLE_HOME", str(tmp_path / "home"))
        monkeypatch.setenv("DHOLE_USAGE_LOG", str(external))
        server._log_tool_call("web_read", True, 12.3)

        assert external.exists()
        assert chmod_spy == [], chmod_spy

    def test_appends_and_keeps_content(self, monkeypatch, tmp_path):
        from dhole_mcp import server

        home = tmp_path / "home"
        monkeypatch.setenv("DHOLE_HOME", str(home))
        monkeypatch.setenv("DHOLE_USAGE_LOG", "1")
        server._log_tool_call("web_read", True, 1.0)
        server._log_tool_call("web_search", False, 2.0, "boom")

        lines = (home / "usage.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["tool"] == "web_read"
        assert json.loads(lines[1])["error"] == "boom"


# ─── 其余状态文件 ────────────────────────────────────────────────────────

class TestOtherStateFiles:
    def test_cdp_hosts_tightens_both(self, chmod_spy, monkeypatch, tmp_path):
        """``cdp_hosts.json`` 记的是「哪些主机的已登录 Chrome 可以连」—— 这台机器
        上它等同于「谁的浏览器里那些站点算你」。17.0 先问再记的新落盘处，权限必须
        和别的凭据文件同级。"""
        from dhole_mcp import server

        target = tmp_path / "cdp_hosts.json"
        monkeypatch.setattr(server, "_cdp_hosts_file", lambda: str(target))
        server._cdp_authorize_host("127.0.0.1")

        assert (str(tmp_path), oct(0o700)) in chmod_spy, chmod_spy
        assert (str(target), oct(0o600)) in chmod_spy, chmod_spy
        assert json.loads(target.read_text(encoding="utf-8"))["hosts"] == ["127.0.0.1"]

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

    @pytest.mark.real_state_paths
    def test_the_resolver_mirror_tightens_both(self, chmod_spy, monkeypatch, tmp_path):
        """17.0 批次 6d 的第十个状态文件 `resolver_probe.json`：一个进程把自己的
        resolver 判词写给别的进程看。里面不是凭据，但它是 SSRF 复查层开还是关的
        依据，也是 doctor 那句话的唯一出处，所以和别的状态文件同级收紧。

        带 `real_state_paths`：这条验的正是路径由 ``paths.home()`` 派生，而 conftest
        把这个文件接管去了临时目录（套件的 DNS 挂具会让每个用例都写一份"公网"）。
        """
        from dhole_mcp import security

        home = tmp_path / "home"
        monkeypatch.setenv("DHOLE_HOME", str(home))
        security._record_resolver_probe("fake-ip", "198.18.0.32")

        assert (str(home), oct(0o700)) in chmod_spy, chmod_spy
        assert (str(home / "resolver_probe.json"), oct(0o600)) in chmod_spy, chmod_spy
        assert security.resolver_probe_record()["verdict"] == "fake-ip"


# ─── POSIX 上真正落到 inode 的位 ─────────────────────────────────────────
# 本机（Windows）chmod 只能改只读位，stat 报出的 mode 一律是 0o777/0o666 —— 连
# 已经用了 harden_file 的 circuit_breaker.json 也一样（refactor_artifacts/
# analysis/kb6_before.txt 的对照组实测）。所以真正的权限位只能由 POSIX 断言。

posix_only = pytest.mark.skipif(
    os.name != "posix", reason="Windows 的 chmod 不设 POSIX 权限位（README 明说）"
)


@posix_only
class TestRealModeOnPosix:
    def test_usage_log_lands_0600(self, monkeypatch, tmp_path):
        from dhole_mcp import server

        home = tmp_path / "home"
        monkeypatch.setenv("DHOLE_HOME", str(home))
        monkeypatch.setenv("DHOLE_USAGE_LOG", "1")
        server._log_tool_call("web_read", True, 1.0)

        assert stat.S_IMODE((home / "usage.jsonl").stat().st_mode) == 0o600
        assert stat.S_IMODE(home.stat().st_mode) == 0o700

    def test_cdp_hosts_lands_0600(self, monkeypatch, tmp_path):
        """``cdp_hosts.json`` 在 POSIX 上真的落到 0600/0700，不只是"发过 chmod"。

        直接钉 ``_cdp_hosts_file`` 而不是 DHOLE_HOME：conftest 把这个文件指到了
        测试临时目录（防止一次 confirm=true 写进用户真实的围栏）。
        """
        from dhole_mcp import server

        home = tmp_path / "home"
        target = home / "cdp_hosts.json"
        monkeypatch.setattr(server, "_cdp_hosts_file", lambda: str(target))
        server._cdp_authorize_host("127.0.0.1")

        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        assert stat.S_IMODE(home.stat().st_mode) == 0o700


def test_python_is_actually_from_src():
    """守卫 KB-8：不带 PYTHONPATH=src 时 site-packages 里躺着 14.6 的旧轮子，
    整套断言会"通过"但验的是旧代码。"""
    import dhole_mcp

    assert "site-packages" not in dhole_mcp.__file__, dhole_mcp.__file__
    assert os.path.abspath(dhole_mcp.__file__).startswith(
        os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
    ), dhole_mcp.__file__
