"""P07 甘棠4 · 验光师 L2 —— 重复测量一致性（三连 cache replay 的诚实形状）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(7, "甘棠4·验光师")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-首灌(真回源)")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/collections", "cache_ttl": 3600})
print(f"   首发: cached={'cached' in str(r.get('summary')).lower()} | fetched_at: {r.get('fetched_at')}")

p.set_scenario("S2-S3-三连 replay(一致性)")
sigs = []
for i in range(3):
    r = p.call("web_read", {"url": "http://127.0.0.1:8399/collections", "cache_ttl": 3600})
    s = str(r.get("summary"))
    has_attempt = "attempt" in s or "learned ceiling" in s
    sigs.append(("cached" in s.lower(), has_attempt, r.get("content_ok"), r.get("fetched_at")))
    p.pause(3)
for i, sig in enumerate(sigs, 1):
    print(f"   第{i}发: cached={sig[0]} attempt句={sig[1]} ok={sig[2]} fetched_at={sig[3]}")
print(f"   一致: {len(set(sigs)) == 1} (round-02 W3 教训: replay 无 attempt 句=正确)")
print("P07 DONE")
