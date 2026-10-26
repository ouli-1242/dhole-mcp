"""P03 商无咎 · 法医 L3 猎手 —— 链③dialogs 台账：arm→trigger→读回执对账 + 误序教学。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(3, "商无咎·法医")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-read /dialog 现场")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/dialog"})
print(f"   status={r.get('status')} chars={r.get('total_extracted_chars')}")

p.pause(6)
p.set_scenario("S2-arm confirm→click 删除（台账行 dialog_armed）")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/dialog", "timeout": 45000,
                       "actions": [
                           {"dialog": {"answer": "accept"}},
                           {"click": {"selector": "#delbtn"}},
                           {"read": {"selector": "#status"}},
                       ]})
acts = (r.get("metadata") or {}).get("actions") or []
for a in acts:
    print(f"   {a.get('action')}: ok={a.get('ok')} armed={a.get('dialog_armed')} read={str(a.get('read'))[:25]!r}")

p.pause(6)
p.set_scenario("S3-arm prompt（prompt_text）→click 填名")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/dialog", "timeout": 45000,
                       "actions": [
                           {"dialog": {"answer": "商无咎"}},
                           {"click": {"selector": "#namebtn"}},
                           {"read": {"selector": "#status"}},
                       ]})
acts = (r.get("metadata") or {}).get("actions") or []
for a in acts:
    print(f"   {a.get('action')}: ok={a.get('ok')} armed={a.get('dialog_armed')} read={str(a.get('read'))[:35]!r}")

p.pause(6)
p.set_scenario("S4-误序：无 arm 直接 trigger")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/dialog", "timeout": 45000,
                       "actions": [{"click": {"selector": "#delbtn"}},
                                   {"read": {"selector": "#status"}}]})
acts = (r.get("metadata") or {}).get("actions") or []
for a in acts:
    print(f"   {a.get('action')}: ok={a.get('ok')} err={str(a.get('error'))[:90]} read={str(a.get('read'))[:25]!r}")
na = str(r.get("next_action") or "")
if na:
    print(f"   next_action: {na[:160]}")
print("P03 DONE")
