"""P04 黎照2 · 移动端开发 L2 —— iframe 表单组合(frame= 三种指法 + 误打提示回归)。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(4, "黎照2·移动端开发")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-frame=name 正形")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/frame",
                       "actions": [{"fill": {"selector": "#note", "text": "来自外层的问候", "frame": "app"}},
                                    {"click": {"selector": "#save", "frame": "app"}},
                                    {"read": {"selector": "#saved", "frame": "app"}}],
                       "timeout": 60000})
got = ""
for s in (r.get("steps") or []):
    if str(s.get("action")) == "read":
        got = str(s.get("text") or s.get("detail") or "")
print(f"   name 指法: err={str(r.get('error') or '')[:60]} | #saved={got[:50]!r} (期望 SAVED:来自外层的问候)")

p.pause(6)
p.set_scenario("S2-frame=CSS 指法(#app)")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/frame",
                       "actions": [{"fill": {"selector": "#note", "text": "css 指法", "frame": "#app"}},
                                    {"click": {"selector": "#save", "frame": "#app"}},
                                    {"read": {"selector": "#saved", "frame": "#app"}}],
                       "timeout": 60000})
got = ""
for s in (r.get("steps") or []):
    if str(s.get("action")) == "read":
        got = str(s.get("text") or s.get("detail") or "")
print(f"   #app 指法: err={str(r.get('error') or '')[:60]} | #saved={got[:50]!r}")

p.pause(6)
p.set_scenario("S3-frame=URL 前缀指法")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/frame",
                       "actions": [{"fill": {"selector": "#note", "text": "url 指法", "frame": "/frame/inner"}},
                                    {"read": {"selector": "#note", "property": "value", "frame": "/frame/inner"}}],
                       "timeout": 60000})
got = ""
for s in (r.get("steps") or []):
    if str(s.get("action")) == "read":
        got = str(s.get("text") or s.get("detail") or "")
print(f"   url 指法: err={str(r.get('error') or '')[:60]} | value={got[:40]!r}")

p.pause(6)
p.set_scenario("S4-误打内层不带 frame(R04-F1 回归)")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/frame",
                       "actions": [{"fill": {"selector": "#note", "text": "x"}}],
                       "timeout": 45000})
err = str(r.get("error") or "")
print("   误打 err:", err[:230])
print("   iframe 出路在场:", ("iframe" in err and "frame" in err))
print("P04 DONE")
