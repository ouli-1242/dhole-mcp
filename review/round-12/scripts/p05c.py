"""P05c 补条2：pin 命中全链——fill + read(property:value) 铸值 → 第二发同 url/session/pin 回读。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(5, "常镜秋·评估师(补2)")

p.set_scenario("S11-pin 铸值（read 用 property:value）")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/login", "session_id": "p05c",
                       "pin": True, "timeout": 45000,
                       "actions": [
                           {"fill": {"selector": "input[name=username]", "text": "gupiaoren"}},
                           {"read": {"selector": "input[name=username]",
                                     "property": "value", "as": "who"}},
                       ]})
acts = (r.get("metadata") or {}).get("actions") or []
for a in acts:
    print(f"   {a.get('action')}: ok={a.get('ok')} read={str(a.get('read'))[:30]!r} as={a.get('read_as')}")
s = r.get("summary") or ""
print(f"   pinned 句在场={'page pinned' in s}")

p.pause(6)
p.set_scenario("S12-pin 命中回读（同 url/session/pin, TTL 内）")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/login", "session_id": "p05c",
                       "pin": True, "timeout": 45000,
                       "actions": [{"read": {"selector": "input[name=username]",
                                             "property": "value"}}]})
acts = (r.get("metadata") or {}).get("actions") or []
val = next((str(a.get("read")) for a in acts if a.get("ok") and a.get("read")), "")
print(f"   回读值: {val[:40]!r}（期待 gupiaoren=跨调用页保持）")
s = r.get("summary") or ""
print(f"   pin hit 句在场={'pin hit' in s}")
print("P05c DONE")
