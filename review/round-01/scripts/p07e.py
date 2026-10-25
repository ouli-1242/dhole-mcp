"""P07 续：全量读 call 手册 -> 找 fill 语义 -> 走通抄写挑战。"""
import sys, json, re
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from driver import PersonaSim, Client, ROOT

c = Client()
c.rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                     "clientInfo": {"name": "p07-manual", "version": "1"}})
c.post({"jsonrpc": "2.0", "method": "notifications/initialized"})
resp, _ = c.rpc("tools/call", {"name": "sys_guide", "arguments": {"topic": "call"}})
texts = [b.get("text", "") for b in ((resp or {}).get("result") or {}).get("content", [])
         if isinstance(b, dict)]
txt = "\n".join(texts)
print("manual len:", len(txt))
for kw in ("fill", "challenge"):
    for m in re.finditer(kw, txt):
        i = m.start()
        seg = txt[max(0, i-200):i+350].replace("\n", " | ")
        print("[%s] ...%s..." % (kw, seg))
        break

p = PersonaSim(7, "阿凯·安全")
p.seq = 12
p.set_scenario("S3e-按手册走链")
r = p.call("web_act", {"url": ROOT + "/challenge/text", "timeout": 60000,
                       "actions": [
                           {"read": {"selector": "#challenge-text", "as": "code"}},
                           {"fill": {"selector": "#answer", "text": "$code"}},
                           {"click": {"selector": "#submit", "confirm": True}},
                       ]})
p.observe("chain $code", r, "content_ok", "content")
for a in (r.get("metadata") or {}).get("actions") or []:
    print("   act:", json.dumps(a, ensure_ascii=False)[:260])
print("P07E DONE")
