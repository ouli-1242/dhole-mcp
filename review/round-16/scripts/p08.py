"""P08 伊万 · 俄语分析师 L3 —— 西里尔页 · 西里尔搜索 · 西里尔 research。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(8, "伊万·俄语分析师")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-西里尔页保真")
r = p.call("web_read", {"url": "http://127.0.0.1:8398/ru.html?cb=русский"})
txt = "".join(str(x) for x in (r.get("content") or []))
print(f"   ok={r.get('content_ok')} chars={r.get('total_extracted_chars')} "
      f"has_cyrillic={any('\u0400' <= ch <= '\u04ff' for ch in txt)} head={txt[:70]!r}")

p.pause(4)
p.set_scenario("S2-西里尔搜索")
r = p.call("web_search", {"query": "кодировка текст проверка", "max_results": 2})
print(f"   {str(r.get('summary'))[:180]}")
for x in (r.get("results") or [])[:2]:
    print("   -", str(x.get('title'))[:100])

p.pause(5)
p.set_scenario("S3-西里尔 research")
r = p.call("web_research", {"query": "кодировка UTF-8 проверка", "max_sources": 1,
                            "per_source_chars": 600, "timeout": 90000})
print(f"   sources={len(r.get('sources') or [])} failed={len(r.get('failed') or [])} "
      f"summary={str(r.get('summary'))[:160]}")

p.pause(3)
p.set_scenario("S4-复读西里尔页缓存")
r = p.call("web_read", {"url": "http://127.0.0.1:8398/ru.html?cb=русский"})
print(f"   cached={r.get('cached')} chars={r.get('total_extracted_chars')}")
print("P08 DONE")
