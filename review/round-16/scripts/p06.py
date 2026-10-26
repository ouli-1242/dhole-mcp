"""P06 克莱尔 · 法语编辑 L2 —— Latin-1 重音页 · 重音搜索 · 重音 CSV。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(6, "克莱尔·法语编辑")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-Latin-1 页重音保真")
r = p.call("web_read", {"url": "http://127.0.0.1:8398/latin1.html?cb=fr"})
txt = "".join(str(x) for x in (r.get("content") or []))
print(f"   ok={r.get('content_ok')} chars={r.get('total_extracted_chars')} "
      f"replacement={'�' in txt} accent_ok={'français' in txt} head={txt[:80]!r}")

p.pause(4)
p.set_scenario("S2-重音搜索")
r = p.call("web_search", {"query": "encodage français accents", "max_results": 2})
print(f"   {str(r.get('summary'))[:180]}")
for x in (r.get("results") or [])[:2]:
    print("   -", str(x.get('title'))[:100])

p.pause(4)
p.set_scenario("S3-重音 CSV")
F = Path(__file__).resolve().parent.parent / "sessions" / "files"
r = p.call("doc_parse", {"file_path": str(F / "emoji_table.csv")})
txt = "".join(str(x) for x in (r.get("content") or []))
print(f"   ok={r.get('content_ok')} café={'café' in txt} très={'très bien' in txt}")

p.pause(3)
p.set_scenario("S4-recall 重音词")
r = p.call("web_recall", {"query": "café français", "limit": 4})
print(f"   results={len(r.get('results') or [])} terms={str(r.get('terms_found'))[:90]}")
print("P06 DONE")
