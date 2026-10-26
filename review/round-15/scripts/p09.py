"""P09 花婶 · 园艺师 L1 —— 具名 session 的表单状态：不 pin 丢掉 / pin 留住。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(9, "花婶·园艺师")
p.call("cred_defaults", {"set": {"allow_private": True}})

READ = [{"read": {"selector": "input[name=username]", "property": "value"}}]

p.set_scenario("S1-session 填名字（不 pin）")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/login", "session_id": "gardenA",
                       "timeout": 45000,
                       "actions": [{"fill": {"selector": "input[name=username]", "text": "花婶"}}] + READ})
acts = (r.get("metadata") or {}).get("actions") or []
print(f"   fill={acts[0].get('ok') if acts else None} read={acts[-1].get('read') if acts else None!r}")

p.pause(3)
p.set_scenario("S2-同 session 再来一次（不 pin，应重导航）")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/login", "session_id": "gardenA",
                       "timeout": 45000, "actions": READ})
acts = (r.get("metadata") or {}).get("actions") or []
print(f"   pin_hit={'pin hit' in str(r.get('summary')).lower()} "
      f"read={acts[-1].get('read') if acts else None!r}")

p.pause(3)
p.set_scenario("S3-换 session 填值并 pin")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/login", "session_id": "gardenB",
                       "pin": True, "timeout": 45000,
                       "actions": [{"fill": {"selector": "input[name=username]", "text": "月季"}}] + READ})
acts = (r.get("metadata") or {}).get("actions") or []
print(f"   fill={acts[0].get('ok') if acts else None} read={acts[-1].get('read') if acts else None!r}")

p.pause(3)
p.set_scenario("S4-同 session pin 回读（应仍月季）")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/login", "session_id": "gardenB",
                       "pin": True, "timeout": 45000, "actions": READ})
acts = (r.get("metadata") or {}).get("actions") or []
print(f"   pin_hit={'pin hit' in str(r.get('summary')).lower()} "
      f"read={acts[-1].get('read') if acts else None!r}")

p.pause(4)
p.set_scenario("S5-state 收尾")
r = p.call("sys_state", {})
print(f"   message: {str(r.get('message'))[:180]}")
print("P09 DONE")
