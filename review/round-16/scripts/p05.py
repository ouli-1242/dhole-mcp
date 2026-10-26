"""P05 小满 · 社媒运营 L1 —— emoji 页/URL · emoji recall · 订阅。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(5, "小满·社媒运营")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-emoji 页读两遍")
U = "http://127.0.0.1:8398/emoji.html?cb=😀🚀"
a = p.call("web_read", {"url": U})
b = p.call("web_read", {"url": U})
ta = "".join(str(x) for x in (a.get("content") or []))
print(f"   ok={a.get('content_ok')} emoji={'😀' in ta and '🚀' in ta} "
      f"cached={a.get('cached')}->{b.get('cached')} same_ts={a.get('fetched_at') == b.get('fetched_at')}")

p.pause(3)
p.set_scenario("S2-emoji recall")
r = p.call("web_recall", {"query": "emoji 😀 表情", "limit": 4})
print(f"   results={len(r.get('results') or [])} terms={str(r.get('terms_found'))[:100]}")
for x in (r.get("results") or [])[:3]:
    print("   -", str({k: x.get(k) for k in ('url', 'title') if x.get(k)})[:130])

p.pause(4)
p.set_scenario("S3-订阅流（近三天）")
r = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"],
                         "since": "3d", "max_items": 4})
for x in r.get("feeds") or []:
    f = x
    print(f"   since={f.get('since')} older={f.get('items_older_than_since')} "
          f"items={len(f.get('items') or [])}")
    for it in (f.get('items') or [])[:2]:
        print("    -", str(it.get('title'))[:90])

p.pause(3)
p.set_scenario("S4-中文标签页")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/article?cb=标签😀"})
print(f"   ok={r.get('content_ok')} chars={r.get('total_extracted_chars')} "
      f"summary={str(r.get('summary'))[:100]}")
print("P05 DONE")
