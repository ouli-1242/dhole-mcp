"""round-15 P5 复验：R15-001 真机 · 隔离 · 缓存时间戳 · r13/r14 回归。"""
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

p = PersonaSim(15, "r15复验")
N = str(int(time.time()))
p.call("cred_defaults", {"set": {"allow_private": True}})
fails = []


def check(name, ok, detail):
    print(("[PASS] " if ok else "[FAIL] ") + name)
    print("      " + str(detail)[:260])
    if not ok:
        fails.append(name)


# V1 R15-001：空框 property 读不再抛 Python 异常
r = p.call("web_act", {"url": "http://127.0.0.1:8399/login", "session_id": "v15empty",
                       "timeout": 45000,
                       "actions": [{"read": {"selector": "input[name=username]",
                                             "property": "value"}}]})
rows = (r.get("metadata") or {}).get("actions") or []
err = str(rows[0].get("error")) if rows else ""
check("V1 空框 property 读给可读说明而非未绑定异常",
      bool(rows) and rows[0].get("ok") is False and "empty_hint" not in err
      and "current value" in err,
      f"ok={rows[0].get('ok') if rows else None} err={err[:160]}")

# V2 双 session + pin 隔离
READ = [{"read": {"selector": "input[name=username]", "property": "value"}}]
r = p.call("web_act", {"url": "http://127.0.0.1:8399/login", "session_id": "v15a",
                       "pin": True, "timeout": 45000,
                       "actions": [{"fill": {"selector": "input[name=username]", "text": "甲"}}] + READ})
r = p.call("web_act", {"url": "http://127.0.0.1:8399/login", "session_id": "v15b",
                       "pin": True, "timeout": 45000,
                       "actions": [{"fill": {"selector": "input[name=username]", "text": "乙"}}] + READ})
r = p.call("web_act", {"url": "http://127.0.0.1:8399/login", "session_id": "v15a",
                       "pin": True, "timeout": 45000, "actions": READ})
acts = (r.get("metadata") or {}).get("actions") or []
val = acts[-1].get("read") if acts else None
check("V2 双 pin session 隔离（A 仍读回甲）",
      val == "甲" and "pin hit" in str(r.get("summary")).lower(), f"read={val!r}")

# V3 live -> replay fetched_at：回放不得比 live 更新
U = "http://127.0.0.1:8399/work/1?cb=v15ts" + N
a = p.call("web_read", {"url": U})
b = p.call("web_read", {"url": U})
tsa, tsb = str(a.get("fetched_at")), str(b.get("fetched_at"))
ok = bool(tsa) and bool(tsb) and tsb <= tsa and b.get("cached") is True
check("V3 缓存回放 fetched_at 不晚于 live（≤1s 漂移如实记录）",
      ok, f"live={tsa} replay={tsb} cached={b.get('cached')}")

# V4 r14：相对 since + offset 窗口 + check 两相
r = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"],
                         "since": "3d", "max_items": 5})
f = (r.get("feeds") or [{}])[0]
s = str(f.get("since") or "")
r2 = p.call("web_read", {"url": "http://127.0.0.1:8399/longaside?cb=v15win",
                         "max_content_chars": 500})
r3 = p.call("web_read", {"url": "http://127.0.0.1:8399/longaside?cb=v15win",
                         "max_content_chars": 500, "offset": r2.get("next_offset") or 500})
summ = str(r3.get("summary") or "")
c1 = p.call("web_read", {"urls": ["http://127.0.0.1:8399/changing?cb=v15chk" + N],
                         "extraction_type": "check"})
c2 = p.call("web_read", {"urls": ["http://127.0.0.1:8399/changing?cb=v15chk" + N],
                         "extraction_type": "check"})
check("V4 相对 since / offset 窗口 / check 两相",
      "T" in s and ("Z" in s or "+" in s) and " of " in summ and "markdown" in summ
      and "baseline" in str(c1.get("summary")) and "changed" in str(c2.get("summary")),
      f"since={s} window={summ[:80]!r} check={str(c2.get('summary'))[:70]!r}")

# V5 r13：css 收窄 / selector 注记 live+hit / feed 负数
a = p.call("web_read", {"url": "http://127.0.0.1:8399/article?cb=v15css",
                        "css_selector": "h1"})
u = "http://127.0.0.1:8399/article?cb=v15note"
n1 = p.call("web_read", {"url": u, "css_selector": "#no-such"})
n2 = p.call("web_read", {"url": u, "css_selector": "#no-such"})
neg = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"], "max_items": -3})
check("V5 r13 三件（收窄 / 注记 live+hit / feed -3）",
      a.get("content_ok") is True and (a.get("total_extracted_chars") or 0) > 0
      and "matched 0 elements" in str(n1.get("summary"))
      and n2.get("cached") is True and "matched 0 elements" in str(n2.get("summary"))
      and bool(neg.get("error")),
      f"css={a.get('content_ok')} live={('matched 0 elements' in str(n1.get('summary')))} "
      f"hit={n2.get('cached')} neg={str(neg.get('error'))[:60]}")

print("VERIFY R15 " + ("DONE" if not fails else ("FAILED: " + ", ".join(fails))))
