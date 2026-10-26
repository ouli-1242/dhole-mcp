"""P03 白盒 · 测试工程师 L3 —— 未知字段/空对象/null/危险键名/未知动作/空动作。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim, ROOT

p = PersonaSim(3, "白盒·测试工程师")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-未知字段")
r = p.call("web_read", {"url": ROOT + "/article?cb=p03a", "unknown_field": "x"})
print("   err:", str(r.get("error"))[:160], "| chars:", r.get("total_extracted_chars"))

p.pause(2)
p.set_scenario("S2-url 为 null")
r = p.call("web_read", {"url": None})
print("   err:", str(r.get("error"))[:220], "| next:", str(r.get("next_action"))[:120])

p.pause(2)
p.set_scenario("S3-空对象")
r = p.call("web_read", {})
print("   err:", str(r.get("error"))[:220], "| next:", str(r.get("next_action"))[:120])

p.pause(2)
p.set_scenario("S4-危险键名")
r = p.call("web_read", {"url": ROOT + "/article?cb=p03b",
                        "__proto__": {"x": 1}, "constructor": {"prototype": 1}})
print("   err:", str(r.get("error"))[:120], "| chars:", r.get("total_extracted_chars"))

p.pause(3)
p.set_scenario("S5-未知动作键")
r = p.call("web_act", {"url": ROOT + "/dialog?cb=p03", "timeout": 30000,
                       "actions": [{"teleport": "#qty"}]})
print("   err:", str(r.get("error"))[:260])

p.pause(2)
p.set_scenario("S6-空动作对象")
r = p.call("web_act", {"url": ROOT + "/dialog?cb=p03", "timeout": 30000,
                       "actions": [{}]})
print("   err:", str(r.get("error"))[:260])

p.pause(2)
p.set_scenario("S7-收尾正常读")
r = p.call("web_read", {"url": ROOT + "/article?cb=p03c"})
print("   ok:", r.get("content_ok"), "chars:", r.get("total_extracted_chars"))
