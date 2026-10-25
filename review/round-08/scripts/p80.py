"""p80 旁支（修形重跑）：web_search / web_research / web_recall / sys_state。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(80, "p80-旁支b")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("B1-search")
r = p.call("web_search", {"query": "fixture site 靶站"})
print("   ", str(r.get("summary"))[:120])

p.pause(6)
p.set_scenario("B2-research")
r = p.call("web_research", {"query": "web_act 组合语义 动作链", "max_sources": 2, "timeout": 60000})
print("   ", str(r.get("summary"))[:140])

p.pause(6)
p.set_scenario("B3-recall")
r = p.call("web_recall", {"query": "fixture", "limit": 3})
print("   ", str(r.get("summary"))[:130])

p.pause(6)
p.set_scenario("B4-sys_state(sessions)")
r = p.call("sys_state", {"show": ["sessions"]})
sess = r.get("sessions")
print("   ok:", not r.get("error"), "| sessions 型:", type(sess).__name__, "| 行数:", len(sess) if isinstance(sess, list) else "-")
print("P80 DONE")
