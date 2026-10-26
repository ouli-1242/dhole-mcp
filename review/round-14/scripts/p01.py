"""P01 方寸 · 图书馆采编员 L2 —— feed 相对 since → 回访缓存 → recall。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(1, "方寸·图书馆采编员")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-feed 相对窗口 3d")
r = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"],
                         "since": "3d", "max_items": 5})
for x in r.get("feeds") or []:
    print(f"   since={x.get('since')} older={x.get('items_older_than_since')} "
          f"nodate={x.get('items_without_date')} items={len(x.get('items') or [])}")
print(f"   err={str(r.get('error'))[:120]}")

p.pause(5)
p.set_scenario("S2-相对窗口不认则退 ISO（自愈）")
r = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"],
                         "since": "2026-10-24", "max_items": 5})
for x in r.get("feeds") or []:
    print(f"   since={x.get('since')} older={x.get('items_older_than_since')} "
          f"items={len(x.get('items') or [])}")

p.pause(6)
p.set_scenario("S3-回访同一页两遍（缓存账）")
r1 = p.call("web_read", {"url": "http://127.0.0.1:8399/article"})
print(f"   first cached={r1.get('cached')} fetched_at={r1.get('fetched_at')} "
      f"chars={r1.get('total_extracted_chars')}")
p.pause(4)
r2 = p.call("web_read", {"url": "http://127.0.0.1:8399/article"})
print(f"   second cached={r2.get('cached')} fetched_at={r2.get('fetched_at')} "
      f"same_fetched_at={r1.get('fetched_at') == r2.get('fetched_at')}")
print(f"   summary尾: {str(r2.get('summary'))[-180:]}")

p.pause(5)
p.set_scenario("S4-强制回源（cache_ttl=0）")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/article", "cache_ttl": 0})
print(f"   cached={r.get('cached')} fetched_at={r.get('fetched_at')} "
      f"fresh_ts={'cache' not in str(r.get('summary'))[:80]}")

p.pause(5)
p.set_scenario("S5-recall 找上周读过的页")
r = p.call("web_recall", {"query": "缓存 抓取", "limit": 5})
print(f"   results={len(r.get('results') or [])}")
for x in (r.get('results') or [])[:3]:
    print("   -", str({k: x.get(k) for k in ('url', 'title', 'snippet') if x.get(k)})[:160])
print("P01 DONE")
