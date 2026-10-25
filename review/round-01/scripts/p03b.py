"""P03 追加：robots 拒绝对照 + 日期挑刺（moguvet）。"""
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from driver import PersonaSim, ROOT
p = PersonaSim(3, "Sarah·自媒体")
p.seq = 6
p.set_scenario("S5-robots对照")
r = p.call("web_read", {"url": ROOT + "/article", "max_content_chars": 500})
p.observe("fixture robots", r, "metadata.robots", "content_ok")
p.pause(6)
r = p.call("web_read", {"url": "https://www.sohu.com/a/853570744_120580522"})
p.observe("sohu retry", r, "error", "diagnosis.code", "next_action")
p.pause(8)
p.set_scenario("S6-日期挑刺")
r = p.call("web_read", {"url": "https://m.moguvet.com/article/1004362",
                        "max_content_chars": 2000})
p.observe("moguvet", r, "metadata.date", "metadata.published",
          "metadata.title", "total_extracted_chars", "metadata.robots")
print("P03B DONE")
