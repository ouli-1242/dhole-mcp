"""P06 黎照3 · 专利代理 L3 猎手 —— 查全率攻击（生僻/中英混排/零结果不装满）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(6, "黎照3·专利代理")
p.call("cred_defaults", {"set": {"allow_private": True}})

def rows_of(r):
    return r.get("rows") or r.get("results") or []

p.set_scenario("S1-生僻术语")
r = p.call("web_search", {"query": "量子退相干 声子谱 退相干时间 测量", "max_results": 6})
rows = rows_of(r)
print(f"   {len(rows)} 行 | 0 high 现场如实: {str(r.get('summary'))[:120]}")

p.pause(6)
p.set_scenario("S2-中英混排")
r = p.call("web_search", {"query": "zeta potential Zeta电位 测量 标准", "max_results": 6})
print(f"   {len(rows_of(r))} 行 | {str(r.get('summary'))[:120]}")

p.pause(6)
p.set_scenario("S3-同义改写三连(行集不串)")
q1 = p.call("web_search", {"query": "browser automation agent", "max_results": 4})
q2 = p.call("web_search", {"query": "网页自动化 智能体", "max_results": 4})
u1 = {str(x.get("url")) for x in rows_of(q1)}
u2 = {str(x.get("url")) for x in rows_of(q2)}
print(f"   q1 {len(u1)} 行 / q2 {len(u2)} 行 / 交集 {len(u1 & u2)}（交集如实，不夸大）")

p.pause(6)
p.set_scenario("S4-零结果现场(乱码查询)")
r = p.call("web_search", {"query": "zxqv wombatathon 0xdeadbeef 无意义", "max_results": 6})
rows = rows_of(r)
print(f"   零/少结果: {len(rows)} 行 | summary: {str(r.get('summary'))[:180]}")

p.pause(6)
p.set_scenario("S5-min_relevance 高门槛(行被砍而非重排)")
r = p.call("web_search", {"query": "python", "min_relevance": 0.99, "max_results": 6})
print(f"   0.99 门槛: {len(rows_of(r))} 行 | {str(r.get('summary'))[:140]}")
print("P06 DONE")
