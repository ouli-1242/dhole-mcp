"""P03 阿吉 · 咖啡店主 L1 —— 两条订阅流交错 · 长文分片窗口。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(3, "阿吉·咖啡店主")
p.call("cred_defaults", {"set": {"allow_private": True}})

A = "http://127.0.0.1:8399/feed.xml"
B = "http://127.0.0.1:8399/feed.xml?cb=p03b"

p.set_scenario("S1-订阅 A：近三天")
r = p.call("feed_read", {"urls": [A], "since": "3d", "max_items": 5})
for x in r.get("feeds") or []:
    print(f"   since={x.get('since')} older={x.get('items_older_than_since')} "
          f"items={len(x.get('items') or [])} err={x.get('error')}")

p.pause(4)
p.set_scenario("S2-订阅 B：近两周")
r = p.call("feed_read", {"urls": [B], "since": "2w", "max_items": 5})
for x in r.get("feeds") or []:
    print(f"   since={x.get('since')} older={x.get('items_older_than_since')} "
          f"items={len(x.get('items') or [])} err={x.get('error')}")

p.pause(4)
p.set_scenario("S3-长文第一片")
U = "http://127.0.0.1:8399/longaside?cb=p03long"
a = p.call("web_read", {"url": U, "max_content_chars": 400})
print(f"   truncated={a.get('is_truncated')} next={a.get('next_offset')} "
      f"summary={str(a.get('summary'))[:200]}")

p.pause(3)
p.set_scenario("S4-长文续片（窗口点名）")
nxt = a.get("next_offset") or 400
b = p.call("web_read", {"url": U, "max_content_chars": 400, "offset": nxt})
print(f"   offset={nxt} truncated={b.get('is_truncated')} summary={str(b.get('summary'))[:220]}")

p.pause(4)
p.set_scenario("S5-两条流同时收口")
r = p.call("feed_read", {"urls": [A, B], "since": "3h", "max_items": 3})
for x in r.get("feeds") or []:
    print(f"   since={x.get('since')} older={x.get('items_older_than_since')} "
          f"items={len(x.get('items') or [])}")
print("P03 DONE")
