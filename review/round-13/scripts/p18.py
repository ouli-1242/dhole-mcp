"""P18 覃九鼎补条 · checkout submit 正形 confirm 真提交 → 订单日志 +1。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(18, "覃九鼎·拍卖师补条")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-read 看单")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/checkout"})
print(f"   status={r.get('status')} chars={r.get('total_extracted_chars')}")

p.pause(6)
p.set_scenario("S2-fill qty=2 + submit confirm→真提交")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/checkout", "timeout": 60000,
                       "actions": [
                           {"fill": {"selector": "#qty", "text": "2"}},
                           {"submit": {"selector": "#place-order", "confirm": True}},
                           {"read": {"selector": "h1"}},
                       ]})
acts = (r.get("metadata") or {}).get("actions") or []
for a in acts:
    print(f"   {a.get('action')}: ok={a.get('ok')} read={str(a.get('read'))[:50]!r} "
          f"err={str(a.get('error'))[:80]}")
print(f"   summary 尾段: {str(r.get('summary'))[-120:]}")

p.pause(6)
p.set_scenario("S3-state 复盘")
r = p.call("sys_state", {})
print(f"   message: {str(r.get('message'))[:130]}")
print("P18 DONE")
