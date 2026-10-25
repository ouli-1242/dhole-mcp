"""P05b 霍砚:按 next_action 照抄 confirm=true 重发 POST(写闸解锁路径)。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(15, "霍砚·重发")
r = p.call("web_request", {"method": "POST", "url": "http://127.0.0.1:8399/login",
                           "body": {"username": "demo", "password": "wrong"},
                           "headers": {"Content-Type": "application/x-www-form-urlencoded"},
                           "confirm": True, "timeout": 20000})
p.observe("post-confirm", r, "status", "content_ok", "summary")
p.observe("headers-ct", r, "headers.Content-Type")
print("P05b DONE")
