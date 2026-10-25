"""p01b 补全：同链领取奖励带 confirm:true（闸放行 → 抄写正确）。"""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(11, "p01b-补全")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S2b-同链+confirm(完整领取)")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/challenge/text",
                       "actions": [{"read": {"selector": "#challenge", "as": "code"}},
                                    {"fill": {"selector": "#answer", "text": "{=code}"}},
                                    {"click": {"selector": "#challenge-btn", "confirm": True}},
                                    {"read": {"selector": "h1"}}],
                       "timeout": 60000})
for a in (r.get("metadata") or {}).get("actions") or []:
    print("   ", json.dumps(a, ensure_ascii=False)[:150])
print("P01B DONE")
