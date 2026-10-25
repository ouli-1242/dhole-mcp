"""P10 桑无咎 · 架构师 L3 —— 绝对句攻击。"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim
p = PersonaSim(10, "桑无咎·架构师")
p.set_scenario("S1-『never opens cloud metadata』")
for v in (True, ["169.254.169.254"], "169.254.169.254"):
    r = p.call("cred_defaults", {"set": {"allow_private": v}})
    e = str(r.get("error") or r.get("message") or "")[:80]
    print(f"   allow_private={v!r}: {e}")
    p.call("cred_defaults", {"unset": ["allow_private"]})
p.set_scenario("S2-『No parameter disables it』(archive 档)")
p.call("cred_defaults", {"set": {"allow_private": True}})
r = p.call("web_read", {"url": "http://127.0.0.1:8399/no-such-page?abs=1", "cache_ttl": 0, "timeout": 30000})
import json
s = json.dumps(r, ensure_ascii=False)
print("   archive 被问:", "archive" in s, "| 404 行:", str(r.get("error"))[:50])
print("P10 DONE")
