"""P04 老纪 · 运维 L2 —— URL 形状（无协议/ftp/file/javascript/data/超长/空格）与 method 拼错。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim, ROOT

p = PersonaSim(4, "老纪·运维工程师")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-无协议 URL")
r = p.call("web_request", {"url": "127.0.0.1:8399/article", "method": "GET"})
print("   err:", str(r.get("error"))[:200])

p.pause(2)
p.set_scenario("S2-ftp 协议")
r = p.call("web_request", {"url": "ftp://127.0.0.1/x", "method": "GET"})
print("   err:", str(r.get("error"))[:200])

p.pause(2)
p.set_scenario("S3-file 协议")
r = p.call("web_request", {"url": "file:///C:/Windows/win.ini", "method": "GET"})
print("   err:", str(r.get("error"))[:200])

p.pause(2)
p.set_scenario("S4-javascript 协议")
r = p.call("web_read", {"url": "javascript:alert(1)"})
print("   err:", str(r.get("error"))[:200])

p.pause(2)
p.set_scenario("S5-method 拼错")
r = p.call("web_request", {"url": ROOT + "/article?cb=p04a", "method": "GETT"})
print("   err:", str(r.get("error"))[:240])

p.pause(2)
p.set_scenario("S6-method 小写")
r = p.call("web_request", {"url": ROOT + "/article?cb=p04b", "method": "get"})
print("   ok:", r.get("content_ok"), "err:", str(r.get("error"))[:120])

p.pause(2)
p.set_scenario("S7-含空格与中文的 URL")
r = p.call("web_read", {"url": ROOT + "/article?q=机械 键盘&cb=p04c"})
print("   ok:", r.get("content_ok"), "err:", str(r.get("error"))[:200])

p.pause(2)
p.set_scenario("S8-data 协议")
r = p.call("web_read", {"url": "data:text/html,<h1>hi</h1>"})
print("   err:", str(r.get("error"))[:200])

p.pause(2)
p.set_scenario("S9-收尾正常 request")
r = p.call("web_request", {"url": ROOT + "/about?cb=p04d", "method": "GET"})
print("   ok:", r.get("content_ok"), "chars:", r.get("total_extracted_chars"))
