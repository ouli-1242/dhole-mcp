#!/usr/bin/env python3
"""真实查询复测：同一批候选，两颗重排器各排一次，人工盲评，配对 bootstrap 判区间。

为什么不用 67 题冻结语料（计划 §2.3 / §2.5 已写死）：67 题只能分辨 8.6 pp 以上
的差异，而模型间只差 0.6 pp —— 连实测不行的 `ms-marco` 都能打 0.546。真实场景
没有 qrels，判据改成「top-5 里我认可几条」，配对 bootstrap 95% 区间跨 0 就是
「没有证据支持换」，不许看均值说话。

四个阶段，人工只在中间出现一次：

  fetch  → 每条真实查询真搜一次，存同一批候选（title + snippet）
  score  → 两颗模型各自给**同一批**候选打分（子进程，各用 DHOLE_RERANK_MODEL）
  emit   → 盲表：A/B 顺序随机、隐去模型名；人工只勾「这条我要」
  report → 每题「top-5 认可几条」+ 配对 bootstrap 95% 区间 + 本次 SD

用法：

    python evals/real_query_eval.py run --queries real.jsonl --out evals/.runtime/real_eval/r1
    # 打开 out/sheet.html 勾选 → 下载 judgments.json
    python evals/real_query_eval.py report --run-dir evals/.runtime/real_eval/r1 \\
        --judgments judgments.json --baseline bge-zh --candidate mmarco

查询从哪来：
  - 手工记下的真实搜索（计划 §2.5 路径①）；或
  - `DHOLE_QUERY_LOG=1` 攒下的 `~/.dhole/queries.jsonl`（路径②）：
        python evals/real_query_eval.py fetch --from-log --limit 100 --out DIR

查询文件每行一个 JSON（`{"id": ..., "text": "..."}`，`evals/queries.jsonl` 的形状），
也接受纯文本一行一条。重复查询按首次出现去重。
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import math
import os
import pathlib
import random
import statistics
import subprocess
import sys
import time
from typing import Iterable, Sequence

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

DEFAULT_MODELS = ("bge-zh", "mmarco")
DEFAULT_TOP_K = 5
DEFAULT_SEED = 20261027
DEFAULT_SLEEP = 1.0
SNIPPET_SHOW = 240
BOOTSTRAP_ITERATIONS = 10_000


# ─── 输入 ──────────────────────────────────────────────────────────────


def load_queries(path: pathlib.Path) -> list[dict]:
    """读查询文件：JSONL（text / query 键）或纯文本一行一条；按文本去重。"""
    rows: list[dict] = []
    seen: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        qid = ""
        note = ""
        if line.startswith("{"):
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            text = str(obj.get("text") or obj.get("query") or "").strip()
            qid = str(obj.get("id") or "").strip()
            note = str(obj.get("note") or "").strip()
        else:
            text = line
        if not text or text in seen:
            continue
        seen.add(text)
        rows.append({"qid": qid or f"q{len(rows) + 1:03d}",
                     "query": text, "note": note})
    # 文件里给了 id 但不唯一时，后面的补序号，避免覆盖评分表。
    used: set[str] = set()
    for i, row in enumerate(rows, 1):
        base = row["qid"]
        qid = base
        n = 2
        while qid in used:
            qid = f"{base}-{n}"
            n += 1
        used.add(qid)
        row["qid"] = qid
    return rows


def load_query_log(path: pathlib.Path) -> list[dict]:
    """把 DHOLE_QUERY_LOG 的 JSONL 变成查询行：只取 query，按文本去重。"""
    rows: list[dict] = []
    seen: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        text = str(obj.get("query") or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        rows.append({"qid": f"log{len(rows) + 1:03d}", "query": text, "note": "query-log"})
    return rows


# ─── 纯函数（盲评与统计，测试直接喂假数据） ─────────────────────────────


def rank_indices(scores: Sequence[float], k: int) -> list[int]:
    """按分数降序取前 k 个下标；同分按原始顺序（稳定、可复现）。"""
    order = sorted(range(len(scores)), key=lambda i: (-float(scores[i]), i))
    return order[:max(0, int(k))]


def slot_order(models: Sequence[str], rng: random.Random) -> dict[str, str]:
    """为一道题决定 A/B 各放哪颗模型；每道题独立掷硬币。"""
    names = list(dict.fromkeys(models))
    if len(names) != 2:
        raise ValueError("slot_order needs exactly two distinct models")
    a, b = names[0], names[1]
    if rng.random() < 0.5:
        a, b = b, a
    return {"A": a, "B": b}


def bootstrap_ci(diffs: Sequence[float], *, iterations: int = BOOTSTRAP_ITERATIONS,
                 seed: int = DEFAULT_SEED) -> tuple[float, float] | None:
    """配对差值的 percentile bootstrap 95% 区间；空样本交回 None。"""
    vals = [float(d) for d in diffs]
    if not vals:
        return None
    rng = random.Random(seed)
    n = len(vals)
    means = []
    for _ in range(iterations):
        means.append(sum(rng.choices(vals, k=n)) / n)
    means.sort()
    lo = means[int(0.025 * iterations)]
    hi = means[min(iterations - 1, int(math.ceil(0.975 * iterations)) - 1)]
    return lo, hi


def measured_rows(candidates: Iterable[dict], scores: dict[str, dict[str, list[float]]],
                  slots: dict[str, dict[str, str]], judgments: dict[str, dict[str, list[int]]],
                  k: int) -> list[dict]:
    """把「候选 / 两模型分数 / 盲位映射 / 人工勾选」折成逐题记录。

    每题的分数 = 该模型 top-k 里被勾选的条数；分母是 min(k, 该列表长度)。
    """
    rows: list[dict] = []
    for cand in candidates:
        qid = cand["qid"]
        if qid not in slots or qid not in judgments:
            continue
        judged = judgments[qid]
        if "A" not in judged or "B" not in judged:
            continue
        per: dict[str, float] = {}
        ok = True
        for slot, model in slots[qid].items():
            model_scores = scores.get(model, {}).get(qid)
            docs = cand.get("docs") or []
            if model_scores is None or len(model_scores) != len(docs) or not docs:
                ok = False
                break
            top = rank_indices(model_scores, k)
            relevant = {int(i) for i in judged.get(slot, [])}
            per[model] = float(len(set(top) & relevant))
        if ok:
            rows.append({"qid": qid, "query": cand["query"], **per})
    return rows


def paired_stats(rows: Sequence[dict], baseline: str, candidate: str,
                 *, iterations: int = BOOTSTRAP_ITERATIONS,
                 seed: int = DEFAULT_SEED) -> dict:
    """逐题配对比较：candidate − baseline；返回均值、SD、区间、胜负。"""
    diffs: list[float] = []
    base_vals: list[float] = []
    cand_vals: list[float] = []
    for row in rows:
        if baseline not in row or candidate not in row:
            continue
        b = float(row[baseline])
        c = float(row[candidate])
        base_vals.append(b)
        cand_vals.append(c)
        diffs.append(c - b)
    if not diffs:
        return {"n": 0, "error": "no paired rows"}
    n = len(diffs)
    mean_diff = statistics.fmean(diffs)
    sd = statistics.stdev(diffs) if n > 1 else 0.0
    ci = bootstrap_ci(diffs, iterations=iterations, seed=seed)
    wins = sum(1 for d in diffs if d > 0)
    ties = sum(1 for d in diffs if d == 0)
    losses = sum(1 for d in diffs if d < 0)
    # α=.05 / power=.8 的近似分辨率（配对 t 检验的 2.8σ/√n）。
    detectable = (2.8 * sd / math.sqrt(n)) if n > 1 else float("inf")
    verdict = "insufficient"
    if n >= 100 and ci is not None:
        if ci[0] > 0:
            verdict = "candidate_better"
        elif ci[1] < 0:
            verdict = "baseline_better"
        else:
            verdict = "no_difference"
    return {
        "n": n,
        "baseline": baseline,
        "candidate": candidate,
        "baseline_mean": statistics.fmean(base_vals),
        "candidate_mean": statistics.fmean(cand_vals),
        "mean_diff": mean_diff,
        "sd": sd,
        "ci": list(ci) if ci else None,
        "wins": wins,
        "ties": ties,
        "losses": losses,
        "detectable": detectable if math.isfinite(detectable) else None,
        "verdict": verdict,
    }


# ─── fetch ─────────────────────────────────────────────────────────────


def _candidate_row(qid: str, query: str, payload: dict) -> dict:
    docs = []
    for i, row in enumerate(payload.get("results") or []):
        docs.append({
            "n": i,
            "url": str(row.get("url") or ""),
            "title": str(row.get("title") or ""),
            "snippet": str(row.get("snippet") or ""),
            "pipeline_score": row.get("relevance_score"),
        })
    return {
        "qid": qid,
        "query": query,
        "fetched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "error": str(payload.get("error") or ""),
        "engines_used": list(payload.get("engines_used") or []),
        "rerank_model": str(payload.get("rerank_model") or ""),
        "docs": docs,
    }


async def _fetch_all(queries: list[dict], *, max_results: int, engines: list[str] | None,
                     sleep: float) -> list[dict]:
    from dhole_mcp.context import _arg_notes_scope
    from dhole_mcp.server import MasterFetchServer

    server = MasterFetchServer()
    out: list[dict] = []
    for i, row in enumerate(queries, 1):
        args = {
            "query": row["query"],
            "max_results": max_results,
            "min_relevance": 0.0,
            "min_raw_relevance": 0.0,
        }
        if engines:
            args["engines"] = engines
        try:
            with _arg_notes_scope():
                _content, structured = await server._dispatch("web_search", args)
            payload = structured if isinstance(structured, dict) else {}
        except Exception as exc:  # 真实网络 + 引擎池：一行失败不该毁掉整批
            payload = {"error": f"{exc.__class__.__name__}: {exc}", "results": []}
        cand = _candidate_row(row["qid"], row["query"], payload)
        out.append(cand)
        err = f"  ERROR {cand['error'][:120]}" if cand["error"] else ""
        print(f"[{i}/{len(queries)}] {row['qid']} {row['query'][:60]!r} "
              f"-> {len(cand['docs'])} docs{err}", flush=True)
        if sleep and i < len(queries):
            time.sleep(sleep)
    return out


def cmd_fetch(args) -> int:
    # 候选集只生成一次；用哪颗模型生成要固定，否则两颗模型看到的候选不同。
    os.environ.pop("DHOLE_RERANK_MODEL", None)
    if args.from_log is not None:
        target = args.from_log
        if target in ("1", "true", "yes", "on"):
            from dhole_mcp import paths
            target = str(paths.file("queries.jsonl"))
        path = pathlib.Path(target).expanduser()
        if not path.exists():
            print(f"query log not found: {path}", file=sys.stderr)
            print("turn it on with DHOLE_QUERY_LOG=1, or pass --queries FILE",
                  file=sys.stderr)
            return 2
        queries = load_query_log(path)
    else:
        path = pathlib.Path(args.queries).expanduser()
        if not path.exists():
            print(f"query file not found: {path}", file=sys.stderr)
            return 2
        queries = load_queries(path)
    if args.limit:
        queries = queries[:args.limit]
    if not queries:
        print("no queries to fetch", file=sys.stderr)
        return 2
    run_dir = pathlib.Path(args.out)
    run_dir.mkdir(parents=True, exist_ok=True)
    engines = [e.strip() for e in (args.engines or "").split(",") if e.strip()] or None
    rows = asyncio.run(_fetch_all(queries, max_results=args.max_results,
                                  engines=engines, sleep=args.sleep))
    out = run_dir / "candidates.jsonl"
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                   encoding="utf-8")
    good = sum(1 for r in rows if r["docs"])
    print(f"wrote {out} ({good}/{len(rows)} queries returned candidates)")
    return 0 if good else 1


# ─── score ─────────────────────────────────────────────────────────────


def cmd_score(args) -> int:
    # 模型选择在 reranker 导入期读一次；必须在这之前设好。
    os.environ["DHOLE_RERANK_MODEL"] = args.model
    try:
        from dhole_mcp import reranker
    except Exception as exc:
        print(f"cannot import dhole_mcp.reranker: {exc}", file=sys.stderr)
        return 2
    if args.model not in reranker.MODELS:
        print(f"unknown model {args.model!r}; known: {', '.join(reranker.MODELS)}",
              file=sys.stderr)
        return 2
    candidates = [json.loads(line) for line in
                  (pathlib.Path(args.run_dir) / "candidates.jsonl")
                  .read_text(encoding="utf-8").splitlines() if line.strip()]
    rer = asyncio.run(reranker.ensure_reranker(download=True))
    if rer is None:
        print(f"reranker unavailable: {reranker.unavailable_reason() or 'not loaded'}",
              file=sys.stderr)
        return 2
    print(f"scoring with {reranker.active_model().name} "
          f"({len(candidates)} queries)", flush=True)
    rows = []
    started = time.monotonic()
    for i, cand in enumerate(candidates, 1):
        docs = [f"{d.get('title', '')} {d.get('snippet', '')}".strip()
                for d in cand.get("docs") or []]
        scores = rer.score(cand["query"], docs) if docs else []
        rows.append({"qid": cand["qid"], "model": args.model,
                     "scores": [round(float(s), 6) for s in scores]})
        if i % 10 == 0 or i == len(candidates):
            print(f"  {i}/{len(candidates)} ({time.monotonic() - started:.1f}s)",
                  flush=True)
    out = pathlib.Path(args.run_dir) / f"scores-{args.model}.jsonl"
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                   encoding="utf-8")
    print(f"wrote {out}")
    return 0


# ─── emit ──────────────────────────────────────────────────────────────


def _read_scores(run_dir: pathlib.Path) -> dict[str, dict[str, list[float]]]:
    out: dict[str, dict[str, list[float]]] = {}
    for path in sorted(run_dir.glob("scores-*.jsonl")):
        model = path.stem.removeprefix("scores-")
        per: dict[str, list[float]] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            per[row["qid"]] = [float(s) for s in row["scores"]]
        out[model] = per
    return out


def cmd_emit(args) -> int:
    run_dir = pathlib.Path(args.run_dir)
    candidates = [json.loads(line) for line in
                  (run_dir / "candidates.jsonl").read_text(encoding="utf-8").splitlines()
                  if line.strip()]
    scores = _read_scores(run_dir)
    models = sorted(scores)
    if len(models) != 2:
        print(f"need exactly two scores-*.jsonl files, found: {models or 'none'}",
              file=sys.stderr)
        return 2
    rng = random.Random(args.seed)
    sheet = []
    key = {"seed": args.seed, "top_k": args.top_k, "slots": {}}
    skipped = 0
    for cand in candidates:
        qid = cand["qid"]
        docs = cand.get("docs") or []
        if not docs or any(
                qid not in scores[m] or len(scores[m][qid]) != len(docs)
                for m in models):
            skipped += 1
            continue
        slots = slot_order(models, rng)
        key["slots"][qid] = slots
        entry = {"qid": qid, "query": cand["query"], "A": [], "B": []}
        for slot, model in slots.items():
            for i in rank_indices(scores[model][qid], args.top_k):
                d = docs[i]
                entry[slot].append({
                    "n": i,
                    "title": d.get("title") or "",
                    "snippet": (d.get("snippet") or "")[:SNIPPET_SHOW],
                })
        sheet.append(entry)
    (run_dir / "key.json").write_text(
        json.dumps(key, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    payload = {"seed": args.seed, "top_k": args.top_k, "queries": sheet}
    (run_dir / "sheet.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    html = _render_sheet(payload)
    (run_dir / "sheet.html").write_text(html, encoding="utf-8")
    print(f"wrote {run_dir / 'sheet.html'} ({len(sheet)} queries, {skipped} skipped)")
    print("open it, tick every row you would actually click, then download judgments.json")
    return 0


def _render_sheet(payload: dict) -> str:
    data = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    return """<!doctype html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>dhole real-query blind sheet</title>
<style>
 body { font: 15px/1.5 system-ui, sans-serif; max-width: 1080px; margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; }
 h1 { font-size: 1.3rem; } .q { border-top: 1px solid #ddd; padding: 1rem 0; }
 .q h2 { font-size: 1rem; margin: 0 0 .6rem; } .cols { display: grid; grid-template-columns: 1fr 1fr; gap: 1.2rem; }
 .col h3 { font-size: .85rem; margin: 0 0 .4rem; color: #666; }
 label.item { display: block; padding: .45rem .5rem; border-radius: 6px; }
 label.item:hover { background: #f5f5f5; }
 .title { font-weight: 600; } .snip { color: #555; font-size: .9rem; }
 #bar { position: sticky; bottom: 0; background: #fff; border-top: 1px solid #ddd; padding: .7rem 0; }
 button { font: inherit; padding: .45rem 1rem; cursor: pointer; }
</style>
</head>
<body>
<h1>dhole 真实查询盲评</h1>
<p>只看标题与摘要，勾选你在这一侧愿意点开/采用的条目。两侧来自同一批候选，只有排序不同；模型名已隐藏。勾完点底部下载，再用 <code>report</code> 算区间。</p>
<div id="queries"></div>
<div id="bar"><span id="count"></span> <button id="save">下载 judgments.json</button></div>
<script id="payload" type="application/json">__PAYLOAD__</script>
<script>
const data = JSON.parse(document.getElementById('payload').textContent);
const root = document.getElementById('queries');
const boxes = [];
for (const q of data.queries) {
  const wrap = document.createElement('div'); wrap.className = 'q';
  const h = document.createElement('h2');
  h.textContent = q.qid + ' · ' + q.query;
  wrap.appendChild(h);
  const cols = document.createElement('div'); cols.className = 'cols';
  for (const slot of ['A', 'B']) {
    const col = document.createElement('div'); col.className = 'col';
    const h3 = document.createElement('h3'); h3.textContent = '列表 ' + slot;
    col.appendChild(h3);
    for (const item of q[slot]) {
      const lab = document.createElement('label'); lab.className = 'item';
      const cb = document.createElement('input'); cb.type = 'checkbox';
      cb.dataset.qid = q.qid; cb.dataset.slot = slot; cb.dataset.n = item.n;
      boxes.push(cb);
      const t = document.createElement('span'); t.className = 'title'; t.textContent = item.title || '(无标题)';
      const s = document.createElement('div'); s.className = 'snip'; s.textContent = item.snippet || '';
      lab.appendChild(cb); lab.appendChild(t); lab.appendChild(s);
      col.appendChild(lab);
    }
    cols.appendChild(col);
  }
  wrap.appendChild(cols); root.appendChild(wrap);
}
function update() {
  const done = new Set(boxes.filter(b => b.checked).map(b => b.dataset.qid)).size;
  document.getElementById('count').textContent =
    done + '/' + data.queries.length + ' 题已勾选';
}
boxes.forEach(b => b.addEventListener('change', update)); update();
document.getElementById('save').addEventListener('click', () => {
  const out = {};
  for (const q of data.queries) out[q.qid] = {A: [], B: []};
  for (const b of boxes) if (b.checked) out[b.dataset.qid][b.dataset.slot].push(Number(b.dataset.n));
  const blob = new Blob([JSON.stringify(out, null, 2)], {type: 'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = 'judgments.json'; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
});
</script>
</body>
</html>
""".replace("__PAYLOAD__", data)


# ─── report ────────────────────────────────────────────────────────────


def _fmt(x: float | None, digits: int = 2) -> str:
    return "—" if x is None else f"{x:.{digits}f}"


def cmd_report(args) -> int:
    run_dir = pathlib.Path(args.run_dir)
    key = json.loads((run_dir / "key.json").read_text(encoding="utf-8"))
    candidates = [json.loads(line) for line in
                  (run_dir / "candidates.jsonl").read_text(encoding="utf-8").splitlines()
                  if line.strip()]
    scores = _read_scores(run_dir)
    judgments = json.loads(pathlib.Path(args.judgments).read_text(encoding="utf-8"))
    rows = measured_rows(candidates, scores, key["slots"], judgments, key["top_k"])
    stats = paired_stats(rows, args.baseline, args.candidate,
                         iterations=args.iterations, seed=key["seed"])
    if not stats.get("n"):
        print("no paired rows - check that judgments.json has A and B for the same qid",
              file=sys.stderr)
        return 2
    k = key["top_k"]
    lo, hi = stats["ci"]
    stats["ci_pp"] = [lo / k * 100, hi / k * 100]
    stats["mean_diff_pp"] = stats["mean_diff"] / k * 100
    stats["detectable_pp"] = (stats["detectable"] / k * 100
                              if stats["detectable"] is not None else None)
    stats["judged_queries"] = len(rows)
    stats["skipped_queries"] = len(key["slots"]) - len(rows)
    verdict_text = {
        "candidate_better": f"{args.candidate} 更好（区间整段在 0 以上）",
        "baseline_better": f"{args.baseline} 更好（区间整段在 0 以下）",
        "no_difference": "区间跨 0：没有证据支持换默认档",
        "insufficient": f"n={stats['n']} < 100：样本不足，不做换型结论",
    }[stats["verdict"]]
    lines = [
        "# 真实查询盲评",
        "",
        f"- 查询数（有完整 A/B 判定）：**{stats['n']}**；跳过 {stats['skipped_queries']} 题",
        f"- 判据：每题「top-{k} 里我认可几条」；配对差 = {args.candidate} - {args.baseline}",
        f"- {args.baseline}: {stats['baseline_mean']:.2f}/{k}　"
        f"{args.candidate}: {stats['candidate_mean']:.2f}/{k}",
        f"- 配对差值：**{stats['mean_diff']:+.2f} 条**（{stats['mean_diff_pp']:+.1f} pp），"
        f"95% 区间 [{lo:+.2f}, {hi:+.2f}] 条 "
        f"[{stats['ci_pp'][0]:+.1f}, {stats['ci_pp'][1]:+.1f}] pp",
        f"- 胜 / 平 / 负：{stats['wins']} / {stats['ties']} / {stats['losses']}",
        f"- 本次 SD：{stats['sd']:.2f} 条；这个样本能分辨约 "
        f"{_fmt(stats['detectable'], 2)} 条（{_fmt(stats['detectable_pp'], 1)} pp）的差异",
        "",
        f"**结论：{verdict_text}**",
        "",
        "> 区间跨 0 就是「不明显」——不许看均值说话。结论门槛是 n ≥ 100 条真实查询；"
        "样本不足时只报告分辨率，不换默认档。",
    ]
    report_md = "\n".join(lines) + "\n"
    (run_dir / "report.md").write_text(report_md, encoding="utf-8")
    (run_dir / "report.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(report_md)
    print(f"wrote {run_dir / 'report.md'} and {run_dir / 'report.json'}")
    return 0


# ─── run ───────────────────────────────────────────────────────────────


def _score_subprocess(run_dir: pathlib.Path, model: str) -> int:
    env = dict(os.environ)
    env["DHOLE_RERANK_MODEL"] = model
    cmd = [sys.executable, str(pathlib.Path(__file__).resolve()),
           "score", "--run-dir", str(run_dir), "--model", model]
    return subprocess.run(cmd, env=env).returncode


def cmd_run(args) -> int:
    run_dir = pathlib.Path(args.out)
    if cmd_fetch(argparse.Namespace(
            queries=args.queries, from_log=None, out=str(run_dir),
            max_results=args.max_results, limit=args.limit,
            sleep=args.sleep, engines=args.engines)) != 0:
        print("fetch failed - fix the query source before scoring", file=sys.stderr)
        return 2
    for model in args.models:
        print(f"scoring with {model} (a first run downloads the model, ~119MB for mmarco)",
              flush=True)
        if _score_subprocess(run_dir, model) != 0:
            return 2
    if cmd_emit(argparse.Namespace(run_dir=str(run_dir), top_k=args.top_k,
                                   seed=args.seed)) != 0:
        return 2
    print("\nnext: open sheet.html, tick the rows you would click, download judgments.json, then:")
    print(f"  python evals/real_query_eval.py report --run-dir {run_dir} "
          f"--judgments judgments.json --baseline {args.models[0]} "
          f"--candidate {args.models[1]}")
    return 0


# ─── CLI ───────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="run each real query once, save one candidate set")
    src = f.add_mutually_exclusive_group(required=True)
    src.add_argument("--queries", help="JSONL {'text'|'query'} or plain text, one per line")
    src.add_argument("--from-log", nargs="?", const="1", metavar="FILE",
                     help="read DHOLE_QUERY_LOG (default: ~/.dhole/queries.jsonl)")
    f.add_argument("--out", required=True, help="run directory")
    f.add_argument("--max-results", type=int, default=10,
                   help="candidates per query (default 10; the same set feeds both models)")
    f.add_argument("--limit", type=int, default=0, help="first N queries (0 = all)")
    f.add_argument("--sleep", type=float, default=DEFAULT_SLEEP,
                   help="seconds between live searches (default 1.0)")
    f.add_argument("--engines", default="", help="comma-separated engine names (optional)")
    f.set_defaults(func=cmd_fetch)

    s = sub.add_parser("score", help="score candidates with one model (one process per model)")
    s.add_argument("--run-dir", required=True)
    s.add_argument("--model", required=True, help="bge-zh / mmarco (DHOLE_RERANK_MODEL)")
    s.set_defaults(func=cmd_score)

    e = sub.add_parser("emit", help="write the blind A/B sheet")
    e.add_argument("--run-dir", required=True)
    e.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    e.add_argument("--seed", type=int, default=DEFAULT_SEED)
    e.set_defaults(func=cmd_emit)

    r = sub.add_parser("report", help="score collected judgments and compute the interval")
    r.add_argument("--run-dir", required=True)
    r.add_argument("--judgments", required=True, help="judgments.json downloaded from the sheet")
    r.add_argument("--baseline", default=DEFAULT_MODELS[0])
    r.add_argument("--candidate", default=DEFAULT_MODELS[1])
    r.add_argument("--iterations", type=int, default=BOOTSTRAP_ITERATIONS)
    r.set_defaults(func=cmd_report)

    run = sub.add_parser("run", help="fetch + score + emit in one go")
    run.add_argument("--queries", required=True)
    run.add_argument("--out", required=True, help="run directory")
    run.add_argument("--models", nargs=2, default=list(DEFAULT_MODELS),
                     metavar=("BASELINE", "CANDIDATE"))
    run.add_argument("--max-results", type=int, default=10)
    run.add_argument("--limit", type=int, default=0)
    run.add_argument("--sleep", type=float, default=DEFAULT_SLEEP)
    run.add_argument("--engines", default="")
    run.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    run.add_argument("--seed", type=int, default=DEFAULT_SEED)
    run.set_defaults(func=cmd_run)
    return p


def main(argv: list[str] | None = None) -> int:
    # Windows 控制台默认 GBK：报告里有中文与箭头，不换编码会在 print 上炸掉。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
