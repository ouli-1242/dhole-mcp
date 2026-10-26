"""P06 小满 · 内容编辑 L1 —— emoji/零宽/换行/尾随空格进查询、selector、URL。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim, ROOT

p = PersonaSim(6, "小满·内容编辑")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-查询带零宽与 emoji")
r = p.call("web_search", {"query": "机械键盘\u200b 👩‍💻", "max_results": 2})
print("   err:", str(r.get("error"))[:160], "| results:", len(r.get("results") or []))

p.pause(3)
p.set_scenario("S2-selector 尾随空格")
r = p.call("web_act", {"url": ROOT + "/frame/inner?cb=p06a", "timeout": 30000,
                       "actions": [{"click": "#note "}]})
err = str(r.get("error"))
print("   err:", err[:200])
acts = (r.get("metadata") or {}).get("actions") or []
if acts:
    print("   act0:", acts[0].get("ok"), str(acts[0].get("error"))[:120])

p.pause(2)
p.set_scenario("S3-selector 含换行")
r = p.call("web_act", {"url": ROOT + "/frame/inner?cb=p06b", "timeout": 30000,
                       "actions": [{"click": ".card\n"}]})
acts = (r.get("metadata") or {}).get("actions") or []
print("   err:", str(r.get("error"))[:160],
      "| act0:", acts[0].get("ok") if acts else None,
      str(acts[0].get("error"))[:160] if acts else "")

p.pause(2)
p.set_scenario("S4-URL 带中文与 emoji")
r = p.call("web_read", {"url": ROOT + "/article?q=机械👩‍💻&cb=p06c"})
print("   ok:", r.get("content_ok"), "err:", str(r.get("error"))[:160],
      "| final:", (r.get("metadata") or {}).get("final_url"))

p.pause(2)
p.set_scenario("S5-自愈：emoji 文本填表读回")
r = p.call("web_act", {"url": ROOT + "/frame/inner?cb=p06d", "timeout": 30000,
                       "actions": [{"fill": {"selector": "#note", "text": "笔记👩‍💻\u200b"}},
                                   {"click": "#save"},
                                   {"read": "#saved"}]})
acts = (r.get("metadata") or {}).get("actions") or []
for i, a in enumerate(acts):
    print(f"   act{i}:", a.get("ok"), str(a.get("error"))[:90] if a.get("error") else "",
          str(a.get("text"))[:80] if a.get("text") else "")

p.pause(2)
p.set_scenario("S6-不存在的 emoji selector")
r = p.call("web_read", {"url": ROOT + "/article?cb=p06e",
                        "css_selector": "#不存在👩‍💻"})
print("   ok:", r.get("content_ok"), "err:", str(r.get("error"))[:160])
print("   note:", str(r.get("note"))[:200])
