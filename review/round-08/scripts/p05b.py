"""p05b 补全：上传+提交同一发（对照跨调用 0 bytes 现场 → received <文件名>）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

fdir = Path(__file__).resolve().parent.parent / "sessions" / "files"
up = str(fdir / "network-audit.txt")

p = PersonaSim(12, "p05b-补全")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1b-上传+提交同链+confirm")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/upload",
                       "actions": [{"upload": {"selector": "#doc", "files": [up]}},
                                    {"click": {"selector": "#send", "confirm": True}},
                                    {"read": {"selector": "#receipt"}}],
                       "timeout": 60000})
for a in (r.get("metadata") or {}).get("actions") or []:
    print("   ", str(a.get("action"))[:40], "ok=" + str(a.get("ok")), str(a.get("read") or a.get("uploaded") or "")[:50])
print("P05B DONE")
import json
