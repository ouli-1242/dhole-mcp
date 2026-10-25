"""P02 顾晚舟 · QA L2 —— web_act 写路径深水（upload/dialog/iframe/hoverdrag/keyboard）。"""
import sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

HERE = Path(__file__).resolve().parent.parent
p = PersonaSim(2, "顾晚舟·QA")

p.set_scenario("S1-upload 全链")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/upload", "actions": [
    {"upload": {"selector": "#doc", "files": [str(HERE / "sessions" / "files" / "报价说明.pdf")]}},
    {"click": {"selector": "#send"}},
], "timeout": 60000})
p.observe("upload", r, "summary")
for st in ((r.get("metadata") or {}).get("actions") or []):
    print("   step:", st.get("action"), "ok:", st.get("ok"), str(st.get("error"))[:80])
p.pause(8)

p.set_scenario("S2-dialog 三兄弟")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/dialog", "actions": [
    {"click": {"selector": "#alertbtn"}},
    {"click": {"selector": "#namebtn"}},
    {"read": {"selector": "#status", "as": "status"}},
], "timeout": 60000})
p.observe("dialog", r, "summary")
for st in ((r.get("metadata") or {}).get("actions") or []):
    print("   step:", st.get("action"), "ok:", st.get("ok"), str(st.get("error"))[:80])
p.pause(8)

p.set_scenario("S3-iframe 内的表单")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/frame", "actions": [
    {"fill": {"selector": "#note", "text": "iframe 里的字"}},
    {"click": {"selector": "#save"}},
    {"read": {"selector": "#saved", "as": "mark"}},
], "timeout": 60000})
p.observe("iframe", r, "summary")
for st in ((r.get("metadata") or {}).get("actions") or []):
    print("   step:", st.get("action"), "ok:", st.get("ok"), str(st.get("error"))[:80])
p.pause(8)

p.set_scenario("S4-drag 全形(回归 R01-010)+正形")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/hoverdrag", "actions": [
    {"drag": {"from": "#card-a", "to": "#slot-b"}},
], "timeout": 60000})
p.observe("drag-refusal", r, "error")
p.pause(6)
r = p.call("web_act", {"url": "http://127.0.0.1:8399/hoverdrag", "actions": [
    {"drag": {"selector": "#card-a", "to": "#slot-b"}},
], "timeout": 60000})
for st in ((r.get("metadata") or {}).get("actions") or []):
    print("   step:", st.get("action"), "ok:", st.get("ok"))
p.pause(8)

p.set_scenario("S5-keyboard 变卦(提交改回车)")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/login", "actions": [
    {"fill": {"selector": "#username", "text": "qa"}},
    {"fill": {"selector": "#password", "text": "qa"}},
    {"press": {"key": "Enter", "selector": "#password"}},
], "timeout": 60000})
p.observe("keyboard", r, "summary")
for st in ((r.get("metadata") or {}).get("actions") or []):
    print("   step:", st.get("action"), "ok:", st.get("ok"), str(st.get("error"))[:80])
print("P02 DONE")
