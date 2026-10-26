"""round-16 P0 自测：13 工具 + 多语言页可达/保真（ar / gbk）。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import Client, PROTOCOL

c = Client()
resp, _ = c.rpc("initialize", {
    "protocolVersion": PROTOCOL, "capabilities": {},
    "clientInfo": {"name": "round16-selftest", "version": "1"}})
c.post({"jsonrpc": "2.0", "method": "notifications/initialized"})
print("server:", json.dumps(((resp or {}).get("result") or {}).get("serverInfo"), ensure_ascii=False))

resp, _ = c.rpc("tools/list")
tools = [t.get("name") for t in ((((resp or {}).get("result") or {}).get("tools")) or [])]
print("tools(%d):" % len(tools), ", ".join(tools))


def call(name, args):
    resp, _ = c.rpc("tools/call", {"name": name, "arguments": args})
    res = (resp or {}).get("result") or {}
    texts = [x.get("text", "") for x in (res.get("content") or []) if isinstance(x, dict)]
    try:
        return json.loads(texts[0]) if texts else {}
    except Exception:
        return {"_raw": texts[0][:300] if texts else ""}


call("cred_defaults", {"set": {"allow_private": True}})
for name, needle in (("ar.html", "العربية"), ("gbk.html", "国标")):
    d = call("web_read", {"url": "http://127.0.0.1:8398/" + name})
    txt = "".join(str(x) for x in (d.get("content") or []))
    print("%s ok=%s chars=%s needle=%s head=%r" % (
        name, d.get("content_ok"), d.get("total_extracted_chars"), needle in txt, txt[:60]))
print("SELFTEST DONE")
