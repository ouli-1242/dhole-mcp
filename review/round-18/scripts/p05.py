"""P05 闻笛 · 安全审计 L3 —— 密码/cookie/bearer 进入后，回执与 state 是否回显明文。"""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim, ROOT

SECRET = "S3cret-闻笛"
BEARER = "sk-live-DEADBEEF"
p = PersonaSim(5, "闻笛·安全审计")
p.call("cred_defaults", {"set": {"allow_private": True}})


def leak(label, receipt, needle):
    dump = json.dumps(receipt, ensure_ascii=False)
    print(f"   {label}: secret_in_receipt={needle in dump}")


p.set_scenario("S1-auth 缺 host")
r = p.call("cred_defaults", {"set": {"auth": {"type": "basic", "username": "auditor",
                                               "password": SECRET}}})
print("   err:", str(r.get("error"))[:220])
leak("S1", r, SECRET)

p.pause(2)
p.set_scenario("S2-auth 带 host")
r = p.call("cred_defaults", {"set": {"auth": {"host": "127.0.0.1", "type": "basic",
                                               "username": "auditor", "password": SECRET}}})
print("   summary:", str(r.get("summary"))[:180], "err:", str(r.get("error"))[:120])
leak("S2", r, SECRET)

p.pause(2)
p.set_scenario("S3-show 回看")
r = p.call("cred_defaults", {"show": True})
print("   summary:", str(r.get("summary"))[:220])
leak("S3", r, SECRET)

p.pause(3)
p.set_scenario("S4-Authorization 明文头")
r = p.call("web_request", {"url": ROOT + "/account?cb=p05", "method": "GET",
                           "headers": {"Authorization": "Bearer " + BEARER}})
print("   status:", (r.get("metadata") or {}).get("status"), "err:", str(r.get("error"))[:120])
leak("S4", r, BEARER)

p.pause(3)
p.set_scenario("S5-带凭据读私有页")
r = p.call("web_read", {"url": ROOT + "/private/secret?cb=p05"})
print("   ok:", r.get("content_ok"), "err:", str(r.get("error"))[:140])
leak("S5", r, SECRET)

p.pause(2)
p.set_scenario("S6-sys_state 汇总")
r = p.call("sys_state", {})
print("   summary:", str(r.get("summary"))[:200])
leak("S6", r, SECRET)
print("   bearer_in_state:", BEARER in json.dumps(r, ensure_ascii=False))
