"""P08 顾准 · 体育数据分析师 L3 —— 广谱 search · 批量读 · 换 selector 回读。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(8, "顾准·体育数据分析师")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-广谱 search")
r = p.call("web_search", {"query": "football data analysis", "max_results": 3})
print(f"   {str(r.get('summary'))[:200]}")
for x in (r.get("results") or [])[:3]:
    print("   -", str({k: x.get(k) for k in ('url', 'title', 'engines') if x.get(k) is not None})[:150])

p.pause(5)
p.set_scenario("S2-订阅流两周")
r = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"],
                         "since": "2w", "max_items": 4})
for x in r.get("feeds") or []:
    print(f"   since={x.get('since')} older={x.get('items_older_than_since')} "
          f"items={len(x.get('items') or [])}")

p.pause(4)
p.set_scenario("S3-批量读三页")
URLS = [f"http://127.0.0.1:8399/work/{i}?cb=p08-{i}" for i in (1, 2, 3)]
r = p.call("web_read", {"urls": URLS})
for x in r.get("results") or []:
    print("   -", str({k: x.get(k) for k in ('url', 'content_ok', 'total_extracted_chars', 'cached')})[:170])

p.pause(4)
p.set_scenario("S4-同页换 selector 回读（缓存键隔离）")
U = URLS[0]
a = p.call("web_read", {"url": U, "css_selector": "h1"})
p.pause(2)
b = p.call("web_read", {"url": U, "css_selector": "p"})
p.pause(2)
c = p.call("web_read", {"url": U})
ta = "".join(str(x) for x in (a.get("content") or []))
tb = "".join(str(x) for x in (b.get("content") or []))
print(f"   h1={len(ta)} p={len(tb)} 不同={ta != tb} cached={a.get('cached')},{b.get('cached')},{c.get('cached')}")

p.pause(4)
p.set_scenario("S5-整批复读（逐行缓存）")
r = p.call("web_read", {"urls": URLS})
for x in r.get("results") or []:
    print("   -", str({k: x.get(k) for k in ('url', 'cached', 'fetched_at') if x.get(k) is not None})[:170])
print("P08 DONE")
