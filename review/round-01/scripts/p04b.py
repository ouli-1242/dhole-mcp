"""P04 追加：直连 arxiv RSS。"""
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from driver import PersonaSim
p = PersonaSim(4, "林老师·研究员")
p.seq = 5
p.set_scenario("S4b-直连RSS")
r = p.call("feed_read", {"urls": ["https://arxiv.org/rss/cs.CL"], "max_items": 4})
p.observe("feed", r, "summary")
p.rows(r, "feeds", 2, 150)
print("P04B DONE")
