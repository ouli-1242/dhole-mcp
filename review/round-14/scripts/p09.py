"""P09 花婶 · 园艺师 L1 —— feed 一周新品 · 本地 xlsx 养护表 · 回访一页 · state。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from driver import PersonaSim

fdir = Path(__file__).resolve().parent.parent / "sessions" / "files"
fdir.mkdir(parents=True, exist_ok=True)
book = fdir / "care_table.xlsx"
try:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "养护"
    ws.append(["花卉", "浇水", "日照"])
    ws.append(["月季", "隔日", "全日照"])
    ws.append(["绣球", "每日", "半阴"])
    wb.save(book)
except Exception as e:
    print(f"   (xlsx 构造失败: {e})")

p = PersonaSim(9, "花婶·园艺师")
p.call("cred_defaults", {"set": {"allow_private": True}})

p.set_scenario("S1-feed 一周新品")
r = p.call("feed_read", {"urls": ["http://127.0.0.1:8399/feed.xml"],
                         "since": "1w", "max_items": 3})
for x in r.get("feeds") or []:
    print(f"   since={x.get('since')} older={x.get('items_older_than_since')} "
          f"items={len(x.get('items') or [])}")

p.pause(5)
p.set_scenario("S2-本地养护表 xlsx")
r = p.call("doc_parse", {"file_path": str(book)})
txt = "".join(str(c) for c in (r.get("content") or []))
print(f"   ok={r.get('content_ok')} 月季={'月季' in txt} 绣球={'绣球' in txt} "
      f"chars={len(txt)}")

p.pause(5)
p.set_scenario("S3-行情页读两遍")
u = "http://127.0.0.1:8399/work/1?cb=p09-price"
a = p.call("web_read", {"url": u})
b = p.call("web_read", {"url": u})
print(f"   cached={a.get('cached')}->{b.get('cached')} "
      f"same_ts={a.get('fetched_at') == b.get('fetched_at')}")

p.pause(5)
p.set_scenario("S4-state 收尾")
r = p.call("sys_state", {})
print(f"   message: {str(r.get('message'))[:160]}")
print("P09 DONE")
