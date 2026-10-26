"""P07 顾之遥 · 博物馆策展人 L2 —— discover→crawl→pin 会话→recall。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(7, "顾之遥·博物馆策展人")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-discover 馆藏地图")
r = p.call("web_discover", {"url": "http://127.0.0.1:8399/", "max_depth": 2})
print(f"   {str(r.get('summary'))[:160]}")
print(f"   pages={len(r.get('pages') or [])}")

p.pause(6)
p.set_scenario("S2-crawl 抓前三页")
r = p.call("web_crawl", {"url": "http://127.0.0.1:8399/", "max_pages": 3, "max_depth": 2})
print(f"   {str(r.get('summary'))[:200]}")
for x in (r.get('pages') or [])[:4]:
    print("   -", str({k: x.get(k) for k in ('url', 'content_ok', 'page_type') if x.get(k) is not None})[:150])

p.pause(6)
p.set_scenario("S3-pin 住详情页改标签")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/dialog",
                       "session_id": "p07pin", "pin": True, "timeout": 45000,
                       "actions": [
                           {"dialog": {"answer": "accept", "prompt_text": "顾之遥"}},
                           {"click": {"selector": "#namebtn"}},
                           {"read": {"selector": "#status"}},
                       ]})
acts = (r.get("metadata") or {}).get("actions") or []
for a in acts:
    print(f"   {a.get('action')}: ok={a.get('ok')} read={str(a.get('read'))[:40]!r}")
print(f"   pin-pending 句={'pin' in str(r.get('summary')).lower()}")

p.pause(4)
p.set_scenario("S4-pin 命中回读")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/dialog",
                       "session_id": "p07pin", "pin": True, "timeout": 45000,
                       "actions": [{"read": {"selector": "#status"}}]})
acts = (r.get("metadata") or {}).get("actions") or []
print(f"   pin hit={'pin hit' in str(r.get('summary')).lower()} "
      f"read={str(acts[0].get('read')) if acts else None!r}")

p.pause(5)
p.set_scenario("S5-recall 找展品页")
r = p.call("web_recall", {"query": "对话框 删除", "limit": 4})
print(f"   results={len(r.get('results') or [])}")
for x in (r.get('results') or [])[:3]:
    print("   -", str({k: x.get(k) for k in ('url', 'title') if x.get(k)})[:140])
print("P07 DONE")
