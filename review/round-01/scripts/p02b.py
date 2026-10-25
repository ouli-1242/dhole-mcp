"""P02 追加：老周对 resume 占位符 / 续爬 scope / recall / offset 钳制的追打。"""
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from driver import PersonaSim, ROOT

p = PersonaSim(2, "老周·后端")
p.seq = 12

p.set_scenario("S5-resume占位符回打")
r = p.call("web_crawl", {"resume_token": "e4590e39e63a:0"})
p.observe("resume echo", r, "summary", "pages_crawled", "error")
p.pause(8)

p.set_scenario("S6-path_include scope")
r = p.call("web_crawl", {"url": ROOT + "/collections", "max_pages": 8,
                         "path_include": ["/work"]})
p.observe("scope", r, "summary", "pages_crawled")
p.pause(8)

p.set_scenario("S7-recall换词")
r = p.call("web_recall", {"query": "抓取礼仪 默认", "limit": 5})
p.observe("recall", r, "summary")
p.rows(r, "results", 5)
p.pause(8)

p.set_scenario("S8-offset钳制对照")
r = p.call("web_read", {"url": ROOT + "/longaside", "offset": 0,
                        "max_content_chars": 800})
p.observe("off0", r, "total_extracted_chars", "next_offset")
p.pause(6)
r = p.call("web_read", {"url": ROOT + "/longaside", "offset": 1295,
                        "max_content_chars": 800})
p.observe("off1295", r, "total_extracted_chars", "next_offset")

print("P02B DONE")
