#!/usr/bin/env python3
"""集 A 的盲评标注工具：emit 出盲表 → 标注者逐条二元判断 → collect 收 qrels →
agree 算两份标注的一致率。

协议（为什么是这些形状，见 evals/README.md「标注协议」）：

- **盲**：标注者只看 查询 + 标题 + 摘要（截 160 字）。引擎名、返回顺序、URL
  一律不给 —— URL 会泄漏引擎（搜狗的包装链接一眼认出），顺序有位置偏置。
- **逐条二元**：relevant: 0|1。不让判官排十条 —— LLM 判官的位置偏置与长度偏置
  都在排序式判断里放大。
- **provenance 落盘**：qrels 记 labeler 身份与盲表内容 sha256。**没记录就不可
  复现，等于没标**。
- **一致率**：两份标注逐题对比；<80% 一致率的题剔除，剔除后不足 20 条 → 判
  "无证据，不换"（B8 的换型判据，现在定死）。

用法：
    python evals/label_serp.py emit --labeler agent-a   # .runtime/labeling/ 出盲表
    # 标注者把每个 item 的 relevant 填上（0/1），保存
    python evals/label_serp.py collect --file <sheet.json>   # 收进 corpus/qrels/
    python evals/label_serp.py agree <a.json> <b.json>       # 一致率
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

CORPUS = REPO_ROOT / "evals" / "corpus" / "serp"
QRELS = REPO_ROOT / "evals" / "corpus" / "qrels"
LABELING = REPO_ROOT / "evals" / ".runtime" / "labeling"

SNIPPET_CAP = 160


def _iter_corpus():
    per_q: dict[str, dict] = {}
    for meta_path in sorted(CORPUS.glob("*.meta.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        per_q.setdefault(meta["query_id"], {"query": meta["query"],
                                            "pages": []})["pages"].append(meta_path)
    return per_q


def _parse_items(engine: str, html_path: pathlib.Path) -> list[dict]:
    from dhole_mcp.search_metasearch import _TEXT_ENGINES, _normalize_url
    eng = _TEXT_ENGINES[engine](proxy=None, timeout=15, verify=True)
    rows = eng.post_extract_results(
        eng.extract_results(html_path.read_text(encoding="utf-8")) or []) or []
    items = []
    for r in rows:
        href = getattr(r, "href", "") or ""
        title = (getattr(r, "title", "") or "").strip()
        if not href or not title:
            continue
        snippet = " ".join(((getattr(r, "body", "") or "").split()))[:SNIPPET_CAP]
        items.append({"url": href, "key": _normalize_url(href),
                      "title": title, "snippet": snippet})
    return items


def cmd_emit(labeler: str) -> int:
    LABELING.mkdir(parents=True, exist_ok=True)
    n_sheets = 0
    for qid, info in sorted(_iter_corpus().items()):
        items: list[dict] = []
        for mp in info["pages"]:
            meta = json.loads(mp.read_text(encoding="utf-8"))
            html_path = mp.parent / (mp.name[:-len(".meta.json")] + ".html")
            items.extend(_parse_items(meta["engine"], html_path))
        # 同一 URL 多引擎给出：盲表里按 key 去重（判一次），引擎来源不进表。
        uniq: dict[str, dict] = {}
        for it in items:
            uniq.setdefault(it["key"], it)
        listed = sorted(uniq.values(), key=lambda it: it["title"])
        sheet = {"query_id": qid, "query": info["query"], "labeler": labeler,
                 "created_at": dt.datetime.now().isoformat(timespec="seconds"),
                 "items": [{"idx": i, "title": it["title"],
                            "snippet": it["snippet"], "relevant": None}
                           for i, it in enumerate(listed)],
                 "_mapping": {str(i): it["url"] for i, it in enumerate(listed)}}
        body = json.dumps(sheet, ensure_ascii=False, indent=2)
        sheet["sheet_sha256"] = hashlib.sha256(body.encode("utf-8")).hexdigest()
        out = LABELING / f"{qid}.{labeler}.json"
        out.write_text(json.dumps(sheet, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        n_sheets += 1
    print(f"emitted {n_sheets} blind sheets for labeler={labeler} under {LABELING}")
    print("标注纪律：只看 title/snippet，逐条填 relevant: 0|1；不看 _mapping。")
    return 0


def cmd_collect(path: str) -> int:
    sheet = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    missing = [it["idx"] for it in sheet["items"] if it["relevant"] not in (0, 1)]
    if missing:
        print(f"拒绝收卷：{len(missing)} 条未判（idx {missing[:8]}...）—— 盲评要逐条二元，"
              "漏判的题没有分辨力。")
        return 1
    QRELS.mkdir(parents=True, exist_ok=True)
    judgments = {}
    for it in sheet["items"]:
        url = sheet["_mapping"][str(it["idx"])]
        judgments[url] = int(it["relevant"])
    qrels = {"query_id": sheet["query_id"], "labeler": sheet["labeler"],
             "sheet_sha256": sheet.get("sheet_sha256"),
             "created_at": dt.datetime.now().isoformat(timespec="seconds"),
             "judgments": judgments}
    out = QRELS / f"{sheet['query_id']}.{sheet['labeler']}.json"
    out.write_text(json.dumps(qrels, indent=2, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    print(f"qrels → {out}  ({len(judgments)} judgments, "
          f"{sum(v for v in judgments.values())} relevant)")
    return 0


def cmd_agree(a_path: str, b_path: str) -> int:
    from dhole_mcp.search_metasearch import _normalize_url
    a = json.loads(pathlib.Path(a_path).read_text(encoding="utf-8"))
    b = json.loads(pathlib.Path(b_path).read_text(encoding="utf-8"))
    if a["query_id"] != b["query_id"]:
        print("两份不是同一题")
        return 1
    # 键用规范化 URL：两个标注者对同一页可能记到不同原始链接（包装/跟踪参数），
    # 规范化后才是"同一条结果"。
    ja = {_normalize_url(u): v for u, v in a["judgments"].items()}
    jb = {_normalize_url(u): v for u, v in b["judgments"].items()}
    common = sorted(set(ja) & set(jb))
    if not common:
        print("没有共同的被评条目")
        return 1
    agree = sum(1 for k in common if ja[k] == jb[k])
    rate = agree / len(common)
    print(f"query {a['query_id']}: 一致 {agree}/{len(common)} = {rate:.0%}"
          f"  {'→ 保留' if rate >= 0.8 else '→ <80%，剔除该题'}")
    for k in common:
        if ja[k] != jb[k]:
            print(f"  分歧 {k}: {a['labeler']}={ja[k]}  {b['labeler']}={jb[k]}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("emit")
    e.add_argument("--labeler", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--file", required=True)
    g = sub.add_parser("agree")
    g.add_argument("a")
    g.add_argument("b")
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if args.cmd == "emit":
        return cmd_emit(args.labeler)
    if args.cmd == "collect":
        return cmd_collect(args.file)
    return cmd_agree(args.a, args.b)


if __name__ == "__main__":
    raise SystemExit(main())
