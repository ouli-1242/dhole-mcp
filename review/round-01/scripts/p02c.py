"""P02 续：resume_token + url 组合。"""
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from driver import PersonaSim, ROOT
p = PersonaSim(2, "老周·后端")
p.seq = 17
p.set_scenario("S9-resume带url")
r = p.call("web_crawl", {"url": ROOT + "/collections",
                         "resume_token": "e4590e39e63a:0"})
p.observe("resume+url", r, "summary", "pages_crawled", "error", "next_action")
print("P02C DONE")
