"""P07 追加：抄写挑战的会话驻留解法（无 url 续操作）。"""
import sys, re, json
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from driver import PersonaSim, ROOT
p = PersonaSim(7, "阿凯·安全")
p.seq = 8
p.set_scenario("S3b-驻留会话解法")
r = p.call("web_act", {"url": ROOT + "/challenge/text", "timeout": 60000,
                       "actions": [{"snapshot": True}]})
p.observe("snapshot", r, "content_ok")
snap_text = json.dumps(r.get("content") or r.get("metadata") or r, ensure_ascii=False)
m = re.search(r"[A-Za-z0-9]{4,8}", snap_text)
print("      snapshot excerpt:", snap_text[:400])
p.pause(8)
r2 = p.call("web_act", {"timeout": 60000, "actions": [
    {"read": {"selector": "#challenge-text", "as": "code"}},
    {"fill": {"selector": "#answer", "text": "占位"}},
    {"click": {"selector": "#submit", "confirm": True}},
]})
p.observe("chain no-url", r2, "content_ok", "content")
for a in (r2.get("metadata") or {}).get("actions") or []:
    print("   act:", json.dumps(a, ensure_ascii=False)[:220])
print("P07B DONE")
