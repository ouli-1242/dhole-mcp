"""round-17 P5 复验：R16-001 / save_to 合同 / 截图·PDF / r13-r15 回归 / 上传。"""
import hashlib
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

F = Path(__file__).resolve().parent.parent / "sessions" / "files"
F.mkdir(parents=True, exist_ok=True)
p = PersonaSim(17, "r17复验")
p.call("cred_defaults", {"set": {"allow_private": True}})
fails = []


def check(name, ok, detail):
    print(("[PASS] " if ok else "[FAIL] ") + name)
    print("      " + str(detail)[:280])
    if not ok:
        fails.append(name)


def text(r):
    return "".join(str(x) for x in (r.get("content") or []))


# V1 R16-001：meta-only 编码页 read + crawl
gbk = p.call("web_read", {"url": "http://127.0.0.1:8398/gbk.html?cb=v17gbk"})
fr = p.call("web_read", {"url": "http://127.0.0.1:8398/latin1.html?cb=v17fr"})
cr = p.call("web_crawl", {"url": "http://127.0.0.1:8398/gbk.html", "max_pages": 1})
crow = (cr.get("pages") or [{}])[0]
check("V1 R16-001：GBK/Latin-1 read 与 crawl 按 meta 解码",
      "国标编码页面" in text(gbk) and "français" in text(fr)
      and "\ufffd" not in text(fr) and "国标编码页面" in text(crow),
      f"gbk={text(gbk)[:40]!r} fr={text(fr)[:40]!r} crawl={text(crow)[:40]!r}")

# V2 save_to：落盘哈希 / 覆盖门 / 小帽拒绝 / 中文名
target = F / "verify17.md"
target.unlink(missing_ok=True)
u = "http://127.0.0.1:8399/article?cb=v17save"
r = p.call("web_read", {"url": u, "save_to": str(target)})
meta = r.get("metadata") or {}
sha = hashlib.sha256(target.read_bytes()).hexdigest() if target.exists() else ""
over = p.call("web_read", {"url": u + "&x=2", "save_to": str(target)})
cap = p.call("web_read", {"url": u + "&x=3", "save_to": str(F / "verify17cap.md"),
                          "max_save_bytes": 50})
check("V2 save_to：哈希一致 / 覆盖拒 / 小帽拒且不落盘",
      target.exists() and sha == meta.get("sha256")
      and "already exists" in str(over.get("error"))
      and "save_to limit of 50" in str(cap.get("error"))
      and not (F / "verify17cap.md").exists(),
      f"sha_ok={sha == meta.get('sha256')} over={str(over.get('error'))[:50]} cap={str(cap.get('error'))[:60]}")

# V3 截图 PNG + PDF 落盘/读回
shot = F / "verify17.png"
pdf = F / "verify17.pdf"
shot.unlink(missing_ok=True)
pdf.unlink(missing_ok=True)
act = p.call("web_act", {"url": "http://127.0.0.1:8399/article?cb=v17shot",
                         "timeout": 45000,
                         "actions": [{"screenshot": {"save_to": str(shot), "full_page": True}},
                                     {"pdf": {"save_to": str(pdf)}}]})
rows = (act.get("metadata") or {}).get("actions") or []
dp = p.call("doc_parse", {"file_path": str(pdf)})
check("V3 截图/PDF 落盘 + doc_parse 读回",
      shot.exists() and shot.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
      and pdf.exists() and pdf.read_bytes()[:5] == b"%PDF-"
      and dp.get("content_ok") is True and len(text(dp)) > 0,
      f"png={shot.exists()} pdf={pdf.exists()} parse_chars={len(text(dp))}")

# V4 r15/r14/r13 回归
empty = p.call("web_act", {"url": "http://127.0.0.1:8399/login", "session_id": "v17empty",
                           "timeout": 45000,
                           "actions": [{"read": {"selector": "input[name=username]",
                                                 "property": "value"}}]})
erows = (empty.get("metadata") or {}).get("actions") or []
eerr = str(erows[0].get("error")) if erows else ""
css = p.call("web_read", {"url": "http://127.0.0.1:8399/article?cb=v17css",
                          "css_selector": "h1"})
un = "http://127.0.0.1:8399/article?cb=v17note"
n1 = p.call("web_read", {"url": un, "css_selector": "#no-such"})
n2 = p.call("web_read", {"url": un, "css_selector": "#no-such"})
neg = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"], "max_items": -3})
since = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"],
                             "since": "3d", "max_items": 3})
s = str(((since.get("feeds") or [{}])[0]).get("since") or "")
w1 = p.call("web_read", {"url": "http://127.0.0.1:8399/longaside?cb=v17win",
                         "max_content_chars": 500})
w2 = p.call("web_read", {"url": "http://127.0.0.1:8399/longaside?cb=v17win",
                         "max_content_chars": 500, "offset": w1.get("next_offset") or 500})
chk1 = p.call("web_read", {"urls": ["http://127.0.0.1:8399/changing?cb=v17chk"],
                           "extraction_type": "check"})
chk2 = p.call("web_read", {"urls": ["http://127.0.0.1:8399/changing?cb=v17chk"],
                           "extraction_type": "check"})
check("V4 r15/r14/r13 回归（空框/收窄/注记/负数/since/窗口/check）",
      "current value" in eerr and "empty_hint" not in eerr
      and css.get("content_ok") is True
      and "matched 0 elements" in str(n1.get("summary"))
      and n2.get("cached") is True and "matched 0 elements" in str(n2.get("summary"))
      and bool(neg.get("error")) and "T" in s
      and " of " in str(w2.get("summary"))
      and "baseline" in str(chk1.get("summary")) and "changed" in str(chk2.get("summary")),
      f"empty={eerr[:40]!r} css={css.get('content_ok')} note={('matched 0 elements' in str(n2.get('summary')))} "
      f"since={s} win={str(w2.get('summary'))[:40]!r}")

# V5 上传中文名 + #receipt
up = F / "verify17-上传.txt"
up.write_text("复验上传\n", encoding="utf-8")
ur = p.call("web_act", {"url": "http://127.0.0.1:8399/upload", "timeout": 45000,
                        "actions": [
                            {"upload": {"selector": "input[type=file]", "files": [str(up)]}},
                            {"click": {"selector": "button[type=submit]", "confirm": True}},
                            {"read": {"selector": "#receipt"}},
                        ]})
urows = (ur.get("metadata") or {}).get("actions") or []
receipt = str(urows[-1].get("read")) if urows else ""
check("V5 上传中文名文件 + 回执点名",
      "verify17-上传.txt" in receipt and "bytes" in receipt,
      f"receipt={receipt[:120]!r}")

print("VERIFY R17 " + ("DONE" if not fails else ("FAILED: " + ", ".join(fails))))
