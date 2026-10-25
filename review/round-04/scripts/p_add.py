"""P2 补条: web_request / web_discover / feed_read 覆盖(轮内 13/13 达标用)。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim
p = PersonaSim(30, "覆盖补条")
p.call("cred_defaults", {"set": {"allow_private": True}})
r = p.call("web_discover", {"url": "http://127.0.0.1:8399/collections", "timeout": 30000})
p.observe("discover", r, "summary")
r = p.call("web_request", {"method": "POST", "url": "http://127.0.0.1:8399/login",
                           "body": {"username": "a", "password": "b"}, "confirm": True,
                           "timeout": 20000})
p.observe("post", r, "status")
r = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"], "max_items": 3})
p.observe("feed", r, "summary")
p.rows(r, "feeds", 1, 100)
print("ADD DONE")
