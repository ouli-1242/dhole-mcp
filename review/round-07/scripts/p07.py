"""P07 越千帆 · QA L3 —— 合法值名单逐个验证。"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim
p = PersonaSim(7, "越千帆·QA")
p.call("cred_defaults", {"set": {"allow_private": True}})
p.set_scenario("S1-名单内真收/名单外真拒")
cases = [("force_fetcher", "http"), ("force_fetcher", "stealthy"), ("force_fetcher", "curl")]
for k, v in cases:
    r = p.call("web_read", {"url": "http://127.0.0.1:8399/collections?n=1", "cache_ttl": 0, k: v, "timeout": 30000})
    print(f"   {k}={v}: err={str(r.get('error'))[:60] or 'NONE'} ok={r.get('content_ok')}")
    p.pause(4)
r = p.call("web_read", {"url": "http://127.0.0.1:8399/collections?n=2", "focus": "缓存", "focus_mode": "filter", "cache_ttl": 0, "timeout": 30000})
print("   focus_mode=filter:", "err" if r.get("error") else "OK")
p.pause(6)
p.set_scenario("S2-名单句完整性")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/collections?n=3", "extraction_type": "bogus"})
print("   extraction_type 名单句:", str(r.get("error"))[:130])
print("P07 DONE")
