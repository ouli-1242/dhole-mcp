"""P04 贺兰珠 · HR L1 —— 登录态保持 + 凭据不进参数 + 跨会话找回。"""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(4, "贺兰珠·HR")

p.set_scenario("S1-登录一次(凭据存库,不进参数)")
p.call("cred_defaults", {"set": {"auth": {"type": "basic", "username": "hr01", "password": "hr-secret"}, "host": "127.0.0.1:8399"}})
r = p.call("web_act", {"url": "http://127.0.0.1:8399/login", "actions": [
    {"fill": {"selector": "#username", "text": "hr01"}},
    {"fill": {"selector": "#password", "text": "hr-secret"}},
    {"click": {"selector": "#login-btn", "confirm": True}},
], "timeout": 60000})
p.observe("login", r, "summary")
p.pause(7)

p.set_scenario("S2-登录态里读账户页")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/account", "timeout": 30000})
p.observe("account", r, "content_ok", "summary")
p.pause(6)

p.set_scenario("S3-存两个候选人页")
for u in ("/article", "/collections"):
    r = p.call("web_read", {"url": f"http://127.0.0.1:8399{u}", "timeout": 30000})
    p.observe(f"read {u}", r, "content_ok")
    p.pause(5)

p.set_scenario("S4-跨会话找回")
pb = PersonaSim(4, "贺兰珠·下午")
r = pb.call("web_recall", {"query": "专题 推荐"})
p.rows(r, "results", 4, 130)
p.pause(6)
r = pb.call("sys_guide", {"topic": "call"})
p.observe("guide", r, "summary")
print("P04 DONE")
