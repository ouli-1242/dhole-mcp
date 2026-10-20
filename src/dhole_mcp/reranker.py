"""Local neural reranker: a semantic relevance scorer for search results.

Runs a cross-encoder (query, title+snippet) -> relevance score, in-process via
ONNX. It answers "does this result actually match the query?" — something the
source engines cannot tell you, since each engine only returns its own order.

两张注册表（见 MODELS）：``bge-zh`` 仍是默认（中文更强），``mmarco`` 是后加的
快档（同一份 67 题冻结语料上整体 P@5 不降、CPU 快 1.8×、体积 119MB vs 279MB）。
选择走 ``DHOLE_RERANK_MODEL``，启动期读一次 —— 模型会话是进程级单例，运行中换档
会让同一个进程里的搜索用两个打分器，所以换档要重启。17.0 曾把注册表收敛成一颗；
这次恢复多档是因为有了**实测的**性价比档，而不是把「多一个旋钮」当卖点：
``ms-marco``（23MB 英文）在同一把尺上 raw 分数饱和、tier 失去区分度，所以没进表。
老机器上遗留的 ``~/.dhole/config/reranker.json`` 仍不选任何东西：**不静默换掉
打分器**，只在日志里说明它不再起作用、以及现在该用哪个变量（见
``_legacy_selection``）。

Graceful fallback: if onnxruntime/tokenizers are missing (lean install) or the
model download fails / is offline, `get_reranker()` returns None and the caller
falls back to cross-engine consensus + engine-position order. Neural rerank is
an `[all]` extra; lean installs get consensus-ordered search.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import platform

from dhole_mcp import env_registry
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dhole_mcp import paths

logger = logging.getLogger("dhole-mcp.reranker")


@dataclass(frozen=True)
class RerankerModel:
    """One downloadable cross-encoder.

    relpaths maps the LOCAL filename dhole stores to the repo-relative HF path
    under ``<endpoint>/<repo>/resolve/<rev>/``. min_bytes is a sanity floor so
    a truncated download is rejected, NOT trusted.
    """

    name: str                     # registry key + local dir name
    repo: str                     # HuggingFace repo id
    rev: str                      # pinned commit: bytes are identical everywhere
    relpaths: dict[str, str] = field(default_factory=dict)
    approx_bytes: int = 90_000_000   # expected ONNX size, for honest UI text
    min_bytes: int = 50_000_000   # sanity floor; smaller = truncated, not trusted
    label: str = ""               # short human description for -v / docs
    # 按 CPU 架构分叉的 ONNX 文件（架构 -> 与 relpaths 同形的映射）。缺省/空 = 一份
    # 文件走所有架构。量化文件按硬件内核导出时只能这样登记；空映射在某个架构上
    # 等于"这颗模型在这台机器上没有可用的字节"，加载侧会明确拒绝而不是拿错文件。
    variants: dict[str, dict[str, str]] = field(default_factory=dict)
    # 发布方记录的 sha256（本地文件名 -> 摘要）。**空 = 没有权威摘要可用**，此时
    # 下载后只是"算自己刚下的字节再写盘"，那是完整性不是真实性。填了才谈得上验证
    # 镜像给的就是作者发布的那份。填法：`<endpoint>/<repo>/raw/<rev>/<relpath>` 拿到
    # 的是 git 里提交的 LFS 指针（`oid sha256:...`，与下载端点不同的路径），取值后
    # **必须与本机已下载的字节逐位核对**再写进来 —— 拿不到就留空，别编。
    publisher_sha256: dict[str, str] = field(default_factory=dict)
    # 变体文件的摘要按**仓库内路径**索引（同一颗模型的 AVX2 / ARM64 量化文件字节
    # 不同，本地文件名却都叫 model.onnx，按本地名索引会张冠李戴）。
    variant_sha256: dict[str, str] = field(default_factory=dict)
    # 这颗模型自己标定出的 tier 线。raw 分布是模型特有的：同一对 (query, doc) 在
    # bge-zh 上是 0.93，在另一个打分器上可能是 0.99 或 0.2，把一颗模型的 0.5 套到
    # 另一颗上就是让 badge 说谎。线来自 `evals/ranking_baseline.py` 同尺测量。
    raw_high: float = 0.5
    raw_med: float = 0.05
    role: str = "balanced"        # balanced|fast —— 选择器与文档用
    languages: tuple[str, ...] = ("zh", "en")
    # tokenizer.json 读不动时能否退回 vocab.txt 建 WordPiece。mMARCO 的 vocab 是
    # SentencePiece，退回 WordPiece 会造出一套错的切词而不是报错，所以关掉。
    wordpiece_fallback: bool = True


# Registry (key order = display order). The default is DEFAULT_MODEL below.
MODELS: dict[str, RerankerModel] = {
    # Bilingual BGE reranker (BAAI), int8. Trained on zh+en pairs — better
    # Chinese ranking than a multilingual distillation, and 38% smaller than
    # the fp32 alternative. Standard int8 ops, runs anywhere onnxruntime does.
    "bge-zh": RerankerModel(
        name="bge-zh",
        repo="Xenova/bge-reranker-base",
        rev="280bcc27a84e0b898c251e06fddb25171bd9b101",
        relpaths={
            "model.onnx": "onnx/model_int8.onnx",
            "tokenizer.json": "tokenizer.json",
            "vocab.txt": "sentencepiece.bpe.model",
        },
        approx_bytes=279_000_000,
        min_bytes=100_000_000,
        label="bilingual BGE reranker int8 (BAAI, zh+en), default",
        role="balanced",
        languages=("zh", "en"),
        # LFS oid @ 上面的 rev，取自 hf-mirror 的仓库元数据；与本机下载的
        # onnx/model_int8.onnx（278,825,308 字节）逐位核对一致（2026-09-21）。
        publisher_sha256={
            "model.onnx":
                "2059d8ef0b6e935b4845e11b38c9af9e9e2e7b91f69fc99efe03254e0a7da8d3",
        },
    ),
    # 快档：mMARCO 上蒸馏的跨语 MiniLM（含中文），ONNX 量化。同一份 67 题冻结语料
    # （`evals/ranking_baseline.py` 的链）实测：P@5 0.552 vs bge-zh 0.549、MRR 0.749
    # vs 0.737、nDCG@10 0.770 vs 0.764；CPU 263ms -> 143ms/题（1.8×），文件 119MB ->
    # 279MB（2.4× 小）。中文子集 P@5 0.506 vs 0.517（略低），英文 0.606 vs 0.587
    # （更高）—— 所以它是"快档"，不是用来悄悄替换中文默认档的。raw 0.5/0.05 两条
    # tier 线与 bge-zh 在同一批标注行上量出的精确率/召回几乎重合（见下方注释）。
    # 量化文件按内核分叉：x86-64 用 AVX2 版，ARM64 用 arm64 版；其他架构没有可用
    # 文件，加载侧会明说而不是猜一份能跑但慢的文件。
    "mmarco": RerankerModel(
        name="mmarco",
        repo="cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
        rev="1427fd652930e4ba29e8149678df786c240d8825",
        relpaths={
            "model.onnx": "onnx/model_quint8_avx2.onnx",
            "tokenizer.json": "tokenizer.json",
            "vocab.txt": "sentencepiece.bpe.model",
        },
        variants={
            "x86_64": {
                "model.onnx": "onnx/model_quint8_avx2.onnx",
                "tokenizer.json": "tokenizer.json",
                "vocab.txt": "sentencepiece.bpe.model",
            },
            "arm64": {
                "model.onnx": "onnx/model_qint8_arm64.onnx",
                "tokenizer.json": "tokenizer.json",
                "vocab.txt": "sentencepiece.bpe.model",
            },
        },
        approx_bytes=119_000_000,
        min_bytes=100_000_000,
        label="fast multilingual mMARCO MiniLM int8 (zh+en, x86-64/arm64, ~1.8x faster)",
        role="fast",
        languages=("zh", "en"),
        wordpiece_fallback=False,
        raw_high=0.5,
        raw_med=0.05,
        publisher_sha256={
            "model.onnx":
                "6c2513767fb63d008a4377bef7a7a3555433d9436342bb53e35a3a72ffc52d4b",
        },
        variant_sha256={
            "onnx/model_quint8_avx2.onnx":
                "6c2513767fb63d008a4377bef7a7a3555433d9436342bb53e35a3a72ffc52d4b",
            "onnx/model_qint8_arm64.onnx":
                "1825907d6c1a9001ff78124780bbde20a614a8c3df3b63409cf3c72c6fe5c8b4",
        },
    ),
}

DEFAULT_MODEL = "bge-zh"

# Backward-compat aliases for the pre-registry constants (tests + external
# callers may still import them). They track the DEFAULT model (registry
# constants are import-time snapshots; the live answer is active_model()).
MODEL_ID = MODELS[DEFAULT_MODEL].repo
MODEL_REV = MODELS[DEFAULT_MODEL].rev
MODEL_DIR = paths.models_dir() / DEFAULT_MODEL


def _legacy_selection() -> str:
    """读一眼 16.x 遗留的 ``~/.dhole/config/reranker.json``（如果还在）。

    它不再选任何东西：选择走 ``DHOLE_RERANK_MODEL``（启动期读）。留这个函数只有
    一个目的：让 ``active_model`` 能**说明**「你当年选的那个名字不再起作用、现在
    该改哪个变量」，而不是静默把打分器换掉。文件不在 / 读不动 / 没有 ``model`` 键
    —— 一律返回空串（沉默是正确结果）。
    """
    try:
        p = paths.home() / "config" / "reranker.json"
        return (json.loads(p.read_text(encoding="utf-8")).get("model") or "").strip()
    except Exception:
        return ""


#: 启动期那一档的名字。模型会话是进程级单例：如果这个名字在运行中改变，同一进程
#: 里的搜索会一半用旧打分器、一半用新打分器。所以 ``DHOLE_RERANK_MODEL`` 与
#: ``DHOLE_TOOLS`` 同律 —— 导入时读一次（下面的模块级赋值就是那次读），改它要重启
#: 宿主。测试用 ``reset_selection_cache()`` 模拟"新进程"。
_selected_name: Optional[str] = (
    (env_registry.value_of("DHOLE_RERANK_MODEL") or "").strip() or None
)


def reset_selection_cache() -> None:
    """Forget the startup selection (tests simulate a fresh process with this)."""
    global _selected_name
    _selected_name = None


def _selected_model_name() -> str:
    global _selected_name
    if _selected_name is None:
        raw = (env_registry.value_of("DHOLE_RERANK_MODEL") or "").strip()
        _selected_name = raw or DEFAULT_MODEL
    return _selected_name


def active_model() -> RerankerModel:
    """The model this process scores with: ``DHOLE_RERANK_MODEL``, else the default.

    Unknown names are IGNORED with a warning, never mapped: silently scoring with
    a different model would invalidate every ``min_raw_relevance`` calibration the
    caller made against the named one. A legacy ``reranker.json`` no longer
    selects anything; if present we say which variable replaced it.
    """
    name = _selected_model_name()
    model = MODELS.get(name)
    if model is None:
        logger.warning(
            f"DHOLE_RERANK_MODEL names unknown model {name!r} - using default "
            f"{DEFAULT_MODEL}. Known: {', '.join(MODELS)}."
        )
        model = MODELS[DEFAULT_MODEL]
    legacy = _legacy_selection()
    if legacy and legacy != name:
        logger.warning(
            f"~/.dhole/config/reranker.json names {legacy!r}, but it no longer selects "
            f"anything - DHOLE_RERANK_MODEL={name!r} is what runs. Delete that file "
            "and set DHOLE_RERANK_MODEL instead."
        )
    return model


def active_model_dir() -> Path:
    """Local directory for the active model (created on download)."""
    return paths.models_dir() / active_model().name


def _arch_key() -> str:
    """The registry's architecture key for this CPU (x86_64 / arm64 / raw)."""
    machine = platform.machine().lower()
    return {"amd64": "x86_64", "x86_64": "x86_64",
            "arm64": "arm64", "aarch64": "arm64"}.get(machine, machine)


def paths_for(model: RerankerModel) -> dict[str, str]:
    """The local->remote file map usable on THIS CPU; {} = no variant for it."""
    variants = getattr(model, "variants", {}) or {}
    if variants:
        return dict(variants.get(_arch_key(), {}))
    return dict(getattr(model, "relpaths", {}) or {})


def raw_thresholds() -> tuple[float, float]:
    """(high, med) raw-score lines for the ACTIVE model. See RerankerModel."""
    model = active_model()
    return model.raw_high, model.raw_med


def _publisher_digest(model: RerankerModel, selected: dict[str, str]) -> str:
    """Publisher sha256 for the model.onnx this CPU will actually load."""
    remote = selected.get("model.onnx", "")
    return model.variant_sha256.get(remote) or model.publisher_sha256.get("model.onnx", "")


MAX_SEQ = 512
# Sanity floor so a truncated/failed download is rejected; each model's own
# 'min_bytes' is the precise check (this global value is the loose outer bound).
MIN_MODEL_BYTES = 50_000_000


def _hf_endpoints() -> list[str]:
    custom = (env_registry.value_of("DHOLE_HF_ENDPOINT")
              or os.environ.get("HF_ENDPOINT") or "").strip().rstrip("/")
    if custom:
        return [custom]
    return ["https://huggingface.co", "https://hf-mirror.com"]


def _model_urls(model: RerankerModel, name_or_remote: str) -> list[str]:
    """Candidate URLs for one file, in fallback order.

    ``name_or_remote`` may be a LOCAL filename (mapped through ``model.relpaths``,
    the pre-variant call shape) or a repo-relative path (how variant downloads
    reach a file that is not the default one). The revision is pinned, so the
    bytes are identical from any endpoint — the fallback changes where they come
    from, never what they are.
    """
    remote = (getattr(model, "relpaths", {}) or {}).get(name_or_remote, name_or_remote)
    return [f"{ep}/{model.repo}/resolve/{model.rev}/{remote}"
            for ep in _hf_endpoints()]


def _download_model_file(name: str, dest: Path) -> bool:
    """Download one model file for THIS CPU's variant, trying every endpoint."""
    model = active_model()
    selected = paths_for(model)
    remote = selected.get(name)
    if not remote:
        return False
    for url in _model_urls(model, remote):
        if _download_file(url, dest):
            return True
    return False

_reranker: Optional[object] = None
_reranker_tried: bool = False
_reranker_unavailable_reason: str = ""
_reranker_lock: Optional["asyncio.Lock"] = None
# 「这个进程试过几次、那一次花了多少毫秒」的两个读数（R02-020 校正后的那半）：
# 写它们的唯一地点是 `_load_reranker`，读它们的唯一地点是 search 的那句话 —— 句子不自己掐表。
_load_attempts: int = 0
_last_attempt_ms: float = 0.0


def load_attempts() -> int:
    """这个进程真跑过几次加载/下载（`_load_reranker` 每进一次 +1）。

    `search` 在起任务前读一次、等完再读一次：多了就是**这一发**付的账，没多就是它在引用
    别人付过的那笔 —— 这两种情况下该给用户的建议完全不同（等 / 换网 / 离线放模型 vs
    再发一百遍也不会变）。
    """
    return _load_attempts


def last_attempt_ms() -> float:
    """最近那一次尝试的耗时（毫秒）；没试过就是 0，句子据此不说一个凭空的数。"""
    return _last_attempt_ms


class _Reranker:
    """Wraps a warm ONNX InferenceSession + tokenizer for query/doc scoring."""

    def __init__(self, model_path: Path, tokenizer):
        import numpy as np
        import onnxruntime as ort
        so = ort.SessionOptions()
        try:
            so.graph_opt_level = ort.GraphOptLevel.ORT_ENABLE_ALL
        except Exception:
            pass
        self.sess = ort.InferenceSession(
            str(model_path), so, providers=["CPUExecutionProvider"],
        )
        self.input_names = [i.name for i in self.sess.get_inputs()]
        self.tok = tokenizer
        try:
            self.tok.enable_truncation(max_length=MAX_SEQ)
        except Exception:
            pass
        self._np = np

    def _feed(self, input_ids, attn, type_ids):
        arrs = {"input_ids": input_ids, "attention_mask": attn, "token_type_ids": type_ids}
        feed = {}
        for name in self.input_names:
            if name in arrs:
                feed[name] = arrs[name]
        # Positional fallback for any oddly-named inputs.
        canon = [input_ids, attn, type_ids]
        ci = 0
        for name in self.input_names:
            if name in feed:
                continue
            feed[name] = canon[ci % len(canon)]
            ci += 1
        return feed

    def score(self, query: str, docs: list[str]) -> list[float]:
        """Return a sigmoid relevance score (0..1) for each (query, doc) pair."""
        if not docs:
            return []
        np = self._np
        encs = [self.tok.encode(query, d) for d in docs]
        maxlen = max((len(e.ids) for e in encs), default=0) or 1
        n = len(encs)
        input_ids = np.zeros((n, maxlen), dtype=np.int64)
        attn = np.zeros((n, maxlen), dtype=np.int64)
        type_ids = np.zeros((n, maxlen), dtype=np.int64)
        for i, e in enumerate(encs):
            L = len(e.ids)
            input_ids[i, :L] = e.ids
            attn[i, :L] = e.attention_mask
            type_ids[i, :L] = e.type_ids
        try:
            out = self.sess.run(None, self._feed(input_ids, attn, type_ids))
        except Exception as e:
            logger.warning(f"reranker inference failed: {e}")
            return [0.0] * n
        logits = np.asarray(out[0]).reshape(-1)
        scores = []
        for i in range(n):
            try:
                scores.append(1.0 / (1.0 + math.exp(-float(logits[i]))))
            except (OverflowError, ValueError):
                scores.append(0.0 if float(logits[i]) < 0 else 1.0)
        return scores


_IDLE_TIMEOUT = 90.0  # abort a download that produced no byte for this long


def _download_file(url: str, dest: Path) -> bool:
    """Stream a file to disk. Returns True on success.

    Resilient to this network's worst property: HF's CDN dribbles bytes slowly
    and stalls. We (a) RESUME from a leftover ``.part`` (a 449MB download that
    dies at 60% does not restart), (b) abort if no byte arrives for 60s instead
    of hanging on a dead socket forever, and (c) KEEP the partial on a short read
    so the next attempt resumes from it. A server that ignored our Range header
    (status 200 with a full body) makes us truncate first — appending that body
    to the old prefix would corrupt the file. Measured live: a 119MB model file
    was cut at 1.7MB; deleting the partial made every retry start from zero.
    """
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        paths.ensure_private_dir(dest.parent)
        # A plain python UA gets 403'd by some HF mirrors; keep dhole's identity
        # in the string but keep it browser-shaped.
        headers = {"User-Agent": "Mozilla/5.0 (compatible; dhole-mcp)"}
        resume_at = tmp.stat().st_size if tmp.exists() else 0
        if resume_at:
            headers["Range"] = f"bytes={resume_at}-"
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=60) as r:
            if resume_at and getattr(r, "status", 200) != 206:
                logger.info(f"  ... {dest.name}: server ignored Range; restarting clean")
                tmp.unlink(missing_ok=True)
                resume_at = 0
            with open(tmp, "ab") as f:
                total_hdr = r.headers.get("Content-Length")
                total = (int(total_hdr) + resume_at) if total_hdr else 0
                done = resume_at
                last_byte = time.monotonic()
                while True:
                    chunk = r.read(1 << 16)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    last_byte = time.monotonic()
                    if done % (1 << 24) == 0:
                        logger.info(f"  ... {dest.name}: {done:,} bytes")
                    if time.monotonic() - last_byte > _IDLE_TIMEOUT:
                        raise TimeoutError(f"no bytes for {_IDLE_TIMEOUT:.0f}s")
        if total and done < total:
            logger.warning(f"short download for {dest.name}: {done:,}/{total:,} bytes "
                           "(partial kept for the next attempt)")
            return False
        tmp.replace(dest)
        return True
    except Exception as e:
        # Leave the partial file (next attempt resumes from it) but surface why.
        logger.warning(f"download failed for {url} ({tmp.stat().st_size if tmp.exists() else 0:,} bytes so far): {e}")
        return False


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _reject(reason: str) -> None:
    """让"因为摘要不符而拒用"在诊断里说得出原因。

    否则调用方只会填 "reranker model download failed (offline?)"，把一次真实性拒绝
    报成网络问题 —— 那是这次要修的同一类毛病。
    """
    global _reranker_unavailable_reason
    _reranker_unavailable_reason = reason


def _ensure_model() -> Optional[tuple[Path, Path]]:
    """Ensure model.onnx + tokenizer.json are present + valid. Returns paths or None."""
    model = active_model()
    selected = paths_for(model)
    if not selected:
        _reject(
            f"{model.name} has no ONNX file for CPU architecture "
            f"{platform.machine()!r} - choose {DEFAULT_MODEL} with DHOLE_RERANK_MODEL"
        )
        return None
    model_dir = active_model_dir()
    onnx = model_dir / "model.onnx"
    tokjson = model_dir / "tokenizer.json"
    sha_file = model_dir / "model.sha256"

    need = []
    if not onnx.exists() or onnx.stat().st_size < model.min_bytes:
        need.append("model.onnx")
    if not tokjson.exists():
        need.append("tokenizer.json")

    pub = _publisher_digest(model, selected)

    if need:
        logger.info(
            f"Dhole: downloading the local search reranker model "
            f"{model.name} (one-time, ~{model.approx_bytes // 1_000_000}MB, "
            "resumable)..."
        )
        for name in need:
            if not _download_model_file(name, model_dir / name):
                _reject(
                    f"{model.name}: download failed for {name} (network cut or "
                    "offline; any partial file is kept for resume)"
                )
                return None
        # vocab.txt 被 :375 那个 BERT WordPiece 回退路径使用，却不在必需列表里 ——
        # 新装机上它从来不会被下载，回退一触发就是文件不在。刻意做成"尽力补下、失败
        # 不致命"：列为必需项的话，任何仓库里该文件缺失都会让本来能用的重排整体失效，
        # 那比它要修的 bug 更糟。
        if "vocab.txt" in selected and not (model_dir / "vocab.txt").exists():
            _download_model_file("vocab.txt", model_dir / "vocab.txt")
        if onnx.stat().st_size < max(model.min_bytes, MIN_MODEL_BYTES):
            logger.warning("downloaded model.onnx is too small; rejecting")
            _reject(f"{model.name}'s model.onnx is smaller than its sanity floor")
            return None
        if pub and _sha256(onnx) != pub:
            # 镜像/CDN 给的不是作者发布的那份。删掉重来一次；仍不一致就拒绝加载，
            # 而不是拿一份来源存疑的权重去给用户的检索打分。
            logger.warning("model.onnx does not match the publisher sha256; "
                           "discarding and retrying once")
            onnx.unlink(missing_ok=True)
            if not _download_model_file("model.onnx", onnx) or _sha256(onnx) != pub:
                logger.warning("model.onnx still does not match the publisher digest; "
                               "refusing to score with it")
                onnx.unlink(missing_ok=True)
                _reject(f"{model.name}'s model.onnx failed publisher sha256 verification")
                return None
        # 没有权威摘要时只记下"刚下到的字节"的哈希：那用于日后发现文件损坏，
        # 不构成对来源的验证（见 RerankerModel.publisher_sha256）。
        try:
            sha_file.write_text(_sha256(onnx))
            paths.harden_file(sha_file)
        except Exception:
            pass

    # Verify the onnx still matches its recorded hash (detect corruption). When a
    # publisher digest exists it is the standard; the self-recorded sidecar is the
    # fallback and only proves "unchanged since we fetched it".
    if sha_file.exists():
        try:
            want = pub or sha_file.read_text().strip()
            if _sha256(onnx) != want:
                logger.warning("model.onnx hash mismatch; re-downloading")
                if _download_model_file("model.onnx", onnx):
                    if pub and _sha256(onnx) != pub:
                        logger.warning("re-downloaded model.onnx still does not match "
                                       "the publisher digest; refusing to use it")
                        onnx.unlink(missing_ok=True)
                        _reject("model.onnx failed publisher sha256 verification")
                        return None
                    sha_file.write_text(_sha256(onnx))
                else:
                    return None
        except Exception:
            pass

    return onnx, tokjson


def _load_reranker() -> Optional[_Reranker]:
    """Synchronous reranker load (ONNX session + tokenizer). Sets the module
    singleton + 'tried' flag. Never raises. Called under the ensure_reranker
    lock so concurrent callers (prewarm + first search) share ONE load."""
    global _reranker, _reranker_tried, _reranker_unavailable_reason
    global _load_attempts, _last_attempt_ms
    _reranker_tried = True
    _load_attempts += 1
    _started = time.monotonic()
    try:
        _run_load()
    finally:
        _last_attempt_ms = (time.monotonic() - _started) * 1000.0
    return _reranker  # type: ignore[return-value]


def _run_load() -> Optional[_Reranker]:
    """What a load attempt actually is: import the extras, get the model, warm the session."""
    global _reranker, _reranker_unavailable_reason
    try:
        import onnxruntime  # noqa: F401
        import tokenizers  # noqa: F401
        import numpy  # noqa: F401
    except ImportError as e:
        _reranker_unavailable_reason = (
            f"neural rerank needs dhole-mcp[all] ({e.__class__.__name__}: {e})"
        )
        return None
    try:
        paths = _ensure_model()
        if paths is None:
            if not _reranker_unavailable_reason:
                _reranker_unavailable_reason = "reranker model download failed (offline?)"
            return None
        onnx_path, tok_path = paths
        from tokenizers import Tokenizer
        try:
            tok = Tokenizer.from_file(str(tok_path))
        except Exception:
            if not getattr(active_model(), "wordpiece_fallback", True):
                raise RuntimeError(
                    "tokenizer.json could not be read and this model has no "
                    "WordPiece fallback (its vocab is not WordPiece)")
            # Fallback: build a BERT WordPiece tokenizer from vocab.txt.
            from tokenizers import BertWordPieceTokenizer
            tok = BertWordPieceTokenizer(str(active_model_dir() / "vocab.txt"), lowercase=True)
        _reranker = _Reranker(onnx_path, tok)
        logger.info(f"Dhole: neural reranker ready ({active_model().name}, ONNX).")
        return _reranker
    except Exception as e:
        _reranker_unavailable_reason = f"reranker init failed: {e}"
        logger.warning(f"reranker init failed: {e}")
        return None


def get_reranker() -> Optional[_Reranker]:
    """Peek the warm singleton reranker, or None if not yet loaded / unavailable.

    PEEK ONLY — never triggers a load (that's ensure_reranker()'s job, called by
    the prewarm + the search path). Callers (rerank(), search) use this to check
    whether the neural cross-encoder is warm; if None they fall back to
    cross-engine consensus + engine-position order. Never raises.
    """
    return _reranker  # type: ignore[return-value]


async def ensure_reranker(*, download: bool = True) -> Optional[_Reranker]:
    """Race-safe async reranker load. Concurrent callers (the startup prewarm +
    the first search) share ONE load via a lock; the search awaits the in-flight
    prewarm instead of racing it (the old sync get_reranker() would either
    double-load or, if the prewarm had set _reranker_tried mid-load, silently
    return None so the first search lost neural rerank). Returns the warm
    reranker or None (unavailable / model absent and download=False).

    Caches the 'tried' state so a one-time failure doesn't retry every call.
    """
    global _reranker_lock
    if _reranker is not None:
        return _reranker  # type: ignore[return-value]  # warm fast path (no lock)
    # model_present() touches the filesystem (and may rename a model dir), so it
    # stays off the event loop even though the work is now milliseconds.
    if not download and not await asyncio.to_thread(model_present):
        return None
    if _reranker_lock is None:
        _reranker_lock = asyncio.Lock()
    # Acquire the lock so a concurrent in-flight load (the startup prewarm, or a
    # parallel search) is AWAITED rather than raced: the top-level fast path only
    # short-circuits on a warm reranker, NOT on _reranker_tried (which is set
    # early inside _load_reranker while still holding the lock — so a mid-load
    # caller must wait on the lock, then see the finished result).
    async with _reranker_lock:
        if _reranker is not None:
            return _reranker  # type: ignore[return-value]
        if _reranker_tried:
            return None  # a previous load FINISHED and failed; don't retry this process
        if not download and not await asyncio.to_thread(model_present):
            return None
        return await asyncio.to_thread(_load_reranker)


def rerank(query: str, results: list, *, min_relevance: float = 0.0,
           min_raw_relevance: float = 0.0,
           stats: Optional[dict] = None) -> Optional[list[tuple]]:
    """Rerank RawResults with the neural cross-encoder. Returns (result, score)
    pairs sorted desc, or None if the reranker is unavailable (caller falls back
    to consensus + engine-position order).

    Scores are min-max normalized across this result set to 0..1. The
    cross-encoder sigmoid saturates (~1.0 for any clearly-relevant snippet), so
    raw scores cluster tightly and can't discriminate among good results;
    normalizing restores meaningful spread (top=1.0, worst=0.0) for the
    relevance_score field + tier derivation. Ranking ORDER is unchanged
    (normalization is monotonic).

    ``min_relevance`` (G5) drops every pair scoring below it on that NORMALIZED
    scale, so a query whose engine results are mostly off-topic does not return
    the off-topic ones just because they were the best of a bad set. Defaults to
    0.0 = keep everything (the pre-16.0 behaviour).

    Known limit of the normalized scale: because the top result is *defined* to
    be 1.0 and the worst 0.0, a normalized floor can never reject a WHOLE set —
    the best member of an entirely irrelevant set still normalizes to 1.0, and
    when every raw score is identical they all normalize to 1.0. It trims the
    tail of a set that HAS spread; it cannot certify that a set is good.

    ``min_raw_relevance`` is the floor on the raw cross-encoder sigmoid instead,
    and that is what makes "nothing in this round is relevant" expressible.
    Measured on live search results (default bge-zh; every registered model has
    its own `raw_high`/`raw_med` because this distribution is model-specific):
    an off-topic document scores ~1e-4 (p90 0.0001 across 621
    cross-topic pairs, max 0.0104) while an on-topic one sits at 0.93-0.9996,
    so the two populations barely touch — the junk tail of engine results is the
    ~0.0001 band, not a low percentile of the good ones. Same-topic-different-
    aspect pairs (the hard case) land at p50 0.005 / p90 0.18, above which a
    floor stops being safe, which is why this is opt-in with no default value
    baked in and why 0.1 is the recommended starting point rather than 0.5.
    Distributions are model-specific, so a number calibrated on one model is not
    automatically right for another: ``stats`` reports what this round actually
    spanned so a caller can calibrate instead of guessing.

    ``stats`` (optional dict) is filled with ``{"raw_min", "raw_max", "kept",
    "dropped_raw"}`` — the observed raw span of this candidate set. It is an
    out-param rather than a richer return type so every existing caller unpacking
    ``(result, score)`` pairs keeps working. The per-row raw goes a different way
    for the same reason: it is stamped on the row as ``RawResult.neural_raw``
    (None = no model measured it this round) instead of becoming a third element
    nobody asked for.

    **When a floor removes everything, the result is an EMPTY list, not None.**
    The two mean different things and the caller must not conflate them: None =
    "no reranker, fall back to consensus order" (no information was produced);
    [] = "the reranker ran and rejected every candidate" (information was
    produced, and the honest answer is that none of them cleared the floor).
    Callers are expected to report the [] case rather than silently show zero
    results - search.py folds the count into its "engines delivered N results,
    all dropped rather than return noise" accounting.
    """
    rer = get_reranker()
    if rer is None or not results:
        return None
    docs = [f"{r.title} {r.snippet}" for r in results]
    try:
        raw = rer.score(query, docs)
    except Exception as e:
        logger.warning(f"neural rerank failed: {e}")
        return None
    if len(raw) != len(results):
        return None
    mn, mx = min(raw), max(raw)
    if stats is not None:
        stats["raw_min"], stats["raw_max"] = mn, mx
    if mx > mn:
        scores = [(s - mn) / (mx - mn) for s in raw]
    else:
        scores = [1.0 for _ in raw]  # all equal -> no spread to normalize -> all top
    # (result, normalized, raw) — the raw score has to travel with its pair, or a
    # raw floor cannot be applied after the first filter without re-deriving it.
    rows = list(zip(results, scores, raw))
    # 原始分数另外盖在行上（`RawResult.neural_raw`）：交回二元组 `(result, score)`
    # 是这颗函数的公开契约，而 score 是集合内归一的 —— "这一轮里最好的一条"和
    # "这条到底相关不相关"是两个问句，第二个的答案只在 raw 里。归一分数继续走它的
    # 老路（排序、地板、`relevance_score`），raw 跟着行走完 `_rank` → boost →
    # diversify → 铸 badge 那一步。
    for _r, _s, _raw_v in rows:
        _r.neural_raw = _raw_v

    if min_relevance > 0.0:
        kept = [r for r in rows if r[1] >= min_relevance]
        if len(kept) != len(rows):
            logger.info(f"rerank: min_relevance={min_relevance} dropped "
                        f"{len(rows) - len(kept)}/{len(rows)} result(s)")
        rows = kept
    if min_raw_relevance > 0.0:
        kept = [r for r in rows if r[2] >= min_raw_relevance]
        if len(kept) != len(rows):
            logger.info(f"rerank: min_raw_relevance={min_raw_relevance} dropped "
                        f"{len(rows) - len(kept)}/{len(rows)} result(s)")
        rows = kept
    if stats is not None:
        stats["kept"] = len(rows)
        stats["dropped_raw"] = (len(results) - len(rows)) if min_raw_relevance > 0.0 else 0
    pairs = [(r, s) for r, s, _ in rows]
    pairs.sort(key=lambda rs: (-rs[1], rs[0].position))
    return pairs


def unavailable_reason() -> str:
    return _reranker_unavailable_reason


def _model_dir_for(model: RerankerModel) -> Path:
    """This model's cache dir. The active model keeps the monkeypatchable author."""
    if model.name == active_model().name:
        return active_model_dir()
    return paths.models_dir() / model.name


def _present_for(model: RerankerModel) -> bool:
    """Are THIS model's files complete here? (Architecture-aware.)"""
    variants = getattr(model, "variants", {}) or {}
    if variants and not paths_for(model):
        return False
    d = _model_dir_for(model)
    onnx = d / "model.onnx"
    tokjson = d / "tokenizer.json"
    return (onnx.exists() and onnx.stat().st_size >= model.min_bytes
            and tokjson.exists())


def model_present() -> bool:
    """True if the ACTIVE model's files are already cached locally (so
    get_reranker() will NOT trigger a download). Used by startup prewarm to warm
    the ONNX session only when it is free to do so.

    这一颗是「模型在不在盘上」唯一的作者。`updater.py` 里那两行（`dhole -v` 的能力行与
    体检行）从前各自 `Path.exists()` 数文件，一份都不带 `min_bytes` 那道闸 —— 实测
    （2026-10-15 · `review/.runtime/p4/f34_measure_three_authors.py`）：下载被截断时
    （onnx 在、只有 1KB、tokenizer 齐）这里说「不在」而能力行说 `ready (bge-zh…)`、
    体检行说 `ok … 0.0MB in 2 file(s)`；缺 `tokenizer.json` 时体检行同样说 `ok`。
    现在两位读者都只读这一颗。
    """
    return _present_for(active_model())


def deps_present() -> bool:
    """The neural extras are importable here (no import, no download - a lookup)."""
    import importlib.util
    return all(importlib.util.find_spec(m) is not None
               for m in ("onnxruntime", "tokenizers", "numpy"))


def model_status() -> dict:
    """One answer to 「这台机器的重排器现在是什么状态」— the CLI and the wire read this.

    `state` is the verdict, and there is exactly one author for it:
      `loaded`         this process holds a warm cross-encoder session
      `on_disk`        the files are here per `model_present()`, not loaded in this process
      `not_downloaded` (or half-here: a truncated file counts as not here — see that docstring)
      `deps_absent`    the python extras are missing, so no model on disk would help

    The rest are facts, not verdicts: which model the registry points at, roughly how many
    megabytes a missing one costs, and this process's own load ledger (`load_attempts` /
    `last_attempt_ms` / `recorded_reason` - the same numbers `fetch_hint` quotes after R02-F33).
    「盘上有但加载失败」与「盘上有但还没试过」是两种处方，所以账本跟判决一起交回。

    No path in here: this is machine-local state and tool output lands in the transcript
    (the same fence R02-045/048/049 hold). The model NAME is fine - it is registry content,
    not where this box keeps it.

    `loaded` wins over the disk reading on purpose, and the two are NOT the same question:
    a warm session is a fact about THIS process (it was loaded from files that were there
    when it started), while `present` is the fact about the directory read right now. A
    reader who wants "can a fresh call use it" reads `present`; one who wants "is this
    process reranking right now" reads `state`. Neither is allowed to rewrite the other.
    """
    model = active_model()
    deps = deps_present()
    here = _present_for(model)
    if _reranker is not None:
        state = "loaded"
    elif not deps:
        state = "deps_absent"
    elif not here:
        state = "not_downloaded"
    else:
        state = "on_disk"
    # 「有几颗文件」与「模型能不能用」是两件事，分开交回：下载被截断时两颗都在而判决是
    # 不在。合成一件的话，读者就只能挑一句信 —— 今天 `dhole -v` 的两行正是各自挑了一句。
    d = active_model_dir()
    files_here = sum(1 for n in ("model.onnx", "tokenizer.json") if (d / n).exists())
    available = []
    for m in MODELS.values():
        variants = getattr(m, "variants", {}) or {}
        available.append({
            "model": m.name,
            "role": getattr(m, "role", ""),
            "label": m.label,
            "languages": list(getattr(m, "languages", ())),
            "approx_mb": max(1, m.approx_bytes // 1_000_000),
            "present": _present_for(m),
            "arch_supported": bool(paths_for(m)) if variants else True,
            "active": m.name == model.name,
        })
    if model.name not in MODELS:
        available.append({
            "model": model.name,
            "role": getattr(model, "role", ""),
            "label": getattr(model, "label", ""),
            "languages": list(getattr(model, "languages", ())),
            "approx_mb": max(1, getattr(model, "approx_bytes", 1) // 1_000_000),
            "present": here,
            "arch_supported": True,
            "active": True,
        })
    return {
        "model": model.name,
        "role": getattr(model, "role", ""),
        "label": getattr(model, "label", ""),
        "state": state,
        "deps_present": deps,
        "present": here,
        "files_here": files_here,
        "files_expected": 2,
        "approx_mb": max(1, model.approx_bytes // 1_000_000),
        "raw_high": getattr(model, "raw_high", 0.5),
        "raw_med": getattr(model, "raw_med", 0.05),
        "available": available,
        "load_attempts": _load_attempts,
        "last_attempt_ms": round(_last_attempt_ms),
        "recorded_reason": _reranker_unavailable_reason,
    }


def missing_model_phrase(st: dict) -> str:
    """「盘上那一团字节算不算一个模型」这句话唯一的作者（`dhole -v` 两行与 sys_state 共用）。

    分成两句是因为事实是两件事：一颗都没有 = 没下载过；有但不完整 = 下过、坏了。把后者说成
    「not downloaded」会让人以为重下一遍要从头开始，而把它说成「ready」就是今天那两行的错。
    """
    if st["files_here"] == 0:
        return (f"{st['model']} not downloaded (~{st['approx_mb']}MB, resumable; "
                "the first neural search fetches it)")
    return (f"{st['model']} incomplete on disk ({st['files_here']} of {st['files_expected']} "
            f"file(s); a usable model is ~{st['approx_mb']}MB) - not usable, and the "
            "first neural search re-fetches what is missing")


async def prewarm_reranker() -> None:
    """Best-effort startup prewarm: if the reranker model is already cached, load
    the ONNX session now (in a worker thread, race-safe via ensure_reranker's
    lock) so the first neural search skips the ~1-2s init and doesn't race this
    load. Does NOT download (skips when the model is absent). Never raises."""
    try:
        await ensure_reranker(download=False)
    except Exception:
        pass


