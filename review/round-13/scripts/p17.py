"""P17 乌恒补条 · captcha 同链 read→fill→click confirm 真提交 → 抄写正确。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(17, "乌恒·船闸管理员补条")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-captcha 同链读填交（action 级 confirm）")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/challenge/text", "timeout": 60000,
                       "actions": [
                           {"read": {"selector": "#challenge", "as": "code"}},
                           {"fill": {"selector": "#answer", "text": "{=code}"}},
                           {"click": {"selector": "#challenge-btn", "confirm": True}},
                           {"read": {"selector": "h1"}},
                       ]})
acts = (r.get("metadata") or {}).get("actions") or []
for a in acts:
    print(f"   {a.get('action')}: ok={a.get('ok')} read={str(a.get('read'))[:50]!r} "
          f"err={str(a.get('error'))[:80]}")
print("P17 DONE")
