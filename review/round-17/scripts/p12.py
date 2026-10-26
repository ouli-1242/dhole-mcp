"""P12 补条：上传确认后读 #receipt（靶站真实 id）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

F = Path(__file__).resolve().parent.parent / "sessions" / "files"
up = F / "审计清单-中文.txt"
p = PersonaSim(12, "补条·上传回执")
p.call("cred_defaults", {"set": {"allow_private": True}})
r = p.call("web_act", {"url": "http://127.0.0.1:8399/upload", "timeout": 45000,
                       "actions": [
                           {"upload": {"selector": "input[type=file]", "files": [str(up)]}},
                           {"click": {"selector": "button[type=submit]", "confirm": True}},
                           {"read": {"selector": "#receipt"}},
                       ]})
acts = (r.get("metadata") or {}).get("actions") or []
for a in acts:
    print(f"   {a.get('action')}: ok={a.get('ok')} err={str(a.get('error'))[:100]} read={str(a.get('read'))[:120]!r}")
print("P12 DONE")
