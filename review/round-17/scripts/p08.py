"""P08 阿码 · 开发者 L3 —— emoji 文件名 · 缓存命中时的 save_to。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

F = Path(__file__).resolve().parent.parent / "sessions" / "files"
a = F / "笔记😀.md"
b = F / "笔记2😀.md"
c = F / "笔记3😀.md"
for f in (a, b, c):
    f.unlink(missing_ok=True)

p = PersonaSim(8, "阿码·开发者")
p.call("cred_defaults", {"set": {"allow_private": True}})
U = "http://127.0.0.1:8399/article?cb=p08emoji"

p.set_scenario("S1-emoji 文件名保存")
r = p.call("web_read", {"url": U, "save_to": str(a)})
print(f"   ok={r.get('content_ok')} saved={a.exists()} bytes={(a.stat().st_size if a.exists() else None)}")

p.pause(3)
p.set_scenario("S2-命中缓存时再存（另一个文件）")
r = p.call("web_read", {"url": U, "save_to": str(b)})
print(f"   cached={r.get('cached')} saved={b.exists()} bytes={(b.stat().st_size if b.exists() else None)} "
      f"err={str(r.get('error'))[:90]}")

p.pause(3)
p.set_scenario("S3-cache_ttl=0 强制回源再存")
r = p.call("web_read", {"url": U, "cache_ttl": 0, "save_to": str(c)})
print(f"   cached={r.get('cached')} saved={c.exists()} bytes={(c.stat().st_size if c.exists() else None)}")
print("P08 DONE")
