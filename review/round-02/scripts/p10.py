"""P10 岑砚 · 研究生 · L2 —— round-02（跨会话召回/受控 per-call robots/变体）。"""
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim, BASE

p = PersonaSim(10, "岑砚·研究生")

p.set_scenario("S1-读两页再召回")
r = p.call("web_read", {"url": "https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/individual-rights/",
                        "max_content_chars": 2000})
p.observe("read1", r, "content_ok")
p.pause(5)
r = p.call("web_read", {"url": "http://127.0.0.1:8399/article", "focus": "专题", "cache_ttl": 0})
p.observe("read2", r, "content_ok")
p.pause(6)
r = p.call("web_recall", {"query": "data protection individual rights", "limit": 5})
p.observe("recall-en", r, "summary")
p.rows(r, "results", 5, 120)
p.pause(5)
r = p.call("web_recall", {"query": "专题 长文", "limit": 5})
p.observe("recall-zh", r, "summary")
p.rows(r, "results", 5, 120)
p.pause(8)

p.set_scenario("S3-变体观察")
r = p.call("web_recall", {"query": "推荐专题 作品", "limit": 8})
p.rows(r, "results", 8, 130)
p.pause(6)

p.set_scenario("S2-受控旁证：per-call ignore_robots（本地靶站专用进程，env 关闭）")
# 宪章 §8：受控旁证另起只指向本地靶站的进程并关掉 IGNORE_ROBOTS；严禁关标志访问公网
import os
env = dict(os.environ)
env.pop("DHOLE_IGNORE_ROBOTS", None)   # 受控进程：关掉进程级豁免
env.pop("DHOLE_USAGE_LOG", None)
sub = subprocess.Popen(["dhole", "--http", "--host", "127.0.0.1", "--port", "9203"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
import socket
for _ in range(30):
    time.sleep(1)
    s = socket.socket()
    try:
        s.connect(("127.0.0.1", 9203)); s.close(); break
    except OSError:
        continue
try:
    import httpx, json
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
    d = call9203('web_read', {'url': 'http://127.0.0.1:8399/private/secret'})
    print("      [env=off 无豁免] error:", (d.get('error') or 'NONE')[:80])
    d = call9203('cred_defaults', {'set': {'allow_private': True}})
    d = call9203('web_read', {'url': 'http://127.0.0.1:8399/private/secret',
                              'ignore_robots': True})
    print("      [per-call ignore_robots=true] error:", (d.get('error') or 'NONE')[:80],
          "| status:", d.get('status'), "| robots:", (d.get('metadata') or {}).get('robots'))
    (p.dir / '07_web_read_sidecar.json').write_text(
        json.dumps(d, ensure_ascii=False, indent=1), encoding='utf-8')
finally:
    sub.terminate()

print("P10 DONE")
