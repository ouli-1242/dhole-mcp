"""P03 周小满 · 咖啡馆店主 L1 —— 为什么第二次快：guide + 回访两读 + feed + state。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(3, "周小满·咖啡馆店主")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-guide 看回执字段")
r = p.call("sys_guide", {"topic": "fields"})
raw = str(r.get("_raw") or "")
print(f"   guide chars={len(raw)} head={raw[:120]!r}")

p.pause(5)
p.set_scenario("S2-菜单页今天读两遍")
u = "http://127.0.0.1:8399/article?cb=p03-menu"
a = p.call("web_read", {"url": u})
b = p.call("web_read", {"url": u})
print(f"   first cached={a.get('cached')} fetched_at={a.get('fetched_at')}")
print(f"   second cached={b.get('cached')} same_ts={a.get('fetched_at') == b.get('fetched_at')}")

p.pause(5)
p.set_scenario("S3-订阅源看新品")
r = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"], "max_items": 2})
for x in r.get("feeds") or []:
    print(f"   items={len(x.get('items') or [])} newest={(x.get('items') or [{}])[0].get('title')}")

p.pause(5)
p.set_scenario("S4-state 你记住了几页")
r = p.call("sys_state", {})
print(f"   message: {str(r.get('message'))[:200]}")
print("P03 DONE")
