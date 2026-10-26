"""P02 岑万川3 · 证券交易员 L3 猎手 —— 链条②search→research→read 三方对账 + r10 timeout 合同。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(2, "岑万川3·证券交易员")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-search 行")
r = p.call("web_search", {"query": "browser automation agent", "max_results": 4})
results = r.get("results") or []
urls = [x.get("url") for x in results]
print(f"   results={len(results)}")
for u in urls[:4]:
    print(f"     - {str(u)[:70]}")

p.pause(6)
p.set_scenario("S2-research 来源行")
r = p.call("web_research", {"query": "web_act 组合语义 动作链", "max_sources": 2, "timeout": 30000})
srcs = r.get("sources") or []
print(f"   sources={len(srcs)}")
for s in srcs[:3]:
    print(f"     - n={s.get('n')} {str(s.get('url'))[:60]} ok={s.get('ok', s.get('status'))}")

p.pause(6)
p.set_scenario("S3-read search 第一行（timeout=0 边界, r10 家产）")
r = p.call("web_read", {"url": urls[0], "timeout": 0})
print(f"   status={r.get('status')} | {str(r.get('summary'))[:110]}")
na = str(r.get("next_action") or "")
if na:
    print(f"   next_action: {na[:130]}")

p.pause(6)
p.set_scenario("S4-read timeout=999999 边界")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/article", "timeout": 999999})
print(f"   status={r.get('status')} | {str(r.get('summary'))[:110]}")
print("P02 DONE")
