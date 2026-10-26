"""P04 明溪 · 口腔医生 L1 —— 链条④feed 增量→parse 附件→read 原文（r11 必测1/4 回归）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

fdir = Path(__file__).resolve().parent.parent / "sessions" / "files"
fdir.mkdir(parents=True, exist_ok=True)

p = PersonaSim(4, "明溪·口腔医生")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-feed max_items=3")
r = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"], "max_items": 3})
feeds = r.get("feeds") or r.get("results") or []
print(f"   feeds 行={len(feeds)} | {str(r.get('summary'))[:120]}")

p.pause(6)
p.set_scenario("S2-feed max_items=0 (=all, r11 V2 回归)")
r = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"], "max_items": 0})
feeds = r.get("feeds") or r.get("results") or []
n = len((feeds[0] or {}).get("items") or []) if feeds else 0
print(f"   条目数={n}（应 8）| {str(r.get('summary'))[:110]}")

p.pause(6)
p.set_scenario("S3-feed max_items=-3（r11 fix 回归：具名拒绝）")
r = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"], "max_items": -3})
print(f"   err: {str(r.get('error'))[:140]}")

p.pause(6)
p.set_scenario("S4-parse 附件（构造复诊须知）")
f = fdir / "fu_zhen.csv"
f.write_text("项目,间隔\n洗牙,6个月\n复查,3个月\n拍片,12个月\n", encoding="utf-8")
r = p.call("doc_parse", {"file_path": str(f)})
print(f"   | {str(r.get('summary'))[:110]}")

p.pause(6)
p.set_scenario("S5-read 原文比对")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/article"})
print(f"   status={r.get('status')} chars={r.get('total_extracted_chars')} | {str(r.get('summary'))[:100]}")
print("P04 DONE")
