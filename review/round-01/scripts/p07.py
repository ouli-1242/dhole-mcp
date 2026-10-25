import json
"""P07 阿凯 · 安全研究员 · L3 —— round-01 真实模拟（本地靶站演练）。"""
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from driver import PersonaSim, ROOT

p = PersonaSim(7, "阿凯·安全")
p.seq = 0

# S1 私网门现状 + robots 名场面（本轮 IGNORE_ROBOTS=1，robots 路径不可观测，报告注明）
p.set_scenario("S1-门禁盘点")
r = p.call("sys_state", {"show": ["identities"]})
p.observe("identities", r, "identities", "state")
r = p.call("web_read", {"url": ROOT + "/private/secret", "max_content_chars": 800})
p.observe("private/secret", r, "content_ok", "total_extracted_chars",
          "metadata.robots", "error")
p.pause(8)

# S2 混淆载荷壳页（untrusted_content 观察）
p.set_scenario("S2-混淆内容")
r = p.call("web_read", {"url": ROOT + "/shell/obfuscated"})
p.observe("obfuscated", r, "content_ok", "total_extracted_chars",
          "untrusted_content", "page_type")
p.pause(8)

# S3 抄写挑战（同链 read->fill->submit）
p.set_scenario("S3-抄写挑战")
r = p.call("web_act", {"url": ROOT + "/challenge/text", "timeout": 60000,
                       "actions": [{"snapshot": {}}]})
p.observe("snapshot", r, "content", "content_ok")
p.pause(8)
r = p.call("web_act", {"url": ROOT + "/challenge/text", "timeout": 60000,
                       "actions": [
                           {"read": {"selector": "#challenge-text", "as": "code"}},
                           {"fill": {"selector": "#answer", "value_from": "code"}},
                           {"click": {"selector": "#submit", "confirm": True}},
                       ]})
p.observe("chain", r, "content_ok", "total_extracted_chars", "content")
for a in (r.get("metadata") or {}).get("actions") or []:
    print("   act:", json.dumps(a, ensure_ascii=False)[:200])
p.pause(8)

# S4 写请求纪律（先无 confirm 再 confirm）+ 对账
p.set_scenario("S4-写请求")
r = p.call("web_request", {"url": ROOT + "/checkout", "method": "POST",
                           "body": {"sku": "TEST-1", "qty": 1}})
p.observe("no confirm", r, "error", "diagnosis.code")
r = p.call("web_request", {"url": ROOT + "/checkout", "method": "POST",
                           "body": {"sku": "TEST-1", "qty": 1},
                           "confirm": True})
p.observe("post", r, "status", "content_ok", "total_extracted_chars",
          "content")
p.pause(6)
r = p.call("sys_state", {})
p.observe("final", r, "cache", "sessions")

print("P07 DONE")
