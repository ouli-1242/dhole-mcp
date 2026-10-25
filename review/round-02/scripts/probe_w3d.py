"""W3 evidence probe with a run-unique cache buster: call-1 warms the EMA
(cold -> no learned note, correctly), calls 2-3 must carry the learned
ceiling sentence. Saved to sessions/verify/w3-learned.txt."""
import re, sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from driver import PersonaSim

p = PersonaSim(93, "w3-probe")
p.call("cred_defaults", {"set": {"allow_private": True}})
tag = str(int(time.time()))
for i in (1, 2, 3):
    r = p.call("web_read", {"url": f"http://127.0.0.1:8399/article?w3={tag}-{i}"})
    t = str(r)
    m = re.findall(r"one HTTP attempt is bound by the learned ceiling[^\"']*", t)
    print(f"call-{i} learned-note:", m[:1] or "ABSENT")
    if i == 1:
        print("   (call-1 is the EMA-warming fetch; note may be absent here by design)")
