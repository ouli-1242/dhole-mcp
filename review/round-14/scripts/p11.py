"""P11 顾之遥补条 · discover 正形（max_pages）→ 地图行可点。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(11, "顾之遥·策展人补条")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-discover 清单页（max_pages）")
r = p.call("web_discover", {"url": "http://127.0.0.1:8399/collections?items=6",
                            "max_pages": 8})
print(f"   {str(r.get('summary'))[:200]}")
rows = r.get("pages") or []
print(f"   rows={len(rows)} work_rows={sum(1 for x in rows if '/work/' in str(x.get('url')))}")
for x in rows[:4]:
    print("   -", str({k: x.get(k) for k in ('url', 'status', 'title') if x.get(k) is not None})[:160])

p.pause(6)
p.set_scenario("S2-discover path_include=/work")
r = p.call("web_discover", {"url": "http://127.0.0.1:8399/collections?items=6",
                            "max_pages": 8, "path_include": "/work"})
rows = r.get("pages") or []
print(f"   {str(r.get('summary'))[:160]}")
print(f"   rows={len(rows)} all_work={all('/work/' in str(x.get('url')) for x in rows)}")
print("P11 DONE")
