"""P07 金敏 · 韩语用户 L2 —— 韩文页 · 韩文搜索 · 韩文 URL 参数。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(7, "金敏·韩语用户")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-韩文页保真")
r = p.call("web_read", {"url": "http://127.0.0.1:8398/ko.html?cb=한국어"})
txt = "".join(str(x) for x in (r.get("content") or []))
print(f"   ok={r.get('content_ok')} chars={r.get('total_extracted_chars')} "
      f"has_hangul={any('\uac00' <= ch <= '\ud7a3' for ch in txt)} head={txt[:70]!r}")

p.pause(4)
p.set_scenario("S2-韩文搜索")
r = p.call("web_search", {"query": "한국어 인코딩 테스트", "max_results": 2})
print(f"   {str(r.get('summary'))[:180]}")
for x in (r.get("results") or [])[:2]:
    print("   -", str(x.get('title'))[:100])

p.pause(3)
p.set_scenario("S3-韩文参数的靶站页")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/article?cb=한국어"})
print(f"   ok={r.get('content_ok')} chars={r.get('total_extracted_chars')} "
      f"err={str(r.get('error'))[:80]}")

p.pause(3)
p.set_scenario("S4-复读缓存")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/article?cb=한국어"})
print(f"   cached={r.get('cached')} chars={r.get('total_extracted_chars')}")
print("P07 DONE")
