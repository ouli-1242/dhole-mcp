"""P01 苏禾 · 中文编辑 L2 —— 中文搜索 · 中文 URL 参数 · CJK recall。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(1, "苏禾·中文编辑")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-中文搜索")
r = p.call("web_search", {"query": "缓存 失效 重新验证", "max_results": 3})
print(f"   {str(r.get('summary'))[:200]}")
for x in (r.get("results") or [])[:3]:
    print("   -", str({k: x.get(k) for k in ('title', 'url') if x.get(k)})[:140])

p.pause(4)
p.set_scenario("S2-中文 URL 参数两读")
U = "http://127.0.0.1:8399/article?cb=中文参数"
a = p.call("web_read", {"url": U})
b = p.call("web_read", {"url": U})
print(f"   ok={a.get('content_ok')} cached={a.get('cached')}->{b.get('cached')} "
      f"same_ts={a.get('fetched_at') == b.get('fetched_at')} chars={a.get('total_extracted_chars')}")

p.pause(4)
p.set_scenario("S3-中文 recall")
r = p.call("web_recall", {"query": "中文 参数 页面", "limit": 4})
print(f"   results={len(r.get('results') or [])} terms={str(r.get('terms_found'))[:80]}")
for x in (r.get("results") or [])[:3]:
    print("   -", str({k: x.get(k) for k in ('url', 'title') if x.get(k)})[:140])

p.pause(3)
p.set_scenario("S4-state")
r = p.call("sys_state", {})
print(f"   message: {str(r.get('message'))[:140]}")
print("P01 DONE")
