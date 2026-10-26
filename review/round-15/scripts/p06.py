"""P06 林澄 · 兽医 L2 —— 两份本地文件交错解析 · 指定页 · 网页读。"""
import shutil
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

ROUND = Path(__file__).resolve().parent.parent
fdir = ROUND / "sessions" / "files"
fdir.mkdir(parents=True, exist_ok=True)
prev = ROUND.parent / "round-14" / "sessions" / "files"
pdf = fdir / "pharmacopoeia.pdf"
book = fdir / "care_table.xlsx"
if not pdf.exists():
    pdf.write_bytes((prev / "pharmacopoeia.pdf").read_bytes())
if not book.exists():
    try:
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "剂量"
        ws.append(["动物", "药", "剂量"])
        ws.append(["犬", "阿莫西林", "10mg/kg"])
        ws.append(["猫", "甲硝唑", "5mg/kg"])
        wb.save(book)
    except Exception as exc:
        print("   (xlsx构造失败: %s)" % exc)

p = PersonaSim(6, "林澄·兽医")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-PDF 指定页 2-3")
a = p.call("doc_parse", {"file_path": str(pdf), "pages": "2-3"})
ta = "".join(str(x) for x in (a.get("content") or []))
print(f"   ok={a.get('content_ok')} chars={len(ta)} summary={str(a.get('summary'))[:140]}")

p.pause(4)
p.set_scenario("S2-本地 xlsx 剂量表")
b = p.call("doc_parse", {"file_path": str(book)})
tb = "".join(str(x) for x in (b.get("content") or []))
print(f"   ok={b.get('content_ok')} chars={len(tb)} 犬={'犬' in tb} 猫={'猫' in tb}")

p.pause(4)
p.set_scenario("S3-PDF 只第 1 页（与 2-3 对比）")
c = p.call("doc_parse", {"file_path": str(pdf), "pages": "1"})
tc = "".join(str(x) for x in (c.get("content") or []))
print(f"   ok={c.get('content_ok')} chars={len(tc)} 与前片不同={tc != ta}")

p.pause(4)
p.set_scenario("S4-网页对照读")
r = p.call("web_read", {"url": "http://127.0.0.1:8399/about"})
print(f"   ok={r.get('content_ok')} chars={r.get('total_extracted_chars')} "
      f"summary={str(r.get('summary'))[:120]}")
print("P06 DONE")
