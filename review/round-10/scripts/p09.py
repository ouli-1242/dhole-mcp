"""P09 库尔班4 · 兽医师 L1 —— 时间参数第一拒自救。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(9, "库尔班4·兽医师")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-cache_ttl=-1")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/article", "cache_ttl": -1})
print(f"   err: {str(r.get('error'))[:170] or '(收下?)'}")

p.pause(5)
p.set_scenario("S2-cache_ttl 字符串")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/article", "cache_ttl": "soon"})
print(f"   err: {str(r.get('error'))[:170]}")

p.pause(5)
p.set_scenario("S3-timeout 负数")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/article", "timeout": -100})
print(f"   err: {str(r.get('error'))[:170] or '(收下?)'}")

p.pause(5)
p.set_scenario("S4-改正形自救")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/article", "timeout": 20000, "cache_ttl": 0})
print(f"   自救: ok={r.get('content_ok')} | cap={r.get('attempt_cap_ms')}")
print("P09 DONE")
