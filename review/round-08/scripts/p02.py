"""P02 慕容雪2 · 直播运营 L3 猎手 —— 弹窗与页签动作面逐个真触发。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(2, "慕容雪2·直播运营")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-alert 接受")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/dialog",
                       "actions": [{"dialog": "accept"},
                                    {"click": {"selector": "#alertbtn"}},
                                    {"read": {"selector": "#status"}}],
                       "timeout": 45000})
dl = [s for s in (r.get("steps") or []) if "dialog" in str(s.get("action"))]
print("   dialog 账行:", str(dl[0])[:160] if dl else "无")
print("   落页:", str(r.get("summary"))[:90])

p.pause(6)
p.set_scenario("S2-confirm 拒/收两态")
for ans, expect in (("dismiss", "CANCELLED"), ("accept", "DELETED")):
    r = p.call("web_act", {"url": "http://127.0.0.1:8399/dialog",
                           "actions": [{"dialog": ans},
                                        {"click": {"selector": "#delbtn"}},
                                        {"read": {"selector": "#status"}}],
                           "timeout": 45000})
    got = ""
    for s in (r.get("steps") or []):
        if str(s.get("action")) == "read":
            got = str(s.get("text") or s.get("detail") or "")
    print(f"   {ans}: 期望 {expect} | 实得 {got[:40]!r} | err={str(r.get('error') or '')[:40]}")
    p.pause(4)

p.set_scenario("S3-prompt 带文本")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/dialog",
                       "actions": [{"dialog": {"answer": "accept", "prompt_text": "苗苗"}},
                                    {"click": {"selector": "#namebtn"}},
                                    {"read": {"selector": "#status"}}],
                       "timeout": 45000})
got = ""
for s in (r.get("steps") or []):
    if str(s.get("action")) == "read":
        got = str(s.get("text") or s.get("detail") or "")
print(f"   prompt: 期望 NAMED:苗苗 | 实得 {got[:40]!r}")

p.pause(6)
p.set_scenario("S4-未武装弹窗(默认 dismiss 的账)")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/dialog",
                       "actions": [{"click": {"selector": "#delbtn"}},
                                    {"read": {"selector": "#status"}}],
                       "timeout": 45000})
got = ""
for s in (r.get("steps") or []):
    if str(s.get("action")) == "read":
        got = str(s.get("text") or s.get("detail") or "")
dl = [s for s in (r.get("steps") or []) if "dialog" in str(s.get("action"))]
print(f"   未武装: status={got[:30]!r} (期望 CANCELLED=auto-dismiss) | dialog 账行: {str(dl[0])[:140] if dl else '无'}")

p.pause(6)
p.set_scenario("S5-页签动作(new/list/switch/close)")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/collections",
                       "actions": [{"tab": {"open": "http://127.0.0.1:8399/about"}},
                                    {"tab": "list"},
                                    {"tab": 0},
                                    {"read": {"selector": "h1"}}],
                       "timeout": 60000})
print("   tab 链 err:", str(r.get("error") or "")[:80])
for s in (r.get("steps") or []):
    if "tab" in str(s.get("action")):
        print("     -", str(s)[:150])
r2 = p.call("web_act", {"url": "http://127.0.0.1:8399/collections",
                        "actions": [{"tab": "close"}], "timeout": 45000})
print("   最后一枚拒关:", str(r2.get("error") or "")[:120])
print("P02 DONE")
