"""P08 商陆 · 审计 L3 猎手 —— 缓存 provenance 对账 + /changing 必变 + sys_state census。"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim
p = PersonaSim(8, "商陆·审计")
p.set_scenario("S1-同 URL 两读:缓存 vs 真源")
p.call("cred_defaults", {"set": {"allow_private": True}})
r1 = p.call("web_read", {"url": "http://127.0.0.1:8399/longaside?audit=1", "timeout": 30000})
import re
s1 = str(r1.get("summary"))
print("   第一发来源:", re.findall(r"· (http|cache|stealthy) ·", s1)[:1])
r2 = p.call("web_read", {"url": "http://127.0.0.1:8399/longaside?audit=1", "timeout": 30000})
s2 = str(r2.get("summary"))
print("   第二发来源:", re.findall(r"· (http|cache|stealthy) ·", s2)[:1], "| cached 措辞:", "cached copy" in s2)
p.pause(7)
p.set_scenario("S2-真源对账(强制 fresh)")
r3 = p.call("web_read", {"url": "http://127.0.0.1:8399/longaside?audit=1", "cache_ttl": 0, "timeout": 30000})
s3 = str(r3.get("summary"))
print("   第三发来源:", re.findall(r"· (http|cache|stealthy) ·", s3)[:1])
p.pause(7)
p.set_scenario("S3-/changing 必变 vs 缓存")
r4 = p.call("web_read", {"url": "http://127.0.0.1:8399/changing?audit=1", "cache_ttl": 0, "timeout": 30000})
c4 = r4.get("content"); c4 = ''.join(c4) if isinstance(c4, list) else str(c4)
import re as _re
print("   fresh1 第几次:", _re.findall(r"第 (\d+) 次请求", c4)[:1])
r5 = p.call("web_read", {"url": "http://127.0.0.1:8399/changing?audit=1", "timeout": 30000})
c5 = r5.get("content"); c5 = ''.join(c5) if isinstance(c5, list) else str(c5)
print("   缓存回放第几次:", _re.findall(r"第 (\d+) 次请求", c5)[:1], "(应同上——缓存回放不是新请求)")
p.pause(7)
p.set_scenario("S4-sys_state census")
r = p.call("sys_state", {})
md = r.get("metadata") or {}
print("   census keys:", [k for k in (md.keys() if isinstance(md, dict) else []) ][:10])
print("P08 DONE")
