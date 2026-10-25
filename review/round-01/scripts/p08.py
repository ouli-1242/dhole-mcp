"""P08 Devin · 外包开发 · L2 —— round-01 真实模拟。"""
import sys, json
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from driver import PersonaSim, ROOT

p = PersonaSim(8, "Devin·外包")
p.seq = 0

# S1 登录链（回归#1）
p.set_scenario("S1-登录链")
r = p.call("web_request", {"url": ROOT + "/login", "method": "POST",
                           "body": {"username": "devin", "password": "pw-devin-1"},
                           "confirm": True, "session_id": "devin-1"})
p.observe("login", r, "status", "content_ok", "metadata.redirect",
          "session_id", "session_cookie_names")
p.pause(6)
r = p.call("web_read", {"url": ROOT + "/account", "session_id": "devin-1"})
p.observe("account", r, "content_ok", "total_extracted_chars", "content",
          "diagnosis.code")
p.pause(30, "场景间隔")

# S2 上传链（回归#5）
p.set_scenario("S2-上传链")
r = p.call("web_act", {"url": ROOT + "/upload", "timeout": 60000,
                       "actions": [
                           {"upload": {"selector": "#doc", "files": ["tests/sample.docx"]}},
                           {"click": "#send"},
                       ]})
for a in (r.get("metadata") or {}).get("actions") or []:
    print("   act:", json.dumps(a, ensure_ascii=False)[:200])
pend = any(a.get("pending_approval") for a in (r.get("metadata") or {}).get("actions") or [])
print("      paused:", pend)
if pend:
    p.pause(6)
    r = p.call("web_act", {"url": ROOT + "/upload", "timeout": 60000,
                           "actions": [
                               {"upload": {"selector": "#doc", "files": ["tests/sample.docx"]}},
                               {"click": {"selector": "#send", "confirm": True}},
                           ]})
    p.observe("receipt", r, "content", "total_extracted_chars")
p.pause(30, "场景间隔")

# S3 hoverdrag
p.set_scenario("S3-悬停拖拽")
r = p.call("web_act", {"url": ROOT + "/hoverdrag", "timeout": 60000,
                       "actions": [
                           {"hover": "#menu"},
                           {"read": {"selector": "#status", "as": "st1"}},
                       ]})
p.observe("hover", r, "content_ok", "content")
for a in (r.get("metadata") or {}).get("actions") or []:
    print("   act:", json.dumps(a, ensure_ascii=False)[:200])
p.pause(8)
r = p.call("web_act", {"url": ROOT + "/hoverdrag", "timeout": 60000,
                       "actions": [
                           {"drag": {"from": "#knob", "to": "#drop"}},
                           {"read": {"selector": "#status", "as": "st2"}},
                       ]})
p.observe("drag", r, "content_ok", "content")
for a in (r.get("metadata") or {}).get("actions") or []:
    print("   act:", json.dumps(a, ensure_ascii=False)[:200])

# S4 captcha 读取
p.pause(30, "场景间隔")
p.set_scenario("S4-验证码")
r = p.call("web_read", {"url": ROOT + "/captcha", "max_content_chars": 1500})
p.observe("captcha page", r, "content_ok", "total_extracted_chars", "content")
r = p.call("web_read", {"url": ROOT + "/captcha.png"})
p.observe("captcha png", r, "content_ok", "total_extracted_chars", "content",
          "error", "page_type")

print("P08 DONE")
