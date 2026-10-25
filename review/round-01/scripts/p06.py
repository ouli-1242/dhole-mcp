"""P06 小美 · 电商运营 · L2（急躁）—— round-01 真实模拟。"""
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from driver import PersonaSim, ROOT

p = PersonaSim(6, "小美·电商")
p.seq = 0

# S1 报表（多 sheet）
p.set_scenario("S1-报表")
r = p.call("doc_parse", {"file_path": "tests/sample.xlsx"})
p.observe("xlsx", r, "total_extracted_chars", "content")
p.pause(6)

# S2 批量读 12 个商品页（键名 results 考察）
p.set_scenario("S2-批量读")
urls = [ROOT + "/work/%d" % i for i in range(1, 13)]
r = p.call("web_read", {"urls": urls, "max_content_chars_per": 1200})
p.observe("batch", r, "summary")
p.rows(r, "results", 12, 110)
p.pause(10)

# S3 参数写错（急出来的）
p.set_scenario("S3-手滑")
r = p.call("web_read", {"url": 123})
p.observe("url as int", r, "error", "diagnosis.code")
r = p.call("web_read", {"url": ROOT + "/work/1", "anti_detect": "超级隐身"})
p.observe("bad enum", r, "error", "diagnosis.code")
p.pause(30, "场景间隔")

# S4 二维码页
p.set_scenario("S4-二维码")
r = p.call("web_read", {"url": ROOT + "/qr", "max_content_chars": 2000})
p.observe("qr page", r, "content_ok", "total_extracted_chars", "content",
          "page_type")
r = p.call("web_read", {"url": ROOT + "/qr.png"})
p.observe("qr png", r, "content_ok", "total_extracted_chars", "content",
          "page_type", "metadata")

print("P06 DONE")
