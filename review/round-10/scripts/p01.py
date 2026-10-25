"""P01 阮鸿飞4 · 无人机飞手 L2 —— 显式 timeout= 合同（R02-F4 照做）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(1, "阮鸿飞4·无人机飞手")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-读 call 篇(timeout 合同)")
p.call("sys_guide", {"topic": "call"})

p.pause(6)
p.set_scenario("S2-显式 timeout=5000( learned 让位)")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/article", "timeout": 5000, "cache_ttl": 0})
print(f"   attempt_cap_ms: {r.get('attempt_cap_ms')} | learned 句在场: {'learned ceiling' in str(r.get('summary'))}")
print(f"   summary: {str(r.get('summary'))[:140]}")

p.pause(6)
p.set_scenario("S3-缺省(learned 接管)")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/article?r=1", "cache_ttl": 0})
print(f"   attempt_cap_ms: {r.get('attempt_cap_ms')} | learned 句在场: {'learned ceiling' in str(r.get('summary'))}")
print(f"   summary: {str(r.get('summary'))[:140]}")
print("P01 DONE")
