"""P10 艾米 · QA L3 —— sys_guide/sys_state/cred_defaults 形状错；收尾验服务未污染。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim, ROOT

p = PersonaSim(10, "艾米·QA")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-guide topic 拼错")
r = p.call("sys_guide", {"topic": "web_readd"})
print("   err:", str(r.get("error"))[:160], "| summary:", str(r.get("summary"))[:160])

p.pause(2)
p.set_scenario("S2-guide topic 类型错")
r = p.call("sys_guide", {"topic": ["web_read"]})
print("   err:", str(r.get("error"))[:220])

p.pause(2)
p.set_scenario("S3-state show 未知键")
r = p.call("sys_state", {"show": ["nonexistent"]})
print("   err:", str(r.get("error"))[:220], "| summary:", str(r.get("summary"))[:140])

p.pause(2)
p.set_scenario("S4-cred_defaults set 是字符串")
r = p.call("cred_defaults", {"set": "allow_private"})
print("   err:", str(r.get("error"))[:240])

p.pause(2)
p.set_scenario("S5-cred_defaults unset 是字符串")
r = p.call("cred_defaults", {"unset": "allow_private"})
print("   err:", str(r.get("error"))[:240])

p.pause(2)
p.set_scenario("S6-形状错之后私网页仍可读")
r = p.call("web_read", {"url": ROOT + "/private/secret?cb=p10"})
print("   ok:", r.get("content_ok"), "err:", str(r.get("error"))[:160])

p.pause(2)
p.set_scenario("S7-收尾 state")
r = p.call("sys_state", {})
print("   ok:", r.get("content_ok"), "summary:", str(r.get("summary"))[:160])
