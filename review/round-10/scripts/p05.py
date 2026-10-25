"""P05 云苏4 · 同声传译 L3 猎手 —— research 预算分摊（timeout=30s 两源）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(5, "云苏4·同声传译")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-research timeout=30000(分摊账)")
r = p.call("web_research", {"query": "web_act 组合语义 动作链", "max_sources": 2, "timeout": 30000})
rows = r.get("sources") or []
print(f"   summary: {str(r.get('summary'))[:150]}")
for x in rows[:3]:
    print(f"   源行: {str(x.get('url'))[:56]} | chars={x.get('chars')} | {str(x.get('error') or '')[:50]}")

p.pause(6)
p.set_scenario("S2-research 极小 timeout(5000)")
r = p.call("web_research", {"query": "本地抓取工具配置", "max_sources": 1, "timeout": 5000})
print(f"   summary: {str(r.get('summary'))[:150]}")
for x in (r.get("sources") or [])[:2]:
    print(f"   源行: {str(x.get('url'))[:56]} | {str(x.get('error') or '')[:60]}")

p.pause(6)
p.set_scenario("S3-research timeout 边界(300000 外)")
r = p.call("web_research", {"query": "x", "timeout": 999999})
print(f"   err: {str(r.get('error'))[:170] or str(r.get('summary'))[:100]}")
print("P05 DONE")
