import re, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from driver import PersonaSim

p = PersonaSim(98, "w3-probe")
p.call("cred_defaults", {"set": {"allow_private": True}})
r1 = p.call("web_read", {"url": "http://127.0.0.1:8399/article"})
t1 = str(r1)
m1 = re.findall(r"learned ceiling for this host[^\"']*", t1)
print("call-1 learned-note:", m1[:1] or "ABSENT")
r2 = p.call("web_read", {"url": "http://127.0.0.1:8399/article"})
t2 = str(r2)
m2 = re.findall(r"learned ceiling for this host[^\"']*", t2)
print("call-2 learned-note:", m2[:1] or "ABSENT")
b2 = re.findall(r"[^\"']*(?:ceiling|budget|bound by)[^\"']*", t2)
print("call-2 budget-ish:", [b[:130] for b in b2][:4])
