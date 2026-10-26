"""P04 罗盘 · 供应链分析师 L2 —— 批量读与 recall 交错 · 写闸 + 登录/匿名隔离。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(4, "罗盘·供应链分析师")
p.call("cred_defaults", {"set": {"allow_private": True}})

URLS = [f"http://127.0.0.1:8399/work/{i}?cb=p04-{i}" for i in (1, 2, 3)]

p.set_scenario("S1-批量读三页")
r = p.call("web_read", {"urls": URLS})
for x in r.get("results") or []:
    print("   -", str({k: x.get(k) for k in ('url', 'content_ok', 'total_extracted_chars', 'cached')})[:180])

p.pause(4)
p.set_scenario("S2-recall 找回刚读的页")
r = p.call("web_recall", {"query": "规格 供应 工作", "limit": 5})
print(f"   results={len(r.get('results') or [])}")
for x in (r.get("results") or [])[:4]:
    print("   -", str({k: x.get(k) for k in ('url', 'title', 'is_stale') if x.get(k) is not None})[:160])

p.pause(4)
p.set_scenario("S3-写闸（缺 confirm）")
r = p.call("web_request", {"url": "http://127.0.0.1:8399/checkout", "method": "POST",
                           "body": {"item": "供应变更", "qty": "2"}})
print(f"   err={str(r.get('error'))[:150]}")
print(f"   next={str(r.get('next_action'))[:200]}")

p.pause(3)
p.set_scenario("S4-confirm 真写")
r = p.call("web_request", {"url": "http://127.0.0.1:8399/checkout", "method": "POST",
                           "body": {"item": "供应变更", "qty": "2"}, "confirm": True})
print(f"   status={r.get('status')} summary={str(r.get('summary'))[:160]}")

p.pause(4)
p.set_scenario("S5-自有登录会话 vs 匿名")
r = p.call("web_request", {"url": "http://127.0.0.1:8399/login", "method": "POST",
                           "body": {"username": "luopan", "password": "pw"},
                           "session_id": "p04login", "confirm": True})
print(f"   login status={r.get('status')}")
u = "http://127.0.0.1:8399/account"
s = p.call("web_read", {"url": u, "session_id": "p04login"})
anon = p.call("web_read", {"url": u, "cache_ttl": 0})
print(f"   session={str(s.get('summary'))[:90]}")
print(f"   anon={str(anon.get('summary'))[:90]}")
print("P04 DONE")
