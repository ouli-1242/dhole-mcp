"""P06 屠万里 · 高尔夫教练 L2 —— 链条⑥search→recall clamp 回归→read 落点（r09/r11 家产）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(6, "屠万里·高尔夫教练")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-search")
r = p.call("web_search", {"query": "golf swing basics", "max_results": 4})
results = r.get("results") or []
urls = [x.get("url") for x in results]
print(f"   results={len(results)} | first={str(urls[0])[:70] if urls else '-'}")

p.pause(6)
p.set_scenario("S2-recall limit=99（r09 fix 回归：clamp 注记在场）")
r = p.call("web_recall", {"query": "fixture", "limit": 99})
print(f"   clamped: {r.get('clamped')} | results={len(r.get('results') or [])}")

p.pause(6)
p.set_scenario("S3-recall limit=5（未越界应安静）")
r = p.call("web_recall", {"query": "fixture", "limit": 5})
print(f"   clamped: {r.get('clamped')} | results={len(r.get('results') or [])}")

p.pause(6)
p.set_scenario("S4-read search 落点")
r = p.call("web_read", {"url": urls[0] if urls else "http://127.0.0.1:8399/article"})
print(f"   status={r.get('status')} chars={r.get('total_extracted_chars')} | {str(r.get('summary'))[:100]}")
print("P06 DONE")
