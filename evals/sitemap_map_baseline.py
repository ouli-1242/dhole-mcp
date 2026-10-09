"""地图前态基线：一份 locale 副本很多的 sitemap，预算把谁挤出去（刀3-C4）。

**零网络、确定性**：语料在本脚本里按固定规格生成（不是随机），XML 交给真的
`parse_sitemap` + `discover_sitemap`；行序交给 `crawl._order_map_entries`，行价交给
`crawl._map_row_cost` —— 全部调本体，不复刻。注入的只有 `http_get` 那一层，与生产代码
`_make_http_get()` 返回的 `_get(u) -> (status, bytes) | None` 同一形状。所以这里量到的
是取图与排行的真顺序，而"C3 的尺"与"C4 的序"是同一把。

为什么要有这个数：体验测试里那次 `web_discover(sitemap=true)` 交回 559 行，而我那一页
不在里面 —— 站点把自己 100 篇文档的 9 种语言副本排在前面，副本吃掉了整轮预算。
**"覆盖了多少份不同内容"看不丢**：566 行翻译与 101 篇原文折叠后是同一批 key。真正丢的是
"交回的是原文还是它的译本"，所以这里的判据是 `originals_shipped` 与 `target_in_rows`。
排序类 P@k 更看不见这种丢失（整段没进响应不参与排名）。

用法
----
    python evals/sitemap_map_baseline.py            # 文档序 vs 折叠+重排，同一份语料
    python evals/sitemap_map_baseline.py --json     # 另存 .runtime/ 一份
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dhole_mcp import crawl  # noqa: E402
from dhole_mcp.sitemap import discover_sitemap  # noqa: E402

BASE = "https://docs.test"
# 九个**非默认**语言的副本段。原文在这一份语料里没有语言前缀（`/docs/...`），所以
# "副本"与"原文"分得开 —— 而折叠代表该由原文当，把原文折到后面等于把它藏起来。
LOCALES = ["bn", "es", "ja", "de", "fr", "it", "pt", "ru", "zh"]
_GROUPS = ("inference-generation", "quantization", "serving", "features", "api")
ORIGINALS = [f"/docs/{g}/{i}/index.html" for g in _GROUPS for i in range(20)]
TARGET = "/docs/quantization/17/index.html"   # 文档序里第 997 条：在图内、在预算外


def _corpus() -> list[str]:
    """文档序：900 条语言副本打头（100 篇 × 9 语言），然后才是 100 篇原文。

    副本刻意把默认预算（60,000 字符 ≈ 566 行）吃干 —— 于是目标页**在地图里、在这一页
    之外**：帽（1,000）没砍它，预算砍的是它前面的邻居。
    """
    assert TARGET in ORIGINALS
    mirrors = [f"/{loc}{p}" for p in ORIGINALS for loc in LOCALES]
    return mirrors + ORIGINALS


_MIRROR_RE = re.compile(r"^/(?:%s)/" % "|".join(LOCALES))


def _index_xml(paths: list[str]) -> bytes:
    body = "".join(f"<url><loc>{BASE}{p}</loc>"
                   f"<lastmod>2026-09-27T00:00:00Z</lastmod></url>" for p in paths)
    return (f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"{body}</urlset>").encode("utf-8")


def _http_get(paths: list[str]):
    xml = _index_xml(paths)

    def _get(u: str):
        if u.endswith("/sitemap.xml"):
            return (200, xml)
        if u.endswith("/robots.txt"):
            return (200, f"Sitemap: {BASE}/sitemap.xml\n".encode("utf-8"))
        return None
    return _get


def _is_mirror(url: str) -> bool:
    return bool(_MIRROR_RE.match(urlparse(url).path or "/"))


def _is_original(url: str) -> bool:
    return (urlparse(url).path or "") in ORIGINALS


def measure(fold: bool = True) -> dict:
    """`fold=False` = 把 `_order_map_entries` 换成恒等（文档序、不折叠），即 C4 前态。

    同一份语料、同一把尺、两个形状 —— 差值才是结论。
    """
    paths = _corpus()
    cap = min(5000, max(crawl.SITEMAP_MAP_FLOOR, 10 * 10))
    res = discover_sitemap(BASE, http_get=_http_get(paths), max_urls=cap)
    walked = [su.url for su in res.urls]
    target = f"{BASE}{TARGET}"
    if fold:
        ordered, stats = crawl._order_map_entries(res.urls, query="")
    else:
        ordered, stats = list(res.urls), {"mirrors_demoted": 0, "moved": 0,
                                         "paths_total": 0}
    rows, chars, held = [], 0, 0
    for su in ordered:
        row = crawl.CrawlPage(url=su.url, content_ok=True, fetcher_used="sitemap",
                              page_type="sitemap", lastmod=su.lastmod,
                              summary="sitemap entry")
        cost = crawl._map_row_cost(row, sitemap_used=True)
        if rows and chars + cost > crawl.MAX_TOTAL_CHARS_DEFAULT:
            held += 1
            continue
        chars += cost
        rows.append(su.url)
    keys = {crawl._fold_locale_path(urlparse(u).path or "/")[0] for u in rows}
    return {
        "sitemap_entries": len(paths),
        "cap": cap,
        "entries_in_walk": len(walked),
        "target_in_walk": target in walked,
        "target_in_rows": target in rows,
        "rows_shipped": len(rows),
        "chars_shipped": chars,
        "rows_held_back": held,
        "mirror_rows": sum(1 for u in rows if _is_mirror(u)),
        "originals_shipped": sum(1 for u in rows if _is_original(u)),
        "originals_missing": len(ORIGINALS) - sum(1 for u in rows if _is_original(u)),
        "content_keys_covered": len(keys),
        "mirrors_demoted": stats["mirrors_demoted"],
        "rows_moved": stats["moved"],
    }


def _fold_audit(entries: list) -> dict:
    """把折叠交给 `ranking_baseline._dedup_audit` 那台机器 —— 不新造去重指标。

    它打印的是"可疑合并（host 不同却并成一个 key）/ 可疑拆分（同 host+path 却分成两
    key）"，并明说结果**供人抽查，不冒充自动结论**。折叠是一类去重判断，而这类判断
    已经有人在这仓库里立过尺；自己再写一个"看起来对"的数，就是第二个会漂移的真相。
    """
    from ranking_baseline import _dedup_audit

    key_of = {su.url: crawl._fold_locale_path(urlparse(su.url).path or "/")[0]
              for su in entries}
    by_key: dict[str, list[str]] = {}
    for su in entries:
        by_key.setdefault(key_of[su.url], []).append(su.url)
    merges = []
    for key, urls in by_key.items():
        if len(urls) < 2:
            continue
        head = urls[0]
        merges.extend((key, head, other) for other in urls[1:])
    raw_pairs = [(su.url, key_of[su.url]) for su in entries]
    return _dedup_audit({"sitemap-map": merges}, {"sitemap-map": raw_pairs})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="另存 .runtime/ 一份")
    ap.add_argument("--audit", action="store_true",
                    help="顺带跑折叠的去重审计（借 ranking_baseline 那台机器）")
    args = ap.parse_args()
    before, after = measure(fold=False), measure(fold=True)
    print("== sitemap map baseline（零网络；真 parse_sitemap + discover_sitemap + "
          "_order_map_entries + _map_row_cost）==")
    print(f"  {'metric':22s} {'文档序 = C4 前':>16s} {'折叠+重排 = C4 后':>18s}")
    for k in before:
        b, a = before[k], after[k]
        if b == a:
            mark = "  ="
        elif isinstance(b, bool) or isinstance(a, bool):
            mark = "  ->"
        elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
            mark = f"  {'+' if a > b else '-'}{a - b:+,}"
        else:
            mark = "  ?"
        print(f"  {k:22s} {str(b):>16s} {str(a):>18s}{mark}")
    audit = None
    if args.audit:
        paths = _corpus()
        cap = min(5000, max(crawl.SITEMAP_MAP_FLOOR, 10 * 10))
        res = discover_sitemap(BASE, http_get=_http_get(paths), max_urls=cap)
        audit = _fold_audit(list(res.urls))
        print("== 折叠的去重审计（供人抽查，不冒充自动结论）==")
        print(f"  merge_pairs            {audit['merge_pairs']:,}")
        print(f"  suspicious_merge_count {audit['suspicious_merge_count']}")
        for qid, ua, ub in audit["suspicious_merges"][:5]:
            print(f"    {ua}  <->  {ub}")
        print(f"  suspicious_split_count {audit['suspicious_split_count']}")
        for row in audit["suspicious_splits"][:5]:
            print(f"    {row}")
    if args.json:
        out_dir = Path(__file__).resolve().parent / ".runtime" / "sitemap_map_baseline"
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / "latest.json"
        out.write_text(json.dumps({"document_order": before, "folded": after,
                                   "dedup_audit": audit},
                                  indent=2, ensure_ascii=False) + "\n",
                       encoding="utf-8")
        print(f"saved: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
