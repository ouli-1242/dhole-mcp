"""P5 复验（真重启后线上重放）：V1 frame 具名 / V2 重铸注记 / V3 上传寿命。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

OUT = Path(__file__).resolve().parent.parent / "sessions" / "verify-r8.log"
lines = []


def emit(s=""):
    print(s)
    lines.append(s)


fdir = Path(__file__).resolve().parent.parent / "sessions" / "files"
up = str(fdir / "network-audit.txt")

p = PersonaSim(90, "verify-r8")
p.call("cred_defaults", {"set": {"allow_private": True}})

emit("== V1a R08-001: 相对 URL 前缀 → 具名 frame 错误 ==")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/frame",
                       "actions": [{"fill": {"selector": "#note", "text": "x", "frame": "/frame/inner"}}],
                       "timeout": 45000})
err = ""
for a in (r.get("metadata") or {}).get("actions") or []:
    if not a.get("ok"):
        err = str(a.get("error") or "")
emit("   错误: " + err[:260])
emit("   判定: " + ("PASS" if ("frame" in err and "absolute URL prefix" in err) else "FAIL"))

emit("")
emit("== V1b R08-005: 错名 frame → 0 <iframe> 具名 + 在场清单 ==")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/frame",
                       "actions": [{"fill": {"selector": "#note", "text": "x", "frame": "#nosuch"}}],
                       "timeout": 45000})
err = ""
for a in (r.get("metadata") or {}).get("actions") or []:
    if not a.get("ok"):
        err = str(a.get("error") or "")
emit("   错误: " + err[:280])
emit("   判定: " + ("PASS" if ("matched 0 <iframe>" in err and "app" in err) else "FAIL"))

emit("")
emit("== V2 R08-003: 重铸同名 → register_overwritten 注记 ==")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/challenge/text",
                       "actions": [{"read": {"selector": "#challenge", "as": "dup"}},
                                    {"read": {"selector": "h1", "as": "dup"}}],
                       "timeout": 45000})
rows = (r.get("metadata") or {}).get("actions") or []
note = str(rows[1].get("register_overwritten") or "") if len(rows) > 1 else ""
emit("   注记: " + note[:200])
emit("   判定: " + ("PASS" if ("'dup'" in note and "replaced" in note) else "FAIL"))

emit("")
emit("== V3 R08-002: upload 行带寿命句 ==")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/upload",
                       "actions": [{"upload": {"selector": "#doc", "files": [up]}}],
                       "timeout": 45000})
row = ((r.get("metadata") or {}).get("actions") or [{}])[0]
life = str(row.get("lifetime") or "")
emit("   lifetime: " + life[:160])
emit("   判定: " + ("PASS" if ("page instance" in life and "same call" in life) else "FAIL"))

OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
emit("")
emit("VERIFY DONE -> " + str(OUT))
