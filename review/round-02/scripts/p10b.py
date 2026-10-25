"""P10b 受控旁证补全：env=off + allow_private + 不带 ignore_robots → 应被 robots 拒。"""
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(10, "岑砚·研究生")
p.set_scenario("S2b-受控隔离：无 per-call 豁免时应被 robots 拒")

env = dict(os.environ)
env.pop("DHOLE_IGNORE_ROBOTS", None)
sub = subprocess.Popen(["dhole", "--http", "--host", "127.0.0.1", "--port", "9203"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
for _ in range(30):
    time.sleep(1)
    s = socket.socket()
    try:
        s.connect(("127.0.0.1", 9203)); s.close(); break
    except OSError:
        continue

import httpx

def call9203(name, args):
    c = httpx.Client(trust_env=False, timeout=90)
    h = {'Accept': 'application/json, text/event-stream', 'Content-Type': 'application/json'}
    r0 = c.post('http://127.0.0.1:9203/mcp', json={'jsonrpc': '2.0', 'method': 'initialize', 'id': 1,
                'params': {'protocolVersion': '2025-03-26', 'capabilities': {},
                           'clientInfo': {'name': 'p10-sidecar', 'version': '1'}}}, headers=h)
    h['Mcp-Session-Id'] = r0.headers.get('mcp-session-id')
    c.post('http://127.0.0.1:9203/mcp', json={'jsonrpc': '2.0', 'method': 'notifications/initialized'}, headers=h)
    r2 = c.post('http://127.0.0.1:9203/mcp', json={'jsonrpc': '2.0', 'method': 'tools/call', 'id': 2,
                'params': {'name': name, 'arguments': args}}, headers=h)
    d2 = None
    for line in r2.text.splitlines():
        if line.startswith('data:'):
            d2 = line[5:].strip()
    texts = [x.get('text', '') for x in json.loads(d2)['result']['content'] if isinstance(x, dict)]
    return json.loads(texts[0])

try:
    call9203('cred_defaults', {'set': {'allow_private': True}})
    d = call9203('web_read', {'url': 'http://127.0.0.1:8399/private/secret',
                              'ignore_robots': False, 'cache_ttl': 0})
    print("      [env=off + 豁免 + 不带 ignore] error:", (d.get('error') or 'NONE')[:100])
    (p.dir / '08_web_read_sidecar_noignore.json').write_text(
        json.dumps(d, ensure_ascii=False, indent=1), encoding='utf-8')
finally:
    sub.terminate()

print("P10B DONE")
