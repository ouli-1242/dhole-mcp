"""P01 沈砚青 · 站点管理员/SEO L3 猎手 —— robots 受控三态（旁证 9205 env 关）+ crawl path_include + sys_state 对账。"""
import os, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

HERE = Path(__file__).resolve().parent.parent
p = PersonaSim(1, "沈砚青·站长")

p.set_scenario("S1-crawl path_include 语义")
p.call("cred_defaults", {"set": {"allow_private": True}})
r = p.call("web_crawl", {"url": "http://127.0.0.1:8399/collections", "max_pages": 4,
                         "path_include": ["/work"], "cache_ttl": 0})
p.observe("crawl-scope", r, "summary")
p.rows(r, "pages", 4, 100)
p.pause(8)

p.set_scenario("S2-受控旁证 9205（env 关）: robots 三态")
env = {k: v for k, v in os.environ.items() if not k.startswith("DHOLE_")}
env["PYTHONIOENCODING"] = "utf-8"
side = subprocess.Popen(
    [sys.executable, "-m", "dhole_mcp", "--http", "--host", "127.0.0.1", "--port", "9205"],
    env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    cwd=str(HERE.parent.parent))
time.sleep(8)

import httpx

def call(port, tool, args, sid=None):
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
    if sid:
        headers["Mcp-Session-Id"] = sid
    c = httpx.Client(trust_env=False, timeout=60)
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": tool, "arguments": args}}
    r = c.post(f"http://127.0.0.1:{port}/mcp", json=body, headers=headers)
    newsid = r.headers.get("mcp-session-id") or sid
    text = r.text
    for line in text.splitlines():
        if line.startswith("data:"):
            text = line[5:].strip()
            break
    try:
        resp = json.loads(text)
        content = ((resp.get("result") or {}).get("content") or [{}])[0].get("text", "")
        return newsid, (json.loads(content) if content else {})
    except Exception:
        return newsid, {"_raw": text[:300]}

_, init = call(9205, "__init__", {}) if False else (None, None)
c = httpx.Client(trust_env=False, timeout=60)
r = c.post("http://127.0.0.1:9205/mcp", headers={
    "Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
    json={"jsonrpc": "2.0", "id": 0, "method": "initialize",
          "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                     "clientInfo": {"name": "p01-sidecar", "version": "1"}}})
sid = r.headers.get("mcp-session-id")
c.post("http://127.0.0.1:9205/mcp", headers={
    "Content-Type": "application/json", "Accept": "application/json, text/event-stream",
    "Mcp-Session-Id": sid}, json={"jsonrpc": "2.0", "method": "notifications/initialized"})

# 态 1: 无豁免 → 私网门先拒
_, r1 = call(9205, "web_read", {"url": "http://127.0.0.1:8399/private/secret"}, sid)
print("  态1 无豁免:", str(r1.get("error"))[:110])
# 态 2: 豁免 + 不带 ignore → robots_disallowed
call(9205, "cred_defaults", {"set": {"allow_private": True}}, sid)
_, r2 = call(9205, "web_read", {"url": "http://127.0.0.1:8399/private/secret"}, sid)
print("  态2 豁免无ignore:", str(r2.get("error"))[:110])
# 态 3: 豁免 + ignore_robots=true → 200 + bypassed 标注
_, r3 = call(9205, "web_read", {"url": "http://127.0.0.1:8399/private/secret",
                                "ignore_robots": True}, sid)
s3 = str(r3.get("summary"))[:150]
print("  态3 ignore=true:", "bypassed" in s3, "|", s3[:120])
p.pause(8)

p.set_scenario("S3-sys_state 对账")
r = p.call("sys_state", {})
p.observe("state", r, "summary")
print("      (三态只发生在旁证进程；主进程 census 应不受污染)")
side.terminate()
print("P01 DONE")
