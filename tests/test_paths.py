"""Runtime path layout: one root, and nobody looks at the old one any more.

14.3 之前 dhole 把状态写在 ``~/.dhole/``、把缓存和模型写在 ``~/.dhole_mcp_cache/``，
「这个工具在我机器上留下了什么」要两个目录才答得全。17.0 起只有一个根
（``dhole_mcp.paths`` 是唯一事实来源），而把旧根搬进来的那次迁移也删了 —— 见
``TestTheOldRootIsLeftAlone``。B8 把模型注册表收敛成一颗之后，最后一次迁移（同一个根
里的模型目录改名）也随之下线：它搬去的目的地名已不再是模型键。**现在一条迁移都不剩。**
"""

from pathlib import Path

import pytest

from dhole_mcp import paths


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
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
    from dhole_mcp import cache, reranker, search_metasearch, server
    assert cache._CACHE_DIR == paths.cache_dir()
    assert str(reranker.MODEL_DIR).startswith(str(paths.models_dir()))
    # 这些状态文件都必须是**惰性**派生（函数而非 import 期常量）：
    # 否则 DHOLE_HOME 只对其中一部分生效（半失效的开关比没有开关更糟），而测试也指
    # 不动它们 —— 跑一次套件就会改掉用户真实的引擎冷却状态/域名偏好。
    monkeypatch.setenv("DHOLE_HOME", str(tmp_path / "moved"))
    moved = tmp_path / "moved"
    assert search_metasearch._circuit_state_file() == str(moved / "circuit_breaker.json")
    assert search_metasearch._engine_stats_file() == str(moved / "engine_stats.json")
    # cdp_hosts.json 由 server 写、doctor 读，两边都走 paths.file —— 它记的是「哪些
    # 主机的 CDP 授权过」，落错地方等于把围栏留在另一个家里。
    assert server._cdp_hosts_file() == str(moved / "cdp_hosts.json")


class TestTheOldRootIsLeftAlone:
    """17.0 B2 删掉了把 ``~/.dhole_mcp_cache`` 搬进 ``~/.dhole`` 的那次迁移。

    它服务的是 14.3 及更早的安装，代价却长期挂在两个最热的路径上：cache 建库与
    重排模型查表都要先问一次"有没有旧根要搬"，而搬移动作会碰 cache.db 加一棵
    90-450MB 的模型树（跨卷还要 copy+delete），所以它历史上必须避开事件循环 ——
    为它专门写的 to_thread 包装、锁、一次性标志和那组 -32001 回归测试，整套机制
    留着比它解决的问题更贵。

    这里钉的是删除之后的事实：**没有人读那个目录，也没有人动它**。旧根里躺着什么
    就还躺着，用户自己删。
    """

    @pytest.mark.parametrize("name", [
        "migrate_legacy_cache_dir", "migrate_legacy_cache_dir_async",
        "_migrate_legacy_cache_dir_impl", "_merge_tree",
        "_LEGACY_CACHE_DIR_NAME", "_legacy_migrate_lock", "_legacy_migrate_done",
    ])
    def test_paths_exposes_no_migration_api(self, name):
        assert not hasattr(paths, name), f"paths.{name} 又回来了"

    def test_no_module_references_the_old_root(self):
        """扫 AST 而不是扫全文：注释里可以留下"这里曾经搬过旧根"这段历史，
        但被执行的字符串常量里不许再出现那个名字。"""
        import ast
        import pathlib

        src = pathlib.Path(paths.__file__).resolve().parent
        hits = []
        for file in sorted(src.rglob("*.py")):
            if "__pycache__" in file.parts:
                continue
            tree = ast.parse(file.read_text(encoding="utf-8"))
            docs = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                     ast.AsyncFunctionDef)) and node.body:
                    first = node.body[0]
                    if (isinstance(first, ast.Expr)
                            and isinstance(first.value, ast.Constant)
                            and isinstance(first.value.value, str)):
                        docs.add(id(first.value))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if id(node) in docs:
                        continue
                    if ("dhole_mcp_cache" in node.value
                            or "migrate_legacy_cache" in node.value):
                        hits.append(file.name)
                elif isinstance(node, ast.Attribute) and "migrate_legacy" in node.attr:
                    hits.append(file.name)
        assert not hits, sorted(set(hits))

    def test_the_old_root_survives_a_cache_open_and_a_model_lookup(
            self, fake_home, monkeypatch):
        """真实触发点跑一遍：开缓存库、查模型在不在，都不许碰旧根。

        这两个调用点当年就是迁移的入口（cache._ensure_db 与 reranker 的模型查表），
        所以它们是最会复发的地方。
        """
        legacy = fake_home / ".dhole_mcp_cache"
        (legacy / "models" / "bge-zh").mkdir(parents=True)
        (legacy / "cache.db").write_bytes(b"sqlite")
        (legacy / "models" / "bge-zh" / "model.onnx").write_bytes(b"x")
        before = sorted(q.as_posix() for q in legacy.rglob("*"))

        from dhole_mcp import cache, reranker

        monkeypatch.setattr(cache, "_CACHE_DIR", fake_home / ".dhole")
        reranker.model_present()
        assert sorted(q.as_posix() for q in legacy.rglob("*")) == before,             "旧根被搬走或删掉了：那次迁移又回来了"
        assert not (fake_home / ".dhole" / "cache.db").exists(),             "新根里凭空多出一个从旧根搬来的 cache.db"


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
