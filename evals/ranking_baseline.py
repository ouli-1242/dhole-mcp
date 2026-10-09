#!/usr/bin/env python3
"""排序/去重基线 —— 两个集，各出各的数，永不合并。

**集 A（真实 SERP 冻结语料，本脚本量）**：测整条链 —— 引擎解析 → 归一化 → 去重 →
确定性排序。重放按 `web_search` 无神经模型时的真实调用顺序（`_rank(mode="merge")`
→ `_apply_quality_boost` → `_diversify` → `_build_results` → `_quality_filter` →
`_filter_irrelevant_results`），解析用各家引擎自己的 parser 对冻结页跑。有 qrels
的题目上报 P@5 / P@10 / MRR / nDCG@10；没有 qrels 的题目只出去重审计。**神经重排
不在此处**——那是 B8 的事，量它的尺子是集 B。

**集 B（公开数据 qrels，MIRB / MTEB-zh）**：只测纯模型，接口只有
`score(query, docs) -> [float]`，与引擎无关、跨版本可比。数据集几百 MB，**不进
git**：手动放到 `evals/.runtime/eval_sets/` 后本脚本才跑，否则如实报 "not run"。

**去重准确率**（集 A 的免费副产品，不另造数据集）：同一题多引擎返回的 URL 本来
就在语料里，直接算 `_normalize_url` 的两两识别。机械可判的两类——
可疑合并（host 不同的两条并成一个 key：一定是错的）与可疑拆分（host+path 相同、
key 不同：大概率是错的）——直接出数；其余等价性判断（参数去留、路径等价）列出
合并对供人抽查，不冒充自动结论。

用法：
    python evals/ranking_baseline.py            # 集 A（+去重审计）+ 集 B 状态
    python evals/ranking_baseline.py --json     # 另存 .runtime 下 JSON
    python evals/ranking_baseline.py --tiers    # 另加 high/med/low 直方图与空结果比率

`--tiers` 存在的原因：P@k 只奖励**序**，而 `fetch_relevance` 是每行独立铸的 badge。
把全部 high 改成 med 而不动顺序，P@5/P@10/MRR/nDCG 四个数一个都不变 —— 于是"相关性
档位塌方"这种故障在这套指标下是隐形的。改档位规则之前必须先看直方图，改完也要再看
同一张直方图，这才是可比较的前后两态。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import pathlib
import sys
import tempfile
from collections import defaultdict
from urllib.parse import urlparse

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

# 一次性 home：基线只读公开代码与语料，不碰也不依赖真实 ~/.dhole。
os.environ["DHOLE_HOME"] = tempfile.mkdtemp(prefix="dhole_rankbaseline_")
os.environ.setdefault("DHOLE_IGNORE_ROBOTS", "1")  # 离线重放，无请求；防御性

CORPUS = REPO_ROOT / "evals" / "corpus" / "serp"
QRELS = REPO_ROOT / "evals" / "corpus" / "qrels"
EVAL_SETS = REPO_ROOT / "evals" / ".runtime" / "eval_sets"
RUNTIME = REPO_ROOT / "evals" / ".runtime" / "ranking_baseline"

MAX_RESULTS = 20           # 与 B5 之后的对外上限一致
CANDIDATE_CAP = max(2 * MAX_RESULTS, 12)


# ── 集 A：重放整条链 ──────────────────────────────────────────────────────────


def _load_corpus() -> dict[str, list[tuple[str, dict, str]]]:
    """qid -> [(engine, meta, html)]，引擎名排序保证重放确定性。"""
    per_q: dict[str, list[tuple[str, dict, str]]] = defaultdict(list)
    for meta_path in sorted(CORPUS.glob("*.meta.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        # with_suffix 只换最后一段后缀：x.meta.json -> x.meta.html 是错的。
        html_path = meta_path.parent / (meta_path.name[:-len(".meta.json")] + ".html")
        if html_path.exists():
            per_q[meta["query_id"]].append(
                (meta["engine"], meta, html_path.read_text(encoding="utf-8")))
    for qid in per_q:
        per_q[qid].sort(key=lambda t: t[0])
    return per_q


def _parse_page(engine: str, html_text: str) -> list:
    """冻结页 → 该引擎 parser 的产出（与线上一致：extract + post_extract）。"""
    from dhole_mcp.search_metasearch import _TEXT_ENGINES
    eng = _TEXT_ENGINES[engine](proxy=None, timeout=15, verify=True)
    rows = eng.extract_results(html_text) or []
    return eng.post_extract_results(rows) or []


def _merge_with_dedup(pages: list[tuple[str, list]]):
    """metasearch 的合并语义：_normalize_url 去重、首见序、共识=去重家族数。"""
    from dhole_mcp.search_engines import RawResult, _INDEX_FAMILY
    from dhole_mcp.search_metasearch import _normalize_url

    seen: dict[str, RawResult] = {}
    order: list[RawResult] = []
    merges: list[tuple[str, str, str]] = []   # (key, url_a, url_b)
    raw_pairs: list[tuple[str, str]] = []     # (raw_url, key) 每条原始产出
    for engine, rows in pages:
        for r in rows:
            href = getattr(r, "href", "") or ""
            if not href:
                continue
            key = _normalize_url(href)
            raw_pairs.append((href, key))
            if key in seen:
                seen[key].sources = tuple(seen[key].sources) + (engine,)
                merges.append((key, seen[key].url, href))
                continue
            rr = RawResult(title=getattr(r, "title", "") or "",
                           url=href, snippet=getattr(r, "body", "") or "",
                           source=engine, position=len(order) + 1,
                           consensus=1, sources=(engine,))
            seen[key] = rr
            order.append(rr)
    for rr in order:
        rr.consensus = len({_INDEX_FAMILY.get(s, s) for s in rr.sources})
    return order, merges, raw_pairs


def _replay_chain(query: str, ranked: list, tally: dict | None = None) -> list:
    """web_search 的确定性段，按真实调用顺序；返回最终序的 SearchResult 行。

    返回**行**而不是 URL：`--tiers` 要读 `fetch_relevance` 和 `relevance_score`，
    而它们只在行上。指标侧仍按 URL 判（`_metrics` 收 URL 列表），调用点各取各的。

    `tally`（可选，就地累加）是 B0 的落点：P@k 看不出"档位塌方"——把所有 high 换成
    med，只要序不变，P@5 一个数都不动。所以要单独数 high/med/low 的直方图，以及
    三段的行数（铸出多少行 / 质量过滤后剩多少 / 相关性过滤后交付出多少）。
    """
    from dhole_mcp import search as S

    cand = ranked[:CANDIDATE_CAP]
    ranked_list, scores, _mode, _note = S._rank(query, cand, "merge")
    ranked_list, scores = S._apply_quality_boost(ranked_list, scores, query)
    ranked_list, scores = S._diversify(ranked_list, scores, max_per_domain=2)
    ranked_list, scores = ranked_list[:MAX_RESULTS], scores[:MAX_RESULTS]
    results_list = S._build_results(query, ranked_list, scores, total_families=6)
    if tally is not None:
        tally["built"] += len(results_list)
        for r in results_list:
            tally["tiers"][r.fetch_relevance] = tally["tiers"].get(r.fetch_relevance, 0) + 1
            # "high" 里有多少是 `rank == 1` 无条件发出来的（分数自己够不上 0.5）。
            # 这一格是 S2 要移动的量，不是裁判 —— 它只说明 badge 现在由位置而非证据铸。
            if r.fetch_relevance == "high" and (r.relevance_score or 0.0) < 0.5:
                tally["high_from_rank_only"] += 1
    results_list = S._quality_filter(results_list)
    if tally is not None:
        tally["after_quality_filter"] += len(results_list)
    results_list = S._filter_irrelevant_results(results_list, query)
    if tally is not None:
        tally["delivered"] += len(results_list)
    return results_list


# ── qrels 与指标 ──────────────────────────────────────────────────────────────


def _load_qrels(qid: str) -> dict[str, dict]:
    """{labeler: qrels}。一份语料多份标注是常态：指标按 labeler 分开出。"""
    out: dict[str, dict] = {}
    for p in sorted(QRELS.glob(f"{qid}.*.json")):
        labeler = p.name[len(qid) + 1:-len(".json")]
        out[labeler] = json.loads(p.read_text(encoding="utf-8"))
    return out


def _metrics(ranked_urls: list[str], judgments: dict[str, int]) -> dict:
    from dhole_mcp.search_metasearch import _normalize_url
    rel = [_normalize_url(u) in judgments and judgments[_normalize_url(u)] > 0
           for u in ranked_urls]
    def pk(k):
        top = rel[:k]
        return sum(top) / k if k else 0.0
    mrr = 0.0
    for i, r in enumerate(rel, 1):
        if r:
            mrr = 1.0 / i
            break
    dcg = sum((1.0 / math.log2(i + 2)) for i, r in enumerate(rel) if r)
    ideal = sum(1.0 / math.log2(i + 2) for i in range(min(sum(rel), len(rel))))
    ndcg = dcg / ideal if ideal else 0.0
    return {"p@5": round(pk(5), 3), "p@10": round(pk(10), 3),
            "mrr": round(mrr, 3), "ndcg@10": round(ndcg, 3),
            "judged_hits": sum(rel), "ranked": len(ranked_urls)}


# ── 去重两两识别（机械可判的两类 + 人工抽查清单）──────────────────────────────


def _dedup_audit(merges_by_q: dict[str, list],
                 raw_pairs_by_q: dict[str, list[tuple[str, str]]]) -> dict:
    """两两识别的机械可判子集 + 人工抽查清单。

    可疑合并 = host 不同却被并成一个 key（一定是错合并）；
    可疑拆分 = host+path 相同却分成不同 key（大概率是错拆分，参数边界案件）。
    其余等价性判断不冒充自动结论 —— 合并对全量落 JSON 供人抽查。
    """
    suspicious_merges: list = []
    for qid, merges in merges_by_q.items():
        for key, ua, ub in merges:
            ha, hb = urlparse(ua).hostname or "", urlparse(ub).hostname or ""
            if ha and hb and ha != hb:
                suspicious_merges.append((qid, ua, ub))
    suspicious_splits: list = []
    for qid, pairs in raw_pairs_by_q.items():
        by_hostpath: dict = defaultdict(set)
        for url, key in pairs:
            p = urlparse(url)
            by_hostpath[(p.hostname or "", p.path.rstrip("/") or "/")].add(key)
        for (host, path), keys in by_hostpath.items():
            if len(keys) > 1:
                suspicious_splits.append((qid, host, path, sorted(keys)))
    merge_total = sum(len(v) for v in merges_by_q.values())
    return {
        "merge_pairs": merge_total,
        "suspicious_merge_count": len(suspicious_merges),
        "suspicious_merges": suspicious_merges[:20],
        "merge_precision_floor": (round(1 - len(suspicious_merges) / merge_total, 4)
                                  if merge_total else None),
        "suspicious_split_count": len(suspicious_splits),
        "suspicious_splits": suspicious_splits[:20],
        "note": ("merge_precision_floor 是下界：1 − 必错合并占比。其余合并对的等价性"
                 "要人判，全量在 JSON 里；拆分侧按 host+path 聚类列出待裁。"),
    }


# ── 集 B：纯模型（数据集不进 git，缺料如实报 not run）────────────────────────


def _set_b() -> dict:
    if not EVAL_SETS.exists() or not any(EVAL_SETS.iterdir()):
        return {
            "status": "not_run",
            "reason": f"no datasets under {EVAL_SETS}",
            "how_to": ("跑 `python evals/fetch_eval_sets.py`（拉 C-MTEB/T2Reranking），"
                       "或手动放 MIRB / MTEB-zh 到 evals/.runtime/eval_sets/<name>/："
                       "每份 jsonl 一行一题 {query, positives: [doc], negatives: [doc]}。"
                       "接口只有 score(query, docs) -> [float]，与引擎无关。"),
        }
    # 集 B 必须读到**真实** ~/.dhole 里的模型，而本脚本顶部把 DHOLE_HOME 指到了
    # 一次性目录（集 A 不碰用户状态）。两件事都要成立，所以这里临时还回去。
    #
    # 另一处：模型是惰性的，`get_reranker()` 只是 peek（没预热就回 None），而评测
    # 脚本不预热。**这两处一起**决定了修之前这条路径只会永远报
    # "no local model (lean install)" —— 一个看着很确定、其实从没跑过的结论。
    saved_home = os.environ.pop("DHOLE_HOME", None)
    try:
        from dhole_mcp import reranker as _rer
        if not _rer.model_present():
            return {"status": "not_run",
                    "reason": f"no model at {_rer.active_model_dir()} —— 先在真实 "
                              f"~/.dhole 里让它下过一次（跑任意一次神经搜索）"}
        rer = _rer._load_reranker()
    except Exception as exc:
        return {"status": "not_run", "reason": f"model unavailable: {exc}"}
    finally:
        if saved_home is not None:
            os.environ["DHOLE_HOME"] = saved_home
    if rer is None:
        return {"status": "not_run", "reason": "no local model (lean install)"}
    results = {}
    for ds_dir in sorted(p for p in EVAL_SETS.iterdir() if p.is_dir()):
        path = ds_dir / "qrels.jsonl"
        if not path.exists():
            continue
        ndcgs = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            docs = list(row["positives"]) + list(row["negatives"])
            gold = [1.0] * len(row["positives"]) + [0.0] * len(row["negatives"])
            scores = rer.score(row["query"], docs)
            order = sorted(range(len(docs)), key=lambda i: -scores[i])
            gains = [gold[i] for i in order]
            dcg = sum(g / math.log2(i + 2) for i, g in enumerate(gains))
            ideal = sum(g / math.log2(i + 2)
                        for i, g in enumerate(sorted(gold, reverse=True)))
            if ideal:
                ndcgs.append(dcg / ideal)
        results[ds_dir.name] = {"n": len(ndcgs),
                                "ndcg@all": round(sum(ndcgs) / len(ndcgs), 3)
                                if ndcgs else None}
    return {"status": "ok", "datasets": results}


# ── 主流程 ────────────────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--tiers", action="store_true",
                    help="high/med/low 直方图 + 空结果比率（P@k 看不出档位塌方）")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    corpus = _load_corpus()
    if not corpus:
        print("集 A：语料为空 —— 先跑 evals/capture_serp.py")
        print("集 B：", json.dumps(_set_b(), ensure_ascii=False))
        return 1

    merges_by_q: dict[str, list] = {}
    raw_pairs_by_q: dict[str, list] = {}
    per_query: list[dict] = []
    tally: dict = {"built": 0, "after_quality_filter": 0, "delivered": 0,
                   "high_from_rank_only": 0, "tiers": {}, "empty_queries": 0,
                   "queries": 0}
    for qid in sorted(corpus):
        pages = [(eng, _parse_page(eng, html)) for eng, _m, html in corpus[qid]]
        query = corpus[qid][0][1]["query"]
        ranked, merges, raw_pairs = _merge_with_dedup(pages)
        merges_by_q[qid] = merges
        raw_pairs_by_q[qid] = raw_pairs
        results_now = _replay_chain(query, ranked, tally=tally)
        final_urls = [r.url for r in results_now]
        row = {"qid": qid, "engines": [e for e, _ in pages],
               "raw_items": sum(len(rows) for _, rows in pages),
               "unique_after_dedup": len(ranked), "final_order": len(final_urls),
               "tiers": [r.fetch_relevance for r in results_now]}
        qr = _load_qrels(qid)
        if qr:
            row["metrics"] = {}
            for labeler, qrels in qr.items():
                m = _metrics(final_urls, qrels.get("judgments") or {})
                m["labeler"] = labeler
                row["metrics"][labeler] = m
        per_query.append(row)
    tally["queries"] = len(per_query)
    tally["empty_queries"] = sum(1 for r in per_query if r["final_order"] == 0)

    with_metrics = [r for r in per_query if "metrics" in r]

    print("== 集 A：整条链（冻结语料重放，无神经重排）==")
    print(f"queries={len(per_query)}  with_qrels={len(with_metrics)}  "
          f"raw_items={sum(r['raw_items'] for r in per_query)}  "
          f"unique_after_dedup={sum(r['unique_after_dedup'] for r in per_query)}")
    if with_metrics:
        by_labeler: dict[str, list] = defaultdict(list)
        for r in with_metrics:
            for labeler, m in r["metrics"].items():
                by_labeler[labeler].append(m)
        for labeler, ms in sorted(by_labeler.items()):
            print(f"  labeler={labeler} (n={len(ms)}):")
            for k in ("p@5", "p@10", "mrr", "ndcg@10"):
                print(f"    {k}: {sum(m[k] for m in ms)/len(ms):.3f}")
    else:
        print("  （还没有 qrels —— 先做盲评标注，见 evals/README.md 的标注协议）")
    if args.tiers:
        tiers = tally["tiers"]
        total_rows = sum(tiers.values()) or 1
        print("== 档位直方图（这一层 P@k 看不见：序不变而 high 全变 med，P@5 一个数都不动）==")
        for name in ("high", "med", "low"):
            n = tiers.get(name, 0)
            print(f"  {name}: {n} ({n / total_rows:.1%})")
        for other, n in sorted(tiers.items()):
            if other not in ("high", "med", "low"):
                print(f"  {other}: {n} ({n / total_rows:.1%})  <- 词表外的取值")
        print(f"  high 里仅因 rank==1（自身分数够不上 0.5）: "
              f"{tally['high_from_rank_only']} / {tiers.get('high', 0)}")
        print(f"  行数流水: 铸出 {tally['built']} → _quality_filter 后 "
              f"{tally['after_quality_filter']} → 交付 {tally['delivered']}")
        q = max(1, tally["queries"])
        print(f"  空结果题面: {tally['empty_queries']}/{tally['queries']} "
              f"({tally['empty_queries'] / q:.1%})")

    for r in per_query[:8]:
        extra = (f"  metrics={r['metrics']}" if "metrics" in r else "")
        print(f"  {r['qid']}: raw={r['raw_items']} unique={r['unique_after_dedup']} "
              f"final={r['final_order']}{extra}")

    audit = _dedup_audit(merges_by_q, raw_pairs_by_q)
    print("== 去重两两识别（_normalize_url，集 A 免费副产品）==")
    print(f"  可疑合并（host 不同却并成一个 key）: {audit['suspicious_merge_count']}")
    for qid, ua, ub in audit["suspicious_merges"][:5]:
        print(f"    {qid}: {ua}  <->  {ub}")

    print("== 集 B：纯模型（与集 A 永不合并）==")
    b = _set_b()
    print("  ", json.dumps(b, ensure_ascii=False))

    if args.json:
        RUNTIME.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        out = RUNTIME / f"ranking_baseline-{stamp}.json"
        out.write_text(json.dumps(
            {"at": dt.datetime.now().isoformat(timespec="seconds"),
             "set_a": per_query, "tier_tally": tally,
             "dedup_audit": audit, "set_b": b},
            indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"saved: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
