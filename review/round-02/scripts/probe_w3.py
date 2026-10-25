"""W3 probe: two consecutive web_read calls to the fixture; the 2nd should
carry the learned-ceiling sentence once EMA has one sample."""
import re
import sys, os; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from driver import PersonaSim

p = PersonaSim(99, "w3-probe")
r1 = p.call("web_read", {"url": "http://127.0.0.1:8399/article"})
t1 = str(r1)
m1 = re.findall(r"learned ceiling for this host[^\"']*", t1)
print("call-1 learned-note:", m1[:1] or "ABSENT")

r2 = p.call("web_read", {"url": "http://127.0.0.1:8399/article"})
t2 = str(r2)
m2 = re.findall(r"learned ceiling for this host[^\"']*", t2)
print("call-2 learned-note:", m2[:1] or "ABSENT")
budget = re.findall(r"(?:attempt|budget|bound)[^\"']{0,160}", t2)
print("call-2 budget-ish notes:", budget[:3])
