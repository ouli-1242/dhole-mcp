"""M-1 量化基线：BM25 vs 神经 focus 的保留率与耗时。

计划第十章的模型轴基线。**零网络**——语料取仓库内的 README.md / CHANGELOG.md
与 fixture 长文，不依赖任何外站，改版也不会让结果失效。

测什么
------
focus 的文档化弱点是"问句保留页占比"（BM25 下 55~76%，因为 "什么/是/的"
这类泛化词在几乎所有块里都有正的 IDF）。所以核心指标是：

1. ``retention`` —— 保留块 / 总块数。越低越省 token。
2. ``answer_kept`` —— 含接地关键词的块是否被保留。**省 token 不能以丢掉答案
   为代价**，这是保留率的分母约束；只报保留率不报召回就是自欺。
3. ``ms`` —— 单页 focus 耗时。神经路径是每块一次 cross-encoder 推理，这是
   M-1 的主要代价项。

用法
----
    python evals/focus_baseline.py            # 人读表格
    python evals/focus_baseline.py --json     # 另存 .runtime/focus_baseline.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
RUNTIME = HERE / ".runtime"

from dhole_mcp.focus import _split_blocks, focus_content  # noqa: E402
from dhole_mcp.reranker import ensure_reranker  # noqa: E402

# (自然语言问句, 接地关键词) —— 关键词必须真实出现在语料里，否则该用例作废。
CASES = [
    ("会话 cookie 的存活时间怎么配置", "DHOLE_COOKIE_TTL_S"),
    ("CDP attach 默认是开还是关", "DHOLE_CDP_ATTACH"),
    ("神经 focus 用哪个环境变量打开", "DHOLE_NEURAL_FOCUS"),
    ("多模态开关是干什么的", "DHOLE_MULTIMODAL"),
    ("submit 审批闸暂停后返回什么", "pending_approval"),
]


def _fixture_article_text() -> str:
    """复用 fixture 靶站的 /article 正文，避免两处各写一份。

    fixture_site 只在 __main__ 下起服务，导入是安全的；这里把 HTML 段落抽成
    markdown 段落，让它成为 focus 的第三个语料。
    """
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    import re

    import fixture_site

    html = fixture_site._article().decode("utf-8")
    paras = re.findall(r"<(?:h1|p)>(.*?)</(?:h1|p)>", html, flags=re.S)
    return "\n\n".join(p.strip() for p in paras)


def load_docs() -> dict[str, str]:
    docs: dict[str, str] = {}
    for name in ("README.md", "CHANGELOG.md"):
        p = ROOT / name
        if p.is_file():
            docs[name] = p.read_text(encoding="utf-8")
    try:
        docs["fixture_article"] = _fixture_article_text()
    except Exception as e:  # 语料缺一个不影响其余用例
        print(f"!! fixture 长文不可用：{type(e).__name__}: {e}", file=sys.stderr)
    return docs


def run_one(doc: str, question: str, keyword: str, reranker) -> dict | None:
    blocks = _split_blocks(doc)
    n = len(blocks)
    if n <= 1:
        return None
    truth = [i for i, b in enumerate(blocks) if keyword in b]
    if not truth:
        return None  # 语料里没有接地答案，用例作废

    row = {"question": question, "keyword": keyword, "blocks": n,
           "truth_blocks": len(truth)}

    for label, rr in (("bm25", None), ("neural", reranker)):
        if label == "neural" and rr is None:
            row["neural"] = None
            continue
        t0 = time.perf_counter()
        out = focus_content(doc, question, reranker=rr)
        ms = (time.perf_counter() - t0) * 1000.0
        kept = [b for b in blocks if b.strip() and b in out]
        row[label] = {
            "retention": round(len(kept) / n, 3),
            "kept": len(kept),
            "answer_kept": any(blocks[i] in out for i in truth),
            "ms": round(ms, 1),
            "chars": len(out),
        }
    return row


async def main() -> int:
    ap = argparse.ArgumentParser(description="M-1 focus 基线")
    ap.add_argument("--json", action="store_true", help="另存 JSON 结果")
    args = ap.parse_args()

    docs = load_docs()
    if not docs:
        print("没有可用语料（README.md / CHANGELOG.md 都找不到）", file=sys.stderr)
        return 1

    reranker = await ensure_reranker(download=False)
    if reranker is None:
        print("!! 本地 reranker 不可用：神经路径无法测量。"
              "先确认 ~/.dhole/models 下有模型。", file=sys.stderr)
        return 2
    # 预热：第一次调用含 onnx 会话初始化，混进耗时会把神经路径的代价算错。
    focus_content(docs[list(docs)[0]], "预热", reranker=reranker)
    print(f"神经打分器已就绪：{type(reranker).__name__}\n")

    rows = []
    for name, doc in docs.items():
        print(f"── {name}（{len(doc)} 字符）")
        for question, keyword in CASES:
            r = run_one(doc, question, keyword, reranker)
            if r is None:
                print(f"   · {question} → 跳过（语料无接地答案 {keyword}）")
                continue
            r["doc"] = name
            rows.append(r)
            b, nn = r["bm25"], r["neural"]
            if nn is None:
                print(f"   · {question}\n       BM25 {b['retention']:.0%} "
                      f"({b['kept']}/{r['blocks']} 块) 答案{'保留' if b['answer_kept'] else '**丢失**'}")
            else:
                print(f"   · {question}\n"
                      f"       BM25   {b['retention']:.0%} ({b['kept']}/{r['blocks']} 块) "
                      f"答案{'保留' if b['answer_kept'] else '**丢失**'} {b['ms']:.0f}ms\n"
                      f"       神经   {nn['retention']:.0%} ({nn['kept']}/{r['blocks']} 块) "
                      f"答案{'保留' if nn['answer_kept'] else '**丢失**'} {nn['ms']:.0f}ms")
        print()

    if not rows:
        print("没有任何有效用例。", file=sys.stderr)
        return 1

    def agg(label: str, key: str) -> float:
        vals = [r[label][key] for r in rows if r.get(label)]
        return statistics.mean(vals) if vals else float("nan")

    print("═══ 汇总（%d 个有效用例）" % len(rows))
    print(f"{'':10} {'保留率':>8} {'答案召回':>10} {'均耗时':>10}")
    for label, name in (("bm25", "BM25"), ("neural", "神经")):
        got = [r for r in rows if r.get(label)]
        if not got:
            continue
        recall = sum(1 for r in got if r[label]["answer_kept"]) / len(got)
        print(f"{name:10} {agg(label, 'retention'):>7.0%} "
              f"{recall:>9.0%} {agg(label, 'ms'):>9.0f}ms")

    if args.json:
        RUNTIME.mkdir(parents=True, exist_ok=True)
        out = RUNTIME / "focus_baseline.json"
        out.write_text(json.dumps(rows, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        print(f"\n已写出 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
