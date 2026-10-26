"""round-16 P5 复验：多语言正向 + r13/r14/r15 回归 + R16-001 已知缺陷复现。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(16, "r16复验")
p.call("cred_defaults", {"set": {"allow_private": True}})
fails = []


def check(name, ok, detail):
    print(("[PASS] " if ok else "[FAIL] ") + name)
    print("      " + str(detail)[:280])
    if not ok:
        fails.append(name)


def text(r):
    return "".join(str(x) for x in (r.get("content") or []))


# V1 R15-001：空框 property 读仍是可读说明
r = p.call("web_act", {"url": "http://127.0.0.1:8399/login", "session_id": "v16empty",
                       "timeout": 45000,
                       "actions": [{"read": {"selector": "input[name=username]",
                                             "property": "value"}}]})
rows = (r.get("metadata") or {}).get("actions") or []
err = str(rows[0].get("error")) if rows else ""
check("V1 空框 property 读（R15-001 回归）",
      bool(rows) and rows[0].get("ok") is False and "current value" in err
      and "empty_hint" not in err, f"err={err[:150]}")

# V2 多语言正向：UTF-8 文字保真 + 非 ASCII URL 参数缓存键
def has_range(t, lo, hi):
    return any(lo <= ord(c) <= hi for c in t)

ar = p.call("web_read", {"url": "http://127.0.0.1:8398/ar.html?cb=ت"})
ja = p.call("web_read", {"url": "http://127.0.0.1:8398/ja.html?cb=日"})
ko = p.call("web_read", {"url": "http://127.0.0.1:8398/ko.html?cb=한"})
ru = p.call("web_read", {"url": "http://127.0.0.1:8398/ru.html?cb=р"})
u = "http://127.0.0.1:8399/article?cb=复验中文"
a = p.call("web_read", {"url": u})
b = p.call("web_read", {"url": u})
check("V2 UTF-8 四文字保真 + 中文 URL 参数两读同 ts",
      has_range(text(ar), 0x0600, 0x06FF) and has_range(text(ja), 0x3040, 0x30FF)
      and has_range(text(ko), 0xAC00, 0xD7A3) and has_range(text(ru), 0x0400, 0x04FF)
      and b.get("cached") is True and a.get("fetched_at") == b.get("fetched_at"),
      f"ar={has_range(text(ar),0x0600,0x06FF)} ja={has_range(text(ja),0x3040,0x30FF)} "
      f"ko={has_range(text(ko),0xAC00,0xD7A3)} ru={has_range(text(ru),0x0400,0x04FF)} "
      f"cn_cache={b.get('cached')} same_ts={a.get('fetched_at')==b.get('fetched_at')}")

# V3 R16-001 已知缺陷复现（不计 PASS/FAIL，只记录形状）
gbk = p.call("web_read", {"url": "http://127.0.0.1:8398/gbk.html?cb=v16gbk"})
fr = p.call("web_read", {"url": "http://127.0.0.1:8398/latin1.html?cb=v16fr"})
gt, ft = text(gbk), text(fr)
print("[KNOWN] V3 R16-001 仍复现：")
print("      gbk ok=%s content=%r" % (gbk.get("content_ok"), gt[:90]))
print("      latin1 ok=%s replacement=%s content=%r" % (
    fr.get("content_ok"), "\ufffd" in ft, ft[:70]))

# V4 r13/r14：css 注记 / feed -3 / 相对 since / offset 窗口
css = p.call("web_read", {"url": "http://127.0.0.1:8399/article?cb=v16css",
                          "css_selector": "h1"})
un = "http://127.0.0.1:8399/article?cb=v16note"
n1 = p.call("web_read", {"url": un, "css_selector": "#no-such"})
n2 = p.call("web_read", {"url": un, "css_selector": "#no-such"})
neg = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"], "max_items": -3})
since = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"],
                             "since": "3d", "max_items": 3})
s = str(((since.get("feeds") or [{}])[0]).get("since") or "")
w1 = p.call("web_read", {"url": "http://127.0.0.1:8399/longaside?cb=v16win",
                         "max_content_chars": 500})
w2 = p.call("web_read", {"url": "http://127.0.0.1:8399/longaside?cb=v16win",
                         "max_content_chars": 500, "offset": w1.get("next_offset") or 500})
check("V4 r13/r14 四件（css/注记/feed -3/相对 since+窗口）",
      css.get("content_ok") is True
      and "matched 0 elements" in str(n1.get("summary"))
      and n2.get("cached") is True and "matched 0 elements" in str(n2.get("summary"))
      and bool(neg.get("error")) and "T" in s
      and " of " in str(w2.get("summary")) and "markdown" in str(w2.get("summary")),
      f"css={css.get('content_ok')} note={('matched 0 elements' in str(n2.get('summary')))} "
      f"neg={str(neg.get('error'))[:50]} since={s} win={str(w2.get('summary'))[:60]!r}")

# V5 多语言 HTML 的 selector 读（web_act）+ 阿语填表
act = p.call("web_act", {"url": "http://127.0.0.1:8398/mixed.html?cb=v16act",
                         "timeout": 45000, "actions": [{"read": {"selector": "p"}}]})
arows = (act.get("metadata") or {}).get("actions") or []
fill = p.call("web_act", {"url": "http://127.0.0.1:8399/login", "timeout": 45000,
                          "actions": [
                              {"fill": {"selector": "input[name=username]", "text": "ليلى"}},
                              {"read": {"selector": "input[name=username]", "property": "value"}},
                          ]})
frows = (fill.get("metadata") or {}).get("actions") or []
check("V5 web_act 多语言：混合页 selector 读 + 阿语填表读回",
      bool(arows) and arows[0].get("ok") is True and "العربية" in str(arows[0].get("read"))
      and bool(frows) and frows[-1].get("read") == "ليلى",
      f"read={str(arows[0].get('read'))[:60]!r} fill={frows[-1].get('read')!r}" if arows and frows else "no rows")

print("VERIFY R16 " + ("DONE" if not fails else ("FAILED: " + ", ".join(fails))))
