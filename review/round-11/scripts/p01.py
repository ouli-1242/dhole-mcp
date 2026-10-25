"""P01 桑无咎5 · 字幕组长 L3 猎手 —— doc_parse 深水（编码/伪 PDF/多 sheet）。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

fdir = Path(__file__).resolve().parent.parent / "sessions" / "files"
fdir.mkdir(parents=True, exist_ok=True)

utf = fdir / "subs_utf8.csv"
utf.write_text("时间轴,台词\n00:01,你好\n00:02,再见\n", encoding="utf-8")
gbk = fdir / "subs_gbk.csv"
gbk.write_bytes("时间轴,台词\n00:01,设备检查\n00:02,开始录制\n".encode("gbk"))
fake = fdir / "not_a_pdf.pdf"
fake.write_bytes(b"%PDF-1.4 this is not really a pdf body \xff\xff garbage")

p = PersonaSim(1, "桑无咎5·字幕组长")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-UTF-8 CSV")
r = p.call("doc_parse", {"file_path": str(utf)})
print(f"   summary: {str(r.get('summary'))[:130]}")

p.pause(6)
p.set_scenario("S2-GBK CSV(R01 家产:不乱码)")
r = p.call("doc_parse", {"file_path": str(gbk)})
print(f"   summary: {str(r.get('summary'))[:130]}")
body = " ".join(str(x) for x in (r.get("content") or []))[:120]
print(f"   正文采样: {body}")
print(f"   乱码检查: {'设备检查' in body or '开始录制' in body}")

p.pause(6)
p.set_scenario("S3-伪 PDF(not_a_pdf 具名)")
r = p.call("doc_parse", {"file_path": str(fake)})
print(f"   err: {str(r.get('error'))[:170]} | summary: {str(r.get('summary'))[:90]}")
print("P01 DONE")
