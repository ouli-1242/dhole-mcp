"""P02 焦挺 · 消防员 L2 —— 链②discover→act hover/drag→read 复核改后状态。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(2, "焦挺·消防员")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-discover 地图（/ 为种子）")
r = p.call("web_discover", {"url": "http://127.0.0.1:8399/"})
pages = r.get("pages") or []
print(f"   mapped {len(pages)} rows（地图诚实与首页行数对齐）")

p.pause(6)
p.set_scenario("S2-act hover 展开")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/hoverdrag", "timeout": 45000,
                       "actions": [{"hover": {"selector": "#nav-files"}},
                                   {"read": {"selector": "#status"}}]})
acts = (r.get("metadata") or {}).get("actions") or []
for a in acts:
    print(f"   {a.get('action')}: ok={a.get('ok')} read={str(a.get('read'))[:30]!r}")

p.pause(6)
p.set_scenario("S3-act drag 卡片A→落点B")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/hoverdrag", "timeout": 45000,
                       "actions": [{"drag": {"selector": "#card-a",
                                             "to": {"selector": "#slot-b"}}},
                                   {"read": {"selector": "#status"}}]})
acts = (r.get("metadata") or {}).get("actions") or []
for a in acts:
    print(f"   {a.get('action')}: ok={a.get('ok')} read={str(a.get('read'))[:30]!r}")

p.pause(6)
p.set_scenario("S4-read 复核（页面上看得见的状态说话）")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/hoverdrag", "cache_ttl": 0})
print(f"   status={r.get('status')} chars={r.get('total_extracted_chars')} | {str(r.get('summary'))[:100]}")
print("P02 DONE")
