"""P06 乌恒 · 船闸管理员 L2 —— 链⑥request 写闸→captcha 三试（pin 挂页）→pin 命中回读。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(6, "乌恒·船闸管理员")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-request POST 写闸（无 confirm）")
r = p.call("web_request", {"url": "http://127.0.0.1:8399/login", "method": "POST",
                           "body": {"username": "chuozha", "password": "pw6"},
                           "session_id": "p06a"})
print(f"   next_action: {str(r.get('next_action'))[:140]}")

p.pause(6)
p.set_scenario("S2-confirm 真提交")
r = p.call("web_request", {"url": "http://127.0.0.1:8399/login", "method": "POST",
                           "body": {"username": "chuozha", "password": "pw6"},
                           "session_id": "p06a", "confirm": True})
print(f"   status={r.get('status')} | {str(r.get('summary'))[:100]}")

p.pause(6)
p.set_scenario("S3-captcha 第一试（pin 挂页, 读题→填→交）")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/challenge/text",
                       "session_id": "p06a", "pin": True, "timeout": 60000,
                       "actions": [
                           {"read": {"selector": "#challenge", "as": "code"}},
                           {"fill": {"selector": "#answer", "text": "{=code}"}},
                           {"click": {"selector": "#challenge-btn"}},
                           {"read": {"selector": "#status"}},
                       ]})
acts = (r.get("metadata") or {}).get("actions") or []
for a in acts:
    print(f"   {a.get('action')}: ok={a.get('ok')} read={str(a.get('read'))[:40]!r}")
print(f"   summary 尾段: {str(r.get('summary'))[-150:]}")

p.pause(6)
p.set_scenario("S4-pin 命中回读（r12 必测1：同 url/session/pin 站原页）")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/challenge/text",
                       "session_id": "p06a", "pin": True, "timeout": 60000,
                       "actions": [{"read": {"selector": "#challenge"}}]})
s = r.get("summary") or ""
acts = (r.get("metadata") or {}).get("actions") or []
q = next((str(a.get("read")) for a in acts if a.get("read")), "")
print(f"   pin hit 句在场={'pin hit' in s} | 页上题目: {q[:50]!r}")
print("P06 DONE")
