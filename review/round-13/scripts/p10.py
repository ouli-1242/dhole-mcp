"""P10 覃九鼎 · 拍卖师 L2 —— 链⑩checkout submit 闸（pending 分家）→confirm 真提交→state。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(10, "覃九鼎·拍卖师")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-read /checkout（看单）")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/checkout"})
print(f"   status={r.get('status')} chars={r.get('total_extracted_chars')}")

p.pause(6)
p.set_scenario("S2-act 填 qty=3 + submit（无 confirm → 闸）")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/checkout", "timeout": 60000,
                       "actions": [
                           {"fill": {"selector": "#qty", "text": "3"}},
                           {"submit": {"selector": "#place-order"}},
                       ]})
acts = (r.get("metadata") or {}).get("actions") or []
for a in acts:
    print(f"   {a.get('action')}: ok={a.get('ok')} pending={a.get('pending_approval')} not_submitted={a.get('not_submitted')}")
print(f"   content_ok={r.get('content_ok')} error={str(r.get('error'))[:80]}")
print(f"   next_action: {str(r.get('next_action'))[:170]}")

p.pause(6)
p.set_scenario("S3-同链 + confirm 真提交")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/checkout", "timeout": 60000,
                       "confirm": True,
                       "actions": [
                           {"fill": {"selector": "#qty", "text": "3"}},
                           {"submit": {"selector": "#place-order"}},
                           {"read": {"selector": "h1"}},
                       ]})
acts = (r.get("metadata") or {}).get("actions") or []
for a in acts:
    print(f"   {a.get('action')}: ok={a.get('ok')} read={str(a.get('read'))[:30]!r}")

p.pause(6)
p.set_scenario("S4-state 复盘")
r = p.call("sys_state", {})
print(f"   message: {str(r.get('message'))[:130]}")
print("P10 DONE")
