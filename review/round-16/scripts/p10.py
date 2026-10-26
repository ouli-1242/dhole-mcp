"""P10 艾米 · 多语言 QA L3 —— 混合页 · crawl/discover 多语言站 · emoji 文件。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(10, "艾米·多语言QA")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-混合语言页两读")
U = "http://127.0.0.1:8398/mixed.html?cb=qa"
a = p.call("web_read", {"url": U})
b = p.call("web_read", {"url": U})
print(f"   ok={a.get('content_ok')} cached={a.get('cached')}->{b.get('cached')} "
      f"same_ts={a.get('fetched_at') == b.get('fetched_at')}")

p.pause(4)
p.set_scenario("S2-crawl 多语言站")
r = p.call("web_crawl", {"url": "http://127.0.0.1:8398/", "max_pages": 4})
print(f"   {str(r.get('summary'))[:220]}")
for x in (r.get("pages") or [])[:5]:
    print("   -", str({k: x.get(k) for k in ('url', 'content_ok', 'page_type', 'total_extracted_chars') if x.get(k) is not None})[:170])

p.pause(4)
p.set_scenario("S3-discover 多语言站")
r = p.call("web_discover", {"url": "http://127.0.0.1:8398/", "max_pages": 6})
print(f"   {str(r.get('summary'))[:220]}")
print(f"   rows={len(r.get('pages') or [])}")

p.pause(4)
p.set_scenario("S4-emoji 文件解析")
F = Path(__file__).resolve().parent.parent / "sessions" / "files"
r = p.call("doc_parse", {"file_path": str(F / "emoji_table.csv")})
txt = "".join(str(x) for x in (r.get("content") or []))
print(f"   ok={r.get('content_ok')} emoji={'😀' in txt} accent={'café' in txt} chars={len(txt)}")

p.pause(3)
p.set_scenario("S5-state")
r = p.call("sys_state", {})
print(f"   message: {str(r.get('message'))[:140]}")
print("P10 DONE")
