"""P05 云苏3 · 半导体 FAE L2 —— web_research 多源 evidence pack 深用。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(5, "云苏3·半导体FAE")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-research 两源(1 败行的分账家产)")
r = p.call("web_research", {"query": "web_act 组合语义 动作链",
                            "max_sources": 2, "timeout": 90000})
rows = r.get("sources") or r.get("rows") or []
print(f"   summary: {str(r.get('summary'))[:150]}")
for x in rows[:3]:
    print(f"   源行: {str(x.get('url'))[:60]} | ok={x.get('ok')} | {str(x.get('error') or '')[:60]}")

p.pause(6)
p.set_scenario("S2-research 本地靶源(可控对账)")
r = p.call("web_research", {"query": "本地抓取工具配置", "max_sources": 1, "timeout": 90000})
print(f"   summary: {str(r.get('summary'))[:150]}")
pk = r.get("evidence_pack") or r.get("pack") or {}
print(f"   pack 键: {list(pk)[:6] if isinstance(pk, dict) else type(pk).__name__}")

p.pause(6)
p.set_scenario("S3-max_sources 边界(0 拒/5 上限)")
r = p.call("web_research", {"query": "x", "max_sources": 0})
print(f"   0 源: {str(r.get('error'))[:140]}")
print("P05 DONE")
