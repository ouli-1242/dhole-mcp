"""Reranker model registry + selection: which cross-encoder scores search
results, and how the choice is persisted/validated.

The registry itself lives in ``reranker.MODELS`` (that module owns ONNX loading,
so it owns the model list too). This file covers the parts a user touches:
``~/.dhole/config/reranker.json``, the default, and the fact that an unknown
name is REJECTED rather than silently mapped to something else.
"""

from pathlib import Path

import pytest

from dhole_mcp import reranker, reranker_config


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    """Point the config at a temp file (never the real ~/.dhole)."""
    path = tmp_path / "reranker.json"
    monkeypatch.setattr(reranker_config, "_path", lambda: path)
    return path


def test_default_model_is_the_chinese_capable_one(config_file):
    """Default (no config file) must be the cross-lingual model, not the EN one."""
    assert reranker.DEFAULT_MODEL == "bge-zh"
    assert reranker.active_model().name == "bge-zh"
    assert not config_file.exists()


def test_registry_entries_are_complete():
    """Every registry entry must be downloadable + loadable as-is."""
    for name, model in reranker.MODELS.items():
        assert model.name == name
        assert "/" in model.repo
        assert len(model.rev) >= 12, "revision must be pinned"
        assert set(model.relpaths) == {"model.onnx", "tokenizer.json", "vocab.txt"}
        assert model.approx_bytes > 10_000_000
        assert 0 < model.min_bytes <= model.approx_bytes
        assert model.label


def test_config_selects_the_model(config_file):
    assert reranker.active_model().name == "bge-zh"
    reranker_config.set_selected("ms-marco")
    assert reranker.active_model().name == "ms-marco"
    assert reranker.active_model_dir() == \
        reranker.paths.models_dir() / "ms-marco"


def test_unknown_name_is_rejected_on_write(config_file):
    with pytest.raises(ValueError, match="unknown reranker model"):
        reranker_config.set_selected("bge-large-zh")
    assert reranker.active_model().name == "bge-zh"


def test_unknown_name_in_file_falls_back_to_default(config_file, caplog):
    """A typo in the file must not silently pick a different model."""
    config_file.write_text('{"model": "not-a-model"}', encoding="utf-8")
    with caplog.at_level("WARNING", logger="dhole-mcp.reranker"):
        assert reranker.active_model().name == "bge-zh"
    assert "unknown model" in caplog.text
    assert "bge-zh" in caplog.text


def test_missing_or_corrupt_config_uses_default(config_file):
    assert reranker.active_model().name == "bge-zh"      # missing file
    config_file.write_text("not json at all", encoding="utf-8")
    assert reranker.active_model().name == "bge-zh"      # corrupt file
    config_file.write_text('{"model": "   "}', encoding="utf-8")
    assert reranker.active_model().name == "bge-zh"      # blank value


def test_model_present_follows_the_active_model(config_file, tmp_path, monkeypatch):
    """present() must describe the SELECTED model, not whichever files exist."""
    models_dir = tmp_path / "models"
    monkeypatch.setattr(reranker.paths, "models_dir", lambda: models_dir)

    def _populate(name):
        model = reranker.MODELS[name]
        d = models_dir / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "model.onnx").write_bytes(b"x" * model.min_bytes)
        (d / "tokenizer.json").write_bytes(b"{}")

    _populate("bge-zh")
    assert reranker.model_present() is True
    _populate("ms-marco")
    reranker_config.set_selected("ms-marco")
    assert reranker.model_present() is True
    # A truncated active model is NOT "present" (min_bytes floor).
    (models_dir / "ms-marco" / "model.onnx").write_bytes(b"x" * 10)
    assert reranker.model_present() is False


def test_capabilities_names_the_active_model(config_file, tmp_path, monkeypatch):
    from dhole_mcp import updater

    monkeypatch.setattr(reranker.paths, "models_dir", lambda: tmp_path / "models")
    monkeypatch.setattr(updater, "_has_module", lambda name: True)
    caps = {label: (state, ok) for label, state, ok in updater.capabilities()}
    state, ok = caps["neural rerank"]
    assert "bge-zh" in state and ok is False       # not downloaded yet
    reranker_config.set_selected("ms-marco")
    caps = {label: (state, ok) for label, state, ok in updater.capabilities()}
    assert "ms-marco" in caps["neural rerank"][0]


def test_legacy_model_dir_is_renamed_not_redownloaded(tmp_path, monkeypatch):
    """14.x stored the EN model under the repo-derived dir name. Renaming keeps
    ~90MB of already-downloaded weights instead of re-fetching them."""
    models_dir = tmp_path / "models"
    legacy = models_dir / "msmarco-minilm-l6-v2"
    legacy.mkdir(parents=True)
    (legacy / "model.onnx").write_bytes(b"weights")
    monkeypatch.setattr(reranker.paths, "models_dir", lambda: models_dir)

    reranker.paths.rename_legacy_model_dirs()

    assert not legacy.exists()
    assert (models_dir / "ms-marco" / "model.onnx").read_bytes() == b"weights"


def test_rename_never_overwrites_an_existing_dir(tmp_path, monkeypatch):
    models_dir = tmp_path / "models"
    legacy = models_dir / "msmarco-minilm-l6-v2"
    legacy.mkdir(parents=True)
    (legacy / "model.onnx").write_bytes(b"old")
    target = models_dir / "ms-marco"
    target.mkdir(parents=True)
    (target / "model.onnx").write_bytes(b"new")
    monkeypatch.setattr(reranker.paths, "models_dir", lambda: models_dir)

    reranker.paths.rename_legacy_model_dirs()

    assert (target / "model.onnx").read_bytes() == b"new"
    assert legacy.is_dir(), "旧目录未被搬走时必须保留"


def test_config_path_is_inside_the_dhole_home(monkeypatch, tmp_path):
    """配置文件也属于「工具在我机器上留下的东西」，必须落在同一个根下。"""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert reranker_config._path() == \
        tmp_path / ".dhole" / "config" / "reranker.json"