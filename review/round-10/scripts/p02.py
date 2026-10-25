"""P02 白景舟2 · 外汇交易员 L3 猎手 —— ceiling 攻击（极小/超长 timeout 边界）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(2, "白景舟2·外汇交易员")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-timeout=1ms(极小)")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/article", "timeout": 1, "cache_ttl": 0})
print(f"   err: {str(r.get('error'))[:120] or '(成功?)'} | cap: {r.get('attempt_cap_ms')}")

p.pause(6)
p.set_scenario("S2-timeout=0(非法)")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/article", "timeout": 0})
print(f"   err: {str(r.get('error'))[:180] or f'(收下 cap={r.get(chr(97)+chr(116)+chr(116)+chr(101)+chr(109)+chr(112)+chr(116)+chr(95)+chr(99)+chr(97)+chr(112)+chr(95)+chr(109)+chr(115))})'}")

p.pause(6)
p.set_scenario("S3-timeout=999999(边界外)")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/about", "timeout": 999999, "cache_ttl": 0})
print(f"   err: {str(r.get('error'))[:100]} | cap: {r.get('attempt_cap_ms')} | summary: {str(r.get('summary'))[:120]}")

p.pause(6)
p.set_scenario("S4-learned ceiling 学习申报(同 host 二访)")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/nav", "cache_ttl": 0})
print(f"   learned 句在场: {'learned ceiling' in str(r.get('summary'))} | summary: {str(r.get('summary'))[:150]}")
print("P02 DONE")
