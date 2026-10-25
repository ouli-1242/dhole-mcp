"""P10 越千帆4b · 数据标注员 L3 猎手 —— 组合态注入（act 步级 deadline 帽/crawl×read 代际交叉）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(10, "越千帆4b·数据标注员")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-act wait 帽申报(120s→30s)")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/about",
                       "actions": [{"wait": 120000}],
                       "timeout": 60000})
s = str(r.get("summary"))
print(f"   帽申报在场: {'30,000' in s or '30000' in s or 'capped' in s.lower()} | summary 尾: {s[-180:]}")

p.pause(6)
p.set_scenario("S2-wait_selector 慢元素(帽内超时账)")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/about",
                       "actions": [{"wait_selector": "#nope-forever"}],
                       "timeout": 45000})
err = ""
for a in (r.get("metadata") or {}).get("actions") or []:
    if not a.get("ok"):
        err = str(a.get("error") or "")
print(f"   err: {err[:160]}")

p.pause(6)
p.set_scenario("S3-crawl→read 代际交叉")
p.call("web_crawl", {"url": "http://127.0.0.1:8399", "max_pages": 2, "cache_ttl": 600})
r = p.call("web_read", {"url": "http://127.0.0.1:8399/", "cache_ttl": 600})
print(f"   crawl 后 read: cached={'cached' in str(r.get('summary')).lower()} | fetched_at: {r.get('fetched_at')}")

p.pause(6)
p.set_scenario("S4-组合边界(urls= 类型错第一拒)")
r = p.call("web_read", {"urls": "http://127.0.0.1:8399/"})
print(f"   err: {str(r.get('error'))[:180]}")
print("P10 DONE")
