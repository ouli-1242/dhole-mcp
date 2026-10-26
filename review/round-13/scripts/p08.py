"""P08 温故 · 水质检验员 L1 —— 链⑧feed since 增量→混发分账→parse xlsx 单表→read。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

fdir = Path(__file__).resolve().parent.parent / "sessions" / "files"
fdir.mkdir(parents=True, exist_ok=True)
try:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "水样"
    ws.append("点位,pH,浊度".split(","))
    ws.append(["1号井", 7.2, 0.8])
    ws.append(["2号井", 6.9, 1.1])
    wb.save(fdir / "water_quality.xlsx")
except Exception as e:
    print(f"   (xlsx 构造失败: {e})")

p = PersonaSim(8, "温故·水质检验员")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-feed since 增量")
r = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"],
                         "since": "2026-10-24", "max_items": 5})
print(f"   | {str(r.get('summary'))[:150]}")
rows = r.get("feeds") or r.get("results") or []
if rows:
    x = rows[0]
    print(f"   items={len(x.get('items') or [])} older_than_since={x.get('items_older_than_since')} no_date={x.get('items_without_date')}")

p.pause(6)
p.set_scenario("S2-混发分账（好源+404，r12 必测5）")
r = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml",
                                  "http://127.0.0.1:8399/feed-404.xml"],
                         "max_items": 2})
rows = r.get("feeds") or r.get("results") or []
for x in rows:
    print(f"     - items={len(x.get('items') or [])} err={str(x.get('error') or '-')[:60]}")

p.pause(6)
p.set_scenario("S3-parse 水样 xlsx")
r = p.call("doc_parse", {"file_path": str(fdir / "water_quality.xlsx")})
txt = "".join(str(c) for c in (r.get("content") or []))
print(f"   1号井={'1号井' in txt} | {str(r.get('summary'))[:100]}")

p.pause(6)
p.set_scenario("S4-read 原文")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/article"})
print(f"   status={r.get('status')} chars={r.get('total_extracted_chars')}")
print("P08 DONE")
