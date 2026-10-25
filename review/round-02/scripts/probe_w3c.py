import re, sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from driver import PersonaSim

p = PersonaSim(97, "w3-probe")
p.call("cred_defaults", {"set": {"allow_private": True}})
for i in (1, 2, 3):
    r = p.call("web_read", {"url": f"http://127.0.0.1:8399/article?w3probe={i}"})
    t = str(r)
    m = re.findall(r"learned ceiling for this host[^\"']*", t)
    print(f"call-{i} learned-note:", m[:1] or "ABSENT")
    if i == 3:
        for b in re.findall(r"[^\"']*(?:ceiling|attempt budget|bound by|one HTTP attempt)[^\"']*", t)[:4]:
            print("   note:", b[:160])
    time.sleep(0.5)
