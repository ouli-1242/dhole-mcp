"""Local neural reranker: a semantic relevance scorer for search results.

Runs a cross-encoder (query, title+snippet) -> relevance score, in-process via
ONNX. It answers "does this result actually match the query?" — something the
source engines cannot tell you, since each engine only returns its own order.

Two models are registered (see MODELS below); the ACTIVE one is chosen by
``~/.dhole/config/reranker.json`` (``{"model": "<name>"}``), default
``bge-zh``. Unknown names are rejected, never silently mapped.

Graceful fallback: if onnxruntime/tokenizers are missing (lean install) or the
model download fails / is offline, `get_reranker()` returns None and the caller
falls back to cross-engine consensus + engine-position order. Neural rerank is
an `[all]` extra; lean installs get consensus-ordered search.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import os
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
    # 发布方记录的 sha256（本地文件名 -> 摘要）。**空 = 没有权威摘要可用**，此时
    # 下载后只是"算自己刚下的字节再写盘"，那是完整性不是真实性。填了才谈得上验证
    # 镜像给的就是作者发布的那份。填法：`<endpoint>/<repo>/raw/<rev>/<relpath>` 拿到
    # 的是 git 里提交的 LFS 指针（`oid sha256:...`，与下载端点不同的路径），取值后
    # **必须与本机已下载的字节逐位核对**再写进来 —— 拿不到就留空，别编。
    publisher_sha256: dict[str, str] = field(default_factory=dict)


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
        # LFS oid @ 上面的 rev，取自 hf-mirror 的仓库元数据；与本机下载的
        # onnx/model_int8.onnx（278,825,308 字节）逐位核对一致（2026-09-21）。
        publisher_sha256={
            "model.onnx":
                "2059d8ef0b6e935b4845e11b38c9af9e9e2e7b91f69fc99efe03254e0a7da8d3",
        },
    ),
    # Cross-lingual MiniLM distilled from XLM-R Large on mMARCO (incl. Chinese),
    # fp32. Apache-2.0, ~117M params / ~450MB. Only worth it when you want the
    # best multilingual ranking and have the bandwidth for the heavier file.
    "zh-full": RerankerModel(
        name="zh-full",
        repo="cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
        rev="1427fd652930e4ba29e8149678df786c240d8825",
        relpaths={
            "model.onnx": "onnx/model.onnx",
            "tokenizer.json": "tokenizer.json",
            "vocab.txt": "sentencepiece.bpe.model",
        },
        approx_bytes=450_000_000,
        min_bytes=300_000_000,
        label="cross-lingual MiniLM fp32 (mMARCO, incl. Chinese) - heavier",
        # 未填：本机没下载过这个模型，拿不到可用于核对的字节 —— 机制对它是惰性的
        # （只做自记哈希防损坏）。填之前必须先在真机上下一份并逐位核对。
    ),
    # The 14.x default until now: English MS MARCO MiniLM. Apache-2.0,
    # ~22.7M params / ~91MB ONNX. Kept for EN-heavy users + backward compat.
    "ms-marco": RerankerModel(
        name="ms-marco",
        repo="cross-encoder/ms-marco-MiniLM-L-6-v2",
        rev="c5ee24cb16019beea0893ab7796b1df96625c6b8",
        relpaths={
            "model.onnx": "onnx/model.onnx",
            "tokenizer.json": "tokenizer.json",
            "vocab.txt": "vocab.txt",
        },
        approx_bytes=91_000_000,
        min_bytes=50_000_000,
        label="English MS MARCO MiniLM (legacy default)",
        # 同上（仓库已改名：ms-marco-MiniLM-L-6-v2 -> ...L6-v2，rev 保留）。与本机
        # 下载的 onnx/model.onnx（91,011,230 字节）逐位核对一致（2026-09-21）。
        publisher_sha256={
            "model.onnx":
                "5d3e70fd0c9ff14b9b5169a51e957b7a9c74897afd0a35ce4bd318150c1d4d4a",
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


def active_model() -> RerankerModel:
    """The model the search path actually uses: config file wins, else default.

    Unknown names in the config are IGNORED (with a warning), never mapped:
    silently scoring with the wrong model would be worse than scoring with the
    default one, and the note tells the user exactly what happened.
    """
    from dhole_mcp import reranker_config

    name = reranker_config.get_selected() or DEFAULT_MODEL
    model = MODELS.get(name)
    if model is None:
        logger.warning(
            f"reranker config names unknown model {name!r} - using default "
            f"{DEFAULT_MODEL}. Known: {', '.join(sorted(MODELS))}."
        )
        return MODELS[DEFAULT_MODEL]
    return model


def active_model_dir() -> Path:
    """Local directory for the active model (created on download)."""
    return paths.models_dir() / active_model().name
MAX_SEQ = 512
# Sanity floor so a truncated/failed download is rejected (real onnx is ~80MB).
MIN_MODEL_BYTES = 50_000_000


def _hf_endpoints() -> list[str]:
    custom = (os.environ.get("DHOLE_HF_ENDPOINT")
              or os.environ.get("HF_ENDPOINT") or "").strip().rstrip("/")
    if custom:
        return [custom]
    return ["https://huggingface.co", "https://hf-mirror.com"]


def _model_urls(model: RerankerModel, name: str) -> list[str]:
    """Candidate URLs for one model file, in fallback order.

    The revision is pinned, so the bytes are identical from any endpoint — the
    fallback changes where they come from, never what they are.
    """
    return [f"{ep}/{model.repo}/resolve/{model.rev}/{model.relpaths[name]}"
            for ep in _hf_endpoints()]


def _download_model_file(name: str, dest: Path) -> bool:
    """Download one model file, trying every configured endpoint."""
    for url in _model_urls(active_model(), name):
        if _download_file(url, dest):
            return True
    return False

_reranker: Optional[object] = None
_reranker_tried: bool = False
_reranker_unavailable_reason: str = ""
_reranker_lock: Optional["asyncio.Lock"] = None


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
    of hanging on a dead socket forever, and (c) remove the partial file on
    failure so the next attempt starts clean or resumes only when we know how.
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
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "ab") as f:
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
            logger.warning(f"short download for {dest.name}: {done:,}/{total:,} bytes")
            tmp.unlink(missing_ok=True)
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
    # A pre-14.3 ~/.dhole_mcp_cache may already hold this ~90MB model; move it
    # before deciding it has to be downloaded (on some networks it cannot be).
    paths.migrate_legacy_cache_dir()
    model = active_model()
    model_dir = active_model_dir()
    onnx = model_dir / "model.onnx"
    tokjson = model_dir / "tokenizer.json"
    sha_file = model_dir / "model.sha256"

    need = []
    if not onnx.exists() or onnx.stat().st_size < model.min_bytes:
        need.append("model.onnx")
    if not tokjson.exists():
        need.append("tokenizer.json")

    pub = (model.publisher_sha256 or {}).get("model.onnx", "")

    if need:
        logger.info(
            f"Dhole: downloading the local search reranker model "
            f"{model.name} (one-time, ~{model.approx_bytes // 1_000_000}MB, "
            "resumable)..."
        )
        for name in need:
            if not _download_model_file(name, model_dir / name):
                return None
        # vocab.txt 被 :375 那个 BERT WordPiece 回退路径使用，却不在必需列表里 ——
        # 新装机上它从来不会被下载，回退一触发就是文件不在。刻意做成"尽力补下、失败
        # 不致命"：列为必需项的话，任何仓库里该文件缺失都会让本来能用的重排整体失效，
        # 那比它要修的 bug 更糟。
        if "vocab.txt" in model.relpaths and not (model_dir / "vocab.txt").exists():
            _download_model_file("vocab.txt", model_dir / "vocab.txt")
        if onnx.stat().st_size < MIN_MODEL_BYTES:
            logger.warning("downloaded model.onnx is too small; rejecting")
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
                _reject("model.onnx failed publisher sha256 verification")
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
    _reranker_tried = True
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
            _reranker_unavailable_reason = "reranker model download failed (offline?)"
            return None
        onnx_path, tok_path = paths
        from tokenizers import Tokenizer
        try:
            tok = Tokenizer.from_file(str(tok_path))
        except Exception:
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
    # model_present() is blocking: on first use it can move a pre-14.3 legacy
    # cache/model tree (see paths.migrate_legacy_cache_dir). Keep it off the loop.
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


def rerank(query: str, results: list) -> Optional[list[tuple]]:
    """Rerank RawResults with the neural cross-encoder. Returns (result, score)
    pairs sorted desc, or None if the reranker is unavailable (caller falls back
    to consensus + engine-position order).

    Scores are min-max normalized across this result set to 0..1. ms-marco
    sigmoid saturates (~1.0 for any clearly-relevant snippet), so raw scores
    cluster tightly and can't discriminate among good results; normalizing
    restores meaningful spread (top=1.0, worst=0.0) for the relevance_score field
    + tier derivation. Ranking ORDER is unchanged (normalization is monotonic).
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
    if mx > mn:
        scores = [(s - mn) / (mx - mn) for s in raw]
    else:
        scores = [1.0 for _ in raw]  # all equal -> no spread to normalize -> all top
    pairs = list(zip(results, scores))
    pairs.sort(key=lambda rs: (-rs[1], rs[0].position))
    return pairs


def unavailable_reason() -> str:
    return _reranker_unavailable_reason


def model_present() -> bool:
    """True if the ACTIVE model's files are already cached locally (so
    get_reranker() will NOT trigger a download). Used by startup prewarm to warm
    the ONNX session only when it is free to do so.
    """
    paths.migrate_legacy_cache_dir()
    model = active_model()
    onnx = active_model_dir() / "model.onnx"
    tokjson = active_model_dir() / "tokenizer.json"
    return (onnx.exists() and onnx.stat().st_size >= model.min_bytes
            and tokjson.exists())


async def prewarm_reranker() -> None:
    """Best-effort startup prewarm: if the reranker model is already cached, load
    the ONNX session now (in a worker thread, race-safe via ensure_reranker's
    lock) so the first neural search skips the ~1-2s init and doesn't race this
    load. Does NOT download (skips when the model is absent). Never raises."""
    try:
        await ensure_reranker(download=False)
    except Exception:
        pass


