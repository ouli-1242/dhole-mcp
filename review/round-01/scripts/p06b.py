"""P06 追加：批量读参数改对重发。"""
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from driver import PersonaSim, ROOT
p = PersonaSim(6, "小美·电商")
p.seq = 6
p.set_scenario("S2b-批量读重试")
urls = [ROOT + "/work/%d" % i for i in range(1, 13)]
r = p.call("web_read", {"urls": urls, "max_content_chars": 1200})
p.observe("batch", r, "summary")
p.rows(r, "results", 12, 110)
print("P06B DONE")
