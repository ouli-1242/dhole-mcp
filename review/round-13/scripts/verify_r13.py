"""round-13 P5 真重启复验：R13-001 / R13-002 + 阴性对照 + 家产抽检。"""
import sys, json, uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import Client

c = Client()
c.rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                     "clientInfo": {"name": "r13-verify", "version": "0"}})
c.rpc("notifications/initialized")


def call(tool, args):
    r, _ = c.rpc("tools/call", {"name": tool, "arguments": args})
    txt = "".join(b.get("text", "") for b in (r.get("result") or {}).get("content", [])
                  if isinstance(b, dict))
    try:
        return json.loads(txt) if txt else {}
    except Exception:
        return {"_raw": txt}


call("cred_defaults", {"set": {"allow_private": True}})


def emit(head, ok, detail):
    print(f"[{'PASS' if ok else 'FAIL'}] {head}")
    print(f"      {detail[:260]}")


# V1 · R13-001：显式 selector 收窄不再判 extraction_collapsed
u1 = "http://127.0.0.1:8399/article?cb=v13a-" + uuid.uuid4().hex[:8]
r = call("web_read", {"url": u1, "css_selector": "h1"})
err = str(r.get("error") or "")
emit("V1 R13-001 收窄不误判",
     r.get("content_ok") is True and not err and r.get("total_extracted_chars") == 10,
     f"content_ok={r.get('content_ok')} chars={r.get('total_extracted_chars')} err={err[:100]}")

# V2 · R13-002：0 匹配注记 live 与回放都在
u2 = "http://127.0.0.1:8399/article?cb=v13b-" + uuid.uuid4().hex[:8]
a = call("web_read", {"url": u2, "css_selector": "#no-such"})
b = call("web_read", {"url": u2, "css_selector": "#no-such", "cache_ttl": 3600})
sa, sb = str(a.get("summary") or ""), str(b.get("summary") or "")
emit("V2 R13-002 回放补回 selector 0 匹配注记",
     "matched 0 elements" in sa and "matched 0 elements" in sb and b.get("cached") is True,
     f"live={'matched 0 elements' in sa} cached={b.get('cached')} replay={'matched 0 elements' in sb}")

# V3 · 对照：收窄不报；无 selector 的旧塌陷页由恢复尺接住（线上无法再触发 collapse——
# /longaside 现由 plain converter 回收，summary 点名）。判决尺的阴性由单测
# test_without_a_selector_a_real_loss_stays_collapsed 承担。
u3 = "http://127.0.0.1:8399/longaside?cb=v13c-" + uuid.uuid4().hex[:8]
n = call("web_read", {"url": u3})
u4 = "http://127.0.0.1:8399/longaside?cb=v13d-" + uuid.uuid4().hex[:8]
s = call("web_read", {"url": u4, "css_selector": "article.main"})
ne = str(n.get("error") or "")
se = str(s.get("error") or "")
ns = str(n.get("summary") or "")
emit("V3 对照：点名收窄不报 / 无 selector 旧页由恢复尺接住",
     not se.startswith("extraction_collapsed") and n.get("content_ok") is True
     and "plain converter" in ns,
     f"selector-err={se[:60] or '(none)'} | no-selector content_ok={n.get('content_ok')} "
     f"recovered={'plain converter' in ns} err={ne[:60] or '(none)'}")

# V4 · 家产抽检：pin 命中 + feed 负数拒
r = call("web_act", {"url": "http://127.0.0.1:8399/login", "session_id": "v13pin",
                     "pin": True, "timeout": 45000,
                     "actions": [{"fill": {"selector": "input[name=username]", "text": "r13"}},
                                 {"read": {"selector": "input[name=username]",
                                           "property": "value", "as": "who"}}]})
acts = (r.get("metadata") or {}).get("actions") or []
mint = next((a.get("read") for a in acts if a.get("read_as")), "")
r = call("web_act", {"url": "http://127.0.0.1:8399/login", "session_id": "v13pin",
                     "pin": True, "timeout": 45000,
                     "actions": [{"read": {"selector": "input[name=username]",
                                           "property": "value"}}]})
acts = (r.get("metadata") or {}).get("actions") or []
back = next((a.get("read") for a in acts if a.get("ok") and a.get("read")), "")
pin_hit = "pin hit" in str(r.get("summary") or "")
f = call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"], "max_items": -3})
emit("V4 家产：pin 命中 + feed 负数具名拒",
     mint == back == "r13" and pin_hit and str(f.get("error") or "").startswith(
         "invalid_request: max_items must be"),
     f"pin mint={mint!r} back={back!r} hit={pin_hit} | feed={str(f.get('error'))[:80]}")
print("VERIFY R13 DONE")
