"""P03 阮鸿飞5 · 地产经纪 L1 —— web_request POST 流（写闸→confirm→会话）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(3, "阮鸿飞5·地产经纪")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-POST 无 confirm(写闸)")
r = p.call("web_request", {"url": "http://127.0.0.1:8399/login", "method": "POST",
                           "body": {"username": "dichan", "password": "pw1"}, "session_id": "s3r"})
err = str(r.get("error") or "")
print(f"   err: {err[:180]}")

p.pause(6)
p.set_scenario("S2-POST confirm(真提交)")
kw = {"url": "http://127.0.0.1:8399/login", "method": "POST",
      "body": {"username": "dichan", "password": "pw1"}, "session_id": "s3r"}
if "confirm" in err.lower():
    kw["confirm"] = True
r = p.call("web_request", kw)
print(f"   ok: {not r.get('error')} | status: {r.get('status')} | summary: {str(r.get('summary'))[:130]}")

p.pause(6)
p.set_scenario("S3-会话复查(/account)")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/account", "session_id": "s3r",
                       "actions": [{"read": {"selector": "h1"}}], "timeout": 45000})
got = ""
for a in (r.get("metadata") or {}).get("actions") or []:
    if str(a.get("action", "")).startswith("read"):
        got = str(a.get("read") or "")
print(f"   /account h1: {got[:30]!r}")
print("P03 DONE")
