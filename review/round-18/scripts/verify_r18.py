"""round-18 P5 复验：R18-001 真机 + R16-001 / r15 / save_to / 截图·PDF / r13·r14 家产。"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim, ROOT

F = Path(__file__).resolve().parent.parent / "sessions" / "files"
F.mkdir(parents=True, exist_ok=True)
SITE = "http://127.0.0.1:8398"
p = PersonaSim(17, "P5 复验")
p.call("cred_defaults", {"set": {"allow_private": True}})
ok_all = True


def check(label, cond, detail=""):
    global ok_all
    ok_all = ok_all and bool(cond)
    print(f"   [{'PASS' if cond else 'FAIL'}] {label} {detail}")


# V1 R18-001
p.set_scenario("V1-R18-001-press-key")
for i, key in enumerate([["Enter"], 13, {"code": 13}]):
    r = p.call("web_act", {"url": ROOT + "/frame/inner?cb=v18-%d" % i, "timeout": 30000,
                           "actions": [{"press": {"selector": "#note", "key": key}}]})
    err = str(r.get("error"))
    check("非字符串 key 当场具名 %r" % (key,),
          "press.key" in err and "key string" in err and "selector matched no element" not in err,
          "| " + err[:120])
r = p.call("web_act", {"url": ROOT + "/frame/inner?cb=v18-ok", "timeout": 30000,
                       "actions": [{"press": {"selector": "#note", "key": "Enter"}}]})
acts = (r.get("metadata") or {}).get("actions") or []
check("合法 key 仍成功", bool(acts) and acts[0].get("ok") is True)

# V2 R16-001 meta-only 编码
p.set_scenario("V2-R16-001-编码")
g = p.call("web_read", {"url": SITE + "/gbk.html?cb=v18"})
gt = "".join(g.get("content") or [])
check("GBK meta-only 解码", "国标编码" in gt or "这是一个 GBK" in gt, "| " + gt[:60])
l = p.call("web_read", {"url": SITE + "/latin1.html?cb=v18"})
lt = "".join(l.get("content") or [])
check("Latin-1 meta-only 解码", "français" in lt and "é" in lt, "| " + lt[:60])
c = p.call("web_crawl", {"url": SITE + "/gbk.html?cb=v18c", "max_pages": 1, "timeout": 30000})
cpages = c.get("pages") or []
ctext = "".join((cpages[0].get("content") or [""])[0]) if cpages else ""
check("crawl GBK 不占位/不乱码", "国标编码" in ctext or "GBK" in ctext, "| " + ctext[:60])

# V3 r15 空框 property
p.set_scenario("V3-r15-空框property")
r = p.call("web_act", {"url": ROOT + "/checkout?cb=v18p", "timeout": 30000,
                       "actions": [{"fill": {"selector": "#qty", "text": ""}},
                                   {"read": {"selector": "#qty", "property": "value"}}]})
acts = (r.get("metadata") or {}).get("actions") or []
a1 = acts[1] if len(acts) > 1 else {}
check("空框 property 读 ok=False + current value",
      a1.get("ok") is False and "current value" in str(a1.get("error")),
      "| " + str(a1.get("error"))[:120])

# V4 save_to
p.set_scenario("V4-save_to")
t = F / "v18-笔记.md"
t.unlink(missing_ok=True)
r = p.call("web_read", {"url": ROOT + "/article?cb=v18s", "save_to": str(t)})
meta = r.get("metadata") or {}
disk = t.read_bytes() if t.exists() else b""
check("保存落盘 + sha256 一致", t.exists() and hashlib.sha256(disk).hexdigest() == meta.get("sha256"),
      "| bytes=%s" % meta.get("bytes"))
r2 = p.call("web_read", {"url": ROOT + "/article?cb=v18s2", "save_to": str(t)})
check("同路径二次保存被拒（覆盖门）", "save_to" in str(r2.get("error")), "| " + str(r2.get("error"))[:100])
small = F / "v18-small.md"
small.unlink(missing_ok=True)
r3 = p.call("web_read", {"url": ROOT + "/article?cb=v18s3", "save_to": str(small),
                         "max_save_bytes": 8})
check("小帽拒绝且不落半截", not small.exists() and "max_save_bytes" in str(r3.get("error")),
      "| " + str(r3.get("error"))[:100])

# V5 截图 + PDF + doc_parse
p.set_scenario("V5-截图PDF")
shot = F / "v18-shot.png"
shot.unlink(missing_ok=True)
r = p.call("web_act", {"url": ROOT + "/article?cb=v18shot", "timeout": 45000,
                       "actions": [{"screenshot": {"save_to": str(shot), "full_page": True}}]})
check("整页截图落盘且 PNG 头正确",
      shot.exists() and shot.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n",
      "| %s bytes" % (shot.stat().st_size if shot.exists() else 0))
pdf = F / "v18-page.pdf"
pdf.unlink(missing_ok=True)
r = p.call("web_act", {"url": ROOT + "/article?cb=v18pdf", "timeout": 45000,
                       "actions": [{"pdf": {"save_to": str(pdf)}}]})
check("PDF 落盘且 %PDF 头正确", pdf.exists() and pdf.read_bytes()[:4] == b"%PDF",
      "| %s bytes" % (pdf.stat().st_size if pdf.exists() else 0))
d = p.call("doc_parse", {"file_path": str(pdf)})
dt = "".join(d.get("content") or [])
check("PDF → doc_parse 往返", ("本地抓取" in dt or "缓存" in dt), "| " + dt[:60])

# V6 r13/r14 家产
p.set_scenario("V6-r13r14")
f = p.call("feed_read", {"urls": [ROOT + "/feed.xml"], "max_items": -3})
check("feed max_items 负数拒绝", "max_items" in str(f.get("error")), "| " + str(f.get("error"))[:100])
s = p.call("web_read", {"url": ROOT + "/article?cb=v18sel", "css_selector": "#nope-v18"})
check("selector 0 匹配注记在 summary",
      "matched 0 elements" in str(s.get("summary")), "| " + str(s.get("summary"))[:100])
r1 = p.call("web_read", {"url": ROOT + "/article?cb=v18cache"})
r2 = p.call("web_read", {"url": ROOT + "/article?cb=v18cache"})
check("缓存回放 fetched_at 不晚于首读",
      r2.get("fetched_at") == r1.get("fetched_at"), "| %s" % r1.get("fetched_at"))

print("")
print("VERDICT:", "ALL_PASS" if ok_all else "FAIL")
