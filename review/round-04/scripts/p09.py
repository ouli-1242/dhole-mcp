"""P09 阮青梧 · 心理咨询师 L2 —— allow_private 点名清单边界 + 撤销豁免。"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim
p = PersonaSim(9, "阮青梧·咨询师")
p.set_scenario("S1-点名放行一台主机")
r = p.call("cred_defaults", {"set": {"allow_private": ["127.0.0.1"]}})
p.observe("set-list", r, "message")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/article", "timeout": 30000})
p.observe("named-host read", r, "content_ok")
p.pause(7)
p.set_scenario("S2-名单外的主机仍拒")
r = p.call("web_read", {"url": "http://127.0.0.2:8399/article", "timeout": 20000})
p.observe("other-host", r, "error")
p.pause(7)
p.set_scenario("S3-撤销豁免(unset)再试")
r = p.call("cred_defaults", {"unset": ["allow_private"]})
p.observe("unset", r, "message")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/article?fresh=1", "cache_ttl": 0, "timeout": 30000})
p.observe("after-unset", r, "error")
print("P09 DONE")
