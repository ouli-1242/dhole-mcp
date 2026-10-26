"""round-14 P5 真重启复验：主题四面 + r13 回归抽检。"""
import sys, json, uuid, time
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import Client

c = Client()
c.rpc("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                     "clientInfo": {"name": "r14-verify", "version": "0"}})
c.rpc("notifications/initialized")


def call(tool, args):
    r, _ = c.rpc("tools/call", {"name": tool, "arguments": args})
    txt = "".join(b.get("text", "") for b in (r.get("result") or {}).get("content", [])
                  if isinstance(b, dict))
    try:
        return json.loads(txt) if txt else {}
    except Exception:
        return {"_raw": txt}


call("cred_defaults", {"set": {"allow_private": True}})


def emit(head, ok, detail):
    print(f"[{'PASS' if ok else 'FAIL'}] {head}")
    print(f"      {detail[:260]}")


# V1 · 相对 since 3d
r = call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"], "since": "3d",
                       "max_items": 5})
feed = (r.get("feeds") or [{}])[0]
since = feed.get("since") or ""
ok1 = False
try:
    dt = datetime.fromisoformat(since)
    delta = abs((datetime.now(timezone.utc) - dt).total_seconds() - 3 * 86400)
    ok1 = delta < 300
except Exception:
    pass
emit("V1 相对 since 3d 从当下起算", ok1,
     f"since={since} older={feed.get('items_older_than_since')} "
     f"items={len(feed.get('items') or [])}")

# V2 · 缓存回访
u = "http://127.0.0.1:8399/article?cb=v14cache-" + uuid.uuid4().hex[:6]
a = call("web_read", {"url": u})
b = call("web_read", {"url": u})
emit("V2 回访缓存：第二读 cached 且时间戳不变",
     b.get("cached") is True and a.get("fetched_at") == b.get("fetched_at"),
     f"first={a.get('cached')} second={b.get('cached')} same_ts="
     f"{a.get('fetched_at') == b.get('fetched_at')}")

# V3 · check 两相（/changing 必变）
u = "http://127.0.0.1:8399/changing?cb=v14check-" + uuid.uuid4().hex[:6]
x = call("web_read", {"urls": [u], "extraction_type": "check"})
y = call("web_read", {"urls": [u], "extraction_type": "check"})
row = (y.get("results") or [{}])[0]
emit("V3 check 两相：首问基线 / 再问 changed",
     "baseline recorded" in str(x.get("summary")) and row.get("changed") is True,
     f"first={str(x.get('summary'))[:80]} second_changed={row.get('changed')}")

# V4 · offset 窗口命名
u = "http://127.0.0.1:8399/longaside?cb=v14off-" + uuid.uuid4().hex[:6]
p1 = call("web_read", {"url": u, "max_content_chars": 500})
p2 = call("web_read", {"url": u, "max_content_chars": 500,
                       "offset": p1.get("next_offset") or 500})
s2 = str(p2.get("summary") or "")
emit("V4 offset 续片点名本窗 of 总量",
     p1.get("is_truncated") is True and "of 7.0KB markdown" in s2,
     f"next={p1.get('next_offset')} summary={s2[:120]}")

# V5 · r13 回归：css 收窄 + selector 注记 + feed 负数
u = "http://127.0.0.1:8399/article?cb=v14css-" + uuid.uuid4().hex[:6]
css = call("web_read", {"url": u, "css_selector": "h1"})
note_u = "http://127.0.0.1:8399/article?cb=v14note-" + uuid.uuid4().hex[:6]
n1 = call("web_read", {"url": note_u, "css_selector": "#no-such"})
n2 = call("web_read", {"url": note_u, "css_selector": "#no-such"})
neg = call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"], "max_items": -3})
emit("V5 r13 回归：css 收窄 / 注记 live+hit / feed 负数",
     css.get("content_ok") is True and css.get("total_extracted_chars") == 10
     and "matched 0 elements" in str(n1.get("summary"))
     and "matched 0 elements" in str(n2.get("summary"))
     and str(neg.get("error") or "").startswith("invalid_request: max_items must be"),
     f"css_ok={css.get('content_ok')} chars={css.get('total_extracted_chars')} "
     f"note_live={'matched 0 elements' in str(n1.get('summary'))} "
     f"note_hit={'matched 0 elements' in str(n2.get('summary'))}")
print("VERIFY R14 DONE")
