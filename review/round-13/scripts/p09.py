"""P09 常岁晏 · 灯光师 L3 猎手 —— 链⑨tab new/list/switch/close→跨页签 read→中环第一拒 3 发。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(9, "常岁晏·灯光师")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-tab new 开 /article 并在其上 read")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/", "timeout": 60000,
                       "actions": [
                           {"tab": {"open": "http://127.0.0.1:8399/article"}},
                           {"read": {"selector": "h1"}},
                       ]})
acts = (r.get("metadata") or {}).get("actions") or []
for a in acts:
    print(f"   {a.get('action')}: ok={a.get('ok')} read={str(a.get('read'))[:40]!r}")

p.pause(6)
p.set_scenario("S2-tab list 清单")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/", "timeout": 45000,
                       "actions": [{"tab": "list"}]})
acts = (r.get("metadata") or {}).get("actions") or []
for a in acts:
    print(f"   {a.get('action')}: ok={a.get('ok')} read={str(a.get('read'))[:120]!r}")

p.pause(6)
p.set_scenario("S3-tab 0 号位回切 + read")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/", "timeout": 45000,
                       "actions": [{"tab": 0}, {"read": {"selector": "h1"}}]})
acts = (r.get("metadata") or {}).get("actions") or []
for a in acts:
    print(f"   {a.get('action')}: ok={a.get('ok')} read={str(a.get('read'))[:40]!r}")

p.pause(6)
p.set_scenario("S4-tab close 当前枚")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/", "timeout": 45000,
                       "actions": [{"tab": "close"}, {"tab": "list"}]})
acts = (r.get("metadata") or {}).get("actions") or []
for a in acts:
    print(f"   {a.get('action')}: ok={a.get('ok')} err={str(a.get('error'))[:80]} read={str(a.get('read'))[:100]!r}")

p.pause(6)
p.set_scenario("S5-中环拒×3（GET / max_results=0 / feed 键名）")
r = p.call("web_request", {"url": "http://127.0.0.1:8399/", "method": "GET"})
print(f"   GET: {str(r.get('error'))[:90]}")
r = p.call("web_search", {"query": "dhole", "max_results": 0})
print(f"   search0: {str(r.get('summary'))[:110]}")
r = p.call("feed_read", {"feeds": ["http://127.0.0.1:8399/feed.xml"]})
print(f"   feed: {str(r.get('error'))[:110]}")

p.pause(6)
p.set_scenario("S6-断后链可续")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/work/1"})
print(f"   status={r.get('status')}（断后=200）")
print("P09 DONE")
