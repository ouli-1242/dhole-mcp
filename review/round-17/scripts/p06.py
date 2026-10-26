"""P06 杜衡 · 数据员 L2 —— PDF 动作落盘 → doc_parse 读回。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

F = Path(__file__).resolve().parent.parent / "sessions" / "files"
pdf = F / "article17.pdf"
pdf.unlink(missing_ok=True)

p = PersonaSim(6, "杜衡·数据员")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-把当前页打印成 PDF")
r = p.call("web_act", {"url": "http://127.0.0.1:8399/article?cb=p06pdf",
                       "timeout": 45000,
                       "actions": [{"pdf": {"save_to": str(pdf)}}]})
acts = (r.get("metadata") or {}).get("actions") or []
a = acts[0] if acts else {}
print(f"   ok={a.get('ok')} saved={a.get('saved')} bytes={a.get('saved_bytes')} "
      f"magic={pdf.read_bytes()[:5] if pdf.exists() else None}")

p.pause(4)
p.set_scenario("S2-doc_parse 读回整份 PDF")
r = p.call("doc_parse", {"file_path": str(pdf)})
t = "".join(str(x) for x in (r.get("content") or []))
print(f"   ok={r.get('content_ok')} chars={len(t)} summary={str(r.get('summary'))[:100]}")
print(f"   head={t[:100]!r}")

p.pause(3)
p.set_scenario("S3-只读第 1 页")
r = p.call("doc_parse", {"file_path": str(pdf), "pages": "1"})
t1 = "".join(str(x) for x in (r.get("content") or []))
print(f"   ok={r.get('content_ok')} chars={len(t1)} 与整份不同={t1 != t}")
print("P06 DONE")
