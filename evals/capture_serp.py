#!/usr/bin/env python3
"""集 A 语料采集器：真实 SERP → 冻结 HTML + provenance，落 evals/corpus/serp/。

集 A 测的是**整条链**（引擎解析 → 归一化 → 去重 → 排序），语料必须是某一天真实
引擎页面的冻结件，provenance 记进同名 .meta.json。两条硬规矩从引擎 fixture 机制
原样继承（tests/test_engine_fixtures_live.py 的同一文体）：

1. **capture 拒写无结果页** —— 一条可用结果都没有的页面不能当基线，跳过并记进
   failures.jsonl（"0 条"是天气，不是语料）。
2. **每页 provenance 四件齐全** —— captured_from_url / sha256 / usable / note，
   scrubbed 说明会话痕迹已剥。

**robots 例外（显式声明，不是悄悄开）**：搜索引擎的 robots.txt 普遍 Disallow
`/search`，而 dhole 的 robots.py 默认遵守。造评测语料必须豁免 —— 下面这行
`DHOLE_IGNORE_ROBOTS=1` 是**造语料的专用例外**：不进产品路径、不进任何测试、
不默认开。理由与登记见 evals/README.md「集 A 语料的 robots 例外」。
（实情：引擎抓取走 search_metasearch 的自建 HTTP 客户端，本来就不经过 robots.py；
这行是纵深防御 —— 语料脚本的任何辅助路径都不许被 robots 拦住。）

分层抽样（确定性，可复核）：每题只抓 3–4 家引擎， seeding 用 crc32(query_id)，
同一天重跑抽到同一组。保证两条：每题的引擎组横跨 ≥2 个索引家族（共识分母是
家族数）；sogou 家族进所有中文题（它的中文索引是它存在的理由），英文题只给
少数派名额 —— 英文题硬塞 sogou 抓回来的多半是空页，而空页按规矩不能写。
bing 家族（bing/bing_global/duckduckgo）在两种语言里都保有固定名额。

用法：
    python evals/capture_serp.py --dry                 # 只打印抽样方案
    python evals/capture_serp.py --limit 4             # 前 4 题试跑
    python evals/capture_serp.py --only q003 --engines bing
    python evals/capture_serp.py                       # 全量（断点续跑：已有页面跳过）
"""

from __future__ import annotations

# ── robots 例外：本文件唯一的 env 写入，理由见文件头与 evals/README.md ────────
import os
os.environ["DHOLE_IGNORE_ROBOTS"] = "1"

import argparse
import datetime as dt
import hashlib
import html as _html
import json
import pathlib
import random
import re
import sys
import time
import zlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

CORPUS = REPO_ROOT / "evals" / "corpus" / "serp"
QUERIES = REPO_ROOT / "evals" / "queries.jsonl"

# 8 家未来默认池。CN 家系抓自己隔壁就通；bing_global/brave/yandex/duckduckgo
# 在部分网络下需要代理或 VPN（与 tests 引擎 fixture 的 capture 同一限制）。
ENGINES: dict[str, dict] = {
    "baidu":       {"family": "baidu", "interval_s": 4.0},
    "bing":        {"family": "bing",  "interval_s": 3.0},
    "sogou":       {"family": "sogou", "interval_s": 4.0},
    "bing_global": {"family": "bing",  "interval_s": 3.0},
    "yandex":      {"family": "yandex", "interval_s": 5.0},
    "brave":       {"family": "brave", "interval_s": 5.0},
    "duckduckgo":  {"family": "bing",  "interval_s": 5.0},
    "so360":       {"family": "so360", "interval_s": 4.0},
}
ITEMS_KEPT = 10          # 每页冻结的结果容器数（排序评测要看前 10）
TIMEOUT_S = 20


def engines_for(qid: str, lang: str) -> list[str]:
    """确定性分层抽样。改抽样规则 = 改这份函数 + 重抓语料，没有第三条路。"""
    rng = random.Random(zlib.crc32(qid.encode("utf-8")))
    if lang == "zh":
        picks = ["sogou", rng.choice(["baidu", "so360"]),
                 rng.choice(["bing", "bing_global"])]
        if zlib.crc32((qid + ":4th").encode()) % 3 == 0:
            picks.append(rng.choice(["duckduckgo", "brave", "yandex"]))
    else:
        picks = [rng.choice(["bing", "bing_global"]), "duckduckgo",
                 rng.choice(["brave", "yandex"])]
        if zlib.crc32((qid + ":4th").encode()) % 4 == 0:
            picks.append("sogou")
    return picks


# ── 引擎抓取（与 tests/test_engine_fixtures_live.py 同一调用形状）────────────


def _fetch_raw(engine_name: str, query: str) -> tuple[str | None, str, int, int]:
    """一次真实请求。返回 (raw_html|None, request_url, item_nodes, usable)。"""
    from dhole_mcp.search_metasearch import _TEXT_ENGINES

    eng = _TEXT_ENGINES[engine_name](proxy=None, timeout=TIMEOUT_S, verify=True)
    payload = eng.build_payload(query=query, region="us-en", safesearch="moderate",
                                timelimit=None, page=1)
    if eng.search_method == "GET":
        text = eng.request(eng.search_method, eng.search_url, params=payload)
        url = eng.search_url + "?" + "&".join(
            f"{k}={v}" for k, v in payload.items())
    else:
        text = eng.request(eng.search_method, eng.search_url, data=payload)
        url = eng.search_url
    if not text:
        return None, url, 0, 0
    rows = eng.extract_results(text) or []
    usable = sum(1 for r in rows
                 if getattr(r, "href", None) and getattr(r, "title", None))
    nodes = eng.last_extract[0] if eng.last_extract[0] >= 0 else len(rows)
    return text, url, nodes, usable


def _trim_to_items(eng, html_text: str, cap: int) -> tuple[str, int]:
    """只留前 cap 个结果容器及其祖先链（fixture 版的参数化复制，cap=10）。"""
    from lxml import html as lhtml
    tree = lhtml.fromstring(eng.pre_process_html(html_text))
    nodes = tree.xpath(eng.items_xpath)[:cap]
    if not nodes:
        return "<html><body></body></html>", 0
    parts = [lhtml.tostring(n, encoding="unicode") for n in nodes]
    chain = [a for a in nodes[0].iterancestors() if a.tag not in ("html", "body")]

    def _open(a) -> str:
        attrs = "".join(f' {k}="{_html.escape(str(v), quote=True)}"'
                        for k, v in a.attrib.items())
        return f"<{a.tag}{attrs}>"

    envelope = ("".join(_open(a) for a in reversed(chain))
                + "\n".join(parts)
                + "".join(f"</{a.tag}>" for a in chain))
    return "<html><body>" + envelope + "</body></html>", len(nodes)


def _scrub(text: str) -> str:
    """剥会话痕迹（与 fixture 版同一分隔符集合，含 HTML 转义形态）。"""
    return re.sub(
        r"((?:[?&]|&amp;)(?:q|Cookie|token|sig|key|sid|session|auth|_c)\w*=)[^&\"'\s<>]+",
        r"\1REDACTED", text)


# ── 主流程 ────────────────────────────────────────────────────────────────────


def _load_queries() -> list[dict]:
    rows = []
    for line in QUERIES.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            rows.append(json.loads(line))
    if not (60 <= len(rows) <= 80):
        print(f"[capture] 警告：活集规模 {len(rows)} 在 60–80 之外", file=sys.stderr)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 题（试跑）")
    ap.add_argument("--only", default="", help="逗号分隔的 query_id")
    ap.add_argument("--engines", default="", help="逗号分隔的引擎名过滤")
    ap.add_argument("--dry", action="store_true", help="只打印抽样方案")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    queries = _load_queries()
    if args.only:
        want = {q.strip() for q in args.only.split(",") if q.strip()}
        queries = [q for q in queries if q["id"] in want]
    if args.limit:
        queries = queries[:args.limit]
    engine_filter = {e.strip() for e in args.engines.split(",") if e.strip()}

    plan: list[tuple[dict, str]] = []
    for q in queries:
        for e in engines_for(q["id"], q["lang"]):
            if not engine_filter or e in engine_filter:
                plan.append((q, e))

    if args.dry:
        for q, e in plan:
            print(f"{q['id']}  {q['lang']}  {e:12s}  {q['text']}")
        per_engine: dict[str, int] = {}
        for _, e in plan:
            per_engine[e] = per_engine.get(e, 0) + 1
        print(f"\n{len(plan)} fetches over {len(queries)} queries; per engine: "
              + ", ".join(f"{k}={v}" for k, v in sorted(per_engine.items())))
        return 0

    CORPUS.mkdir(parents=True, exist_ok=True)
    failures_path = CORPUS / "failures.jsonl"
    last_request_at: dict[str, float] = {}
    written = skipped_existing = refused_empty = failed = 0

    def _fail(qid, engine, reason):
        nonlocal failed
        failed += 1
        with failures_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"at": dt.datetime.now().isoformat(timespec="seconds"),
                                "query_id": qid, "engine": engine,
                                "reason": reason}, ensure_ascii=False) + "\n")

    for q, engine_name in plan:
        target = CORPUS / f"{q['id']}__{engine_name}.html"
        if target.exists():
            skipped_existing += 1
            continue
        # 每引擎礼貌间隔：语料采集不享受免密引擎的宽容。
        wait = ENGINES[engine_name]["interval_s"] - (time.monotonic()
                                                     - last_request_at.get(engine_name, 0.0))
        if wait > 0:
            time.sleep(wait)
        try:
            raw, req_url, nodes, usable = _fetch_raw(engine_name, q["text"])
        except Exception as exc:
            last_request_at[engine_name] = time.monotonic()
            _fail(q["id"], engine_name, f"{type(exc).__name__}: {str(exc)[:160]}")
            print(f"[capture] {q['id']} {engine_name}: FAILED {type(exc).__name__}")
            continue
        last_request_at[engine_name] = time.monotonic()

        if usable == 0:
            # 硬规矩：capture 拒写无结果页（含解析器漂移形态：容器在、可用 0）。
            refused_empty += 1
            _fail(q["id"], engine_name,
                  f"no usable results (item_nodes={nodes}) - not corpus material")
            print(f"[capture] {q['id']} {engine_name}: REFUSED (usable=0, nodes={nodes})")
            continue

        from dhole_mcp.search_metasearch import _TEXT_ENGINES
        eng = _TEXT_ENGINES[engine_name](proxy=None, timeout=TIMEOUT_S, verify=True)
        trimmed, kept = _trim_to_items(eng, raw, ITEMS_KEPT)
        trimmed = _scrub(trimmed)
        data = trimmed.encode("utf-8")
        meta = {
            "query_id": q["id"], "query": q["text"], "engine": engine_name,
            "captured_at": dt.datetime.now().isoformat(timespec="seconds"),
            "captured_from_url": _scrub(req_url),
            "sha256": hashlib.sha256(data).hexdigest(),
            "item_nodes": nodes, "usable": usable, "trimmed_items": kept,
            "scrubbed": True, "labeler": None, "note": q.get("note", ""),
        }
        target.write_bytes(data)
        target.with_suffix(".meta.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        written += 1
        print(f"[capture] {q['id']} {engine_name}: wrote {kept} items "
              f"(usable={usable}, nodes={nodes})")

    print(f"\n[capture] done: written={written} skipped(existing)={skipped_existing} "
          f"refused_empty={refused_empty} failed={failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
