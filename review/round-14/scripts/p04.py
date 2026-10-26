"""P04 韩冬 · 供应链计划员 L2 —— 三供应商批量读 → 回访缓存 → 本地写闸/confirm。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(4, "韩冬·供应链计划员")
p.call("cred_defaults", {"set": {"allow_private": True}})

URLS = [f"http://127.0.0.1:8399/work/{i}?cb=p04-{i}" for i in (1, 2, 3)]

p.set_scenario("S1-批量读三个规格页")
r = p.call("web_read", {"urls": URLS})
rows = r.get("results") or []
print(f"   rows={len(rows)}")
for x in rows:
    print("   -", str({k: x.get(k) for k in ('url', 'content_ok', 'total_extracted_chars')})[:160])

p.pause(5)
p.set_scenario("S2-回访同三页（逐行缓存账）")
r = p.call("web_read", {"urls": URLS})
for x in (r.get("results") or []):
    print("   -", str({k: x.get(k) for k in ('url', 'cached', 'fetched_at')})[:160])

p.pause(6)
p.set_scenario("S3-本地写闸（无 confirm）")
r = p.call("web_request", {"url": "http://127.0.0.1:8399/checkout", "method": "POST",
                           "body": {"item": "供应商变更记录", "qty": "1"}})
print(f"   err={str(r.get('error'))[:140]}")
print(f"   next={str(r.get('next_action'))[:160]}")

p.pause(5)
p.set_scenario("S4-confirm 真写")
r = p.call("web_request", {"url": "http://127.0.0.1:8399/checkout", "method": "POST",
                           "body": {"item": "供应商变更记录", "qty": "1"},
                           "confirm": True})
print(f"   status={r.get('status')} summary={str(r.get('summary'))[:140]}")
print("P04 DONE")
