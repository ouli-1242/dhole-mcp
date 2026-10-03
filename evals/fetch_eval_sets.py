#!/usr/bin/env python3
"""拉取集 B 的公开 qrels 语料 —— 换型评测（B8）的地基。

集 B 只测**纯模型**：接口只有 `score(query, docs) -> [float]`，与引擎无关、跨版本
可比。数据集不进 git（几百 MB 级），落 `evals/.runtime/eval_sets/<name>/`；
`ranking_baseline.py` 读到才跑，读不到如实报 `not_run`。

来源 `C-MTEB/T2Reranking`（中文，MTEB-zh 的 reranking 子集）：每行
`{query, positive: [5 条], negative: [8 条]}`，与集 B 要的
`{query, positives, negatives}` 一一对应，不需要二次加工。走 HuggingFace 的
datasets-server rows API 分页取，不下载 120MB parquet，也不需要 pyarrow。

**为什么用 T2Reranking 而不是 T2Retrieval**：后者要下 150MB 的 corpus 再自己挑
正负例，而这里的正负例是数据集自带的、已经按"同一 query 下的候选"成组 —— 正是
rerank 要判的那件事。换型要回答的问题是"换个打分器，同一批候选的排序变好还是变坏"，
不是"能不能从百万语料里召回"。

**取前 N 行，不随机**：确定性。重跑同一条命令得到同一份语料，两次评测才可比；
随机抽样会把"换型的效果"和"换了种子"混在一起。

用法：
    python evals/fetch_eval_sets.py                       # 300 行 → t2reranking/
    python evals/fetch_eval_sets.py --rows 100 --out my_set
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import urllib.parse
import urllib.request

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
EVAL_SETS = REPO_ROOT / "evals" / ".runtime" / "eval_sets"
API = "https://datasets-server.huggingface.co/rows"
PAGE = 100          # datasets-server 单页上限


def _page(dataset: str, config: str, split: str, offset: int) -> dict:
    q = urllib.parse.urlencode(
        {"dataset": dataset, "config": config, "split": split,
         "offset": offset, "length": PAGE})
    req = urllib.request.Request(f"{API}?{q}",
                                 headers={"User-Agent": "dhole-mcp-eval/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def fetch(dataset: str, config: str, split: str, rows: int) -> list[dict]:
    out: list[dict] = []
    offset = 0
    total = None
    while len(out) < rows:
        page = _page(dataset, config, split, offset)
        if total is None:
            total = page.get("num_rows_total")
            print(f"  dataset has {total} rows; taking {rows}")
        got = page.get("rows") or []
        if not got:
            break
        for item in got:
            row = item["row"]
            out.append({
                "query": row["query"],
                "positives": list(row["positive"]),
                "negatives": list(row["negative"]),
            })
            if len(out) >= rows:
                break
        offset += len(got)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default="C-MTEB/T2Reranking")
    ap.add_argument("--config", default="default")
    ap.add_argument("--split", default="dev")
    ap.add_argument("--rows", type=int, default=300)
    ap.add_argument("--out", default="t2reranking",
                    help="evals/.runtime/eval_sets/<out>/qrels.jsonl")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    print(f"fetching {args.dataset} [{args.config}/{args.split}] …")
    rows = fetch(args.dataset, args.config, args.split, args.rows)
    if not rows:
        print("no rows fetched", file=sys.stderr)
        return 1

    dest = EVAL_SETS / args.out
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / "qrels.jsonl"
    blob = "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    # newline="" 是承重的：默认的 write_text 在 Windows 上会把 \n 翻成 \r\n，
    # 于是"记录下来的 sha256"与"盘上那份文件的 sha256"对不上 —— 一份看着可复现、
    # 其实复现不了的 provenance，比没有 provenance 更糟。
    path.write_text(blob, encoding="utf-8", newline="")

    n_pos = sum(len(r["positives"]) for r in rows)
    n_neg = sum(len(r["negatives"]) for r in rows)
    meta = {
        "source": args.dataset,
        "config": args.config,
        "split": args.split,
        "rows": len(rows),
        "positives": n_pos,
        "negatives": n_neg,
        "selection": "first N rows, deterministic",
        # 算在**落盘字节**上，不是内存里的那个字符串。
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "note": ("集 B 只测纯模型；与集 A（真实 SERP 冻结语料）各出各的数，永不合并。"),
    }
    (dest / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"wrote {path.relative_to(REPO_ROOT)}  "
          f"({len(rows)} queries, {n_pos} pos / {n_neg} neg)")
    print(f"sha256 {meta['sha256'][:16]}…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
