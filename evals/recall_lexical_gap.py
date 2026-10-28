#!/usr/bin/env python3
"""web_recall 的字面候选洞有多大：拿真实查询 + 人工正负例量一遍。

计划 §3.2 的问题：`web_recall` 的候选集来自 `content LIKE '%词%'`（分词是
`focus._tokens` 的 CJK 二元组 + 拉丁词）。查询与目标页**零字面重合**时，候选集
里根本没有那一页，后面的 BM25 与交叉编码器再强也救不回来——这是嵌入方案唯一
能买到的东西，也是唯一值得为它付一个新模型的地方。

真缓存上的「零候选率」要等 P1 的真实回忆查询（`DHOLE_QUERY_LOG`）。在那之前，
这份脚本用 `C-MTEB/T2Reranking` 的真实中文查询与真人标注做代理测量：把全部
正负例 passage 当成一台机器读过的页，逐条查询走**和 `recall_pages` 完全相同的
第一段**（`focus._tokens` 最多 8 个词 → 子串预筛 → 页级 BM25），回答两个数：

  - 零候选率：整台缓存里没有任何一行命中查询词；
  - 正例可达率：真人标成相关的 passage 至少有一条进了候选集。

第二个数才是 §3.2 的洞：正例不可达 = 交叉编码器与重排都没有机会看见它。BM25
的 R@5 / R@10 / MRR 同时给出「候选进来了但排不上去」的那部分损失——嵌入只替换
候选生成，不替换排序，所以这两部分的账不能混。

代理语料的偏差，读报告前先认下：

  - T2 查询是**搜索词**，不是「读过之后凭记忆回想」的改写；回忆式查询的字面重合
    通常更低，所以这里的洞是**真实回忆洞的下界附近**，不是它的上界。
  - passage 短于整页：整页有更多字，查询词撞上的机会只会更大。所以对「同一批
    查询配整页」而言，这里的正例不可达率偏高而不是偏低。
  - 不模拟 `_MAX_CANDIDATES=400` 的近期截断（那取决于缓存里各页的 fetched_at，
    代理语料没有时间轴）；真实缓存只会更难，不会更容易。

用法：

    python evals/recall_lexical_gap.py                 # 默认读 T2 前 300 行
    python evals/recall_lexical_gap.py --limit 100 --json out.json --md report.md
"""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import sys
from typing import Iterable, Sequence

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from dhole_mcp.focus import _tokens  # noqa: E402
from dhole_mcp.recall import _bm25  # noqa: E402

DEFAULT_QRELS = (REPO_ROOT / "evals" / ".runtime" / "eval_sets"
                 / "t2reranking" / "qrels.jsonl")

#: 与 `recall_pages` 同一条预算：LIKE 预筛最多 8 个词，第 9 个基本是同义重复。
MAX_QUERY_TOKENS = 8

#: 与 recall 的正文截断同值；passage 语料通常远短于它，留着是为了口径一致。
TOKEN_CHARS = 8_000


# ─── 语料与查询 ────────────────────────────────────────────────────────


def load_qrels(path: pathlib.Path) -> list[dict]:
    """读 T2 形状的 JSONL：{query, positives, negatives}；空查询跳过。"""
    rows: list[dict] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        obj = json.loads(line)
        query = str(obj.get("query") or "").strip()
        if not query:
            continue
        rows.append({
            "query": query,
            "positives": [str(p) for p in (obj.get("positives") or []) if str(p).strip()],
            "negatives": [str(p) for p in (obj.get("negatives") or []) if str(p).strip()],
        })
    return rows


def build_corpus(rows: Iterable[dict]) -> tuple[list[str], list[list[int]], list[list[int]]]:
    """正负例去重成一台「读过的缓存」，返回 (docs, pos_idx, neg_idx)。"""
    docs: list[str] = []
    index: dict[str, int] = {}

    def place(text: str) -> int:
        if text not in index:
            index[text] = len(docs)
            docs.append(text)
        return index[text]

    pos_idx: list[list[int]] = []
    neg_idx: list[list[int]] = []
    for row in rows:
        pos_idx.append([place(t) for t in row["positives"]])
        neg_idx.append([place(t) for t in row["negatives"]])
    return docs, pos_idx, neg_idx


def query_tokens(query: str) -> list[str]:
    """`recall_pages` 的查询侧：去重保序，最多 8 个词。"""
    return list(dict.fromkeys(_tokens(query)))[:MAX_QUERY_TOKENS]


# ─── 候选生成与排序（镜像 recall 的字面段） ───────────────────────────


def candidate_indices(tokens: Sequence[str], docs_lower: Sequence[str]) -> set[int]:
    """每个词一趟子串扫描，与 SQL 的 `content LIKE '%词%'` 同语义。

    缓存里的正文是 `json.dumps(ensure_ascii=True)` 的形状，中文以 ``\\uXXXX``
    落库；`recall._json_escape` 把查询词翻成同一形状后匹配。逐字符转义对子串
    关系是保序的，所以这里在原文上匹配与在转义串上匹配等价——唯一的出入是
    十六进制串恰好拼出 ASCII 词的假命中，那种命中不是我们要量的东西。
    """
    hit: set[int] = set()
    for token in tokens:
        needle = token.lower()
        for i, text in enumerate(docs_lower):
            if needle in text:
                hit.add(i)
    return hit


def lexical_ranking(tokens: Sequence[str], docs: Sequence[str],
                     candidates: Iterable[int]) -> list[int]:
    """BM25 排序，镜像 `recall_pages` 的页级打分（不接神经重排）。"""
    cand = sorted(candidates)
    if not cand:
        return []
    doc_tokens = [_tokens(docs[i][:TOKEN_CHARS]) for i in cand]
    scores = _bm25(set(tokens), doc_tokens)
    return [cand[i] for i in sorted(range(len(cand)), key=lambda i: -scores[i])]


# ─── 指标 ──────────────────────────────────────────────────────────────


def measure(rows: Sequence[dict], docs: Sequence[str],
            pos_idx: Sequence[Sequence[int]], *, k: int = 10,
            max_failures: int = 10) -> dict:
    """逐条查询跑候选与 BM25，汇总计划 §3.2 关心的那几个数。"""
    docs_lower = [d.lower() for d in docs]
    zero = 0
    reachable = 0
    with_positives = 0
    cand_sizes: list[int] = []
    r5 = r10 = 0
    rr = 0.0
    failures: list[dict] = []
    for row, positives in zip(rows, pos_idx):
        tokens = query_tokens(row["query"])
        cand = candidate_indices(tokens, docs_lower)
        cand_sizes.append(len(cand))
        if not cand:
            zero += 1
        if not positives:
            continue
        with_positives += 1
        wanted = set(positives)
        if cand & wanted:
            reachable += 1
        ranking = lexical_ranking(tokens, docs, cand)
        ranks = [i + 1 for i, doc in enumerate(ranking) if doc in wanted]
        best = min(ranks) if ranks else 0
        if 0 < best <= 5:
            r5 += 1
        if 0 < best <= k:
            r10 += 1
        if best:
            rr += 1.0 / best
        elif len(failures) < max_failures:
            failures.append({
                "query": row["query"],
                "tokens": tokens,
                "positives": len(positives),
                "preview": row["positives"][0][:240].replace("\n", " "),
                "candidates": len(cand),
            })

    def rate(n: int, d: int) -> float:
        return round(n / d, 4) if d else 0.0

    return {
        "queries": len(rows),
        "passages": len(docs),
        "positives": sum(len(x) for x in pos_idx),
        "queries_without_positives": len(rows) - with_positives,
        "zero_candidate_queries": zero,
        "zero_candidate_rate": rate(zero, len(rows)),
        "positive_unreachable_queries": with_positives - reachable,
        "positive_unreachable_rate": rate(with_positives - reachable, with_positives),
        "positive_reachable_rate": rate(reachable, with_positives),
        "lexical_R@5": rate(r5, with_positives),
        "lexical_R@10": rate(r10, with_positives),
        "lexical_MRR": round(rr / with_positives, 4) if with_positives else 0.0,
        "candidates_median": int(statistics.median(cand_sizes)) if cand_sizes else 0,
        "candidates_mean": round(statistics.mean(cand_sizes), 1) if cand_sizes else 0.0,
        "failures": failures,
    }


def format_report(stats: dict, *, source: str) -> str:
    """一张能贴进 README 的表 + 读法。"""
    lines = [
        "# web_recall 字面候选的洞（代理测量）",
        "",
        f"语料：`{source}`；去重后 {stats['passages']} 条 passage，"
        f"{stats['queries']} 条真实查询（其中 {stats['queries_without_positives']} "
        "条没有正例，不参与可达率与 R@k 的分母）。",
        "",
        "| 指标 | 值 |",
        "|---|---|",
        f"| 零候选查询 | {stats['zero_candidate_queries']} / {stats['queries']} "
        f"({stats['zero_candidate_rate']:.1%}) |",
        f"| 正例不可达查询 | {stats['positive_unreachable_queries']} / "
        f"{stats['queries'] - stats['queries_without_positives']} "
        f"({stats['positive_unreachable_rate']:.1%}) |",
        f"| 正例可达率 | {stats['positive_reachable_rate']:.1%} |",
        f"| 字面 R@5 | {stats['lexical_R@5']:.4f} |",
        f"| 字面 R@10 | {stats['lexical_R@10']:.4f} |",
        f"| 字面 MRR | {stats['lexical_MRR']:.4f} |",
        f"| 候选数（中位 / 均值） | {stats['candidates_median']} / "
        f"{stats['candidates_mean']} |",
        "",
        "读法：正例不可达是候选生成的洞（嵌入唯一能补的那一块）；R@10 与可达率"
        "之间的差是**排序**的损失（嵌入不补这一块）。候选生成要先看见正例，"
        "BM25 / 交叉编码器才有机会把它排上来。",
    ]
    if stats["failures"]:
        lines += ["", "不可达明细（最多 10 条）：", ""]
        for f in stats["failures"]:
            preview = f["preview"].replace("|", "\\|")
            lines.append(f"- `{f['query']}` — 词 {f['tokens']}；"
                         f"正例 {f['positives']} 条，候选 {f['candidates']} 条；"
                         f"片段：{preview}")
    return "\n".join(lines) + "\n"


# ─── CLI ───────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--qrels", default=str(DEFAULT_QRELS),
                   help="T2 形状的 JSONL：{query, positives, negatives}")
    p.add_argument("--limit", type=int, default=0, help="只取前 N 条查询（0 = 全部）")
    p.add_argument("--top-k", type=int, default=10, help="R@k 的 k（默认 10）")
    p.add_argument("--json", dest="json_out", default="",
                   help="把指标写成 JSON")
    p.add_argument("--md", dest="md_out", default="",
                   help="把 Markdown 报告写到文件")
    return p


def main(argv: list[str] | None = None) -> int:
    # Windows 控制台默认 GBK：报告里有中文与全角符号，不换编码会在 print 上炸掉。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    args = build_parser().parse_args(argv)
    qrels = pathlib.Path(args.qrels)
    rows = load_qrels(qrels)
    if args.limit > 0:
        rows = rows[:args.limit]
    if not rows:
        print(f"no queries in {qrels}", file=sys.stderr)
        return 2
    docs, pos_idx, _ = build_corpus(rows)
    stats = measure(rows, docs, pos_idx, k=args.top_k)
    report = format_report(stats, source=str(qrels))
    print(report)
    if args.json_out:
        out = pathlib.Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(stats, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
    if args.md_out:
        out = pathlib.Path(args.md_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
