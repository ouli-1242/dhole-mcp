"""P09 库尔班5 · 营养师 L1 —— 照手册 read 路线（focus/css_selector 组合+自救）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(9, "库尔班5·营养师")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-照手册 focus")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/article", "focus": "本地抓取", "cache_ttl": 0})
print(f"   focus: ok={r.get('content_ok')} chars={len(str(r.get('content') or ''))} | {str(r.get('summary'))[:110]}")

p.pause(6)
p.set_scenario("S2-css_selector 组合")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/collections", "css_selector": ".card a", "cache_ttl": 0})
print(f"   css: ok={r.get('content_ok')} | {str(r.get('summary'))[:120]}")

p.pause(6)
p.set_scenario("S3-css 0 匹配(R06-F1 家产:说出去)")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/article", "css_selector": "#nope", "cache_ttl": 0})
s = str(r.get("summary"))
print(f"   0 匹配注记在场: {'NOT applied' in s or 'matched 0' in s} | {s[:130]}")
print("P09 DONE")
