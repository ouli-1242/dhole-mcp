"""P01 苏晓 · 新手产品经理 L1 —— 照教程抄错：数字带引号 / camelCase / 参数名记错 / actions 写成字符串。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim, ROOT

p = PersonaSim(1, "苏晓·新手产品经理")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-数字带引号")
r = p.call("web_read", {"url": ROOT + "/article?cb=p01a", "max_content_chars": "500"})
print("   err:", str(r.get("error"))[:180])
print("   chars:", r.get("total_extracted_chars"))

p.pause(2)
p.set_scenario("S2-自愈整形")
r = p.call("web_read", {"url": ROOT + "/article?cb=p01a", "max_content_chars": 500})
print("   chars:", r.get("total_extracted_chars"), "ok:", r.get("content_ok"))

p.pause(2)
p.set_scenario("S3-camelCase 字段名")
r = p.call("web_read", {"url": ROOT + "/article?cb=p01b", "maxContentChars": 300})
print("   chars:", r.get("total_extracted_chars"), "err:", str(r.get("error"))[:120])

p.pause(2)
p.set_scenario("S4-参数名记成 q")
r = p.call("web_search", {"q": "机械键盘"})
print("   err:", str(r.get("error"))[:180])
print("   next:", str(r.get("next_action"))[:160])

p.pause(3)
p.set_scenario("S5-自愈 query")
r = p.call("web_search", {"query": "机械键盘 评测", "max_results": 2})
print("   results:", len(r.get("results") or []), "summary:", str(r.get("summary"))[:100])

p.pause(3)
p.set_scenario("S6-actions 写成字符串")
r = p.call("web_act", {"url": ROOT + "/dialog?cb=p01", "actions": "click #note",
                       "timeout": 30000})
print("   err:", str(r.get("error"))[:220])

p.pause(2)
p.set_scenario("S7-自愈数组形状")
r = p.call("web_act", {"url": ROOT + "/dialog?cb=p01", "timeout": 30000,
                       "actions": [{"click": "#note"}]})
print("   ok:", r.get("content_ok"), "err:", str(r.get("error"))[:120])
acts = (r.get("metadata") or {}).get("actions") or []
if acts:
    print("   act0:", acts[0].get("ok"), str(acts[0].get("error"))[:80])
