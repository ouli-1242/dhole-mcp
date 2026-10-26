"""P05 程野 · 数据记者 L3 —— research 并行 · crawl/read 同页代际。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(5, "程野·数据记者")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-research 第一问")
r = p.call("web_research", {"query": "HTTP cache freshness revalidation",
                            "max_sources": 2, "per_source_chars": 900,
                            "timeout": 120000})
print(f"   sources={len(r.get('sources') or [])} failed={len(r.get('failed') or [])} "
      f"more={len(r.get('more_hits') or [])}")

p.pause(6)
p.set_scenario("S2-crawl 靶站前三页")
r = p.call("web_crawl", {"url": "http://127.0.0.1:8399/", "max_pages": 3})
print(f"   {str(r.get('summary'))[:200]}")
for x in (r.get("pages") or [])[:4]:
    print("   -", str({k: x.get(k) for k in ('url', 'content_ok', 'page_type', 'cached') if x.get(k) is not None})[:160])

p.pause(4)
p.set_scenario("S3-crawl 刚抓的页改用 read")
U = "http://127.0.0.1:8399/work/1?cb=p05gen"
a = p.call("web_read", {"url": U})
print(f"   cached={a.get('cached')} ok={a.get('content_ok')} chars={a.get('total_extracted_chars')}")

p.pause(3)
p.set_scenario("S4-同页复读（阅读缓存）")
b = p.call("web_read", {"url": U})
print(f"   cached={b.get('cached')} same_ts={a.get('fetched_at') == b.get('fetched_at')}")

p.pause(4)
p.set_scenario("S5-research 第二问（窄源）")
r = p.call("web_research", {"query": "dhole MCP web_read", "max_sources": 1,
                            "per_source_chars": 600, "timeout": 90000})
print(f"   sources={len(r.get('sources') or [])} failed={len(r.get('failed') or [])}")
print("P05 DONE")
