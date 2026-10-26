"""P02 陆知微 · 专利审查员 L3 猎手 —— offset 续片 · cache_ttl 语义 · check 变化。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(2, "陆知微·专利审查员")
p.call("cred_defaults", {"set": {"allow_private": True}})

U = "http://127.0.0.1:8399/longaside?cb=p02-1"

p.set_scenario("S1-长文截断续片（max 500）")
r = p.call("web_read", {"url": U, "max_content_chars": 500})
print(f"   truncated={r.get('is_truncated')} next_offset={r.get('next_offset')} "
      f"chars={r.get('total_extracted_chars')}")
nxt = r.get("next_offset")
p.pause(5)
p.set_scenario("S2-offset 续片（summary 点名切片）")
r = p.call("web_read", {"url": U, "max_content_chars": 500, "offset": nxt or 500})
print(f"   offset={nxt or 500} chars={r.get('total_extracted_chars')}")
print(f"   summary: {str(r.get('summary'))[:400]}")

p.pause(6)
p.set_scenario("S3-连打三种读法（cache_ttl=0 → 默认 → 再 0）")
V = "http://127.0.0.1:8399/article?cb=p02-2"
a = p.call("web_read", {"url": V, "cache_ttl": 0})
p.pause(3)
b = p.call("web_read", {"url": V})
p.pause(3)
c = p.call("web_read", {"url": V, "cache_ttl": 0})
print(f"   0→default→0: cached={a.get('cached')},{b.get('cached')},{c.get('cached')}")

p.pause(6)
p.set_scenario("S4-check 变化检查（两 URL 批量）")
r = p.call("web_read", {"urls": [V, "http://127.0.0.1:8399/work/1?cb=p02-3"],
                        "extraction_type": "check"})
rows = r.get("results") or []
print(f"   rows={len(rows)}")
for x in rows[:3]:
    print("   -", str({k: x.get(k) for k in
          ('url', 'changed', 'status', 'note', 'fetched_at') if x.get(k) is not None})[:220])
print(f"   summary: {str(r.get('summary'))[:300]}")

p.pause(5)
p.set_scenario("S5-state 复盘")
r = p.call("sys_state", {})
print(f"   message: {str(r.get('message'))[:200]}")
print("P02 DONE")
