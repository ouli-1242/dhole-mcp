"""P04 越千帆4 · 电梯检验师 L3 猎手 —— cache TTL 代际账（三态+过期）。"""
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

URL = "http://127.0.0.1:8399/changing"

p = PersonaSim(4, "越千帆4·电梯检验师")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-ttl=3600 首灌(真回源)")
r = p.call("web_read", {"url": URL + "?gen=1", "cache_ttl": 3600})
print(f"   cache 字样: {'cached' in str(r.get('summary')).lower()} | fetched_at: {r.get('fetched_at')}")

p.pause(6)
p.set_scenario("S2-命中(代内 replay)")
r = p.call("web_read", {"url": URL + "?gen=1", "cache_ttl": 3600})
s = str(r.get("summary"))
print(f"   replay: {'cached copy' in s} | summary: {s[:130]}")

p.pause(6)
p.set_scenario("S3-ttl=0 强制回源(代际更替)")
r = p.call("web_read", {"url": URL + "?gen=1", "cache_ttl": 0})
print(f"   真回源: {'cached copy' not in str(r.get('summary')).lower()} | fetched_at: {r.get('fetched_at')}")

p.pause(6)
p.set_scenario("S4-ttl=1 过期(sleep 后 replay 应脱靶)")
p.call("web_read", {"url": URL + "?gen=2", "cache_ttl": 1})
time.sleep(2.5)
r = p.call("web_read", {"url": URL + "?gen=2", "cache_ttl": 1})
print(f"   过期后: {'cached copy' in str(r.get('summary')).lower()} (期望 False=真回源) | fetched_at: {r.get('fetched_at')}")

p.pause(6)
p.set_scenario("S5-recall stale 标注")
r = p.call("web_recall", {"query": "轮换", "limit": 6})
rows = r.get("results") or []
st = [(str(x.get("url"))[-14:], x.get("is_stale")) for x in rows if "changing" in str(x.get("url"))]
print(f"   changing 行 stale 标注: {st}")
print("P04 DONE")
