"""P05b 闻笛补条 —— 正确 host 形状存 auth、POST 带 bearer 头，查回执是否回显明文。"""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim, ROOT

SECRET = "S3cret-闻笛"
BEARER = "sk-live-DEADBEEF"
p = PersonaSim(5, "闻笛·安全审计")
p.set_scenario("补条")


def leak(label, receipt, needle):
    dump = json.dumps(receipt, ensure_ascii=False)
    print(f"   {label}: secret_in_receipt={needle in dump}")


r = p.call("cred_defaults", {"set": {"auth": {"type": "basic", "username": "auditor",
                                               "password": SECRET},
                                     "host": "127.0.0.1"}})
print("   stored:", str(r.get("message"))[:160], "err:", str(r.get("error"))[:120])
leak("store", r, SECRET)

p.pause(2)
r = p.call("cred_defaults", {"show": True})
print("   show:", str(r.get("message"))[:160])
leak("show", r, SECRET)

p.pause(2)
r = p.call("web_read", {"url": ROOT + "/account?cb=p05b"})
print("   read ok:", r.get("content_ok"), "chars:", r.get("total_extracted_chars"))
leak("read", r, SECRET)

p.pause(3)
r = p.call("web_request", {"url": ROOT + "/checkout?cb=p05b", "method": "POST",
                           "body": "item=x&qty=1",
                           "content_type": "application/x-www-form-urlencoded",
                           "headers": {"Authorization": "Bearer " + BEARER},
                           "confirm": True})
print("   post ok:", r.get("content_ok"), "err:", str(r.get("error"))[:120])
leak("post", r, BEARER)
leak("post-auth", r, SECRET)

p.pause(2)
r = p.call("cred_defaults", {"unset": ["auth"]})
print("   unset:", str(r.get("message"))[:160])
