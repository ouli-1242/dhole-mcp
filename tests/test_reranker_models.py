"""Reranker model registry: which cross-encoder scores search results.

The registry lives in ``reranker.MODELS`` (that module owns ONNX loading, so it
owns the model list too). 17.0 B8 collapsed it to a **single** entry — the
semantic-swap evaluation (set B) found the candidate ``gte-multilingual-base``
indistinguishable from ``bge-zh`` on nDCG (Δ+0.0030, 95% CI spans 0) while being
2.3× slower on CPU, so "choose a model" was removed rather than kept as a lever
that buys nothing.

This file covers what is left of the user-facing surface: the default, the fact
that the registry is one model by design, and the one honest leftover — a
``~/.dhole/config/reranker.json`` written by a 16.x machine no longer selects
anything, but we **say so** instead of silently switching the scorer.
"""

import pytest

from dhole_mcp import reranker


@pytest.fixture
def legacy_config(tmp_path, monkeypatch):
    """Point ``~/.dhole`` at a temp dir; return the legacy config file path.

    ``_legacy_selection`` reads ``paths.home() / config / reranker.json``, so
    redirecting ``home`` is the whole isolation this needs.
    """
    home = tmp_path / ".dhole"
    monkeypatch.setattr(reranker.paths, "home", lambda: home)
    return home / "config" / "reranker.json"


def _write_legacy(path, model: str) -> None:
    """Write the 16.x selection file the way a user can only do it by hand now."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"model": "%s"}' % model, encoding="utf-8")


def test_default_model_is_the_chinese_capable_one():
    """Default must be the cross-lingual model, not an English-only one."""
    assert reranker.DEFAULT_MODEL == "bge-zh"
    assert reranker.active_model().name == "bge-zh"


def test_registry_is_a_single_model():
    """收敛是有意的产品决定，不是某次重构漏掉的入口。

    留一颗以上的话，``min_raw_relevance`` 的标定值就得按模型分叉 —— 而集 B 已经
    证明那颗候选换不来质量。所以"不给选"是结论，不是妥协。
    """
    assert set(reranker.MODELS) == {"bge-zh"}
    assert reranker.DEFAULT_MODEL in reranker.MODELS


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


# ─── 16.x 遗留的选择文件：不再选，但不静默 ─────────────────────────────────

def test_legacy_config_warns_but_does_not_switch(legacy_config, caplog):
    """手写过的 reranker.json 不再选模型，但**不许静默换掉打分器**。

    一台 16.x 上选了 ``ms-marco`` 的机器升上来，唯一能证明它"被换掉了"的就是这句
    日志。删掉它 = 让升级变成一次看不见的变更。
    """
    _write_legacy(legacy_config, "ms-marco")
    with caplog.at_level("WARNING", logger="dhole-mcp.reranker"):
        assert reranker.active_model().name == "bge-zh"
    assert "ms-marco" in caplog.text
    assert "removed" in caplog.text and "bge-zh" in caplog.text


def test_naming_the_default_is_silent(legacy_config, caplog):
    """文件里写的就是那颗唯一的模型 —— 没有"被换掉"这回事，不该吵。"""
    _write_legacy(legacy_config, "bge-zh")
    with caplog.at_level("WARNING", logger="dhole-mcp.reranker"):
        assert reranker.active_model().name == "bge-zh"
    assert not caplog.text


def test_missing_corrupt_or_blank_config_is_silent(legacy_config, caplog):
    """读不动 / 不是 JSON / 值是空白 —— 都当"没有这个文件"，沉默是正确结果。"""
    with caplog.at_level("WARNING", logger="dhole-mcp.reranker"):
        assert reranker.active_model().name == "bge-zh"       # missing file
    legacy_config.parent.mkdir(parents=True, exist_ok=True)
    legacy_config.write_text("not json at all", encoding="utf-8")
    with caplog.at_level("WARNING", logger="dhole-mcp.reranker"):
        assert reranker.active_model().name == "bge-zh"       # corrupt file
    legacy_config.write_text('{"model": "   "}', encoding="utf-8")
    with caplog.at_level("WARNING", logger="dhole-mcp.reranker"):
        assert reranker.active_model().name == "bge-zh"       # blank value
    assert not caplog.text


def test_active_model_dir_is_under_models_dir(tmp_path, monkeypatch):
    models_dir = tmp_path / "models"
    monkeypatch.setattr(reranker.paths, "models_dir", lambda: models_dir)
    assert reranker.active_model_dir() == models_dir / "bge-zh"


def test_model_present_uses_the_single_model(tmp_path, monkeypatch):
    """present() 描述的是那颗唯一的模型，不是"随便哪些文件在"。"""
    models_dir = tmp_path / "models"
    monkeypatch.setattr(reranker.paths, "models_dir", lambda: models_dir)
    model = reranker.MODELS["bge-zh"]
    d = models_dir / "bge-zh"
    d.mkdir(parents=True, exist_ok=True)
    (d / "model.onnx").write_bytes(b"x" * model.min_bytes)
    (d / "tokenizer.json").write_bytes(b"{}")
    assert reranker.model_present() is True
    # 截断的模型不算"在"（min_bytes 下限）。
    (d / "model.onnx").write_bytes(b"x" * 10)
    assert reranker.model_present() is False


def test_capabilities_names_the_single_model(tmp_path, monkeypatch):
    from dhole_mcp import updater

    monkeypatch.setattr(reranker.paths, "models_dir", lambda: tmp_path / "models")
    monkeypatch.setattr(updater, "_has_module", lambda name: True)
    caps = {label: (state, ok) for label, state, ok in updater.capabilities()}
    state, ok = caps["neural rerank"]
    assert "bge-zh" in state and ok is False       # not downloaded yet


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
    格式"，以及"该字段的用途只有一个"：填错一个字符 = 所有下载都被拒用。收敛成单模型
    后，在册的每一颗都必须有摘要 —— 它是真实性的唯一来源（此前 zh-full 留空是因为
    从未在本机下载过、无从核对；那颗已随 B8 下线）。
    """
    import re
    hex64 = re.compile(r"^[0-9a-f]{64}$")
    for name, m in reranker.MODELS.items():
        for fname, digest in (m.publisher_sha256 or {}).items():
            assert hex64.match(digest), f"{name}/{fname} 的摘要不是 64 位十六进制: {digest!r}"
            assert fname in m.relpaths, f"{name} 的摘要指向一个不下载的文件: {fname}"
        assert m.publisher_sha256, \
            f"{name} 的摘要不能空着 —— 空了等于没有真实性校验"


def test_vocab_txt_is_fetched_best_effort(tiny_env, monkeypatch):
    """回退分词器要用的 vocab.txt 此前从不下发；列为必需项反而更危险。"""
    holder, model_dir = tiny_env
    log: list = []
    monkeypatch.setattr(reranker, "_download_model_file", _fake_download(b"Z" * 40, log))
    reranker._ensure_model()
    assert "vocab.txt" in log, "缺失时应尝试补下"


def test_preprovisioned_model_dir_downloads_nothing(tiny_env, monkeypatch):
    """文件齐了就不许再联网：README 让用户「整个目录拷过去」，这条钉住那句话。

    KB-5 的另一半：行为（首次搜索下载）是有意的、也已在 README 写明；缺的是
    "离线/多机怎么办"的说明 —— 那句话只有在这个不变量成立时才敢写。
    """
    holder, model_dir = tiny_env
    for name, blob in (("model.onnx", b"M" * 8), ("tokenizer.json", b"{}"),
                       ("vocab.txt", b"v")):
        (model_dir / name).write_bytes(blob)
    attempted: list[str] = []

    def _record(name, dest):
        attempted.append(name)
        return True

    monkeypatch.setattr(reranker, "_download_model_file", _record)
    assert reranker.model_present() is True
    assert reranker._ensure_model() is not None
    assert attempted == [], "预置齐全时不该发起任何下载"

    # 下载失败也不能让整体失效
    def _fail(name, dest):
        return name != "vocab.txt"
    monkeypatch.setattr(reranker, "_download_model_file", _fail)
    (model_dir / "vocab.txt").unlink(missing_ok=True)
    onnx, tok = reranker._ensure_model()
    assert onnx is not None and tok is not None, "vocab.txt 缺失不该弄死本来能用的重排"
