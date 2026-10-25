"""P05 长孙蔷 · 电商 L2 —— check diff 可读性 + include_headers 对账。"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim
p = PersonaSim(5, "长孙蔷·电商")
p.call("cred_defaults", {"set": {"allow_private": True}})
p.set_scenario("S1-check 盯价(/changing 三连)")
for i in range(2):
    r = p.call("web_read", {"url": "http://127.0.0.1:8399/changing?price=1", "extraction_type": "check",
                            "cache_ttl": 0, "timeout": 30000})
    d = r
    print(f"   check{i+1}: changed={d.get('changed')} hunks={d.get('diff_hunks')} window={str(d.get('diff_window'))[:60]}")
    p.pause(5)
p.set_scenario("S2-include_headers 对账")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/collections?h=1", "cache_ttl": 0,
                        "include_headers": True, "timeout": 30000})
md = r.get("metadata") or {}
print("   status:", md.get("status"), "| server header:", str((md.get("headers") or {}).get("Server") or (md.get("headers") or {}).get("server"))[:40])
p.observe("headers", r, "summary")
p.pause(7)
p.set_scenario("S3-checkout 旁支(web_act)")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/checkout", "actions": [
    {"fill": {"selector": "#item", "text": "SKU-9"}}, {"fill": {"selector": "#qty", "text": "3"}},
    {"click": {"selector": "#place-order", "confirm": True}}], "timeout": 60000})
p.observe("checkout", r, "summary")
print("P05 DONE")
