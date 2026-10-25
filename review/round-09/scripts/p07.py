"""P07 甘棠3 · 跨境物流 L2 —— search 行 → 批量 read 落页 三对账 ×5。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(7, "甘棠3·跨境物流")
p.call("cred_defaults", {"set": {"allow_private": True}})

def rows_of(r):
    return r.get("rows") or r.get("results") or []

p.set_scenario("S1-search 5 行")
r = p.call("web_search", {"query": "跨境物流 关务 合规", "max_results": 5})
rows = rows_of(r)
print(f"   {len(rows)} 行")
for x in rows[:5]:
    print(f"   - {str(x.get('title'))[:40]} | {str(x.get('url'))[:70]}")

p.pause(6)
p.set_scenario("S2-batch read(urls=) 落页对账")
urls = [str(x.get("url")) for x in rows[:5] if x.get("url")]
if urls:
    r2 = p.call("web_read", {"urls": urls, "timeout": 45000})
    pages = r2.get("pages") or r2.get("rows") or []
    print(f"   批量: {len(urls)} URL asked → {len(pages)} 页回")
    for i, pg in enumerate(pages[:5]):
        row_t = str(rows[i].get("title"))[:36] if i < len(rows) else "?"
        pg_t = str((pg.get("metadata") or {}).get("title") or pg.get("title"))[:36]
        ok = pg.get("content_ok")
        print(f"   - 行[{i}] {row_t!r} vs 页 {pg_t!r} | content_ok={ok}")
else:
    print("   (无行可点——现场如实记录)")

p.pause(6)
p.set_scenario("S3-批量上限断言(urls=11 全收,R07 家产)")
big = ["http://127.0.0.1:8399/work/%d" % i for i in range(1, 9)] + \
      ["http://127.0.0.1:8399/about", "http://127.0.0.1:8399/nav", "http://127.0.0.1:8399/qr"]
r3 = p.call("web_read", {"urls": big, "cache_ttl": 0, "timeout": 45000})
print(f"   urls=11: {str(r3.get('summary'))[:120]}")
print("P07 DONE")
