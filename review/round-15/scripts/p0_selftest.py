"""round-15 P0 自测：initialize / tools/list / 真 search / 靶站可达。"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import Client, PROTOCOL

c = Client()
resp, _ = c.rpc("initialize", {
    "protocolVersion": PROTOCOL, "capabilities": {},
    "clientInfo": {"name": "round15-selftest", "version": "1"}})
c.post({"jsonrpc": "2.0", "method": "notifications/initialized"})
info = ((resp or {}).get("result") or {}).get("serverInfo")
print("server:", json.dumps(info, ensure_ascii=False))

resp, _ = c.rpc("tools/list")
tools = [t.get("name") for t in (((resp or {}).get("result") or {}).get("tools") or [])]
print("tools(%d):" % len(tools), ", ".join(tools))

resp, _ = c.rpc("tools/call", {"name": "web_search",
                              "arguments": {"query": "dhole mcp", "max_results": 2}})
res = (resp or {}).get("result") or {}
texts = [x.get("text", "") for x in (res.get("content") or []) if isinstance(x, dict)]
try:
    parsed = json.loads(texts[0]) if texts else {}
except Exception:
    parsed = {"_raw": texts[0][:300] if texts else ""}
rows = parsed.get("results") or []
print("search: rows=%d rerank=%s err=%s" % (
    len(rows), (parsed.get("diagnosis") or {}).get("rerank"), parsed.get("error")))
for r in rows[:2]:
    print("  -", str(r.get("url"))[:90])

resp, _ = c.rpc("tools/call", {"name": "web_read",
                              "arguments": {"url": "http://127.0.0.1:8399/about"}})
res = (resp or {}).get("result") or {}
texts = [x.get("text", "") for x in (res.get("content") or []) if isinstance(x, dict)]
try:
    parsed = json.loads(texts[0]) if texts else {}
except Exception:
    parsed = {"_raw": texts[0][:300] if texts else ""}
print("fixture: ok=%s chars=%s err=%s" % (
    parsed.get("content_ok"), parsed.get("total_extracted_chars"), parsed.get("error")))
print("SELFTEST DONE")
