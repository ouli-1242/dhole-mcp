"""p06b 补全：长链双寄存器带 confirm（完整跑通 + 链尾读结果页）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(13, "p06b-补全")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1b-长链+confirm(双寄存器端到端)")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/challenge/text",
                       "actions": [{"read": {"selector": "#challenge", "as": "code"}},
                                    {"read": {"selector": "h1", "as": "title"}},
                                    {"fill": {"selector": "#answer", "text": "{=code}"}},
                                    {"click": {"selector": "#challenge-btn", "confirm": True}},
                                    {"read": {"selector": "h1"}}],
                       "timeout": 60000})
for a in (r.get("metadata") or {}).get("actions") or []:
    print("   ", str(a.get("action"))[:30], "ok=" + str(a.get("ok")), str(a.get("read") or "")[:40])
print("P06B DONE")
import json
