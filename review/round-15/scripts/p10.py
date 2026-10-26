"""P10 江照 · 编剧 L1 —— 两个上下文的 recall 交错 · 长文续片 · 订阅。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(10, "江照·编剧")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-长文第一片")
U = "http://127.0.0.1:8399/longaside?cb=p10"
a = p.call("web_read", {"url": U, "max_content_chars": 450})
print(f"   truncated={a.get('is_truncated')} next={a.get('next_offset')} "
      f"summary={str(a.get('summary'))[:190]}")

p.pause(3)
p.set_scenario("S2-续片（尾窗也点切片）")
nxt = a.get("next_offset") or 450
b = p.call("web_read", {"url": U, "max_content_chars": 450, "offset": nxt})
print(f"   offset={nxt} truncated={b.get('is_truncated')} summary={str(b.get('summary'))[:220]}")

p.pause(4)
p.set_scenario("S3-订阅近三小时")
r = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"],
                         "since": "3h", "max_items": 3})
for x in r.get("feeds") or []:
    print(f"   since={x.get('since')} older={x.get('items_older_than_since')} "
          f"items={len(x.get('items') or [])}")

p.pause(4)
p.set_scenario("S4-recall 两个上下文的素材")
r = p.call("web_recall", {"query": "长文 脚本 素材", "limit": 5})
print(f"   results={len(r.get('results') or [])}")
for x in (r.get("results") or [])[:5]:
    print("   -", str({k: x.get(k) for k in ('url', 'title', 'is_stale') if x.get(k) is not None})[:160])

p.pause(4)
p.set_scenario("S5-长文再读（阅读缓存）")
c = p.call("web_read", {"url": U, "max_content_chars": 450})
print(f"   cached={c.get('cached')} same_ts={a.get('fetched_at') == c.get('fetched_at')} "
      f"summary={str(c.get('summary'))[:160]}")
print("P10 DONE")
