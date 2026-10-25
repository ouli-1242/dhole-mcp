"""P01b: robots 三态重测（旁证 9205），带原始回执调试。"""
import os, subprocess, sys, time, json
from pathlib import Path
import httpx

HERE = Path(__file__).resolve().parent.parent
env = {k: v for k, v in os.environ.items() if not k.startswith("DHOLE_")}
env["PYTHONIOENCODING"] = "utf-8"
side = subprocess.Popen(
    [sys.executable, "-m", "dhole_mcp", "--http", "--host", "127.0.0.1", "--port", "9205"],
    env=env, stdout=open(os.devnull, "w"), stderr=subprocess.STDOUT,
    cwd=str(HERE.parent.parent))
time.sleep(10)
try:
    c = httpx.Client(trust_env=False, timeout=90)
    H = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    r = c.post("http://127.0.0.1:9205/mcp", headers=H, json={"jsonrpc": "2.0", "id": 0,
        "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {},
        "clientInfo": {"name": "p01b", "version": "1"}}})
    sid = r.headers.get("mcp-session-id")
    print("sid:", bool(sid))
    c.post("http://127.0.0.1:9205/mcp", headers={**H, "Mcp-Session-Id": sid},
           json={"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call(tool, args):
        rr = c.post("http://127.0.0.1:9205/mcp", headers={**H, "Mcp-Session-Id": sid},
                    json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                          "params": {"name": tool, "arguments": args}})
        body = rr.text
        for line in body.splitlines():
            if line.startswith("data:"):
                body = line[5:].strip()
                break
        try:
            resp = json.loads(body)
        except Exception:
            print("  RAW:", body[:200])
            return {}
        txt = ((resp.get("result") or {}).get("content") or [{}])[0].get("text", "")
        try:
            return json.loads(txt)
        except Exception:
            return {"_raw": txt[:200]}

    r1 = call("web_read", {"url": "http://127.0.0.1:8399/private/secret"})
    print("  态1 无豁免:", str(r1.get("error"))[:100])
    call("cred_defaults", {"set": {"allow_private": True}})
    r2 = call("web_read", {"url": "http://127.0.0.1:8399/private/secret"})
    print("  态2 豁免无ignore:", str(r2.get("error"))[:100])
    r3 = call("web_read", {"url": "http://127.0.0.1:8399/private/secret", "ignore_robots": True})
    s3 = str(r3.get("summary"))[:160]
    print("  态3 ignore=true:", "bypassed" in s3, "|", s3[:130])
finally:
    side.terminate()
print("P01b DONE")
