"""P06 屠维4 · 婚礼策划 L1 —— 批量 urls= 每页时间账（无显式 timeout）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(6, "屠维4·婚礼策划")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-批量 6 页(缺省预算)")
urls = ["http://127.0.0.1:8399/work/%d" % i for i in range(1, 5)] + \
       ["http://127.0.0.1:8399/about", "http://127.0.0.1:8399/nav"]
r = p.call("web_read", {"urls": urls, "cache_ttl": 0})
print(f"   summary: {str(r.get('summary'))[:140]}")
rows = r.get("results") or []
for row in rows:
    print(f"   - {str(row.get('url'))[-16:]} status={row.get('status')} ok={row.get('content_ok')} err={str(row.get('error') or '')[:40]}")

p.pause(6)
p.set_scenario("S2-sys_state(cache 账)")
r = p.call("sys_state", {"show": ["cache"]})
c = r.get("cache")
print(f"   cache 型: {type(c).__name__} | 行数: {len(c) if isinstance(c, list) else '-'}")
print("P06 DONE")
