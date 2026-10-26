"""P04b 补条（独立号）：iframe 内操作正形——frame= 在每个 action 的 target 里。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(14, "顾北望·天文台操作员(补)")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("T1-iframe 内填+保存（frame= 在 action 里）")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/frame", "timeout": 60000,
                       "actions": [
                           {"fill": {"selector": "#note", "text": "北落师门",
                                     "frame": "app"}},
                           {"click": {"selector": "#save", "frame": "app"}},
                       ]})
acts = (r.get("metadata") or {}).get("actions") or []
for a in acts:
    print(f"   {a.get('action')}: ok={a.get('ok')} err={str(a.get('error'))[:70]}")

p.pause(6)
p.set_scenario("T2-iframe 内读回执")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/frame", "timeout": 60000,
                       "actions": [{"read": {"selector": "#saved", "frame": "app"}}]})
acts = (r.get("metadata") or {}).get("actions") or []
for a in acts:
    print(f"   {a.get('action')}: ok={a.get('ok')} read={str(a.get('read'))[:40]!r}")

p.pause(6)
p.set_scenario("T3-frame 错名（r08 家产：matched 0 + 在场清单）")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/frame", "timeout": 60000,
                       "actions": [{"read": {"selector": "#saved", "frame": "#no-such"}}]})
print(f"   err: {str(r.get('error'))[:230]}")

p.pause(6)
p.set_scenario("T4-recall iframe 词（0 也如实）")
r = p.call("web_recall", {"query": "北落师门", "limit": 5})
print(f"   results={len(r.get('results') or [])}")
print("P04b DONE")
