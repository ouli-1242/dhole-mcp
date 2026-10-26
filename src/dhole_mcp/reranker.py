"""本地神经重排器：搜索结果的相关性语义打分器。

在进程内通过 ONNX 跑一个交叉编码器 (query, title+snippet) -> 相关性分数。它回答
"这条结果真的与查询匹配吗" —— 源引擎答不了这个问题，因为每家的引擎只交回它自己的
顺序。

两张注册表（见 MODELS）：``bge-zh`` 仍是默认（中文更强），``mmarco`` 是后加的
快档（同一份 67 题冻结语料上整体 P@5 不降、CPU 快 1.8×、体积 119MB vs 279MB）。
选择走 ``DHOLE_RERANK_MODEL``，启动期读一次 —— 模型会话是进程级单例，运行中换档
会让同一个进程里的搜索用两个打分器，所以换档要重启。17.0 曾把注册表收敛成一颗；
这次恢复多档是因为有了**实测的**性价比档，而不是把「多一个旋钮」当卖点：
``ms-marco``（23MB 英文）在同一把尺上 raw 分数饱和、tier 失去区分度，所以没进表。
老机器上遗留的 ``~/.dhole/config/reranker.json`` 仍不选任何东西：**不静默换掉
打分器**，只在日志里说明它不再起作用、以及现在该用哪个变量（见
``_legacy_selection``）。

优雅回退：onnxruntime/tokenizers 缺失（精简安装）、模型下载失败或离线时，
`get_reranker()` 交回 None，调用方回退到跨引擎共识 + 引擎位置顺序。神经重排是一个
`[all]` 额外项；精简安装得到按共识排序的搜索。
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
    """一个可下载的交叉编码器。

    relpaths 把 dhole 本地保存的文件名映射到 HF 仓库内路径
    ``<endpoint>/<repo>/resolve/<rev>/`` 下的位置。min_bytes 是完整性下限，被截断的
    下载会被拒绝，**不**被信任。
    """

    name: str                     # 注册表键 + 本地目录名
    repo: str                     # HuggingFace 仓库 id
    rev: str                      # 钉住的 commit：各处的字节完全相同
    relpaths: dict[str, str] = field(default_factory=dict)
    approx_bytes: int = 90_000_000   # 预期的 ONNX 大小，用于诚实的 UI 文案
    min_bytes: int = 50_000_000   # 完整性下限；更小 = 被截断，不可信
    label: str = ""               # 给 -v / 文档的简短人话描述
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


# 注册表（键序 = 展示顺序）。默认项是下面的 DEFAULT_MODEL。
MODELS: dict[str, RerankerModel] = {
    # 双语 BGE 重排器（BAAI），int8。在 zh+en 句对上训练 —— 中文排序好于多语蒸馏版，
    # 且比 fp32 版本小 38%。标准 int8 算子，凡是 onnxruntime 能跑的地方都能跑。
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

# 注册表出现之前那几个常量的向后兼容别名（测试与外部调用方可能仍在导入它们）。
# 它们跟随 DEFAULT 模型（注册表常量是导入期快照；活答案由 active_model() 给出）。
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
    """忘掉启动期的选择（测试用它模拟一个全新进程）。"""
    global _selected_name
    _selected_name = None


def _selected_model_name() -> str:
    global _selected_name
    if _selected_name is None:
        raw = (env_registry.value_of("DHOLE_RERANK_MODEL") or "").strip()
        _selected_name = raw or DEFAULT_MODEL
    return _selected_name


def active_model() -> RerankerModel:
    """这个进程用来打分的模型：``DHOLE_RERANK_MODEL``，否则默认档。

    不认识的名字只警告、**不映射**：静默换一个模型打分，会让调用方针对点名那个模型
    做过的每一次 ``min_raw_relevance`` 标定全部失效。遗留的 ``reranker.json`` 不再
    选任何东西；如果它还在，我们说清楚是哪个变量取代了它。
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
    """当前模型的本地目录（下载时创建）。"""
    return paths.models_dir() / active_model().name


def _arch_key() -> str:
    """这台 CPU 在注册表里的架构键（x86_64 / arm64 / raw）。"""
    machine = platform.machine().lower()
    return {"amd64": "x86_64", "x86_64": "x86_64",
            "arm64": "arm64", "aarch64": "arm64"}.get(machine, machine)


def paths_for(model: RerankerModel) -> dict[str, str]:
    """**这台** CPU 可用的本地->远端文件映射；{} = 没有适合它的变体。"""
    variants = getattr(model, "variants", {}) or {}
    if variants:
        return dict(variants.get(_arch_key(), {}))
    return dict(getattr(model, "relpaths", {}) or {})


def raw_thresholds() -> tuple[float, float]:
    """**当前模型**的 (high, med) raw 分数线。见 RerankerModel。"""
    model = active_model()
    return model.raw_high, model.raw_med


def _publisher_digest(model: RerankerModel, selected: dict[str, str]) -> str:
    """这台 CPU 实际会加载的那个 model.onnx 的发布方 sha256。"""
    remote = selected.get("model.onnx", "")
    return model.variant_sha256.get(remote) or model.publisher_sha256.get("model.onnx", "")


MAX_SEQ = 512
# 完整性下限，被截断/失败的下载会被拒绝；每颗模型自己的 'min_bytes' 才是精确判据
# （这个全局值只是宽松的外层边界）。
MIN_MODEL_BYTES = 50_000_000


def _hf_endpoints() -> list[str]:
    custom = (env_registry.value_of("DHOLE_HF_ENDPOINT")
              or os.environ.get("HF_ENDPOINT") or "").strip().rstrip("/")
    if custom:
        return [custom]
    return ["https://huggingface.co", "https://hf-mirror.com"]


def _model_urls(model: RerankerModel, name_or_remote: str) -> list[str]:
    """一个文件的候选 URL，按回退顺序排列。

    ``name_or_remote`` 可以是**本地**文件名（经 ``model.relpaths`` 映射，变体出现之前
    的调用形状），也可以是仓库内路径（变体下载用它取到非默认那份文件）。revision 是
    钉住的，所以任何端点的字节都相同 —— 回退只改变"从哪里取"，从不改变"取到的是什么"。
    """
    remote = (getattr(model, "relpaths", {}) or {}).get(name_or_remote, name_or_remote)
    return [f"{ep}/{model.repo}/resolve/{model.rev}/{remote}"
            for ep in _hf_endpoints()]


def _download_model_file(name: str, dest: Path) -> bool:
    """为**这台** CPU 的变体下载一个模型文件，逐个端点尝试。"""
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
    """包住一个已热的 ONNX InferenceSession + 分词器，为 query/doc 打分。"""

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
        # 给命名古怪的输入留的位置回退。
        canon = [input_ids, attn, type_ids]
        ci = 0
        for name in self.input_names:
            if name in feed:
                continue
            feed[name] = canon[ci % len(canon)]
            ci += 1
        return feed

    def score(self, query: str, docs: list[str]) -> list[float]:
        """为每个 (query, doc) 对交回一个 sigmoid 相关性分数（0..1）。"""
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


_IDLE_TIMEOUT = 90.0  # 这么久没有收到任何字节就中止下载


def _download_file(url: str, dest: Path) -> bool:
    """把文件流式写到磁盘。成功交回 True。

    要扛住这个网络最糟的脾气：HF 的 CDN 会慢慢滴字节，然后卡住。我们 (a) 从遗留的
    ``.part`` **续传**（一次 449MB 的下载死在 60% 不该从头再来），(b) 60 秒没有字节
    就中止，而不是在死 socket 上永远挂着，(c) 短读时**保留**残缺文件，下一次尝试从它
    继续。服务器若不认我们的 Range 头（状态 200 加整份正文），我们先把残文件截掉 ——
    把整份正文接在旧前缀后面会弄坏文件。现场实测：一个 119MB 的模型文件在 1.7MB 处
    被切断；删掉残缺文件让每一次重试都从零开始。（无字节超时读 `_IDLE_TIMEOUT`，
    数量只有一处定义。）
    """
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        paths.ensure_private_dir(dest.parent)
        # 裸 python UA 会被某些 HF 镜像 403；串里保留 dhole 的身份，但保持浏览器形状。
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
        # 留着残缺文件（下一次尝试从它续传），但要说出原因。
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
    """确保 model.onnx + tokenizer.json 都在且有效。交回路径，否则 None。"""
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

    # 核对 onnx 是否仍与记录的哈希一致（发现损坏）。有发布方摘要时以它为准；
    # 自己记录的 sidecar 是退路，只证明"自我们取回之后没变过"。
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
    """同步加载重排器（ONNX 会话 + 分词器）。写模块单例与 'tried' 标记。从不抛异常。
    在 ensure_reranker 的锁下调用，于是并发调用方（预热 + 首次搜索）共享**一次**加载。"""
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
    """一次加载尝试实际做的事：导入额外依赖、取模型、预热会话。"""
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
            # 回退：用 vocab.txt 建一个 BERT WordPiece 分词器。
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
    """看一眼已经热的单例重排器；尚未加载 / 不可用时交回 None。

    只**窥视** —— 从不触发加载（那是 ensure_reranker() 的活，由预热与搜索路径调用）。
    调用方（rerank()、search）用它检查神经交叉编码器是否已热；是 None 就回退到跨引擎
    共识 + 引擎位置顺序。从不抛异常。
    """
    return _reranker  # type: ignore[return-value]


async def ensure_reranker(*, download: bool = True) -> Optional[_Reranker]:
    """竞态安全的异步重排器加载。并发调用方（启动预热 + 首次搜索）经一把锁共享
    **一次**加载；搜索会等待正在进行的预热，而不是与它赛跑（旧的同步 get_reranker()
    要么重复加载，要么在预热已于加载中途置上 _reranker_tried 时静默交回 None，于是
    首次搜索失去神经重排）。交回已经热的重排器，或 None（不可用 / 模型缺失且
    download=False）。

    缓存 'tried' 状态，于是一次性失败不会每次调用都重试。
    """
    global _reranker_lock
    if _reranker is not None:
        return _reranker  # type: ignore[return-value]  # 已热的快路径（不拿锁）
    # model_present() 会碰文件系统（还可能重命名模型目录），所以即使如今只花几毫秒，
    # 也要留在事件循环之外。
    if not download and not await asyncio.to_thread(model_present):
        return None
    if _reranker_lock is None:
        _reranker_lock = asyncio.Lock()
    # 拿锁，使并发中的加载（启动预热，或并行搜索）被**等待**而不是被赛跑：顶层快路径
    # 只对已热的重排器短路，不对 _reranker_tried 短路（后者由 _load_reranker 在仍持锁
    # 时提前置上 —— 于是加载中途的调用方必须等锁，然后再看已完成的结果）。
    async with _reranker_lock:
        if _reranker is not None:
            return _reranker  # type: ignore[return-value]
        if _reranker_tried:
            return None  # 此前一次加载**已结束**并失败；本进程内不再重试
        if not download and not await asyncio.to_thread(model_present):
            return None
        return await asyncio.to_thread(_load_reranker)


def rerank(query: str, results: list, *, min_relevance: float = 0.0,
           min_raw_relevance: float = 0.0,
           stats: Optional[dict] = None) -> Optional[list[tuple]]:
    """用神经交叉编码器重排 RawResults。交回按分数降序的 (result, score) 对；
    重排器不可用时交回 None（调用方回退到共识 + 引擎位置顺序）。

    分数在本次结果集内做 min-max 归一化到 0..1。交叉编码器的 sigmoid 会饱和
    （任何明显相关的片段都接近 ~1.0），所以 raw 分数挤得很紧，区分不了好结果之间的
    差别；归一化恢复了有意义的跨度（最好=1.0，最差=0.0），供 relevance_score 字段与
    tier 推导使用。排序**顺序**不变（归一化是单调的）。

    ``min_relevance``（G5）会把在这个**归一化**尺度上低于它的每一对都丢掉，于是一次
    查询的引擎结果大多离题时，不会因为它们是烂苹果里最好的就交回离题货。默认 0.0 =
    全留（16.0 之前的行为）。

    归一化尺度的已知局限：因为最好那条被**定义**为 1.0、最差为 0.0，归一化的地板
    永远拒绝不了一个**整体**都无关的集合 —— 全无关集合里最好的成员仍然归一到 1.0，
    而当所有 raw 分数相同时它们全都归一到 1.0。它修剪的是**有跨度**的集合的尾巴；
    它无法证明一个集合是好的。

    ``min_raw_relevance`` 则是 raw 交叉编码器 sigmoid 上的地板，这才让"这一轮没有
    任何东西相关"说得出口。在真实搜索结果上实测（默认 bge-zh；每个注册模型都有自己的
    `raw_high`/`raw_med`，因为这个分布是模型特有的）：离题文档的分数约 1e-4（621 对
    跨主题句对的 p90 是 0.0001，最大 0.0104），而相关文档坐在 0.93-0.9996，两堆几乎
    不重叠 —— 引擎结果里的垃圾尾巴是 ~0.0001 那一条带，不是好结果里的低分位。同主题
    不同侧面的句对（难的那一类）落在 p50 0.005 / p90 0.18，再往上地板就不安全了，
    所以这是 opt-in、没有内定的默认值，而推荐的起点是 0.1 而不是 0.5。分布是模型特有
    的，所以在一颗模型上标定出的数字不自动适用于另一颗：``stats`` 报告这一轮实际跨过
    的范围，调用方据此标定，而不是靠猜。

    ``stats``（可选 dict）会填入 ``{"raw_min", "raw_max", "kept",
    "dropped_raw"}`` —— 这个候选集实际观测到的 raw 跨度。它做成出参而不是更丰富的
    返回类型，是为了让每个按 ``(result, score)`` 解包的既有调用方继续工作。每行的 raw
    走另一条路，理由相同：它被盖在行上（``RawResult.neural_raw``，None = 本轮没有模型
    量过），而不是变成没人要的第三个元素。

    **当地板把一切丢掉时，结果是空列表，不是 None。** 两者意思不同，调用方不许混为一谈：
    None = "没有重排器，回退到共识顺序"（没有产出任何信息）；[] = "重排器跑了，拒绝了
    每一个候选"（产出了信息，而且诚实的答案是它们都没过地板）。调用方应当报告 [] 这种
    情况，而不是静默显示零结果 —— search.py 把这个数并入它"引擎交了 N 条、全被丢掉
    而不是交回噪声"的账。
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
        scores = [1.0 for _ in raw]  # 全部相等 -> 没有跨度可归一 -> 全体置顶
    # (result, 归一分数, raw) —— raw 必须跟着它的那一对走，否则第一道过滤之后再用
    # raw 地板就得重新推导一遍。
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
    """Why the most recent reranker load failed.

    Empty while the reranker is warm, and also empty before the first failed load.
    Callers use it to explain a degradation; it is not how you answer availability (that
    is :func:`get_reranker` or :func:`ensure_reranker`).
    """
    return _reranker_unavailable_reason


def _model_dir_for(model: RerankerModel) -> Path:
    """这颗模型的缓存目录。当前模型保留那条可被 monkeypatch 的路径来源。"""
    if model.name == active_model().name:
        return active_model_dir()
    return paths.models_dir() / model.name


def _present_for(model: RerankerModel) -> bool:
    """**这颗**模型的文件在这台机器上齐了吗？（认 CPU 架构。）"""
    variants = getattr(model, "variants", {}) or {}
    if variants and not paths_for(model):
        return False
    d = _model_dir_for(model)
    onnx = d / "model.onnx"
    tokjson = d / "tokenizer.json"
    return (onnx.exists() and onnx.stat().st_size >= model.min_bytes
            and tokjson.exists())


def model_present() -> bool:
    """**当前**模型的文件是否已经缓存在本地（于是 get_reranker() 不会触发下载）。
    启动预热只在可以免费预热时用它来热 ONNX 会话。

    这一颗是「模型在不在盘上」唯一的作者。`updater.py` 里那两行（`dhole -v` 的能力行与
    体检行）从前各自 `Path.exists()` 数文件，一份都不带 `min_bytes` 那道闸 —— 实测
    （2026-10-15 · `review/.runtime/p4/f34_measure_three_authors.py`）：下载被截断时
    （onnx 在、只有 1KB、tokenizer 齐）这里说「不在」而能力行说 `ready (bge-zh…)`、
    体检行说 `ok … 0.0MB in 2 file(s)`；缺 `tokenizer.json` 时体检行同样说 `ok`。
    现在两位读者都只读这一颗。
    """
    return _present_for(active_model())


def deps_present() -> bool:
    """神经重排的额外依赖在这里可导入吗（不导入、不下载 —— 只查）。"""
    import importlib.util
    return all(importlib.util.find_spec(m) is not None
               for m in ("onnxruntime", "tokenizers", "numpy"))


def model_status() -> dict:
    """对「这台机器的重排器现在是什么状态」的唯一回答 —— CLI 与线上回执都读它。

    `state` 是判决，而且它只有一位作者：
      `loaded`         本进程持有一个已热的交叉编码器会话
      `on_disk`        文件按 `model_present()` 判据在盘上，但本进程没加载
      `not_downloaded`（或只到一半：被截断的文件算"不在" —— 见那颗的 docstring）
      `deps_absent`    python 额外依赖缺失，盘上有没有模型都救不了

    其余是事实，不是判决：注册表指向哪颗模型、缺一颗大约要多少 MB，以及本进程自己的
    加载账本（`load_attempts` / `last_attempt_ms` / `recorded_reason` —— 与
    `fetch_hint` 在 R02-F33 之后引用的是同一批数）。「盘上有但加载失败」与「盘上有但
    还没试过」是两种处方，所以账本跟判决一起交回。

    这里没有路径：这是本机本地状态，而工具输出会落进 transcript（R02-045/048/049
    守的是同一道篱笆）。模型**名字**没关系 —— 它是注册表内容，不是这台机器把它放在哪。

    `loaded` 压过磁盘读数是有意的，而两者**不是**同一个问题：热会话是关于**本进程**的
    事实（它加载时那些文件在），`present` 是此刻读目录的事实。想要"新来一次调用能不能
    用它"的读者读 `present`；想要"这个进程现在在不在重排"的读者读 `state`。两者都不
    许改写对方。
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
    """尽力而为的启动预热：重排器模型已缓存时，现在（在工作线程里，经 ensure_reranker
    的锁保证竞态安全）加载 ONNX 会话，好让首个神经搜索跳过 ~1-2s 的初始化，也不与这次
    加载赛跑。**不**下载（模型缺失时直接跳过）。从不抛异常。"""
    try:
        await ensure_reranker(download=False)
    except Exception:
        pass


