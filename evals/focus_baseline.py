"""M-1 量化基线：BM25 vs 神经 focus 的保留率与耗时。

计划第十章的模型轴基线。**零网络**——语料取仓库内的 README.md / CHANGELOG.md
与 fixture 长文，不依赖任何外站，改版也不会让结果失效。

测什么
------
focus 的文档化弱点是"问句保留页占比"（BM25 下 55~76%，因为 "什么/是/的"
这类泛化词在几乎所有块里都有正的 IDF）。所以核心指标是：

1. ``retention`` —— **保留下来的字符 / 全文字符**（2026-10-10 之前这里是"保留块 /
   总块数"）。换尺子的理由不是精度，是可见性：一页若只被切成一块，块数占比只能回答
   0% 或 100%，而那一页 focus 其实**一刀没切** —— 这台量具对它的真实表现是瞎的。
   字符加权之后同一页交出 `1.0` 加一个 `one_block` 的记号（`run_one` 从前遇到
   `len(blocks) <= 1` 直接 `return None`，那些页连"瞎"都算不上，是从分母里消失了）。
   语料里因此带一份**无空行**的同文变体（`article_no_blank_lines`：靶站 /article 那一篇
   把段落之间的空行抹掉 —— 只有 399 字，形状却是照真实站点那份 81,000 字的在线文档抄的：
   段落之间只有一个换行）。`_split_blocks` 只按空行切，所以这一份必然是一整块。
   配这一形状的是"每份语料问它自己答得出的题"（`load_docs` 交出 `正文 + 问题组`）：
   形状备好了而问的是那五颗仓库旋钮，用例全部作废，那一格还是没进分母 —— 2026-10-10
   活跑量到的就是这一格，汇总当时印着"其中整页一块的 0 个"。
2. ``answer_kept`` —— 含接地关键词的块是否被保留。**省 token 不能以丢掉答案
   为代价**，这是保留率的分母约束；只报保留率不报召回就是自欺。
3. ``ms`` —— 单页 focus 耗时。神经路径是每块一次 cross-encoder 推理，这是
   M-1 的主要代价项。

汇总那三行是**字符加权**的和（`Σchars_kept / Σchars_total`），不是各用例占比的均值：
均值会把一页一块与一页四百块同等对待，而那正是这次要消灭的瞎。

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

# 靶站 /article 那一篇讲的是抓取缓存与截断，上面五颗仓库旋钮它一个都不含 —— 拿 CASES 问它，
# 五个用例全部作废。2026-10-10 活跑量到的正是这件事：语料里明明备好了**无空行**的那一份
# （形状对），汇总却印着"其中整页一块的 0 个"。**形状在语料里、行没进分母，就是还没看见。**
# 所以一份语料配一组它自己答得出的问题，而不是全仓共一张问题表。
FIXTURE_CASES = [
    ("抓取缓存默认保存多久", "cache_ttl"),
    ("正文截断落在什么边界上", "字符边界"),
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


def load_docs() -> dict[str, tuple[str, list[tuple[str, str]]]]:
    """每份语料连**它自己答得出的问题**一起交出。

    这一步是"让整页一块可见"的另一半：形状备好了而问的题不在正文里，用例全部作废，
    报表照样一行不留（2026-10-10 活跑量到的）。配对写在构造点上而不是按名字查表 ——
    改了名字就静默退回仓库旋钮那组题，那正是要防的那种瞎。
    """
    docs: dict[str, tuple[str, list[tuple[str, str]]]] = {}
    for name in ("README.md", "CHANGELOG.md"):
        p = ROOT / name
        if p.is_file():
            docs[name] = (p.read_text(encoding="utf-8"), CASES)
    try:
        article = _fixture_article_text()
        docs["fixture_article"] = (article, FIXTURE_CASES)
        # 段落之间**只剩单换行**的变体：真实站点上一份 81,000 字的在线文档就是这个形状，
        # 而 `_split_blocks` 只按空行切 —— 那一页必然是一整块，它在这里必须是报表里的
        # 一行，而不是分母里少掉的一个用例（见文件头第 1 条）。
        docs["article_no_blank_lines"] = (article.replace("\n\n", "\n"), FIXTURE_CASES)
    except Exception as e:  # 语料缺一个不影响其余用例
        print(f"!! fixture 长文不可用：{type(e).__name__}: {e}", file=sys.stderr)
    return docs


def weighted_retention(rows: list[dict], label: str) -> float:
    """汇总的保留率：`Σchars_kept / Σchars_total`，不是各用例占比的均值。

    均值把"一页一块"与"一页四百块"同等对待，而这次换尺子要消灭的正是那个瞎：一份
    40,000 字的页一刀没切（占比 1.0）与一份 2,000 字的页切到 1/8（占比 0.125）在均值里
    差不多，在字符加权里前者占的权重是它真实的大小。
    """
    got = [r for r in rows if r.get(label)]
    total = sum(r["chars_total"] for r in got)
    if not total:
        return float("nan")
    return sum(r[label]["chars_kept"] for r in got) / total


def run_one(doc: str, question: str, keyword: str, reranker) -> dict | None:
    """一发用例。返回 None 只有一种理由：**语料里没有那个答案**（用例作废）。

    从前 `len(blocks) <= 1` 也返回 None，于是"focus 在这一页一刀没切"这件事连记录都没有
    —— 一台量具对某一整类页面瞎，比它报一个难看的数更糟。
    """
    blocks = _split_blocks(doc)
    truth = [i for i, b in enumerate(blocks) if keyword in b]
    if not truth:
        return None  # 语料里没有接地答案，用例作废

    chars_total = sum(len(b) for b in blocks)
    n = len(blocks)
    row = {"question": question, "keyword": keyword, "blocks": n,
           "chars_total": chars_total, "truth_blocks": len(truth),
           "one_block": n <= 1}

    for label, rr in (("bm25", None), ("neural", reranker)):
        if label == "neural" and rr is None:
            row["neural"] = None
            continue
        t0 = time.perf_counter()
        out = focus_content(doc, question, reranker=rr)
        ms = (time.perf_counter() - t0) * 1000.0
        kept = [b for b in blocks if b.strip() and b in out]
        chars_kept = sum(len(b) for b in kept)
        row[label] = {
            # 字符加权：省下来的就是这块，块数只是它的形状说明
            "retention": round(chars_kept / chars_total, 3) if chars_total else 1.0,
            "chars_kept": chars_kept,
            "blocks_kept": len(kept),
            "block_retention": round(len(kept) / n, 3) if n else 1.0,
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
    focus_content(docs[list(docs)[0]][0], "预热", reranker=reranker)
    print(f"神经打分器已就绪：{type(reranker).__name__}\n")

    rows = []
    for name, (doc, cases) in docs.items():
        # 块数印在段头上：一份语料是不是"整页一块"要一眼看得见，
        # 不必等到用例作废才发现题目问错了。
        print(f"── {name}（{len(doc)} 字符 / {len(_split_blocks(doc))} 块）")
        for question, keyword in cases:
            r = run_one(doc, question, keyword, reranker)
            if r is None:
                print(f"   · {question} → 跳过（语料无接地答案 {keyword}）")
                continue
            r["doc"] = name
            rows.append(r)
            b, nn = r["bm25"], r["neural"]
            tag = " ← 整页一块，focus 一刀没切" if r["one_block"] else ""
            if nn is None:
                print(f"   · {question}{tag}\n       BM25 {b['retention']:.1%} 字符 "
                      f"({b['chars_kept']:,}/{r['chars_total']:,}) "
                      f"答案{'保留' if b['answer_kept'] else '**丢失**'}")
            else:
                print(f"   · {question}{tag}\n"
                      f"       BM25   {b['retention']:.1%} 字符 "
                      f"({b['chars_kept']:,}/{r['chars_total']:,}) "
                      f"{b['blocks_kept']}/{r['blocks']} 块 "
                      f"答案{'保留' if b['answer_kept'] else '**丢失**'} {b['ms']:.0f}ms\n"
                      f"       神经   {nn['retention']:.1%} 字符 "
                      f"({nn['chars_kept']:,}/{r['chars_total']:,}) "
                      f"{nn['blocks_kept']}/{r['blocks']} 块 "
                      f"答案{'保留' if nn['answer_kept'] else '**丢失**'} {nn['ms']:.0f}ms")
        print()

    if not rows:
        print("没有任何有效用例。", file=sys.stderr)
        return 1

    def agg(label: str, key: str) -> float:
        vals = [r[label][key] for r in rows if r.get(label)]
        return statistics.mean(vals) if vals else float("nan")

    print("═══ 汇总（%d 个有效用例；其中整页一块的 %d 个）"
          % (len(rows), sum(1 for r in rows if r["one_block"])))
    print(f"{'':10} {'保留率(字符加权)':>14} {'答案召回':>10} {'均耗时':>10}")
    for label, name in (("bm25", "BM25"), ("neural", "神经")):
        got = [r for r in rows if r.get(label)]
        if not got:
            continue
        recall = sum(1 for r in got if r[label]["answer_kept"]) / len(got)
        print(f"{name:10} {weighted_retention(rows, label):>13.1%} "
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
