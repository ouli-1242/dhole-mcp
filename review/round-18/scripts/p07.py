"""P07 阿图 · 分析师 L2 —— steps 形状错：字典/字符串元素/错位 selector/OpenAI 风格/空数组。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim, ROOT

URL = ROOT + "/frame/inner?cb=p07"
p = PersonaSim(7, "阿图·分析师")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-actions 是字典")
r = p.call("web_act", {"url": URL, "timeout": 30000, "actions": {"click": "#note"}})
print("   err:", str(r.get("error"))[:240])

p.pause(2)
p.set_scenario("S2-元素是字符串")
r = p.call("web_act", {"url": URL, "timeout": 30000, "actions": ["click #note"]})
print("   err:", str(r.get("error"))[:240])

p.pause(2)
p.set_scenario("S3-selector 是数字")
r = p.call("web_act", {"url": URL, "timeout": 30000, "actions": [{"click": 123}]})
acts = (r.get("metadata") or {}).get("actions") or []
print("   err:", str(r.get("error"))[:200],
      "| act0:", acts[0].get("ok") if acts else None,
      str(acts[0].get("error"))[:160] if acts else "")

p.pause(2)
p.set_scenario("S4-OpenAI 风格动作")
r = p.call("web_act", {"url": URL, "timeout": 30000,
                       "actions": [{"action": "click", "selector": "#note"}]})
print("   err:", str(r.get("error"))[:260])

p.pause(3)
p.set_scenario("S5-不存在的 selector")
r = p.call("web_act", {"url": URL, "timeout": 30000,
                       "actions": [{"click": {"selector": "#nope"}}]})
acts = (r.get("metadata") or {}).get("actions") or []
print("   err:", str(r.get("error"))[:200],
      "| act0:", acts[0].get("ok") if acts else None,
      str(acts[0].get("error"))[:160] if acts else "")

p.pause(2)
p.set_scenario("S6-空数组")
r = p.call("web_act", {"url": URL, "timeout": 30000, "actions": []})
print("   ok:", r.get("content_ok"), "err:", str(r.get("error"))[:220])

p.pause(2)
p.set_scenario("S7-自愈 fill/click/read")
r = p.call("web_act", {"url": URL, "timeout": 30000,
                       "actions": [{"fill": {"selector": "#note", "text": "ok"}},
                                   {"click": "#save"},
                                   {"read": "#saved"}]})
acts = (r.get("metadata") or {}).get("actions") or []
for i, a in enumerate(acts):
    print(f"   act{i}:", a.get("ok"), str(a.get("text"))[:70] if a.get("text") else "",
          str(a.get("error"))[:90] if a.get("error") else "")
