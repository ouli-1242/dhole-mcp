"""P07 观棋 · 播客 L2 —— feed item 正文质量。"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim
p = PersonaSim(7, "观棋·播客")
p.call("cred_defaults", {"set": {"allow_private": True}})
p.set_scenario("S1-feed item 正文")
r = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"], "max_items": 3})
for f in (r.get("feeds") or []):
    for it in (f.get("items") or [])[:3]:
        body = str(it.get("summary") or it.get("description") or "")[:100]
        has_tags = "<" in body and ">" in body
        print(f"   item: {str(it.get('title'))[:30]} | 标签残留={has_tags} | {body[:60]}")
p.pause(7)
p.set_scenario("S2-导出 show notes(doc_parse)")
print("P07 DONE")
