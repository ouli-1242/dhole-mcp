"""P06 唐栗 · 兽医 L2 —— 本地 PDF 指定页 · feed 相对 since · 回访缓存。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

fdir = Path(__file__).resolve().parent.parent / "sessions" / "files"
fdir.mkdir(parents=True, exist_ok=True)
pdf = fdir / "pharmacopoeia.pdf"
if not pdf.exists():
    pdf.write_bytes(Path("tests/wrapped_heading.pdf").read_bytes())

p = PersonaSim(6, "唐栗·兽医")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-PDF 指定页 2-3")
r = p.call("doc_parse", {"file_path": str(pdf), "pages": "2-3"})
txt = "".join(str(c) for c in (r.get("content") or []))
print(f"   ok={r.get('content_ok')} chars={len(txt)} summary={str(r.get('summary'))[:120]}")
print(f"   head={txt[:100]!r}")

p.pause(5)
p.set_scenario("S2-feed 一周内公告")
r = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"],
                         "since": "1w", "max_items": 3})
for x in r.get("feeds") or []:
    print(f"   since={x.get('since')} older={x.get('items_older_than_since')} "
          f"items={len(x.get('items') or [])}")

p.pause(5)
p.set_scenario("S3-剂量页读两遍")
u = "http://127.0.0.1:8399/article?cb=p06-dose"
a = p.call("web_read", {"url": u})
b = p.call("web_read", {"url": u})
print(f"   first cached={a.get('cached')} second cached={b.get('cached')} "
      f"same_ts={a.get('fetched_at') == b.get('fetched_at')}")

p.pause(5)
p.set_scenario("S4-state 复盘")
r = p.call("sys_state", {})
print(f"   message: {str(r.get('message'))[:180]}")
print("P06 DONE")
