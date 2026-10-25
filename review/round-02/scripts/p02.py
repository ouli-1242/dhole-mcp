"""P02 杜蘅 · 法务/合规 · L2 —— round-02 真实模拟（PDF/交叉/archive/check）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(2, "杜蘅·法务")

p.set_scenario("S1-GDPR PDF 指定页")
r = p.call("web_read", {"url": "https://eur-lex.europa.eu/legal-content/EN/TXT/PDF/?uri=CELEX:32016R0679",
                        "pages": "1-2", "timeout": 90000})
p.observe("pdf", r, "summary", "page_type", "total_extracted_chars", "fetcher_used")
p.pause(12)

p.set_scenario("S2-双源交叉")
r = p.call("web_read", {"urls": ["https://ico.org.uk/for-organisations/uk-gdpr-guidance-and-resources/individual-rights/",
                                 "https://commission.europa.eu/law/law-topic/data-protection_en"],
                        "max_content_chars": 3000})
p.observe("batch2", r, "summary")
p.rows(r, "results", 2, 130)
p.pause(15)

p.set_scenario("S3-硬墙 archive 档")
r = p.call("web_read", {"url": "https://www.wsj.com/articles/tech-companies-data-privacy-11634996753",
                        "timeout": 60000})
p.observe("wall", r, "error", "content_ok", "archived_at", "source", "summary")
p.pause(15)

p.set_scenario("S4-check 批量基线")
r = p.call("web_read", {"urls": ["https://example.com/", "https://www.rfc-editor.org/rfc/rfc9110.html"],
                        "extraction_type": "check", "max_content_chars": 500})
p.observe("check1", r, "summary")
p.rows(r, "results", 2, 110)
p.pause(8)
r = p.call("web_read", {"urls": ["https://example.com/", "https://www.rfc-editor.org/rfc/rfc9110.html"],
                        "extraction_type": "check", "max_content_chars": 500})
p.observe("check2", r, "summary")
p.rows(r, "results", 2, 110)

print("P02 DONE")
