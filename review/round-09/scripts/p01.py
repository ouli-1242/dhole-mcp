"""P01 桑无咎3 · 药师 L1 —— 路线2：URL 无 → search → read，结果行三对账。"""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(1, "桑无咎3·药师")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-读 start 篇(照手册)")
r = p.call("sys_guide", {"topic": "start"})
print(f"   start 篇: {len(str(r.get('_raw') or ''))} chars")

p.pause(6)
p.set_scenario("S2-search(路线2)")
r = p.call("web_search", {"query": "布洛芬 说明书 用法", "max_results": 5})
rows = r.get("rows") or r.get("results") or []
print(f"   summary: {str(r.get('summary'))[:130]}")
print(f"   行数: {len(rows)}")
if rows:
    top = rows[0]
    print(f"   top: {str(top.get('title'))[:60]} | {str(top.get('url'))[:70]}")
    print(f"   top 来源: {top.get('engines') or top.get('source')} | grade: {top.get('relevance') or top.get('grade')}")

p.pause(6)
p.set_scenario("S3-read 落页对账")
url = ""
if rows:
    url = str((rows[0] or {}).get("url") or "")
if url:
    r2 = p.call("web_read", {"url": url, "timeout": 30000})
    d2 = r2
    print(f"   行标题 vs 页标题: {str((rows[0] or {}).get('title'))[:40]!r} vs {str((d2.get('metadata') or {}).get('title'))[:40]!r}")
    print(f"   落页: {str(d2.get('summary'))[:120]}")
else:
    print("   (search 无行可点——现场如实记录)")
print("P01 DONE")
