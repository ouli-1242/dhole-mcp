#!/usr/bin/env python3
"""B8 换型评测 —— 交叉编码器（现状 bge-zh） vs 双编码器（候选 gte-multilingual-base）。

**判据（§10 现在定死，别到时候再谈）**：分差不明显 → 不换。换型要"明显更好"，
不是"好一点" —— 二元判断的样本分辨率本就低，拿"好一点点"去换一个 600MB 模型加
一整套新代码不划算。这里把"明显"操作化成**逐题配对差值的 bootstrap 95% 置信区间**：
区间跨过 0，就是"不明显"。

**集 B 只测纯模型**：接口只有 `score(query, docs) -> [float]`，与引擎无关、跨版本
可比。两个后端实现同一个接口，跑同一批题（`evals/.runtime/eval_sets/<name>/qrels.jsonl`），
各出各的数，**永不与集 A 合并**（合起来会掩盖"模型涨了但解析器拖后腿"）。

nDCG@all 的算法与 `ranking_baseline._set_b()` 逐字一致 —— 换型要回答的是"同一个
指标下谁更好"，不是"换一个指标谁更好"。

用法：
    python evals/model_swap_b8.py                  # 两个后端都跑，出对照
    python evals/model_swap_b8.py --only cross     # 只跑现状（基线）
    python evals/model_swap_b8.py --limit 100      # 小样本快跑
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import pathlib
import random
import sys
import time

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

EVAL_SETS = REPO_ROOT / "evals" / ".runtime" / "eval_sets"
CANDIDATE_DIR = REPO_ROOT / "evals" / ".runtime" / "models" / "gte-multilingual-base"
RUNTIME = REPO_ROOT / "evals" / ".runtime" / "b8_model_swap"

CANDIDATE_REPO = "onnx-community/gte-multilingual-base"


# ── 两个后端，同一个接口 score(query, docs) -> [float] ────────────────────────


class CrossEncoder:
    """现状：`dhole_mcp.reranker` 里的 ONNX 交叉编码器（bge-zh）。"""

    name = "cross:bge-zh"

    def __init__(self) -> None:
        from dhole_mcp import reranker as R
        self._r = R._load_reranker()
        if self._r is None:
            raise RuntimeError("current reranker unavailable")
        self.model = R.active_model().name

    def score(self, query: str, docs: list[str]) -> list[float]:
        return self._r.score(query, docs)


class BiEncoder:
    """候选：gte-multilingual-base 的 ONNX 双编码器（池化 + cosine）。

    与交叉编码器的区别是结构性的：query 与 doc 各编码一次、比余弦。交叉编码器把
    两者拼起来过一个分类头，所以它**不能建索引**；双编码器能。这是 B8 想换的
    唯一理由 —— 如果排序质量还变差了，那这个理由就不成立。
    """

    def __init__(self, model_dir: pathlib.Path = CANDIDATE_DIR,
                 max_len: int = 512) -> None:
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer

        onnx_path = model_dir / "onnx" / "model.onnx"
        tok_path = model_dir / "tokenizer.json"
        if not onnx_path.exists() or not tok_path.exists():
            raise RuntimeError(
                f"candidate model not present at {model_dir} —— "
                f"先下 {CANDIDATE_REPO} 的 onnx/model.onnx 与 tokenizer.json")
        so = ort.SessionOptions()
        try:
            so.graph_opt_level = ort.GraphOptLevel.ORT_ENABLE_ALL
        except Exception:
            pass
        self.sess = ort.InferenceSession(
            str(onnx_path), so, providers=["CPUExecutionProvider"])
        self.input_names = [i.name for i in self.sess.get_inputs()]
        self.output_names = [o.name for o in self.sess.get_outputs()]
        self.tok = Tokenizer.from_file(str(tok_path))
        # 截断长度是**可比性开关**，不是随便设的：交叉编码器那一侧写死 512
        # （`reranker.MAX_SEQ`），这一侧必须能设成同一个值，否则量的就不是"换个
        # 打分器"，而是"给候选多看 16 倍上下文"。模型本身支持 8192，长窗口是它的
        # 结构性优势之一 —— 但那是另一个问题，分开量（`--max-len 2048`）。
        self.max_len = max_len
        try:
            self.tok.enable_truncation(max_length=max_len)
        except Exception:
            pass
        self.tok.enable_padding()
        self._np = np

    @property
    def name(self) -> str:  # type: ignore[override]
        return f"bi:gte-multilingual-base@{self.max_len}"

    def _embed(self, texts: list[str]):
        np = self._np
        encs = self.tok.encode_batch(texts)
        ids = np.asarray([e.ids for e in encs], dtype=np.int64)
        mask = np.asarray([e.attention_mask for e in encs], dtype=np.int64)
        types = np.asarray([e.type_ids for e in encs], dtype=np.int64)
        arrs = {"input_ids": ids, "attention_mask": mask, "token_type_ids": types}
        feed = {n: arrs[n] for n in self.input_names if n in arrs}
        canon = [ids, mask, types]
        ci = 0
        for n in self.input_names:
            if n not in feed:
                feed[n] = canon[ci % len(canon)]
                ci += 1
        out = self.sess.run(None, feed)
        named = dict(zip(self.output_names, out))
        # 导出通常直接给池化后的句向量；没有就自己按 attention mask 做 mean pooling。
        for key in ("sentence_embedding", "text_embeds", "embeddings"):
            if key in named:
                vecs = np.asarray(named[key], dtype=np.float32)
                break
        else:
            hidden = np.asarray(named.get("last_hidden_state", out[0]), dtype=np.float32)
            m = mask[..., None].astype(np.float32)
            vecs = (hidden * m).sum(axis=1) / np.clip(m.sum(axis=1), 1e-9, None)
        norms = np.clip(np.linalg.norm(vecs, axis=1, keepdims=True), 1e-9, None)
        return vecs / norms

    def score(self, query: str, docs: list[str]) -> list[float]:
        if not docs:
            return []
        qv = self._embed([query])[0]
        dv = self._embed(docs)
        return [float(x) for x in dv @ qv]


# ── 指标（与 ranking_baseline._set_b 逐字一致）────────────────────────────────


def ndcg_at_all(scores: list[float], n_pos: int) -> float:
    gold = [1.0] * n_pos + [0.0] * (len(scores) - n_pos)
    order = sorted(range(len(scores)), key=lambda i: -scores[i])
    dcg = sum(gold[i] / math.log2(k + 2) for k, i in enumerate(order))
    ideal = sum(g / math.log2(k + 2)
                for k, g in enumerate(sorted(gold, reverse=True)))
    return dcg / ideal if ideal else 0.0


def mrr(scores: list[float], n_pos: int) -> float:
    order = sorted(range(len(scores)), key=lambda i: -scores[i])
    for rank, i in enumerate(order, 1):
        if i < n_pos:
            return 1.0 / rank
    return 0.0


def load_sets(limit: int | None) -> list[tuple[str, dict]]:
    out = []
    if not EVAL_SETS.exists():
        return out
    for ds in sorted(p for p in EVAL_SETS.iterdir() if p.is_dir()):
        path = ds / "qrels.jsonl"
        if not path.exists():
            continue
        rows = [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()
                if ln.strip()]
        if limit:
            rows = rows[:limit]
        out.append((ds.name, rows))
    return out


def evaluate(backend, rows: list[dict]) -> list[dict]:
    per_q = []
    for i, row in enumerate(rows):
        docs = list(row["positives"]) + list(row["negatives"])
        n_pos = len(row["positives"])
        scores = backend.score(row["query"], docs)
        per_q.append({"ndcg": ndcg_at_all(scores, n_pos),
                      "mrr": mrr(scores, n_pos)})
        if (i + 1) % 50 == 0:
            print(f"    {backend.name}: {i + 1}/{len(rows)}", flush=True)
    return per_q


def paired_ci(deltas: list[float], *, iters: int = 5000, seed: int = 17) -> tuple[float, float]:
    """逐题配对差值的 bootstrap 95% 置信区间（确定性种子）。"""
    rnd = random.Random(seed)
    n = len(deltas)
    means = []
    for _ in range(iters):
        means.append(sum(deltas[rnd.randrange(n)] for _ in range(n)) / n)
    means.sort()
    return means[int(0.025 * iters)], means[int(0.975 * iters)]


def summarize(per_q: list[dict], key: str) -> dict:
    vals = [r[key] for r in per_q]
    return {"mean": sum(vals) / len(vals), "n": len(vals)}


def compare_reports(path_a: str, path_b: str) -> int:
    """从两份已落盘的报告算配对检验 —— 不必重跑推理。"""
    a = json.loads(pathlib.Path(path_a).read_text(encoding="utf-8"))
    b = json.loads(pathlib.Path(path_b).read_text(encoding="utf-8"))
    for ds in sorted(set(a["datasets"]) & set(b["datasets"])):
        pa, pb = a["datasets"][ds], b["datasets"][ds]
        na, nb = pa.get("per_query") or {}, pb.get("per_query") or {}
        for ka in na:
            for kb in nb:
                if ka == kb:
                    continue
                xs, ys = na[ka], nb[kb]
                if len(xs) != len(ys):
                    print(f"{ds}: {ka}({len(xs)}) vs {kb}({len(ys)}) 题数不同，跳过")
                    continue
                deltas = [y - x for x, y in zip(xs, ys)]
                lo, hi = paired_ci(deltas)
                wins = sum(1 for d in deltas if d > 1e-9)
                losses = sum(1 for d in deltas if d < -1e-9)
                verdict = ("B 明显更好" if lo > 0 else
                           "B 明显更差" if hi < 0 else "分差不明显（区间跨 0）")
                print(f"{ds}: {ka} → {kb}  n={len(deltas)}")
                print(f"  ΔnDCG 均值={sum(deltas)/len(deltas):+.4f} "
                      f"95%CI=[{lo:+.4f},{hi:+.4f}] 胜/负/平={wins}/"
                      f"{losses}/{len(deltas)-wins-losses}")
                print(f"  → {verdict}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["cross", "bi"], default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--max-len", type=int, default=512,
                    help="候选双编码器的截断长度；512 = 与交叉编码器同预算（可比），"
                         "2048 = 顺带看长窗口那一侧")
    ap.add_argument("--compare", nargs=2, metavar=("A.json", "B.json"),
                    help="不跑推理，只从两份已落盘的报告算配对检验")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    if args.compare:
        return compare_reports(*args.compare)

    sets = load_sets(args.limit)
    if not sets:
        print(f"集 B：没有语料 —— 先跑 evals/fetch_eval_sets.py（{EVAL_SETS}）")
        return 1

    backends = []
    if args.only != "bi":
        try:
            backends.append(CrossEncoder())
        except Exception as e:
            print(f"  cross 后端不可用：{e}")
    if args.only != "cross":
        try:
            backends.append(BiEncoder(max_len=args.max_len))
        except Exception as e:
            print(f"  bi 后端不可用：{e}")
    if not backends:
        print("没有可用的后端")
        return 1

    report = {"generated": dt.datetime.now().isoformat(timespec="seconds"),
              "datasets": {}, "backends": [b.name for b in backends]}
    for ds_name, rows in sets:
        print(f"== 集 B / {ds_name}：{len(rows)} 题，后端 {[b.name for b in backends]}")
        results = {}
        for b in backends:
            t = time.time()
            per_q = evaluate(b, rows)
            results[b.name] = per_q
            print(f"  {b.name}: nDCG@all={summarize(per_q,'ndcg')['mean']:.4f} "
                  f"MRR={summarize(per_q,'mrr')['mean']:.4f} "
                  f"({time.time()-t:.1f}s)")
        report["datasets"][ds_name] = {
            "n": len(rows),
            "metrics": {k: {"ndcg": summarize(v, "ndcg"), "mrr": summarize(v, "mrr")}
                        for k, v in results.items()},
            # 逐题数值要落盘：配对检验（bootstrap CI）只能从它算。只存均值就再也
            # 补不回来了 —— 想补就得把整轮推理重跑一遍。
            "per_query": {k: [round(r["ndcg"], 6) for r in v]
                          for k, v in results.items()},
        }
        if len(results) == 2:
            a, b_ = results[backends[0].name], results[backends[1].name]
            deltas = [y["ndcg"] - x["ndcg"] for x, y in zip(a, b_)]
            lo, hi = paired_ci(deltas)
            wins = sum(1 for d in deltas if d > 1e-9)
            losses = sum(1 for d in deltas if d < -1e-9)
            ties = len(deltas) - wins - losses
            verdict = ("候选明显更好" if lo > 0 else
                       "候选明显更差" if hi < 0 else "分差不明显（区间跨 0）")
            pair = {"mean_delta": sum(deltas) / len(deltas),
                    "ci95": [lo, hi], "wins": wins, "losses": losses, "ties": ties,
                    "verdict": verdict}
            report["datasets"][ds_name]["paired"] = pair
            print(f"  配对：ΔnDCG 均值={pair['mean_delta']:+.4f} "
                  f"95%CI=[{lo:+.4f},{hi:+.4f}] 胜/负/平={wins}/{losses}/{ties}")
            print(f"  → {verdict}")

    RUNTIME.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    (RUNTIME / f"{stamp}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"报告 → {RUNTIME.relative_to(REPO_ROOT)}/{stamp}.json")
    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
