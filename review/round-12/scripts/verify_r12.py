"""round-12 P5 真重启复验（V1-V3）：feed 负数 / pin 命中 / 第一拒形状。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import Client

c = Client()
c.rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                     "clientInfo": {"name": "r12-verify", "version": "0"}})
c.rpc("notifications/initialized")


def call(tool, args):
    r, _ = c.rpc("tools/call", {"name": tool, "arguments": args})
    txt = "".join(b.get("text", "") for b in (r.get("result") or {}).get("content", [])
                  if isinstance(b, dict))
    try:
        return json.loads(txt) if txt else {}
    except Exception:
        return {"_raw": txt}


import json  # noqa: E402

call("cred_defaults", {"set": {"allow_private": True}})  # 真重启后进程内默认清零，画像同款先置


def emit(head, ok, detail):
    print(f"[{'PASS' if ok else 'FAIL'}] {head}")
    print(f"      {detail[:220]}")

# V1: feed max_items 负数具名拒（R11-001 维持）+ 0=all
r = call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"], "max_items": -3})
v1a = str(r.get("error") or "")
r2 = call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"], "max_items": 0})
rows = r2.get("feeds") or r2.get("results") or []
n = len((rows[0] or {}).get("items") or []) if rows else 0
emit("V1 R11-001 维持: max_items=-3 具名拒 / 0=all",
     v1a.startswith("invalid_request: max_items must be") and n == 8,
     f"err={v1a[:90]} | 0=all items={n}")

# V2: pin 命中（本轮核心回归）：fill+read(property:value) → 第二发同 url/session/pin 回读
kw = {"url": "http://127.0.0.1:8399/login", "session_id": "v2pin", "pin": True,
      "timeout": 45000,
      "actions": [{"fill": {"selector": "input[name=username]", "text": "verifyr12"}},
                  {"read": {"selector": "input[name=username]", "property": "value",
                            "as": "who"}}]}
r = call("web_act", kw)
acts = (r.get("metadata") or {}).get("actions") or []
mint = next((a.get("read") for a in acts if a.get("read_as")), "")
r = call("web_act", {"url": "http://127.0.0.1:8399/login", "session_id": "v2pin",
                     "pin": True, "timeout": 45000,
                     "actions": [{"read": {"selector": "input[name=username]",
                                           "property": "value"}}]})
acts = (r.get("metadata") or {}).get("actions") or []
back = next((a.get("read") for a in acts if a.get("ok") and a.get("read")), "")
s2 = r.get("summary") or ""
emit("V2 pin 命中: 铸值跨调用回读一致",
     mint == "verifyr12" and back == "verifyr12" and "pin hit" in s2,
     f"mint={mint!r} back={back!r} pin-hit 句={'pin hit' in s2}")

# V3: 第一拒形状（本轮 P09 家产）：GET 拒 + css_selector 0 匹配有账
r = call("web_request", {"url": "http://127.0.0.1:8399/", "method": "GET"})
v3a = str(r.get("error") or "")
r = call("web_read", {"url": "http://127.0.0.1:8399/article", "css_selector": "#no-such",
                      "cache_ttl": 0})
v3s = r.get("summary") or ""
emit("V3 第一拒形状: GET 拒带码+出路 / css 0 匹配 summary 有账",
     v3a.startswith("invalid_request: method=GET") and "was NOT applied" in v3s,
     f"err={v3a[:80]} | NOT-applied 句={'was NOT applied' in v3s}")

print("VERIFY DONE")
