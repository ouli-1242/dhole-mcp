"""P03 阮鸿飞4b · 咖啡师 L1 —— 缺省预算照手册读慢页。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(3, "阮鸿飞4b·咖啡师")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-start 篇照读")
p.call("sys_guide", {"topic": "start"})

p.pause(6)
p.set_scenario("S2-慢页长文(缺省预算)")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/longaside", "cache_ttl": 0})
print(f"   ok: {r.get('content_ok')} | chars: {r.get('content_chars')} | cap: {r.get('attempt_cap_ms')}")
print(f"   summary: {str(r.get('summary'))[:140]}")

p.pause(6)
p.set_scenario("S3-feed(缺省)")
r = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"], "max_items": 3})
print(f"   summary: {str(r.get('summary'))[:120]}")
print("P03 DONE")
