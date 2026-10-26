"""P08 马小川 · 体育数据分析师 L3 猎手 —— 相对 since 6h · feed 混发 · search clamp · 回访。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(8, "马小川·体育数据分析")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-feed 6h 增量")
r = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"],
                         "since": "6h", "max_items": 5})
for x in r.get("feeds") or []:
    print(f"   since={x.get('since')} older={x.get('items_older_than_since')} "
          f"nodate={x.get('items_without_date')} items={len(x.get('items') or [])}")

p.pause(6)
p.set_scenario("S2-feed 混发（好源+404）")
r = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml",
                                  "http://127.0.0.1:8399/feed-404.xml"],
                         "max_items": 2})
print(f"   {str(r.get('summary'))[:220]}")
for x in (r.get('feeds') or []):
    print("   -", str({k: x.get(k) for k in ('source_url', 'items') if x.get(k)})[:80],
          str(x.get('error') or '')[:80])

p.pause(12)
p.set_scenario("S3-search max_results=0（clamp）")
r = p.call("web_search", {"query": "football league standings", "max_results": 0})
print(f"   {str(r.get('summary'))[:260]}")

p.pause(6)
p.set_scenario("S4-战报页读两遍")
u = "http://127.0.0.1:8399/article?cb=p08-report"
a = p.call("web_read", {"url": u})
b = p.call("web_read", {"url": u})
print(f"   cached={a.get('cached')}->{b.get('cached')} "
      f"same_ts={a.get('fetched_at') == b.get('fetched_at')}")
print("P08 DONE")
