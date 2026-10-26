"""P20 回归抽检补条 · css 0 匹配 / auth_wall 分家 / feed 三连 / recall clamp。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(20, "回归抽检·r13补条")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-css 0 匹配（r12 必测2 / R06-001 家产）")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/article",
                        "css_selector": "#no-such"})
print(f"   status={r.get('status')} chars={r.get('total_extracted_chars')}")
print(f"   summary: {str(r.get('summary'))[:400]}")

p.pause(6)
p.set_scenario("S2-auth_wall 分家（r12 必测4）")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/login", "timeout": 45000,
                       "actions": [
                           {"fill": {"selector": "input[name=username]", "text": "r13check"}},
                           {"read": {"selector": "input[name=username]", "property": "value"}},
                       ]})
acts = (r.get("metadata") or {}).get("actions") or []
for a in acts:
    print(f"   {a.get('action')}: ok={a.get('ok')} read={str(a.get('read'))[:30]!r}")
print(f"   content_ok={r.get('content_ok')} diagnosis={str(r.get('diagnosis'))[:160]}")

p.pause(6)
p.set_scenario("S3-feed 三连（r11 必测1/5）")
r = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"], "max_items": -3})
print(f"   neg: {str(r.get('error'))[:160]}")
r = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"], "max_items": 0})
for x in r.get('feeds') or []:
    print(f"   zero(all): items={len(x.get('items') or [])} | {str(r.get('summary'))[:120]}")
r = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"], "max_items": 2})
for x in r.get('feeds') or []:
    print(f"   pos2: items={len(x.get('items') or [])} | {str(r.get('summary'))[:140]}")

p.pause(6)
p.set_scenario("S4-recall clamp（r09 家产 fix-R09-F1）")
r = p.call("web_recall", {"query": "靶站", "limit": 21})
print(f"   21: clamped={r.get('clamped')} results={len(r.get('results') or [])}")
r = p.call("web_recall", {"query": "靶站", "limit": 8})
print(f"   8: clamped={r.get('clamped')} results={len(r.get('results') or [])}")
print("P20 DONE")
