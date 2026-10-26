"""P01 洛砚舟 · 建筑师 L2 —— 链条①discover→read→parse→recall（跨工具行可点性）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

fdir = Path(__file__).resolve().parent.parent / "sessions" / "files"
fdir.mkdir(parents=True, exist_ok=True)

p = PersonaSim(1, "洛砚舟·建筑师")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-discover 地图")
r = p.call("web_discover", {"url": "http://127.0.0.1:8399/collections?items=6"})
pages = (r.get("pages") or [])
rows = [u for pg in pages for u in (pg.get("links") or [])]
works = [u for u in rows if "/work/" in str(u)]
print(f"   pages={len(pages)} rows={len(rows)} work 行={len(works)}")
print(f"   work 样例: {works[:3]}")

p.pause(6)
p.set_scenario("S2-read 地图选中的行")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/work/2"})
print(f"   status={r.get('status')} chars={r.get('total_extracted_chars')} | {str(r.get('summary'))[:110]}")

p.pause(6)
p.set_scenario("S3-parse 构造的材料单")
csv = fdir / "material_list.csv"
csv.write_text("构件,规格,数量\n梁,200x100,12\n柱,300x300,8\n板,120mm,45\n", encoding="utf-8")
r = p.call("doc_parse", {"file_path": str(csv)})
print(f"   | {str(r.get('summary'))[:110]}")

p.pause(6)
p.set_scenario("S4-recall 回查刚读的页")
r = p.call("web_recall", {"query": "作品二", "limit": 6})
res = r.get("results") or []
print(f"   results={len(res)} | {str(r.get('summary'))[:100]}")
for x in res[:3]:
    print(f"     - {str(x.get('url'))[:60]}")
print("P01 DONE")
