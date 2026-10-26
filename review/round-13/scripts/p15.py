"""P15 商无咎补条 · dialog prompt 正形（answer+prompt_text）→#status=NAMED:*。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(15, "商无咎·法医补条")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-prompt 正形 arm→click→status")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/dialog", "timeout": 45000,
                       "actions": [
                           {"dialog": {"answer": "accept", "prompt_text": "商无咎"}},
                           {"click": {"selector": "#namebtn"}},
                           {"read": {"selector": "#status"}},
                       ]})
acts = (r.get("metadata") or {}).get("actions") or []
for a in acts:
    print(f"   {a.get('action')}: ok={a.get('ok')} read={str(a.get('read'))[:40]!r} "
          f"seen={str(a.get('seen'))[:140]}")
print("P15 DONE")
