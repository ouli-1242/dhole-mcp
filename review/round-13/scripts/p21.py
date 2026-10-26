"""P21 P3 对抗探针 · css 缓存/新鲜对照 · tab 寿命（含 pin）· dialog answer 形状。"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(21, "P3探针·审查")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("A1-css 0匹配 缓存路（复现）")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/article",
                        "css_selector": "#no-such"})
s = str(r.get('summary') or '')
print(f"   cached={r.get('cached')} chars={r.get('total_extracted_chars')} "
      f"note={'NOT applied' in s}")

p.pause(4)
p.set_scenario("A2-css 0匹配 新鲜路（?cb=）")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/article?cb=r13a",
                        "css_selector": "#no-such"})
s = str(r.get('summary') or '')
print(f"   cached={r.get('cached')} chars={r.get('total_extracted_chars')} "
      f"note={'NOT applied' in s}")
print(f"   tail: {s[-260:]}")

p.pause(4)
p.set_scenario("A3-css 命中选择器 缓存路（是否收窄）")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/article",
                        "css_selector": "h1"})
s = str(r.get('summary') or '')
print(f"   cached={r.get('cached')} chars={r.get('total_extracted_chars')} "
      f"css_word={'css_selector' in s} note={'NOT applied' in s}")
print(f"   content: {str(r.get('content'))[:120]}")

p.pause(6)
p.set_scenario("B1-tab 同调用内两枚")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/", "timeout": 45000,
                       "actions": [
                           {"tab": {"open": "http://127.0.0.1:8399/article"}},
                           {"tab": "list"},
                       ]})
for a in (r.get('metadata') or {}).get('actions') or []:
    print(f"   {a.get('action')}: ok={a.get('ok')} tabs={a.get('tabs')} url={a.get('url')}")

p.pause(4)
p.set_scenario("B2-tab 不带 pin 的下一调用")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/", "timeout": 45000,
                       "actions": [{"tab": "list"}]})
for a in (r.get('metadata') or {}).get('actions') or []:
    print(f"   {a.get('action')}: ok={a.get('ok')} tabs={a.get('tabs')}")

p.pause(6)
p.set_scenario("B3-tab 带 pin 跨调用")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/", "pin": True, "timeout": 45000,
                       "actions": [
                           {"tab": {"open": "http://127.0.0.1:8399/article"}},
                           {"tab": "list"},
                       ]})
for a in (r.get('metadata') or {}).get('actions') or []:
    print(f"   call1 {a.get('action')}: ok={a.get('ok')} tabs={a.get('tabs')}")
print(f"   pin sentence: {'pin' in str(r.get('summary')).lower()}")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/", "pin": True, "timeout": 45000,
                       "actions": [{"tab": "list"}]})
for a in (r.get('metadata') or {}).get('actions') or []:
    print(f"   call2 {a.get('action')}: ok={a.get('ok')} tabs={a.get('tabs')}")
print(f"   pin hit: {'pin hit' in str(r.get('summary')).lower()}")

p.pause(6)
p.set_scenario("C1-dialog answer=accept 对 prompt（错用但被收？）")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/dialog", "timeout": 45000,
                       "actions": [
                           {"dialog": {"answer": "accept"}},
                           {"click": {"selector": "#namebtn"}},
                           {"read": {"selector": "#status"}},
                       ]})
for a in (r.get('metadata') or {}).get('actions') or []:
    print(f"   {a.get('action')}: ok={a.get('ok')} read={str(a.get('read'))[:30]!r} seen={str(a.get('seen'))[:120]}")
print(f"   error: {str(r.get('error'))[:200]}")
print("P21 DONE")
