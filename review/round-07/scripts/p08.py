"""P08 戚顾 · 平台工程 L2 —— sys_state show= 键全查。"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim
p = PersonaSim(8, "戚顾·平台")
p.call("cred_defaults", {"set": {"allow_private": True}})
for key in ("cache", "engine_state", "sessions", "cred_defaults"):
    p.set_scenario(f"show={key}")
    r = p.call("sys_state", {"show": [key]})
    err = r.get("error")
    md = r.get("metadata") or {}
    print(f"   {key}: err={str(err)[:60] or 'NONE'} | keys={[k for k in (md.keys() if isinstance(md, dict) else [])][:6]}")
    p.pause(4)
p.set_scenario("S2-未知键的名单句")
r = p.call("sys_state", {"show": ["bogus"]})
print("   未知键:", str(r.get("error"))[:150])
print("P08 DONE")
