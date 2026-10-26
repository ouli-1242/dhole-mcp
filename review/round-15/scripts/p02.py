"""P02 陆知微 · 专利审查员 L3 —— read/check 同页交错 · 登录会话 vs 匿名读。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(2, "陆知微·专利审查员")
p.call("cred_defaults", {"set": {"allow_private": True}})

U = "http://127.0.0.1:8399/changing?cb=p02mix"

p.set_scenario("S1-冷读（应无缓存）")
a = p.call("web_read", {"url": U})
print(f"   cached={a.get('cached')} fetched_at={a.get('fetched_at')}")

p.pause(3)
p.set_scenario("S2-复读（应命中）")
b = p.call("web_read", {"url": U})
print(f"   cached={b.get('cached')} same_ts={a.get('fetched_at') == b.get('fetched_at')}")

p.pause(3)
p.set_scenario("S3-check 第一发（基线）")
c = p.call("web_read", {"urls": [U], "extraction_type": "check"})
print(f"   {str(c.get('summary'))[:220]}")
for x in c.get("results") or []:
    print("   -", str({k: x.get(k) for k in ('url', 'changed', 'status', 'note') if x.get(k) is not None})[:220])

p.pause(3)
p.set_scenario("S4-check 第二发（应 changed）")
d = p.call("web_read", {"urls": [U], "extraction_type": "check"})
print(f"   {str(d.get('summary'))[:220]}")
for x in d.get("results") or []:
    print("   -", str({k: x.get(k) for k in ('url', 'changed', 'status', 'note') if x.get(k) is not None})[:220])

p.pause(4)
p.set_scenario("S5-登录会话 vs 匿名读同页")
r = p.call("web_request", {"url": "http://127.0.0.1:8399/login", "method": "POST",
                           "body": {"username": "luzhiwei", "password": "pw"},
                           "session_id": "p02login", "confirm": True})
print(f"   login status={r.get('status')} summary={str(r.get('summary'))[:100]}")
p.pause(2)
u = "http://127.0.0.1:8399/account"
s = p.call("web_read", {"url": u, "session_id": "p02login"})
anon = p.call("web_read", {"url": u, "cache_ttl": 0})
ts, ta = str(s.get("summary")), str(anon.get("summary"))
print(f"   session登录态={'welcome' in ts.lower() or '欢迎' in ts} session_summary={ts[:90]}")
print(f"   anon登录态={'welcome' in ta.lower() or '欢迎' in ta} anon_summary={ta[:90]}")
print("P02 DONE")
