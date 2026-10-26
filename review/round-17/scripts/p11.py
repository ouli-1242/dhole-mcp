"""P11 补条：上传中文名文件 + 动作级 confirm 真提交 + 读状态。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

F = Path(__file__).resolve().parent.parent / "sessions" / "files"
up = F / "审计清单-中文.txt"

p = PersonaSim(11, "补条·上传确认")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-上传 + click confirm 真提交")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/upload", "timeout": 45000,
                       "actions": [
                           {"upload": {"selector": "input[type=file]", "files": [str(up)]}},
                           {"click": {"selector": "button[type=submit]", "confirm": True}},
                           {"read": {"selector": "#status"}},
                       ]})
acts = (r.get("metadata") or {}).get("actions") or []
for a in acts:
    print(f"   {a.get('action')}: ok={a.get('ok')} err={str(a.get('error'))[:100]} "
          f"read={str(a.get('read'))[:80]!r}")

p.pause(3)
p.set_scenario("S2-上传后再读页面")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/upload", "timeout": 45000,
                       "actions": [{"read": {"selector": "#status"}}]})
acts = (r.get("metadata") or {}).get("actions") or []
print(f"   read={str(acts[-1].get('read')) if acts else None!r}")
print("P11 DONE")
