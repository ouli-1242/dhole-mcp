"""Runtime path layout + one-time migration of the legacy cache root.

14.3 之前 dhole 把状态写在 ``~/.dhole/``、把缓存和模型写在
``~/.dhole_mcp_cache/``——「这个工具在我机器上留下了什么」要两个目录才答得全，
卸载说明也因此不完整。现在只有一个根（``dhole_mcp.paths`` 是唯一事实来源），
旧目录在首次使用时被搬进来：缓存可以重建，但 ~90MB 的重排模型在有些网络下
（hosts 钉死 / 代理）根本下不回来，所以必须搬而不是等它重新下载。
"""

from pathlib import Path

import pytest

from dhole_mcp import paths


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(paths, "_legacy_migrate_done", False)
    return home


def test_layout_is_one_root(fake_home):
    assert paths.home() == fake_home / ".dhole"
    assert paths.cache_dir() == paths.home()
    assert paths.db_path() == fake_home / ".dhole" / "cache.db"
    assert paths.models_dir() == fake_home / ".dhole" / "models"
    assert paths.file("circuit_breaker.json") == \
        fake_home / ".dhole" / "circuit_breaker.json"


@pytest.mark.real_state_paths   # 本用例验的就是 paths.py 的落点，不能被隔离 fixture 接管
def test_every_writer_agrees_on_the_layout(monkeypatch, tmp_path):
    """所有落盘模块都必须走 paths.py，不允许各自 expanduser 再拼一个根。"""
    from dhole_mcp import cache, reranker, search, search_metasearch, search_proxy

    assert cache._CACHE_DIR == paths.cache_dir()
    assert str(reranker.MODEL_DIR).startswith(str(paths.models_dir()))
    # 三个 metasearch/search 状态文件都必须是**惰性**派生（函数而非 import 期常量）：
    # 否则 DHOLE_HOME 只对其中一部分生效（半失效的开关比没有开关更糟），而测试也指
    # 不动它们 —— 跑一次套件就会改掉用户真实的引擎冷却状态/域名偏好。
    monkeypatch.setenv("DHOLE_HOME", str(tmp_path / "moved"))
    moved = tmp_path / "moved"
    assert search._feedback_file() == str(moved / "search_feedback.json")
    assert search_metasearch._circuit_state_file() == str(moved / "circuit_breaker.json")
    assert search_metasearch._engine_stats_file() == str(moved / "engine_stats.json")
    assert search_proxy._config_path() == paths.file("search_proxies.json")



class TestLegacyMigration:
    def _make_legacy(self, fake_home, *, with_db=True, with_model=True):
        legacy = fake_home / ".dhole_mcp_cache"
        legacy.mkdir(parents=True)
        if with_db:
            (legacy / "cache.db").write_bytes(b"sqlite")
            (legacy / "cache.db-wal").write_bytes(b"wal")
        if with_model:
            model_dir = legacy / "models" / "msmarco-minilm-l6-v2"
            model_dir.mkdir(parents=True)
            (model_dir / "model.onnx").write_bytes(b"onnx")
        return legacy

    def test_moves_db_sidecars_and_model_into_the_home(self, fake_home):
        legacy = self._make_legacy(fake_home)
        paths.migrate_legacy_cache_dir()

        assert paths.db_path().read_bytes() == b"sqlite"
        assert (fake_home / ".dhole" / "cache.db-wal").read_bytes() == b"wal"
        # The pre-registry dir name is renamed to its model key in the same pass
        # (msmarco-minilm-l6-v2 -> ms-marco), so the weights are not re-fetched.
        assert (paths.models_dir() / "ms-marco" / "model.onnx").read_bytes() == b"onnx"
        assert not legacy.exists(), "搬空之后旧目录应当消失"
        assert not (paths.models_dir() / "msmarco-minilm-l6-v2").exists()

    def test_never_overwrites_the_destination(self, fake_home):
        legacy = self._make_legacy(fake_home)
        paths.home().mkdir(parents=True, exist_ok=True)
        paths.db_path().write_bytes(b"NEW")
        paths.migrate_legacy_cache_dir()

        assert paths.db_path().read_bytes() == b"NEW", "不得覆盖新位置已有的数据"
        assert legacy.is_dir(), "旧数据没能搬走时，旧目录必须原样保留"

    def test_merges_model_children_when_models_dir_exists(self, fake_home):
        self._make_legacy(fake_home)
        dest_model_dir = paths.models_dir() / "ms-marco"
        dest_model_dir.mkdir(parents=True)
        (dest_model_dir / "tokenizer.json").write_bytes(b"tok")

        paths.migrate_legacy_cache_dir()

        assert (dest_model_dir / "tokenizer.json").read_bytes() == b"tok"
        assert (dest_model_dir / "model.onnx").read_bytes() == b"onnx"

    def test_no_legacy_dir_is_a_noop(self, fake_home):
        paths.migrate_legacy_cache_dir()
        assert not (fake_home / ".dhole").exists(), "没有旧目录就不该凭空建目录"

    def test_migration_is_idempotent(self, fake_home):
        legacy = self._make_legacy(fake_home)
        paths.migrate_legacy_cache_dir()
        first = paths.db_path().read_bytes()
        paths.migrate_legacy_cache_dir()  # 幂等：第二次什么都不动
        assert paths.db_path().read_bytes() == first
        assert not legacy.exists()


class TestMigrationNeverBlocksTheEventLoop:
    """首次缓存写入会触发旧目录搬移 —— 它必须在工作线程里跑。

    回归：这个搬移此前是**同步跑在事件循环上**的。实测搬一个 120MB 模型就把事件
    循环完全停摆 2.09s（心跳间隔本该 0.05s），期间 MCP 服务发不出任何响应，于是
    首个工具调用（如 smart_fetch cache_ttl=0）被客户端判为 -32001 超时；而重试
    ——一次性标志此时已置位——瞬间成功。用户看到的就是「首次报错、重试即恢复」。
    """

    def _make_legacy(self, fake_home):
        legacy = fake_home / ".dhole_mcp_cache"
        (legacy / "models" / "bge-zh").mkdir(parents=True)
        (legacy / "models" / "bge-zh" / "model.onnx").write_bytes(b"onnx")
        (legacy / "cache.db").write_bytes(b"sqlite")
        return legacy

    def test_async_wrapper_keeps_the_loop_responsive(self, fake_home, monkeypatch):
        import asyncio
        import time

        self._make_legacy(fake_home)
        real_impl = paths._migrate_legacy_cache_dir_impl

        def slow_impl():
            time.sleep(0.6)          # 模拟跨卷复制 90-450MB 模型
            real_impl()

        monkeypatch.setattr(paths, "_migrate_legacy_cache_dir_impl", slow_impl)

        async def run():
            lags: list[float] = []
            stop = False

            async def heartbeat():
                prev = time.perf_counter()
                while not stop:
                    await asyncio.sleep(0.02)
                    t = time.perf_counter()
                    lags.append(t - prev)
                    prev = t

            hb = asyncio.create_task(heartbeat())
            await asyncio.sleep(0.05)
            t0 = time.perf_counter()
            await paths.migrate_legacy_cache_dir_async()
            elapsed = time.perf_counter() - t0
            stop = True
            await hb
            return max(lags), elapsed

        worst, elapsed = asyncio.run(run())

        assert elapsed >= 0.6, "搬移确实发生了（否则本用例什么都没验到）"
        assert worst < 0.3, f"事件循环被搬移阻塞了 {worst:.2f}s（应远小于 0.6s）"
        assert paths.db_path().read_bytes() == b"sqlite"

    def test_async_wrapper_is_a_noop_once_done(self, fake_home):
        import asyncio

        self._make_legacy(fake_home)
        asyncio.run(paths.migrate_legacy_cache_dir_async())
        # 第二次调用不该再动任何东西（幂等），也不该抛。
        asyncio.run(paths.migrate_legacy_cache_dir_async())
        assert paths.db_path().read_bytes() == b"sqlite"

    def test_concurrent_callers_wait_instead_of_reading_a_half_moved_db(
        self, fake_home, monkeypatch,
    ):
        """第二个调用者必须等第一个搬完，而不是提前返回。

        旧实现先置位再干活（普通 bool），第二个调用者会立刻返回、然后去读一个
        正在被搬移的 cache.db —— 拿到半个数据库比等一会儿糟得多。
        """
        import threading
        import time

        self._make_legacy(fake_home)
        real_impl = paths._migrate_legacy_cache_dir_impl
        started = threading.Event()
        release = threading.Event()

        def slow_impl():
            started.set()
            release.wait(10)
            real_impl()

        monkeypatch.setattr(paths, "_migrate_legacy_cache_dir_impl", slow_impl)

        first = threading.Thread(target=paths.migrate_legacy_cache_dir)
        first.start()
        assert started.wait(10), "第一个调用者没能开始"

        second = threading.Thread(target=paths.migrate_legacy_cache_dir)
        second.start()
        time.sleep(0.2)
        assert second.is_alive(), "第二个调用者不得在搬移完成前返回"

        release.set()
        first.join(10)
        second.join(10)
        assert not first.is_alive() and not second.is_alive()
        assert paths.db_path().read_bytes() == b"sqlite"


class TestDholeHomeOverride:
    """状态目录此前不可移动：cache.db 里是全部抓到的正文明文。

    共享机器上用户应能把它指到别处；顺带也让测试/沙箱能指向一次性目录。
    """

    def test_unset_uses_the_default_dotdir(self, monkeypatch):
        import pathlib
        monkeypatch.delenv("DHOLE_HOME", raising=False)
        monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: pathlib.Path("/fake/home")))
        assert paths.home() == pathlib.Path("/fake/home/.dhole")

    @pytest.mark.parametrize("raw", ["/srv/dhole-data", "  /srv/dhole-data  "])
    def test_env_override_is_honoured_everywhere(self, monkeypatch, raw):
        import pathlib
        monkeypatch.setenv("DHOLE_HOME", raw)
        want = pathlib.Path(raw.strip())
        assert paths.home() == want
        assert paths.cache_dir() == want
        assert paths.db_path() == want / "cache.db"
        assert paths.models_dir() == want / "models"
        assert paths.file("engine_stats.json") == want / "engine_stats.json"

    def test_tilde_is_expanded(self, monkeypatch):
        monkeypatch.setenv("DHOLE_HOME", "~/dhole-x")
        assert "~" not in str(paths.home())
        assert str(paths.home()).endswith("dhole-x")

    def test_migration_targets_the_custom_home(self, monkeypatch, tmp_path):
        """旧目录迁移的目的端必须是 home()，否则设了 DHOLE_HOME 反而丢缓存。"""
        monkeypatch.setenv("DHOLE_HOME", str(tmp_path / "dh"))
        assert (tmp_path / "dh") == paths.home()


class TestPrivateModeHelpers:
    """权限位：POSIX 上真生效，Windows 上 chmod 基本是摆设。

    这台机器实测目录 0o777 / 文件 0o666，所以本平台能验证的只有"调用发生了"；
    真正的 Windows 手段是 DHOLE_HOME。docstring 与 README 都按这个口径写，
    不假装权限位设上了。
    """

    def test_ensure_private_dir_requests_owner_only_mode(self, tmp_path, monkeypatch):
        import os
        calls = []
        monkeypatch.setattr(os, "chmod", lambda p, m: calls.append((str(p), oct(m))))
        d = tmp_path / "sub" / "dir"
        got = paths.ensure_private_dir(d)
        assert got == d and d.is_dir()
        assert calls and calls[-1] == (str(d), oct(0o700))

    def test_harden_file_requests_owner_only_rw(self, tmp_path, monkeypatch):
        import os
        calls = []
        monkeypatch.setattr(os, "chmod", lambda p, m: calls.append((str(p), oct(m))))
        f = tmp_path / "cache.db"
        f.write_text("x", encoding="utf-8")
        paths.harden_file(f)
        assert calls == [(str(f), oct(0o600))]

    def test_helpers_never_raise(self, tmp_path, monkeypatch):
        import os
        def _boom(*a, **k):
            raise OSError("no posix here")
        monkeypatch.setattr(os, "chmod", _boom)
        paths.harden_file(tmp_path / "missing.db")          # 不存在也不抛
        paths.ensure_private_dir(tmp_path / "ok")           # 建目录成功
        assert (tmp_path / "ok").is_dir()

    def test_cache_dir_creation_goes_through_the_helper(self, tmp_path, monkeypatch):
        """缓存目录必须经 ensure_private_dir，否则权限随手就漏在一次 mkdir 上。"""
        import asyncio
        import os
        from dhole_mcp import cache

        calls = []
        monkeypatch.setattr(os, "chmod", lambda p, m: calls.append((str(p), oct(m))))
        d = tmp_path / "cachedir"
        asyncio.run(cache.set_cached("https://x/a", "markdown", ["b"], 200, cache_dir=d))
        assert (d / "cache.db").exists()
        assert (str(d), oct(0o700)) in calls, calls
