"""P03 秦守拙 · 档案管理员 L2 —— cred_defaults 落盘跨重启 + watch 用落盘豁免真查私网（R02-001 出路的另一半）。"""
import os, subprocess, sys, time, json
from pathlib import Path
import httpx

HERE = Path(__file__).resolve().parent.parent
REPO = HERE.parent.parent

env = {k: v for k, v in os.environ.items() if not k.startswith("DHOLE_")}
env["DHOLE_PERSIST_CREDENTIALS"] = "1"
env["PYTHONIOENCODING"] = "utf-8"

def start_sidecar(port):
    return subprocess.Popen(
        [sys.executable, "-m", "dhole_mcp", "--http", "--host", "127.0.0.1", "--port", str(port)],
        env=env, stdout=open(os.devnull, "w"), stderr=subprocess.STDOUT, cwd=str(REPO))

def session(c, port):
    H = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    r = c.post(f"http://127.0.0.1:{port}/mcp", headers=H, json={"jsonrpc": "2.0", "id": 0,
        "method": "initialize", "params": {"protocolVersion": "2025-03-26", "capabilities": {},
        "clientInfo": {"name": "p03", "version": "1"}}})
    sid = r.headers.get("mcp-session-id")
    c.post(f"http://127.0.0.1:{port}/mcp", headers={**H, "Mcp-Session-Id": sid},
           json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    def call(tool, args):
        rr = c.post(f"http://127.0.0.1:{port}/mcp", headers={**H, "Mcp-Session-Id": sid},
                    json={"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                          "params": {"name": tool, "arguments": args}})
        body = rr.text
        for line in body.splitlines():
            if line.startswith("data:"):
                body = line[5:].strip(); break
        resp = json.loads(body)
        txt = ((resp.get("result") or {}).get("content") or [{}])[0].get("text", "")
        return json.loads(txt) if txt else {}
    return call

print("== 1. 起持久化旁证 9206, 存凭据 ==")
side = start_sidecar(9206)
time.sleep(9)
c = httpx.Client(trust_env=False, timeout=90)
call = session(c, 9206)
r = call("cred_defaults", {"set": {"allow_private": True,
                                   "auth": {"type": "basic", "username": "arch", "password": "secret"}}})
print("  set:", str(r.get("summary"))[:120] or "ok")
side.terminate(); side.wait(timeout=15)
print("  sidecar killed")

print("== 2. 重启旁证, 凭据应还在 ==")
side = start_sidecar(9206)
time.sleep(9)
try:
    c2 = httpx.Client(trust_env=False, timeout=90)
    call = session(c2, 9206)
    r = call("web_read", {"url": "http://127.0.0.1:8399/private/secret", "cache_ttl": 0})
    print("  重启后私网读:", r.get("content_ok"), "|", str(r.get("summary"))[:100])
finally:
    side.terminate(); side.wait(timeout=15)

print("== 3. watch 用落盘豁免真查私网(R02-001 出路的另一半) ==")
add = subprocess.run(["dhole", "watch", "add", "http://127.0.0.1:8399/changing"],
                     capture_output=True, text=True, timeout=60)
print("  add:", (add.stdout or add.stderr).strip()[:120])
run = subprocess.run(["dhole", "watch", "run"], capture_output=True, text=True, timeout=180)
blob = (run.stdout or "") + (run.stderr or "")
print("  run exit:", run.returncode)
for line in blob.splitlines():
    if "changing" in line:
        print("  row:", line.strip()[:180])
subprocess.run(["dhole", "watch", "remove", "http://127.0.0.1:8399/changing"],
               capture_output=True, text=True)
print("  (watchlist cleaned; 注意: 本步无 MCP 侧豁免进程, watch 靠的是第 1 步落盘的 allow_private)")
print("P03 DONE")
