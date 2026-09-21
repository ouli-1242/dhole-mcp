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
    monkeypatch.setattr(paths, "_legacy_migrated", False)
    return home


def test_layout_is_one_root(fake_home):
    assert paths.home() == fake_home / ".dhole"
    assert paths.cache_dir() == paths.home()
    assert paths.db_path() == fake_home / ".dhole" / "cache.db"
    assert paths.models_dir() == fake_home / ".dhole" / "models"
    assert paths.file("circuit_breaker.json") == \
        fake_home / ".dhole" / "circuit_breaker.json"


def test_every_writer_agrees_on_the_layout():
    """所有落盘模块都必须走 paths.py，不允许各自 expanduser 再拼一个根。"""
    from dhole_mcp import cache, reranker, search, search_metasearch, search_proxy

    assert cache._CACHE_DIR == paths.cache_dir()
    assert str(reranker.MODEL_DIR).startswith(str(paths.models_dir()))
    assert search._FEEDBACK_FILE == str(paths.file("search_feedback.json"))
    assert search_metasearch._CIRCUIT_STATE_FILE == \
        str(paths.file("circuit_breaker.json"))
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
        assert (paths.models_dir() / "msmarco-minilm-l6-v2"
                / "model.onnx").read_bytes() == b"onnx"
        assert not legacy.exists(), "搬空之后旧目录应当消失"

    def test_never_overwrites_the_destination(self, fake_home):
        legacy = self._make_legacy(fake_home)
        paths.home().mkdir(parents=True, exist_ok=True)
        paths.db_path().write_bytes(b"NEW")
        paths.migrate_legacy_cache_dir()

        assert paths.db_path().read_bytes() == b"NEW", "不得覆盖新位置已有的数据"
        assert legacy.is_dir(), "旧数据没能搬走时，旧目录必须原样保留"

    def test_merges_model_children_when_models_dir_exists(self, fake_home):
        self._make_legacy(fake_home)
        dest_model_dir = paths.models_dir() / "msmarco-minilm-l6-v2"
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
