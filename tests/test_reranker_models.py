"""Reranker model registry: which cross-encoder scores search results.

The registry lives in ``reranker.MODELS`` (that module owns ONNX loading, so it
owns the model list too). 17.0 collapsed it to a single entry; this batch brings
back a **second, measured** profile: ``bge-zh`` stays the default (best Chinese),
``mmarco`` is the faster/smaller multilingual option. ``ms-marco`` (23MB,
English-only) is deliberately NOT registered: on the same 67-query corpus its raw
scores saturate and the tier line loses discrimination.

The selection is process-level (``DHOLE_RERANK_MODEL``), because the ONNX session
is a singleton: two models in one process would score different searches. The
default is unchanged unless the operator names the fast profile.
"""

import dataclasses
import hashlib
import re

import pytest

from dhole_mcp import env_registry, reranker


@pytest.fixture(autouse=True)
def fresh_selection(tmp_path, monkeypatch):
    """每个测试都是"新进程"：环境、配置文件与启动期缓存都从零开始。"""
    home = tmp_path / ".dhole"
    monkeypatch.setattr(reranker.paths, "home", lambda: home)
    monkeypatch.setattr(env_registry, "_load_file", lambda: {})
    monkeypatch.delenv("DHOLE_RERANK_MODEL", raising=False)
    reranker.reset_selection_cache()
    yield
    reranker.reset_selection_cache()


@pytest.fixture
def legacy_config(tmp_path):
    """Path of the 16.x selection file (reads go through the patched home)."""
    return tmp_path / ".dhole" / "config" / "reranker.json"


def _write_legacy(path, model: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"model": "%s"}' % model, encoding="utf-8")


def test_default_is_the_chinese_capable_balanced_model():
    """Default must stay the cross-lingual model, not the fast one."""
    assert reranker.DEFAULT_MODEL == "bge-zh"
    assert reranker.active_model().name == "bge-zh"
    assert reranker.active_model().role == "balanced"


def test_registry_has_a_fast_multilingual_alternative():
    """多模型不是把"多一个旋钮"当卖点：第二颗必须有自己的取舍与语言面。"""
    assert set(reranker.MODELS) == {"bge-zh", "mmarco"}
    fast = reranker.MODELS["mmarco"]
    assert fast.role == "fast"
    assert "zh" in fast.languages and "en" in fast.languages
    assert fast.approx_bytes < reranker.MODELS["bge-zh"].approx_bytes / 2


def test_registry_entries_are_complete():
    """Every registry entry must be downloadable + loadable as-is."""
    for name, model in reranker.MODELS.items():
        assert model.name == name
        assert "/" in model.repo
        assert len(model.rev) >= 12, "revision must be pinned"
        assert set(model.relpaths) == {"model.onnx", "tokenizer.json", "vocab.txt"}
        for variant in model.variants.values():
            assert set(variant) == {"model.onnx", "tokenizer.json", "vocab.txt"}
        assert model.approx_bytes > 10_000_000
        assert 0 < model.min_bytes <= model.approx_bytes
        assert model.label
        assert model.role in {"balanced", "fast"}
        assert 0.0 < model.raw_med < model.raw_high <= 1.0


def test_mmarco_picks_the_onnx_for_this_cpu(monkeypatch):
    model = reranker.MODELS["mmarco"]
    monkeypatch.setattr(reranker.platform, "machine", lambda: "AMD64")
    assert reranker.paths_for(model)["model.onnx"] == "onnx/model_quint8_avx2.onnx"
    monkeypatch.setattr(reranker.platform, "machine", lambda: "arm64")
    assert reranker.paths_for(model)["model.onnx"] == "onnx/model_qint8_arm64.onnx"


def test_an_unsupported_arch_has_no_variant(monkeypatch):
    """没有可用变体时不许猜一份"能跑"的文件 —— 返回空映射，加载侧明确拒绝。"""
    model = reranker.MODELS["mmarco"]
    monkeypatch.setattr(reranker.platform, "machine", lambda: "riscv64")
    assert reranker.paths_for(model) == {}
    assert reranker._present_for(model) is False


def test_env_selection_is_read_once_per_process(monkeypatch):
    """启动期选择：改环境不重启，就不该让同一进程换打分器。"""
    monkeypatch.setenv("DHOLE_RERANK_MODEL", "mmarco")
    reranker.reset_selection_cache()
    assert reranker.active_model().name == "mmarco"

    monkeypatch.setenv("DHOLE_RERANK_MODEL", "bge-zh")
    assert reranker.active_model().name == "mmarco", "运行中改环境不该换档"

    reranker.reset_selection_cache()          # 新进程
    assert reranker.active_model().name == "bge-zh"


def test_unknown_model_name_warns_and_falls_back(monkeypatch, caplog):
    monkeypatch.setenv("DHOLE_RERANK_MODEL", "does-not-exist")
    reranker.reset_selection_cache()
    with caplog.at_level("WARNING", logger="dhole-mcp.reranker"):
        assert reranker.active_model().name == "bge-zh"
    assert "does-not-exist" in caplog.text
    assert "Known: bge-zh, mmarco" in caplog.text


def test_raw_thresholds_come_from_the_active_model(monkeypatch):
    """同一颗模型换了 raw 分布，tier 线必须跟着走，不能套默认模型那一把。"""
    custom = dataclasses.replace(reranker.MODELS["mmarco"], raw_high=0.7, raw_med=0.2)
    monkeypatch.setitem(reranker.MODELS, "mmarco", custom)
    monkeypatch.setenv("DHOLE_RERANK_MODEL", "mmarco")
    reranker.reset_selection_cache()
    assert reranker.raw_thresholds() == (0.7, 0.2)
    from dhole_mcp import search
    assert search._raw_thresholds() == (0.7, 0.2)


def test_tier_reads_the_thresholds_it_is_given():
    from dhole_mcp.search import _tier
    assert _tier(0.9, 1, 10, 0.6, raw_high=0.7, raw_med=0.2) == "med"
    assert _tier(0.9, 1, 10, 0.8, raw_high=0.7, raw_med=0.2) == "high"
    assert _tier(0.9, 1, 10, 0.1, raw_high=0.7, raw_med=0.2) == "low"


def test_build_results_mints_tiers_with_the_active_models_lines(monkeypatch):
    """档位铸在行上时必须用当前模型那两条线，而不是默认模型抄下来的常数。"""
    custom = dataclasses.replace(reranker.MODELS["mmarco"], raw_high=0.7, raw_med=0.2)
    monkeypatch.setitem(reranker.MODELS, "mmarco", custom)
    monkeypatch.setenv("DHOLE_RERANK_MODEL", "mmarco")
    reranker.reset_selection_cache()
    from dhole_mcp.search import _build_results
    from dhole_mcp.search_engines import RawResult
    row = RawResult(title="t", url="https://example.com/x", snippet="s",
                    source="bing", position=1, consensus=1, sources=("bing",))
    row.neural_raw = 0.6          # above bge's 0.5, below mmarco's custom 0.7
    out = _build_results("q", [row], [0.6], total_families=1)
    assert out[0].fetch_relevance == "med"


# ─── 16.x 遗留的选择文件：不再选，但不静默 ─────────────────────────────────

def test_legacy_config_warns_but_does_not_switch(legacy_config, caplog):
    """手写过的 reranker.json 不再选模型，但**不许静默换掉打分器**。"""
    _write_legacy(legacy_config, "ms-marco")
    with caplog.at_level("WARNING", logger="dhole-mcp.reranker"):
        assert reranker.active_model().name == "bge-zh"
    assert "ms-marco" in caplog.text
    assert "DHOLE_RERANK_MODEL" in caplog.text
    assert "bge-zh" in caplog.text


def test_naming_the_default_is_silent(legacy_config, caplog):
    """文件里写的就是默认那颗 —— 没有"被换掉"这回事，不该吵。"""
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


def test_model_present_uses_the_active_model(tmp_path, monkeypatch):
    """present() 描述的是当前那一颗，不是"随便哪些文件在"。"""
    models_dir = tmp_path / "models"
    monkeypatch.setattr(reranker.paths, "models_dir", lambda: models_dir)
    model = reranker.MODELS["bge-zh"]
    d = models_dir / "bge-zh"
    d.mkdir(parents=True, exist_ok=True)
    (d / "model.onnx").write_bytes(b"x" * model.min_bytes)
    (d / "tokenizer.json").write_bytes(b"{}")
    assert reranker.model_present() is True
    (d / "model.onnx").write_bytes(b"x" * 10)
    assert reranker.model_present() is False


def test_capabilities_names_the_active_model(tmp_path, monkeypatch):
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
    """镜像给了不同字节时必须拒用，而不是悄悄拿它打分。"""
    holder, model_dir = tiny_env
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


def test_a_failed_download_names_the_file_not_just_offline(tiny_env, monkeypatch):
    """下载失败要留得下具体是哪一颗文件失败，下一句建议才不是"检查网络"。"""
    holder, model_dir = tiny_env
    monkeypatch.setattr(reranker, "_download_model_file", lambda name, dest: False)
    assert reranker._ensure_model() is None
    assert "download failed for model.onnx" in reranker._reranker_unavailable_reason


def test_registry_digests_are_well_formed_and_verifiable():
    """注册表里填了的摘要必须能核对：64 位小写十六进制。

    值本身来自仓库元数据（LFS oid），没法在离线测试里重新推导 —— 这里钉的是"别写坏
    格式"与"摘要指向的文件确实会下载"。
    """
    hex64 = re.compile(r"^[0-9a-f]{64}$")
    for name, m in reranker.MODELS.items():
        digests = list(m.publisher_sha256.values()) + list(m.variant_sha256.values())
        assert digests, f"{name} 的摘要不能空着 —— 空了等于没有真实性校验"
        for fname, digest in m.publisher_sha256.items():
            assert hex64.match(digest), f"{name}/{fname} 的摘要不是 64 位十六进制: {digest!r}"
            assert fname in m.relpaths, f"{name} 的摘要指向一个不下载的文件: {fname}"
        for remote, digest in m.variant_sha256.items():
            assert hex64.match(digest), f"{name}/{remote} 的摘要不是 64 位十六进制"
            assert any(remote in v.values() for v in m.variants.values()), \
                f"{name} 的变体摘要指向一个不下载的文件: {remote}"


def test_mmarco_digest_follows_the_selected_variant(monkeypatch):
    model = reranker.MODELS["mmarco"]
    monkeypatch.setattr(reranker.platform, "machine", lambda: "AMD64")
    selected = reranker.paths_for(model)
    assert reranker._publisher_digest(model, selected) == \
        model.variant_sha256[selected["model.onnx"]]
    monkeypatch.setattr(reranker.platform, "machine", lambda: "arm64")
    selected = reranker.paths_for(model)
    assert reranker._publisher_digest(model, selected) == \
        model.variant_sha256[selected["model.onnx"]]


def test_vocab_txt_is_fetched_best_effort(tiny_env, monkeypatch):
    """回退分词器要用的 vocab.txt 此前从不下发；列为必需项反而更危险。"""
    holder, model_dir = tiny_env
    log: list = []
    monkeypatch.setattr(reranker, "_download_model_file", _fake_download(b"Z" * 40, log))
    reranker._ensure_model()
    assert "vocab.txt" in log, "缺失时应尝试补下"


def test_preprovisioned_model_dir_downloads_nothing(tiny_env, monkeypatch):
    """文件齐了就不许再联网：README 让用户「整个目录拷过去」，这条钉住那句话。"""
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
