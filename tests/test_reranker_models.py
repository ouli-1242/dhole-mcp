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

# ─── 发布方摘要校验（真实性）与自记哈希（仅完整性）─────────────────────────

def _tiny_model(publisher=None, tmp=None):
    return reranker.RerankerModel(
        name="tiny", repo="someone/tiny", rev="deadbeef",
        relpaths={"model.onnx": "onnx/model.onnx", "tokenizer.json": "tokenizer.json",
                  "vocab.txt": "vocab.txt"},
        approx_bytes=10, min_bytes=1,
        publisher_sha256=publisher or {},
    )


@pytest.fixture
def tiny_env(monkeypatch, tmp_path):
    """把 active_model/目录指向一个 1 字节的假模型，避免为测试写 100MB 文件。"""
    model_dir = tmp_path / "models" / "tiny"
    model_dir.mkdir(parents=True)
    holder = {"model": _tiny_model()}
    monkeypatch.setattr(reranker, "active_model", lambda: holder["model"])
    monkeypatch.setattr(reranker, "active_model_dir", lambda: model_dir)
    monkeypatch.setattr(reranker.paths, "migrate_legacy_cache_dir", lambda: None)
    monkeypatch.setattr(reranker, "_reranker_unavailable_reason", "")
    # 与摘要逻辑无关的尺寸下限（真模型 50MB）放宽，否则测试要先写出 50MB 文件
    monkeypatch.setattr(reranker, "MIN_MODEL_BYTES", 1)
    return holder, model_dir


def _fake_download(content: bytes, log: list):
    def _f(name, dest):
        log.append(name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        return True
    return _f


def test_publisher_digest_rejects_a_foreign_payload(tiny_env, monkeypatch):
    """镜像给了不同字节时必须拒用，而不是悄悄拿它打分。

    修前的行为是：下载完算**自己刚下的字节**的哈希写盘 —— 那只证明以后没损坏，
    任何端点给什么就"验证"通过什么。
    """
    holder, model_dir = tiny_env
    import hashlib
    bad = b"C" * 40                      # 与发布方摘要不符
    want = hashlib.sha256(b"R" * 40).hexdigest()   # 作者实际发布的那份
    holder["model"] = _tiny_model(publisher={"model.onnx": want})
    log: list = []
    monkeypatch.setattr(reranker, "_download_model_file", _fake_download(bad, log))

    assert reranker._ensure_model() is None
    assert not (model_dir / "model.onnx").exists(), "拒用后不该留下那份权重"
    assert "publisher sha256" in reranker._reranker_unavailable_reason, \
        "拒绝原因要能和'网络问题'区分开"


def test_publisher_digest_accepts_the_published_bytes(tiny_env, monkeypatch):
    holder, model_dir = tiny_env
    import hashlib
    good = b"R" * 40
    holder["model"] = _tiny_model(publisher={"model.onnx": hashlib.sha256(good).hexdigest()})
    log: list = []
    monkeypatch.setattr(reranker, "_download_model_file", _fake_download(good, log))
    onnx, tok = reranker._ensure_model()
    assert onnx.exists() and tok.exists()
    # sidecar 记的就是发布方那份字节
    assert (model_dir / "model.sha256").read_text().strip() == hashlib.sha256(good).hexdigest()


def test_no_publisher_digest_still_records_self_hash(tiny_env, monkeypatch):
    """拿不到权威摘要时保持原行为（完整性），但摘要只许来自核对过的字节。"""
    holder, model_dir = tiny_env
    log: list = []
    monkeypatch.setattr(reranker, "_download_model_file", _fake_download(b"Q" * 40, log))
    onnx, _tok = reranker._ensure_model()
    assert onnx.exists()
    assert (model_dir / "model.sha256").exists()


def test_registry_digests_are_well_formed_and_verifiable():
    """注册表里填了的摘要必须能核对：64 位小写十六进制。

    值本身来自仓库元数据（LFS oid），没法在离线测试里重新推导 —— 这里钉的是"别写坏
    格式"，以及"该字段的用途只有一个"：填错一个字符 = 所有下载都被拒用。默认模型必须
    有摘要（它是真实性的唯一来源）；没有摘要的模型只许是那些**从未在本机核对过**的
    （目前只有 zh-full）。
    """
    import re
    hex64 = re.compile(r"^[0-9a-f]{64}$")
    for name, m in reranker.MODELS.items():
        for fname, digest in (m.publisher_sha256 or {}).items():
            assert hex64.match(digest), f"{name}/{fname} 的摘要不是 64 位十六进制: {digest!r}"
            assert fname in m.relpaths, f"{name} 的摘要指向一个不下载的文件: {fname}"
    assert reranker.MODELS["bge-zh"].publisher_sha256, \
        "默认模型的摘要不能空着 —— 空了等于没有真实性校验"
    assert set(reranker.MODELS) - {"zh-full"} == {
        n for n, m in reranker.MODELS.items() if m.publisher_sha256}, \
        "只有 zh-full 允许留空（未在本机下载过，无从核对）；其余模型请按 raw LFS " \
        "指针取值并逐位核对后填上"


def test_vocab_txt_is_fetched_best_effort(tiny_env, monkeypatch):
    """回退分词器要用的 vocab.txt 此前从不下发；列为必需项反而更危险。"""
    holder, model_dir = tiny_env
    log: list = []
    monkeypatch.setattr(reranker, "_download_model_file", _fake_download(b"Z" * 40, log))
    reranker._ensure_model()
    assert "vocab.txt" in log, "缺失时应尝试补下"

    # 下载失败也不能让整体失效
    def _fail(name, dest):
        return name != "vocab.txt"
    monkeypatch.setattr(reranker, "_download_model_file", _fail)
    (model_dir / "vocab.txt").unlink(missing_ok=True)
    onnx, tok = reranker._ensure_model()
    assert onnx is not None and tok is not None, "vocab.txt 缺失不该弄死本来能用的重排"
