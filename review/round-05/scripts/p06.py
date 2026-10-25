"""P06 秋原野 · 摄影师 L1 —— 边界值钳制申报。"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim
p = PersonaSim(6, "秋原野·摄影师")
p.set_scenario("S1-max_content_chars=-1")
p.call("cred_defaults", {"set": {"allow_private": True}})
r = p.call("web_read", {"url": "http://127.0.0.1:8399/longaside", "max_content_chars": -1})
p.observe("neg", r, "error", "summary")
p.pause(7)
p.set_scenario("S2-offset=99999")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/longaside", "offset": 99999})
p.observe("big-offset", r, "error", "summary", "total_extracted_chars")
p.pause(7)
p.set_scenario("S3-超大上限(正常值)")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/longaside?big=1", "cache_ttl": 0,
                        "max_content_chars": 200000, "timeout": 30000})
p.observe("max-cap", r, "summary")
p.pause(6)
p.set_scenario("S4-login 链(web_act)")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/login", "actions": [
    {"fill": {"selector": "#username", "text": "photo"}},
    {"fill": {"selector": "#password", "text": "photo"}},
    {"click": {"selector": "#login-btn", "confirm": True}},
], "timeout": 60000})
p.observe("login", r, "summary")
print("P06 DONE")
