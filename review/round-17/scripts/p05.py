"""P05 何清 · 审计员 L2 —— 中文名文件上传 · 路径纪律。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

F = Path(__file__).resolve().parent.parent / "sessions" / "files"
F.mkdir(parents=True, exist_ok=True)
up = F / "审计清单-中文.txt"
up.write_text("审计项一\n审计项二\n", encoding="utf-8")

p = PersonaSim(5, "何清·审计员")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-上传中文名文件")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/upload", "timeout": 45000,
                       "actions": [
                           {"upload": {"selector": "input[type=file]", "files": [str(up)]}},
                           {"click": {"selector": "button[type=submit]"}},
                           {"read": {"selector": "#status"}},
                       ]})
acts = (r.get("metadata") or {}).get("actions") or []
for a in acts:
    print(f"   {a.get('action')}: ok={a.get('ok')} err={str(a.get('error'))[:90]} "
          f"read={str(a.get('read'))[:60]!r}")

p.pause(3)
p.set_scenario("S2-上传不存在的文件（应在起浏览器前拒）")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/upload", "timeout": 45000,
                       "actions": [{"upload": {"selector": "input[type=file]",
                                               "files": [str(F / "不存在.txt")]}}]})
print(f"   err={str(r.get('error'))[:160]}")
print("P05 DONE")
