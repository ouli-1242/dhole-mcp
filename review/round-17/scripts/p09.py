"""P09 罗薇 · 图书管理员 L2 —— 二进制 PNG 保存 · crawl/discover/feed 顺带。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

F = Path(__file__).resolve().parent.parent / "sessions" / "files"
png = F / "qr17.png"
png.unlink(missing_ok=True)

p = PersonaSim(9, "罗薇·图书管理员")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-qr.png 按字节保存")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/qr.png", "save_to": str(png)})
meta = r.get("metadata") or {}
print(f"   saved={png.exists()} bytes={meta.get('bytes')} disk={(png.stat().st_size if png.exists() else None)} "
      f"png_magic={png.read_bytes()[:8] == b'\x89PNG\\r\\n\\x1a\\n' if png.exists() else None}")
print(f"   content={' '.join(str(x) for x in (r.get('content') or []))[:80]!r}")

p.pause(3)
p.set_scenario("S2-crawl 多语言站三页")
r = p.call("web_crawl", {"url": "http://127.0.0.1:8398/", "max_pages": 3})
print(f"   {str(r.get('summary'))[:150]}")

p.pause(3)
p.set_scenario("S3-discover 地图")
r = p.call("web_discover", {"url": "http://127.0.0.1:8398/", "max_pages": 4})
print(f"   {str(r.get('summary'))[:150]} rows={len(r.get('pages') or [])}")

p.pause(3)
p.set_scenario("S4-订阅流")
r = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"],
                         "since": "2w", "max_items": 3})
for f in r.get("feeds") or []:
    print(f"   since={f.get('since')} older={f.get('items_older_than_since')} items={len(f.get('items') or [])}")
print("P09 DONE")
