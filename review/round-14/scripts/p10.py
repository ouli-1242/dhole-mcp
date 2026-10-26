"""P10 罗青 · 编剧 L1 —— 长文续读 · 查变化 · 搜索 · 点名取块（R13-001 回归）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(10, "罗青·编剧")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-search 背景资料")
r = p.call("web_search", {"query": "history of the printing press", "max_results": 2})
print(f"   {str(r.get('summary'))[:180]}")

p.pause(12)
p.set_scenario("S2-长文截断（max 500）")
U = "http://127.0.0.1:8399/longaside?cb=p10-story"
r = p.call("web_read", {"url": U, "max_content_chars": 500})
first = "".join(r.get("content") or [])
print(f"   truncated={r.get('is_truncated')} next={r.get('next_offset')} "
      f"head={first[:60]!r}")

p.pause(5)
p.set_scenario("S3-offset 续读（总结是否点名切片）")
r = p.call("web_read", {"url": U, "max_content_chars": 500,
                        "offset": r.get("next_offset") or 500})
second = "".join(r.get("content") or [])
print(f"   chars={len(second)} summary={str(r.get('summary'))[:260]}")
print(f"   overlap_head={second[:40]!r}")

p.pause(5)
p.set_scenario("S4-check 记基线")
r = p.call("web_read", {"urls": [U], "extraction_type": "check"})
print(f"   {str(r.get('summary'))[:160]}")

p.pause(4)
p.set_scenario("S5-check 隔天再看（应 unchanged）")
r = p.call("web_read", {"urls": [U], "extraction_type": "check"})
print(f"   {str(r.get('summary'))[:180]}")

p.pause(6)
p.set_scenario("S6-点名取块（css_selector 回归）")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/article?cb=p10-css",
                        "css_selector": "h1"})
print(f"   content_ok={r.get('content_ok')} chars={r.get('total_extracted_chars')} "
      f"err={str(r.get('error'))[:80]}")
print("P10 DONE")
