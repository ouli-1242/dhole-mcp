"""P07 终：读挑战页真实结构 -> 用真实选择器走最后一链。"""
import sys, json, re
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from driver import PersonaSim, ROOT
p = PersonaSim(7, "阿凯·安全")
p.seq = 13
p.set_scenario("S3f-真实选择器")
r = p.call("web_read", {"url": ROOT + "/challenge/text", "css_selector": "form, input, button, .challenge, #challenge", "max_content_chars": 3000})
p.observe("structure", r, "content", "content_ok")
p.pause(8)
r = p.call("web_act", {"url": ROOT + "/challenge/text", "timeout": 60000,
                       "actions": [
                           {"read": {"selector": "code, .challenge-code, [data-code]", "as": "code"}},
                           {"fill": {"selector": "#answer", "text": "{code}"}},
                           {"click": {"selector": "button[type=submit], #challenge-submit", "confirm": True}},
                       ]})
p.observe("chain real", r, "content_ok", "content")
for a in (r.get("metadata") or {}).get("actions") or []:
    print("   act:", json.dumps(a, ensure_ascii=False)[:280])
print("P07F DONE")
