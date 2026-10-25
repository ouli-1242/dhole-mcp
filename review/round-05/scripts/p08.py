"""P08 傅筛 · 安全研究 L3 —— 私网门全谱 + 怪值。"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim
p = PersonaSim(8, "傅筛·安全研究")
p.set_scenario("S1-私网谱(不带豁免)")
for u, tag in [("http://169.254.169.254/latest/meta-data", "link-local"),
               ("http://10.0.0.1/", "ten-net"),
               ("http://[::1]:8399/", "ipv6-loopback")]:
    r = p.call("web_read", {"url": u, "timeout": 15000})
    print(f"   {tag}:", str(r.get("error"))[:100])
    p.pause(5)
p.set_scenario("S2-怪 allow_private 值")
for v, tag in [(0, "zero"), ("", "empty-str"), ([], "empty-list")]:
    r = p.call("cred_defaults", {"set": {"allow_private": v}})
    print(f"   {tag}:", str(r.get("error") or r.get("message"))[:100])
p.pause(6)
p.set_scenario("S3-豁免后 [::1] 再试")
p.call("cred_defaults", {"set": {"allow_private": True}})
r = p.call("web_read", {"url": "http://[::1]:8399/", "timeout": 15000, "cache_ttl": 0})
p.observe("ipv6-after", r, "content_ok", "summary")
r = p.call("sys_state", {})
p.observe("state", r, "summary")
print("P08 DONE")
