"""P09 阿珍 · 粤语用户 L1 —— 繁体 recall · 混合页 · 一周订阅。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(9, "阿珍·粤语用户")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-混合语言页")
r = p.call("web_read", {"url": "http://127.0.0.1:8398/mixed.html?cb=繁中"})
txt = "".join(str(x) for x in (r.get("content") or []))
print(f"   ok={r.get('content_ok')} chars={r.get('total_extracted_chars')} "
      f"cjk={'中文' in txt} arabic={'العربية' in txt} emoji={'😀' in txt} head={txt[:90]!r}")

p.pause(4)
p.set_scenario("S2-繁体 recall")
r = p.call("web_recall", {"query": "混合 語言 頁面", "limit": 4})
print(f"   results={len(r.get('results') or [])} terms={str(r.get('terms_found'))[:100]}")
for x in (r.get("results") or [])[:3]:
    print("   -", str({k: x.get(k) for k in ('url', 'title') if x.get(k)})[:130])

p.pause(4)
p.set_scenario("S3-一周订阅")
r = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"],
                         "since": "1w", "max_items": 4})
for f in r.get("feeds") or []:
    print(f"   since={f.get('since')} older={f.get('items_older_than_since')} "
          f"items={len(f.get('items') or [])}")
    for it in (f.get('items') or [])[:2]:
        print("    -", str(it.get('title'))[:90])

p.pause(3)
p.set_scenario("S4-中文标题页")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/collections?items=6&cb=繁中"})
print(f"   ok={r.get('content_ok')} chars={r.get('total_extracted_chars')} "
      f"summary={str(r.get('summary'))[:100]}")
print("P09 DONE")
