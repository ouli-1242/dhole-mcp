"""P04 黎照 · 合规 L2 —— configuration 篇 env 清单对照 sys_state。"""
import sys, time, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim
p = PersonaSim(4, "黎照·合规")
p.set_scenario("S1-configuration 篇")
r = p.call("sys_guide", {"topic": "configuration"})
s = json.dumps(r, ensure_ascii=False)
import re
envs = sorted(set(re.findall(r"DHOLE_[A-Z_]+", s)))
print("   手册提到的 env 数:", len(envs))
print("   样本:", envs[:8])
p.pause(7)
p.set_scenario("S2-sys_state 对照")
r = p.call("sys_state", {"show": ["config"]})
p.observe("config", r, "summary")
r = p.call("sys_state", {"show": ["env_registry"]})
p.observe("env-registry", r, "summary")
print("P04 DONE")
