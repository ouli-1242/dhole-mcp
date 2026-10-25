"""P05 云苏2 · 网络工程师 L2 —— 上传三跳确认闸 + received 落点 + dropdown select。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

fdir = Path(__file__).resolve().parent.parent / "sessions" / "files"
fdir.mkdir(parents=True, exist_ok=True)
(fdir / "network-audit.txt").write_text("interface audit 2026-10-26\n", encoding="utf-8")

p = PersonaSim(5, "云苏2·网络工程师")
p.call("cred_defaults", {"set": {"allow_private": True}})

up = str(fdir / "network-audit.txt")

p.set_scenario("S1-上传(闸三跳)")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/upload",
                       "actions": [{"upload": {"selector": "#doc", "files": [up]}}],
                       "timeout": 45000})
print("   装文件 err:", str(r.get("error") or "")[:80])
r2 = p.call("web_act", {"url": "http://127.0.0.1:8399/upload",
                        "actions": [{"click": {"selector": "#send"}}],
                        "timeout": 45000})
err = str(r2.get("error") or "")
print("   无 confirm 提交: err=", err[:150], "-> 闸:", "拒" if "confirm" in err.lower() else "?")
p.pause(4)
r3 = p.call("web_act", {"url": "http://127.0.0.1:8399/upload",
                        "actions": [{"click": {"selector": "#send", "confirm": True}},
                                     {"read": {"selector": "#receipt"}}],
                        "timeout": 45000})
got = ""
for s in (r3.get("steps") or []):
    if str(s.get("action")) == "read":
        got = str(s.get("text") or s.get("detail") or "")
print(f"   confirm 后 receipt: {got[:60]!r} (期望 received network-audit.txt (N bytes))")

p.pause(6)
p.set_scenario("S2-dropdown select + select 对照组")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/dropdown",
                       "actions": [{"select": {"selector": "#country", "option": "CN"}},
                                    {"fill": {"selector": "#note", "text": "骨干网巡检"}},
                                    {"click": {"selector": "#save"}},
                                    {"read": {"selector": "#saved"}}],
                       "timeout": 45000})
got = ""
for s in (r.get("steps") or []):
    if str(s.get("action")) == "read":
        got = str(s.get("text") or s.get("detail") or "")
print(f"   select 链: err={str(r.get('error') or '')[:60]} | #saved={got[:40]!r}")
p.pause(4)
r2 = p.call("web_act", {"url": "http://127.0.0.1:8399/dropdown",
                        "actions": [{"fill": {"selector": "#not-a-select", "text": "x"}}],
                        "timeout": 45000})
err = str(r2.get("error") or "")
print("   对 fill 门指 select 候选:", err[:150])
print("P05 DONE")
