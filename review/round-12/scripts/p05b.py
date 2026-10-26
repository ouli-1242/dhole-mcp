"""P05b 补条：pin 复用的正确形（url 必须在场）+ 真 404 + css_selector 0 匹配复查 + / 缓存代际。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(5, "常镜秋·评估师(补)")

p.set_scenario("S6-pin 铸值（url 在场, 新页）")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/login", "session_id": "p05b",
                       "pin": True, "timeout": 45000,
                       "actions": [
                           {"fill": {"selector": "input[name=username]", "text": "gupiaoren"}},
                           {"read": {"selector": "input[name=username]", "as": "who"}},
                       ]})
rows = (r.get("metadata") or {}).get("actions") or []
for a in rows:
    print(f"   action={a.get('action')} read_as={a.get('read_as')} val={str(a.get('read'))[:30]!r}")
print(f"   pin_state notes: {str((r.get('metadata') or {}).get('pin'))[:150]}")

p.pause(6)
p.set_scenario("S7-pin 命中回读（同 url 同 session, TTL 内）")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/login", "session_id": "p05b",
                       "pin": True, "timeout": 45000,
                       "actions": [{"read": {"selector": "input[name=username]"}}]})
rows = (r.get("metadata") or {}).get("actions") or []
val = next((str(a.get("read")) for a in rows if a.get("read")), "")
print(f"   回读值: {val[:40]!r}（期待 gupiaoren）")
print(f"   metadata pin: {str((r.get('metadata') or {}).get('pin'))[:200]}")

p.pause(6)
p.set_scenario("S8-真 404 如实")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/no/such/page"})
print(f"   status={r.get('status')} | {str(r.get('summary'))[:130]}")

p.pause(6)
p.set_scenario("S9-css_selector 0 匹配（cache_ttl=0 强制真源）")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/article",
                        "css_selector": "#no-such", "cache_ttl": 0})
print(f"   status={r.get('status')} chars={r.get('total_extracted_chars')} | {str(r.get('summary'))[:130]}")

p.pause(6)
p.set_scenario("S10-/ 真源代际（cache_ttl=0）")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/", "cache_ttl": 0})
print(f"   status={r.get('status')} chars={r.get('total_extracted_chars')} | {str(r.get('summary'))[:130]}")
print("P05b DONE")
