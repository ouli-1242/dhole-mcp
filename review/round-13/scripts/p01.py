"""P01 商执辔 · 地铁司机 L1 —— 链①guide→read /dropdown→select→state 复盘。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(1, "商执辔·地铁司机")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-guide 引路")
p.call("sys_guide", {"topic": "start"})

p.pause(6)
p.set_scenario("S2-read /dropdown")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/dropdown"})
print(f"   status={r.get('status')} chars={r.get('total_extracted_chars')}")

p.pause(6)
p.set_scenario("S3-act select（value=CN, label=中国 双匹配路之一）")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/dropdown", "timeout": 45000,
                       "actions": [
                           {"select": {"selector": "#country", "option": "CN"}},
                           {"read": {"selector": "#country", "property": "value"}},
                       ]})
acts = (r.get("metadata") or {}).get("actions") or []
for a in acts:
    print(f"   {a.get('action')}: ok={a.get('ok')} read={str(a.get('read'))[:30]!r}")
print(f"   summary 尾段: {str(r.get('summary'))[-120:]}")

p.pause(6)
p.set_scenario("S4-state 复盘")
r = p.call("sys_state", {})
print(f"   message: {str(r.get('message'))[:130]}")
print("P01 DONE")
