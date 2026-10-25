"""P04 云苏5 · 农技员 L1 —— feed_read 增量与账目（since/无日期/max_items）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(4, "云苏5·农技员")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-start 篇照读")
p.call("sys_guide", {"topic": "start"})

p.pause(6)
p.set_scenario("S2-基本账目(max_items=3)")
r = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"], "max_items": 3})
print(f"   summary: {str(r.get('summary'))[:150]}")
feeds = r.get("feeds") or []
for f in feeds[:1]:
    print(f"   note: {str(f.get('note'))[:150]}")
    print(f"   items_without_date: {f.get('items_without_date')} | items_older_than_since: {f.get('items_older_than_since')}")

p.pause(6)
p.set_scenario("S3-since 增量")
r = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"], "since": "1d", "max_items": 5})
print(f"   since=1d: {str(r.get('summary'))[:140]}")
print("P04 DONE")
