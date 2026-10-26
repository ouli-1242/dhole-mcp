"""P03 高桥 · 日文研究员 L3 —— 日文页 · emoji/ZWJ · 日文搜索。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(3, "高桥·日文研究员")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-日文页保真")
r = p.call("web_read", {"url": "http://127.0.0.1:8398/ja.html?cb=日本語"})
txt = "".join(str(x) for x in (r.get("content") or []))
print(f"   ok={r.get('content_ok')} chars={r.get('total_extracted_chars')} "
      f"has_kana={any('\u3040' <= ch <= '\u30ff' for ch in txt)} has_emoji={'😀' in txt}")

p.pause(3)
p.set_scenario("S2-emoji 与组合字符页")
r = p.call("web_read", {"url": "http://127.0.0.1:8398/emoji.html?cb=😀🚀"})
txt = "".join(str(x) for x in (r.get("content") or []))
print(f"   ok={r.get('content_ok')} chars={r.get('total_extracted_chars')} "
      f"emoji={'😀' in txt and '🚀' in txt} zwj={'👩‍💻' in txt} accented={'é' in txt}")
print(f"   head={txt[:120]!r}")

p.pause(4)
p.set_scenario("S3-日文搜索")
r = p.call("web_search", {"query": "東京 天気 予報", "max_results": 2})
print(f"   {str(r.get('summary'))[:180]}")
for x in (r.get("results") or [])[:2]:
    print("   -", str(x.get('title'))[:100])

p.pause(3)
p.set_scenario("S4-日文页分段读（多字节窗口）")
a = p.call("web_read", {"url": "http://127.0.0.1:8398/ja.html?cb=seg",
                        "max_content_chars": 500})
print(f"   ok={a.get('content_ok')} truncated={a.get('is_truncated')} "
      f"next={a.get('next_offset')} summary={str(a.get('summary'))[:120]}")
print("P03 DONE")
