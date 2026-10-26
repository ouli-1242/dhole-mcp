"""P04 顾北望 · 天文台操作员 L2 —— 链④iframe 内操作→外内两本账→frame 错名回归→recall。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(4, "顾北望·天文台操作员")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-act iframe 内填+保存（frame=app）")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/frame", "frame": "app",
                       "timeout": 45000,
                       "actions": [{"fill": {"selector": "#note", "text": "北落师门"}},
                                   {"click": {"selector": "#save"}}]})
acts = (r.get("metadata") or {}).get("actions") or []
for a in acts:
    print(f"   {a.get('action')}: ok={a.get('ok')}")
na = str(r.get("next_action") or "")
if na:
    print(f"   next_action: {na[:140]}")

p.pause(6)
p.set_scenario("S2-act iframe 内读回执")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/frame", "frame": "app",
                       "timeout": 45000,
                       "actions": [{"read": {"selector": "#saved"}}]})
acts = (r.get("metadata") or {}).get("actions") or []
for a in acts:
    print(f"   {a.get('action')}: ok={a.get('ok')} read={str(a.get('read'))[:40]!r}")

p.pause(6)
p.set_scenario("S3-frame 错名（r08 家产：matched 0 <iframe> + 在场清单）")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/frame", "frame": "#no-such",
                       "timeout": 45000, "actions": [{"read": {"selector": "#saved"}}]})
print(f"   err: {str(r.get('error'))[:200]}")

p.pause(6)
p.set_scenario("S4-read 外层（外内两本账）")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/frame"})
print(f"   status={r.get('status')} chars={r.get('total_extracted_chars')} | {str(r.get('summary'))[:90]}")

p.pause(6)
p.set_scenario("S5-recall 收尾（iframe 内的词是否被索引）")
r = p.call("web_recall", {"query": "北落师门", "limit": 5})
res = r.get("results") or []
print(f"   results={len(res)}（0 也如实）| {str(r.get('summary'))[:90]}")
print("P04 DONE")
