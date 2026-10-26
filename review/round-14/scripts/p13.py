"""P13 r13 回归抽检补条：R13-001/002 + auth_wall + frame 错名 + feed 三连 + recall clamp。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(13, "r13回归抽检·补条")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("R1-R13-001 点名取块不判塌陷")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/article?cb=p13css",
                        "css_selector": "h1"})
print(f"   content_ok={r.get('content_ok')} chars={r.get('total_extracted_chars')} "
      f"err={str(r.get('error'))[:80]}")

p.pause(5)
p.set_scenario("R2-R13-002 selector 0 匹配注记 live/hit 都在")
u = "http://127.0.0.1:8399/article?cb=p13note"
a = p.call("web_read", {"url": u, "css_selector": "#no-such"})
b = p.call("web_read", {"url": u, "css_selector": "#no-such"})
sa, sb = str(a.get('summary') or ''), str(b.get('summary') or '')
print(f"   live={'matched 0 elements' in sa} hit={b.get('cached')} "
      f"replay={'matched 0 elements' in sb}")

p.pause(6)
p.set_scenario("R3-auth_wall 判词与动作分家")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/login", "timeout": 45000,
                       "actions": [
                           {"fill": {"selector": "input[name=username]", "text": "r14check"}},
                           {"read": {"selector": "input[name=username]", "property": "value"}},
                       ]})
acts = (r.get("metadata") or {}).get("actions") or []
print(f"   content_ok={r.get('content_ok')} diag={str((r.get('diagnosis') or {}).get('code'))} "
      f"rows={[a.get('ok') for a in acts]} read={acts[-1].get('read') if acts else None!r}")

p.pause(5)
p.set_scenario("R4-frame 错名 matched 0 + 在场清单")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/frame", "timeout": 45000,
                       "actions": [{"read": {"selector": "#saved", "frame": "#no-such"}}]})
acts = (r.get("metadata") or {}).get("actions") or []
print(f"   row={str(acts[0] if acts else {})[:260]}")

p.pause(5)
p.set_scenario("R5-feed 三连（负数 / 0=all）")
r = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"], "max_items": -3})
print(f"   neg={str(r.get('error'))[:110]}")
r = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"], "max_items": 0})
for x in r.get('feeds') or []:
    print(f"   zero items={len(x.get('items') or [])}")

p.pause(5)
p.set_scenario("R6-recall limit 越界 clamp")
r = p.call("web_recall", {"query": "靶站", "limit": 21})
print(f"   clamped={r.get('clamped')} results={len(r.get('results') or [])}")
print("P13 DONE")
