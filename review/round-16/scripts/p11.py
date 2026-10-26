"""P11 补条：web_act 的非 ASCII 面（多语言页读 + 阿语填表）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(11, "补条·非ASCII交互")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-多语言页按 selector 读")
r = p.call("web_act", {"url": "http://127.0.0.1:8398/mixed.html?cb=act",
                       "timeout": 45000,
                       "actions": [{"read": {"selector": "p"}}]})
acts = (r.get("metadata") or {}).get("actions") or []
print(f"   ok={acts[0].get('ok') if acts else None} read={str(acts[0].get('read'))[:90]!r}")

p.pause(3)
p.set_scenario("S2-阿语填表并读回")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/login", "timeout": 45000,
                       "actions": [
                           {"fill": {"selector": "input[name=username]", "text": "ليلى"}},
                           {"read": {"selector": "input[name=username]", "property": "value"}},
                       ]})
acts = (r.get("metadata") or {}).get("actions") or []
print(f"   fill={acts[0].get('ok') if acts else None} read={str(acts[-1].get('read')) if acts else None!r}")
print("P11 DONE")
