"""P09 韩青 · 财务会计 · L2 —— round-02 真实模拟（doc_parse 诊断链）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(9, "韩青·财务")

p.set_scenario("S1-xlsx 多 sheet + GBK csv")
r = p.call("doc_parse", {"file_path": "tests/sample.xlsx"})
p.observe("xlsx", r, "content_ok", "summary")
p.pause(5)
r = p.call("doc_parse", {"file_path": "tests/gbk_sample.csv"})
p.observe("gbk", r, "content_ok", "summary")
meta = r.get("metadata") or {}
print("      encoding:", meta.get("encoding"))
p.pause(6)

p.set_scenario("S2-加密 PDF")
r = p.call("doc_parse", {"file_path": "tests/background_checks.pdf"})
p.observe("pdf-ok", r, "content_ok", "summary")
p.pause(5)

p.set_scenario("S3-后缀骗人")
import tempfile, pathlib
fake = pathlib.Path(tempfile.gettempdir()) / "fake_invoice.pdf"
fake.write_bytes(b"this is not a pdf at all, just text pretending " * 10)
r = p.call("doc_parse", {"file_path": str(fake)})
p.observe("fake-pdf", r, "error", "diagnosis.code", "content_ok")
p.pause(5)

p.set_scenario("S4-批量读三张静态页")
r = p.call("web_read", {"urls": ["http://127.0.0.1:8399/invoice/1",
                                 "http://127.0.0.1:8399/invoice/2",
                                 "http://127.0.0.1:8399/invoice/3"],
                        "max_content_chars": 400, "cache_ttl": 0})
p.observe("batch", r, "summary")
p.rows(r, "results", 3, 100)

print("P09 DONE")
