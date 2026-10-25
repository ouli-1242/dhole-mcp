"""P05 越千帆5 · 播客制作 L2 —— feed_read 多源混发（好源+404+坏地址 逐行分账）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(5, "越千帆5·播客制作")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-三源混发")
r = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml",
                                  "http://127.0.0.1:8399/no-such-feed.xml",
                                  "http://127.0.0.1:1/feed.xml"],
                          "max_items": 2})
feeds = r.get("feeds") or []
print(f"   summary: {str(r.get('summary'))[:150]}")
for f in feeds:
    print(f"   行: {str(f.get('source_url'))[-24:]} | items={len(f.get('items') or [])} | err={str(f.get('error') or '')[:70]}")
print(f"   asked/answered 在场: {'asked' in str(r.get('summary'))}")
print("P05 DONE")
